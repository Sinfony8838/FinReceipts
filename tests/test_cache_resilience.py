"""Exercise publication failures against real cache files, without network I/O."""

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest

from finreceipts.tools.http_cache import JsonCache

URL = "https://example.invalid/financial-facts"


@pytest.mark.parametrize("existing", [False, True])
def test_serialization_failure_does_not_publish_partial_cache(tmp_path, existing):
    cache = JsonCache(tmp_path)
    old = {"revenue": 123}
    if existing:
        cache.put(URL, old)
    before = cache.path_for(URL).read_bytes() if existing else None
    with pytest.raises(TypeError):
        cache.put(URL, {"revenue": 456, "unsupported": object()})
    assert cache.get(URL) == (old if existing else None)
    if existing:
        assert cache.path_for(URL).read_bytes() == before
    assert sorted(tmp_path.iterdir()) == ([cache.path_for(URL)] if existing else [])


def test_reader_sees_old_snapshot_while_refresh_is_being_written(tmp_path, monkeypatch):
    cache = JsonCache(tmp_path)
    cache.put(URL, {"revision": "old"})
    writing, release = Event(), Event()
    original_dump = json.dump

    def paused_dump(payload, stream, **kwargs):
        stream.write('{"partial":')
        stream.flush()
        writing.set()
        assert release.wait(10), "test failed to release writer"
        raise OSError("injected write failure")

    monkeypatch.setattr(json, "dump", paused_dump)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(cache.put, URL, {"revision": "new"})
        try:
            assert writing.wait(10), "writer did not reach publication window"
            assert JsonCache(tmp_path).get(URL) == {"revision": "old"}
        finally:
            release.set()
        with pytest.raises(OSError, match="injected"):
            future.result(timeout=10)
    monkeypatch.setattr(json, "dump", original_dump)
    cache.put(URL, {"revision": "new"})
    assert cache.get(URL) == {"revision": "new"}
    assert list(tmp_path.iterdir()) == [cache.path_for(URL)]


@pytest.mark.parametrize("operation", ["fsync", "replace"])
def test_failed_publication_preserves_previous_bytes(tmp_path, monkeypatch, operation):
    cache = JsonCache(tmp_path)
    cache.put(URL, {"revision": "old"})
    before = cache.path_for(URL).read_bytes()

    def fail(*_):
        raise OSError("injected publication failure")

    monkeypatch.setattr(os, operation, fail)
    with pytest.raises(OSError, match="injected"):
        cache.put(URL, {"revision": "new"})
    assert cache.path_for(URL).read_bytes() == before
    assert cache.get(URL) == {"revision": "old"}
    assert list(tmp_path.iterdir()) == [cache.path_for(URL)]


def test_concurrent_writers_publish_one_complete_payload(tmp_path, monkeypatch):
    cache = JsonCache(tmp_path)
    gate = Barrier(2)
    original_dump = json.dump
    payloads = [{"writer": i, "values": [i] * 1000} for i in range(2)]

    def synchronized_dump(payload, stream, **kwargs):
        original_dump(payload, stream, **kwargs)
        gate.wait(timeout=10)

    monkeypatch.setattr(json, "dump", synchronized_dump)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(JsonCache(tmp_path).put, URL, payload) for payload in payloads]
        for future in futures:
            assert future.result(timeout=15) == cache.path_for(URL)
    assert cache.get(URL) in payloads
    assert list(tmp_path.iterdir()) == [cache.path_for(URL)]


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_transient_windows_publication_conflict_retries_only_replace(
    tmp_path, monkeypatch, winerror
):
    cache = JsonCache(tmp_path)
    cache.put(URL, {"revision": "old"})
    replace, dump = os.replace, json.dump
    attempts, serializations, delays = [], [], []

    def busy_then_publish(source, destination):
        attempts.append(source)
        if len(attempts) <= 2:
            error = PermissionError("injected transient Windows lock")
            error.winerror = winerror
            raise error
        replace(source, destination)

    def record_dump(*args, **kwargs):
        serializations.append(True)
        return dump(*args, **kwargs)

    monkeypatch.setattr(os, "replace", busy_then_publish)
    monkeypatch.setattr(json, "dump", record_dump)
    monkeypatch.setattr(time, "sleep", delays.append)
    cache.put(URL, {"revision": "new"})
    assert len(serializations) == 1
    assert len(attempts) == 3 and len(set(attempts)) == 1
    assert delays == [0.01, 0.02]
    assert cache.get(URL) == {"revision": "new"}


@pytest.mark.parametrize("winerror", [5, 32, 33, None])
def test_permanent_publication_denial_is_bounded_and_preserves_old_cache(
    tmp_path, monkeypatch, winerror
):
    cache = JsonCache(tmp_path)
    cache.put(URL, {"revision": "old"})
    before = cache.path_for(URL).read_bytes()
    attempts, delays = [], []

    def denied(*_):
        attempts.append(True)
        error = PermissionError("injected permanent denial")
        if winerror is not None:
            error.winerror = winerror
        raise error

    monkeypatch.setattr(os, "replace", denied)
    monkeypatch.setattr(time, "sleep", delays.append)
    with pytest.raises(PermissionError):
        cache.put(URL, {"revision": "new"})
    assert len(attempts) == (1 if winerror is None else 5)
    assert sum(delays) == pytest.approx(0 if winerror is None else 0.15)
    assert cache.path_for(URL).read_bytes() == before
    assert list(tmp_path.iterdir()) == [cache.path_for(URL)]


def test_process_death_during_refresh_preserves_last_good_snapshot(tmp_path):
    cache = JsonCache(tmp_path)
    cache.put(URL, {"revision": "old"})
    before = cache.path_for(URL).read_bytes()
    program = """
import json, os, sys
from finreceipts.tools.http_cache import JsonCache
def crash(payload, stream, **kwargs):
    stream.write('{"partial":')
    stream.flush()
    os._exit(23)
json.dump = crash
JsonCache(sys.argv[1]).put(sys.argv[2], {"revision": "new"})
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path), URL],
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 23, result.stderr.decode(errors="replace")
    assert cache.path_for(URL).read_bytes() == before
    assert cache.get(URL) == {"revision": "old"}
    # An abrupt exit can leave an unpublished sibling, never a cache hit.
    abandoned = list(tmp_path.glob("*.tmp"))
    assert len(abandoned) == 1
    cache.put(URL, {"revision": "recovered"})
    assert cache.get(URL) == {"revision": "recovered"}
    assert abandoned[0].exists()  # no unsafe cleanup of another writer's files
