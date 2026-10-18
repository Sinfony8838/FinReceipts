"""Real loopback HTTP intake/deadline drill. Only synthetic data in a NEW directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import socket
import subprocess
import time
from pathlib import Path

import httpx
from admission_load import serve

from finreceipts.runtime.codec import implementation_id

BODY = {"question": "Apple revenue in 2023?", "ticker": "AAPL", "fiscal_years": [2023]}


def server(port, out, statistics, block):
    os.environ.update(
        {
            "FINRECEIPTS_MAX_REQUEST_BYTES": "1024",
            "FINRECEIPTS_REQUEST_READ_TIMEOUT_S": "0.3",
            "FINRECEIPTS_RESPONSE_TIMEOUT_S": "0.5",
            "FINRECEIPTS_ALLOWED_HOSTS": "127.0.0.1,localhost,[::1]",
        }
    )
    serve(port, 1, 1.0, str(out / "runs.sqlite3"), str(out / "server.log"), statistics, block)


def wait_idle(http):
    until = time.monotonic() + 10
    while http.get("/health").json()["execution"]["active"]:
        assert time.monotonic() < until, "worker did not release capacity"
        time.sleep(0.02)


def run(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    ctx = multiprocessing.get_context("spawn")
    statistics = ctx.Array("i", [0, 0, 0])
    block = [ctx.Event() for _ in range(3)]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    process = ctx.Process(target=server, args=(port, out, statistics, block))
    summary = {
        "status": "FAILED",
        "scope": "real loopback HTTP; offline fake model only",
        "checks": [],
    }
    summary["source_implementation_id"] = implementation_id()
    process.start()
    try:
        with httpx.Client(base_url=base, timeout=8, trust_env=False) as http:
            until = time.monotonic() + 20
            while True:
                try:
                    health = http.get("/health").json()
                    break
                except httpx.TransportError:
                    assert process.is_alive() and time.monotonic() < until, "server did not start"
                    time.sleep(0.1)
            summary["health"] = health
            large = http.post("/api/ask", json=BODY | {"question": "汉" * 400})
            assert large.status_code == 413
            assert statistics[2] == 0
            summary["checks"].append({"case": "oversized UTF-8 JSON", "status": large.status_code})

            with socket.create_connection(("127.0.0.1", port), timeout=5) as slow:
                slow.sendall(
                    (
                        f"POST /api/ask HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                        "Content-Type: application/json\r\nContent-Length: 50\r\n\r\n{"
                    ).encode()
                )
                data = b""
                while part := slow.recv(4096):
                    data += part
                assert data.startswith(b"HTTP/1.1 408") and b"request_body_timeout" in data
                assert statistics[2] == 0
            wait_idle(http)
            summary["checks"].append(
                {"case": "unfinished real TCP upload", "status": 408, "model_calls": 0}
            )

            with socket.create_connection(("127.0.0.1", port), timeout=5) as chunked:
                chunked.sendall(
                    (
                        f"POST /api/ask HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                        "Content-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n"
                    ).encode()
                    + b"401\r\n"
                    + b"x" * 1025
                    + b"\r\n0\r\n\r\n"
                )
                data = b""
                while part := chunked.recv(4096):
                    data += part
                assert data.startswith(b"HTTP/1.1 413") and statistics[2] == 0
            summary["checks"].append(
                {"case": "chunked actual byte overflow", "status": 413, "model_calls": 0}
            )

            for streaming in (False, True):
                block[0].set()
                block[1].clear()
                block[2].clear()
                try:
                    response = http.post(
                        "/api/ask" + ("/stream" if streaming else ""),
                        json=BODY | {"mode": "baseline"},
                    )
                    assert block[1].is_set() and not block[2].is_set()
                    assert response.status_code == (200 if streaming else 504)
                    assert "response_timeout" in response.text
                    assert "event: done" not in response.text
                    assert http.get("/health").json()["execution"]["active"] == 1
                    assert http.post("/api/ask", json=BODY).status_code == 503
                    summary["checks"].append(
                        {
                            "case": "SSE deadline" if streaming else "JSON deadline",
                            "status": response.status_code,
                            "active_after_timeout": 1,
                            "retry_status": 503,
                            "request_id": response.headers["x-request-id"],
                        }
                    )
                finally:
                    block[0].clear()
                    block[2].set()
                wait_idle(http)
            if args.node:
                env = dict(os.environ, FINRECEIPTS_TEST_URL=base)
                subprocess.run(
                    [str(args.node), "scripts/browser_limits_smoke.cjs", str(out / "browser")],
                    cwd=Path(__file__).resolve().parents[1],
                    env=env,
                    check=True,
                    timeout=60,
                )
                summary["browser"] = json.loads(
                    (out / "browser/summary.json").read_text(encoding="utf-8")
                )
                wait_idle(http)
            summary["final_execution"] = http.get("/health").json()["execution"]
            summary["model_calls"] = statistics[2]
            summary["peak_model_workers"] = statistics[1]
            assert statistics[1] == 1
            assert implementation_id() == summary["source_implementation_id"]
            summary["status"] = "PASS"
    finally:
        block[2].set()
        process.terminate()  # Only this drill's newly spawned fake server.
        process.join(10)
        summary["server_stopped"] = not process.is_alive()
        summary["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        (out / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--node", type=Path, help="Optional Node with Playwright via NODE_PATH")
    run(parser.parse_args())
