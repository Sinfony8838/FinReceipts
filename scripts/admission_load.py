"""Bounded local HTTP burst with a delayed fake model; not a provider benchmark.

Starts isolated loopback servers and writes only to a NEW output directory.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import math
import multiprocessing
import os
import platform
import socket
import subprocess
import time
from pathlib import Path

import httpx


def serve(port, capacity, delay, database, logfile, statistics, block):
    root = Path(__file__).resolve().parents[1]
    os.environ.update(
        {
            "FINRECEIPTS_MAX_INFLIGHT": str(capacity),
            "FINRECEIPTS_RUN_DB": database,
            "FINRECEIPTS_LLM_PROVIDER": "fake",
            "FINRECEIPTS_OFFLINE": "1",
            "FINRECEIPTS_CACHE_DIR": str(root / "tests/fixtures/sec"),
            "FINRECEIPTS_SMALL_MODEL": "",
            "FINRECEIPTS_LARGE_MODEL": "",
        }
    )
    import uvicorn

    from finreceipts.agent.graph import AgentDeps, build_graph
    from finreceipts.app.diagnostics import request_logging_config
    from finreceipts.app.main import create_app
    from finreceipts.llm.client import FakeLLM

    app = create_app()
    state = app.state.fin
    original_start = state.start

    def respond(*_):
        with statistics.get_lock():
            statistics[0] += 1
            statistics[1] = max(statistics[0], statistics[1])
            statistics[2] += 1
        try:
            if block[0].is_set():
                block[1].set()
                assert block[2].wait(30), "controlled experiment did not release its model"
            else:
                time.sleep(delay)
            return "Apple revenue in 2023 was $383.3 billion."
        finally:
            with statistics.get_lock():
                statistics[0] -= 1

    def start():
        original_start()
        state.llm = FakeLLM(respond)
        state.graph = build_graph(AgentDeps(state.llm, state.provider))
        state.runs.llm = state.llm

    state.start = start
    with (
        open(logfile, "x", encoding="utf-8") as log,
        contextlib.redirect_stdout(log),
        contextlib.redirect_stderr(log),
    ):
        uvicorn.run(
            app, host="127.0.0.1", port=port, access_log=False, log_config=request_logging_config()
        )


def latency(samples):
    values = sorted(row["latency_ms"] for row in samples)
    if not values:
        return {"count": 0, "p50_ms": None, "p95_ms": None}
    return {
        "count": len(values),
        "p50_ms": values[math.ceil(len(values) * 0.5) - 1],
        "p95_ms": values[math.ceil(len(values) * 0.95) - 1],
    }


def disconnected_socket(base, block):
    """Hold the actual model thread while a real HTTP stream is closed."""
    body = {
        "question": "Apple revenue in 2023?",
        "ticker": "AAPL",
        "fiscal_years": [2023],
        "mode": "baseline",
    }
    block[0].set()
    block[1].clear()
    block[2].clear()
    with httpx.Client(base_url=base, timeout=10, trust_env=False) as client:
        try:
            with client.stream("POST", "/api/ask/stream", json=body) as stream:
                assert stream.status_code == 200
                for line in stream.iter_lines():
                    if line == "event: start":
                        break
                assert block[1].wait(5), "model was not running at disconnect"
            # The client socket is closed, but the fake model has not returned.
            held = client.get("/health").json()["execution"]
            assert held["active"] == 1
            rejected = client.post("/api/ask", json=body)
            assert rejected.status_code == 503
            assert rejected.json()["code"] == "capacity_exhausted"
        finally:
            block[0].clear()
            block[2].set()
        deadline = time.monotonic() + 5
        while client.get("/health").json()["execution"]["active"]:
            assert time.monotonic() < deadline, "slot leaked after disconnected model returned"
            time.sleep(0.01)
        assert client.post("/api/ask", json=body).status_code == 200
    return {
        "status": "PASS",
        "socket_closed_while_model_blocked": True,
        "active_after_socket_close": held["active"],
        "new_work_status": rejected.status_code,
        "recovery": "PASS",
    }


async def burst(base, count):
    body = {
        "question": "Apple revenue in 2023?",
        "ticker": "AAPL",
        "fiscal_years": [2023],
        "mode": "baseline",
    }
    async with httpx.AsyncClient(
        base_url=base,
        trust_env=False,
        timeout=15,
        limits=httpx.Limits(max_connections=128, max_keepalive_connections=128),
    ) as client:
        # Warm the fixture/provider path, then measure only the controlled burst.
        assert (await client.post("/api/ask", json=body)).status_code == 200
        before = (await client.get("/health")).json()["execution"]

        async def send(path, payload=None):
            started = time.perf_counter()
            response = await (
                client.get(path) if payload is None else client.post(path, json=payload)
            )
            result = {
                "status": response.status_code,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "request_id": response.headers.get("X-Request-ID"),
            }
            if response.status_code == 503:
                assert response.json()["code"] == "capacity_exhausted"
                result["code"] = response.json()["code"]
            else:
                assert response.status_code == 200, response.text
            return result

        jobs = [asyncio.create_task(send("/api/ask", body)) for _ in range(count)]
        await asyncio.sleep(0.03)
        probes = [asyncio.create_task(send("/health")) for _ in range(8)]
        responses = await asyncio.gather(*jobs)
        health = await asyncio.gather(*probes)
        after = (await client.get("/health")).json()["execution"]
        assert after["active"] == 0
        assert (await client.post("/api/ask", json=body)).status_code == 200  # recovery
        return responses, health, before, after


def browser_case(base, block, folder):
    """Use real UI cancellation/overload while the fake model is held by IPC."""
    script = Path(__file__).with_name("browser_admission_smoke.cjs")
    block[0].set()
    block[1].clear()
    block[2].clear()
    try:
        result = subprocess.run(
            ["node", str(script), str(folder / "browser-ui")],
            env=os.environ | {"FINRECEIPTS_TEST_URL": base},
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=45,
            check=False,
        )
        (folder / "browser.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        assert result.returncode == 0, result.stderr
        assert block[1].is_set(), "browser did not exercise an actual running model"
    finally:
        block[0].clear()
        block[2].set()
    summary = json.loads((folder / "browser-ui/summary.json").read_text(encoding="utf-8"))
    with httpx.Client(base_url=base, trust_env=False, timeout=10) as client:
        deadline = time.monotonic() + 5
        while client.get("/health").json()["execution"]["active"]:
            assert time.monotonic() < deadline, "browser cancellation leaked a slot"
            time.sleep(0.01)
        run = client.get("/api/runs/" + summary["runId"]).json()
        assert run["status"] == "cancelled" and run["dispatches"] == 1
        assert not run["state"].get("answer"), "cancelled worker published late output"
        response = client.post(
            "/api/ask",
            json={
                "question": "Apple revenue?",
                "ticker": "AAPL",
                "fiscal_years": [2023],
                "mode": "baseline",
            },
        )
        assert response.status_code == 200
    return {
        "status": "PASS",
        "ui": summary,
        "late_answer_published": False,
        "recovery": "PASS",
        "script_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
    }


def run_case(out, capacity, count, delay, browser=False):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    context = multiprocessing.get_context("spawn")
    stats = context.Array("i", [0, 0, 0])
    block = (context.Event(), context.Event(), context.Event())
    folder = out / f"capacity-{capacity}"
    folder.mkdir()
    process = context.Process(
        target=serve,
        args=(
            port,
            capacity,
            delay,
            str(folder / "runs.sqlite3"),
            str(folder / "server.log"),
            stats,
            block,
        ),
    )
    process.start()
    try:
        base = f"http://127.0.0.1:{port}"
        with httpx.Client(timeout=2, trust_env=False) as client:
            deadline = time.monotonic() + 20
            while True:
                assert process.is_alive(), "local server exited before readiness"
                try:
                    if client.get(base + "/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline, "local server startup timed out"
                time.sleep(0.05)
        responses, health, before, after = asyncio.run(burst(base, count))
        accepted = [r for r in responses if r["status"] == 200]
        rejected = [r for r in responses if r["status"] == 503]
        assert rejected, "test did not exercise overload; increase burst or fake delay"
        assert stats[0] == 0 and stats[1] <= capacity
        assert stats[2] == len(accepted) + 2  # one warm-up and one recovery call
        assert after["rejected"] - before["rejected"] == len(rejected)
        result = {
            "capacity": capacity,
            "requests": count,
            "fake_delay_ms": delay * 1000,
            "accepted": latency(accepted),
            "rejected": latency(rejected),
            "health": latency(health),
            "peak_model_calls": stats[1],
            "model_calls_in_burst": stats[2] - 2,
            "before": before,
            "after": after,
            "recovery": "PASS",
            "samples": responses,
            "health_samples": health,
        }
        if capacity == 1:
            result["socket_disconnect"] = disconnected_socket(base, block)
            if browser:
                result["browser"] = browser_case(base, block, folder)
        (folder / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=10)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--capacity", type=int, nargs="+", default=[1, 4])
    parser.add_argument("--requests", type=int, default=32)
    parser.add_argument("--delay-ms", type=int, default=250)
    parser.add_argument(
        "--browser",
        action="store_true",
        help="also run controlled Edge UI checks; needs Node + Playwright",
    )
    args = parser.parse_args()
    if any(not 1 <= c <= 32 for c in args.capacity) or len(set(args.capacity)) != len(
        args.capacity
    ):
        parser.error("capacities must be distinct integers in [1, 32]")
    if not 2 <= args.requests <= 128 or not 50 <= args.delay_ms <= 5000:
        parser.error("requests must be 2..128 and fake delay must be 50..5000 ms")
    if args.browser and 1 not in args.capacity:
        parser.error("--browser requires capacity 1 in the experiment")
    args.out.mkdir(parents=True, exist_ok=False)
    results = [
        run_case(args.out, c, args.requests, args.delay_ms / 1000, args.browser)
        for c in args.capacity
    ]
    from finreceipts.runtime.codec import implementation_id

    summary = {
        "status": "PASS",
        "scope": "loopback HTTP with delayed FakeLLM; not production capacity or provider latency",
        "python": platform.python_version(),
        "implementation_id": implementation_id(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cases": [
            {k: v for k, v in r.items() if k not in {"samples", "health_samples"}} for r in results
        ],
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
