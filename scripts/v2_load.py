"""Local fake-provider capacity exercises. Results include rejects and missed schedules."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import subprocess
import time
import uuid
from contextlib import suppress

import httpx
from v2_acceptance import ROOT, URL, client, config, item

COMPOSE = ["docker", "compose", "--env-file", ".env.v2", "-f", "compose.v2.yml"]


def source_digest():
    digest = hashlib.sha256()
    paths = []
    for folder in ("src", "server/src", "console/src", "deploy/v2"):
        paths.extend(
            p for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts
        )
    paths.extend(
        ROOT / p
        for p in (
            "compose.v2.yml",
            "server/pom.xml",
            "scripts/v2_load.py",
            "scripts/v2_query_client.cjs",
        )
    )
    for path in sorted(paths):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def hardware():
    inventory = {"platform": platform.platform(), "cpu_count": os.cpu_count()}
    if os.name == "nt":
        raw = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_ComputerSystem | "
                "Select-Object Model,TotalPhysicalMemory,NumberOfLogicalProcessors | "
                "ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        inventory["host"] = json.loads(raw.stdout)
    ids = subprocess.run(
        [*COMPOSE, "ps", "-q"], capture_output=True, text=True, check=True
    ).stdout.split()
    inventory["containers"] = []
    inventory["fake_delay_seconds"] = float(
        subprocess.run(
            [*COMPOSE, "exec", "-T", "worker1", "printenv", "FAKE_DELAY_SECONDS"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    for identity in ids:
        raw = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .Name}} {{json .HostConfig.Memory}} "
                "{{json .HostConfig.NanoCpus}} {{json .Config.Image}} {{json .Image}}",
                identity,
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        inventory["containers"].append(raw)
    return inventory


def completion_metrics(started_at):
    rows = sql(
        "SELECT t.status,t.updated_at-t.created_at,t.dispatches FROM tasks t "
        "JOIN batches b ON b.id=t.batch_id WHERE b.title LIKE 'load-fake-%' "
        f"AND t.created_at>={int(started_at * 1000)};"
    )
    records = [line.split("\t") for line in rows.strip().splitlines()]
    durations = [int(r[1]) for r in records if r[0] == "COMPLETED"]
    return {
        "accepted_tasks": len(records),
        "completed_tasks": len(durations),
        "end_to_end_p95_ms": percentile(durations, 0.95),
        "states": {state: sum(r[0] == state for r in records) for state in {r[0] for r in records}},
    }


def sql(statement):
    # Only this Compose project's MySQL; credentials are expanded inside its process.
    result = subprocess.run(
        [
            *COMPOSE,
            "exec",
            "-T",
            "mysql",
            "sh",
            "-c",
            'MYSQL_PWD="$MYSQL_PASSWORD" exec mysql -N -u finreceipts finreceipts',
        ],
        input=statement,
        text=True,
        capture_output=True,
        cwd=ROOT,
        check=True,
    )
    return result.stdout


def percentile(values, quantile):
    return sorted(values)[max(0, math.ceil(len(values) * quantile) - 1)] if values else None


def tokens():
    values = []
    for tenant in ("alpha", "beta"):
        with client(tenant + ".analyst") as c:
            values.append(
                c.post("/api/v2/tokens", json={"scope": "submit", "days": 1})
                .raise_for_status()
                .json()["token"]
            )
    return values


def seed(count):
    with client("alpha.analyst") as c:
        batch = (
            c.post(
                "/api/v2/batches",
                json={"title": "Synthetic capacity summaries", "items": [item()]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            .raise_for_status()
            .json()["batch_id"]
        )
    baseline = (
        sql(f"SELECT creator_id,profile_id FROM tasks WHERE batch_id='{batch}' LIMIT 1;")
        .strip()
        .split("\t")
    )
    assert len(baseline) == 2
    created = int(time.time() * 1000)
    fixture = json.dumps(item(), separators=(",", ":"))
    for offset in range(0, count, 1000):
        rows = []
        for _ in range(min(1000, count - offset)):
            rows.append(
                f"('{uuid.uuid4()}','{batch}','alpha','{baseline[0]}','{baseline[1]}',"
                f"'{fixture}','{{}}','CANCELLED',{created},{created})"
            )
        sql(
            "INSERT INTO tasks(id,batch_id,tenant_id,creator_id,profile_id,request,"
            "checkpoint,status,created_at,updated_at) VALUES " + ",".join(rows) + ";"
        )
    return {
        "synthetic_rows": count,
        "batch_id": batch,
        "note": "Cancelled summary fixtures; never executed or exported as approved results.",
    }


async def workload(args, keys):
    samples = []
    streams = []
    snapshots = []
    start = time.monotonic()
    semaphore = asyncio.Semaphore(300)
    limits = httpx.Limits(
        max_connections=max(400, args.connections + 10), max_keepalive_connections=300
    )
    async with httpx.AsyncClient(base_url=URL, timeout=30, limits=limits, trust_env=False) as http:

        async def request(index, due):
            async with semaphore:
                begun = time.monotonic()
                headers = {
                    "Authorization": "Bearer " + keys[0 if args.mode == "query" else index % 2]
                }
                try:
                    if args.mode == "query":
                        response = await http.get("/api/v2/tasks?limit=30", headers=headers)
                    else:
                        headers["Idempotency-Key"] = str(uuid.uuid4())
                        size = 5 if args.mode == "burst" else 1
                        response = await http.post(
                            "/api/v2/batches",
                            headers=headers,
                            json={
                                "title": "load-fake-" + str(index),
                                "items": [item("research") for _ in range(size)],
                            },
                        )
                    status = response.status_code
                except httpx.HTTPError:
                    status = 0
                samples.append(
                    {
                        "status": status,
                        "ms": (time.monotonic() - begun) * 1000,
                        "scheduled_ms": (time.monotonic() - due) * 1000,
                        "start_lag_ms": (begun - due) * 1000,
                    }
                )

        async def monitor():
            while True:
                snapshot = await asyncio.to_thread(
                    sql, "SELECT status,COUNT(*) FROM tasks GROUP BY status;"
                )
                snapshots.append(
                    {
                        "elapsed_s": time.monotonic() - start,
                        "counts": {
                            line.split("\t")[0]: int(line.split("\t")[1])
                            for line in snapshot.strip().splitlines()
                        },
                    }
                )
                await asyncio.sleep(5)

        monitor_job = asyncio.create_task(monitor()) if args.mode in {"burst", "queue"} else None
        if args.mode == "burst":
            await asyncio.gather(*(request(i, start) for i in range(200)))
        elif args.mode in {"query", "queue"}:
            pending = set()
            for i in range(round(args.duration * args.rps)):
                due = start + i / args.rps
                await asyncio.sleep(max(0, due - time.monotonic()))
                job = asyncio.create_task(request(i, due))
                pending.add(job)
                job.add_done_callback(pending.discard)
            await asyncio.gather(*pending)
        elif args.mode == "sse":
            ids = []
            expected = []
            for key in keys:
                r = await http.post(
                    "/api/v2/batches",
                    headers={
                        "Authorization": "Bearer " + key,
                        "Idempotency-Key": str(uuid.uuid4()),
                    },
                    json={"title": "SSE completeness control", "items": [item()]},
                )
                batch = r.raise_for_status().json()["batch_id"]
                for _ in range(90):
                    jobs = (
                        (
                            await http.get(
                                "/api/v2/tasks",
                                params={"batch_id": batch},
                                headers={"Authorization": "Bearer " + key},
                            )
                        )
                        .raise_for_status()
                        .json()["items"]
                    )
                    if jobs and jobs[0]["status"] == "COMPLETED":
                        break
                    await asyncio.sleep(1)
                assert jobs[0]["status"] == "COMPLETED"
                ids.append(jobs[0]["id"])
                expected.append(list(range(1, jobs[0]["revision"] + 1)))
            start = time.monotonic()

            async def stream(index):
                cursor = 0
                received = []
                reconnects = 0
                end = start + args.duration
                while time.monotonic() < end:
                    headers = {
                        "Authorization": "Bearer " + keys[index % 2],
                        "Last-Event-ID": str(cursor),
                    }
                    try:
                        async with http.stream(
                            "GET", f"/api/v2/tasks/{ids[index % 2]}/events", headers=headers
                        ) as response:
                            if response.status_code != 200:
                                streams.append(
                                    {
                                        "index": index,
                                        "status": response.status_code,
                                        "reconnects": reconnects,
                                    }
                                )
                                return
                            async for line in response.aiter_lines():
                                if line.startswith("id:"):
                                    current = int(line[3:])
                                    assert current > cursor
                                    received.append(current)
                                    cursor = current
                                if time.monotonic() >= end:
                                    break
                        reconnects += 1
                    except (httpx.HTTPError, AssertionError) as exc:
                        streams.append(
                            {
                                "index": index,
                                "status": 0,
                                "error": type(exc).__name__,
                                "reconnects": reconnects,
                            }
                        )
                        return
                streams.append(
                    {
                        "index": index,
                        "status": 200 if received == expected[index % 2] else 0,
                        "reconnects": reconnects,
                        "event_ids": received,
                        "complete": received == expected[index % 2],
                    }
                )

            jobs = []
            for i in range(args.connections):
                jobs.append(asyncio.create_task(stream(i)))
                await asyncio.sleep(0.005)  # 200/s, within aggregate admission capacity.
            await asyncio.gather(*jobs)
        if monitor_job:
            await asyncio.sleep(5)
            monitor_job.cancel()
            with suppress(asyncio.CancelledError):
                await monitor_job
    return samples, streams, snapshots


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["seed", "query", "burst", "queue", "sse"])
    parser.add_argument("--duration", type=int, default=600)
    parser.add_argument("--rps", type=float, default=200)
    parser.add_argument("--connections", type=int, default=1000)
    parser.add_argument("--rows", type=int, default=100000)
    parser.add_argument("--query-client", choices=["node", "httpx"], default="node")
    args = parser.parse_args()
    assert config().get("MODEL_PROVIDER", "fake") == "fake", (
        "Load tests must never use a paid model"
    )
    assert (
        not sql(
            "SELECT COUNT(*) FROM execution_profiles "
            "WHERE JSON_UNQUOTE(document->'$.provider') <> 'fake';"
        )
        .strip()
        .strip("0")
    ), "Separate fake-only environment required"
    report = {
        "mode": args.mode,
        "config": vars(args),
        "started_at": time.time(),
        "hardware": hardware(),
        "source_sha256": source_digest(),
        "scope": "Local multi-replica only; no SEC or live model calls.",
    }
    if args.mode == "seed":
        report.update(seed(args.rows))
    else:
        keys = tokens()
        if args.mode in {"query", "burst"} and args.query_client == "node":
            generated = subprocess.run(
                ["node", str(ROOT / "scripts/v2_query_client.cjs")],
                input=json.dumps(
                    {
                        "url": URL,
                        "tokens": keys,
                        "mode": args.mode,
                        "duration": args.duration,
                        "rps": args.rps,
                    }
                ),
                text=True,
                capture_output=True,
                check=True,
            )
            samples, streams, snapshots = json.loads(generated.stdout), [], []
        else:
            samples, streams, snapshots = asyncio.run(workload(args, keys))
        success = [s["ms"] for s in samples if 200 <= s["status"] < 300]
        report.update(
            {
                "requests": len(samples),
                "success": len(success),
                "expected_rejections": sum(s["status"] == 429 for s in samples),
                "unexpected_errors": sum(
                    not (200 <= s["status"] < 300 or s["status"] == 429) for s in samples
                ),
                "success_p95_ms": percentile(success, 0.95),
                "success_p99_ms": percentile(success, 0.99),
                "scheduled_p95_ms": percentile([s["scheduled_ms"] for s in samples], 0.95),
                "max_start_lag_ms": max([s["start_lag_ms"] for s in samples], default=0),
                "streams": streams,
                "samples": samples,
                "queue_snapshots": snapshots,
            }
        )
        if args.mode in {"queue", "burst"}:
            report["completion_at_load_end"] = completion_metrics(report["started_at"])
    report["finished_at"] = time.time()
    destination = (
        ROOT / "runs" / ("v2-load-" + args.mode + "-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
    )
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"samples", "streams"}}))
    print(destination)


if __name__ == "__main__":
    main()
