"""Exercise the deployed two-API system using public HTTP; never prints credentials."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8090"


def config():
    return dict(
        line.split("=", 1)
        for line in (ROOT / ".env.v2").read_text(encoding="utf-8-sig").splitlines()
        if "=" in line and not line.startswith("#")
    )


@contextmanager
def client(username):
    c = httpx.Client(base_url=URL, timeout=20, trust_env=False)
    csrf = c.get("/api/v2/csrf").raise_for_status().json()
    c.headers[csrf["header"]] = csrf["token"]
    c.post(
        "/api/v2/login", data={"username": username, "password": config()["BOOTSTRAP_PASSWORD"]}
    ).raise_for_status()
    csrf = c.get("/api/v2/csrf").raise_for_status().json()
    c.headers[csrf["header"]] = csrf["token"]
    try:
        yield c
    finally:
        c.close()


def item(kind="metric", year=2023):
    return {
        "kind": kind,
        "ticker": "AAPL",
        "target": {"kind": "direct", "metric": "revenue", "fiscal_year": year},
    }


def main():
    report = {"started_at": time.time(), "checks": {}}
    check = report["checks"]
    with (
        client("alpha.analyst") as a,
        client("alpha.reviewer") as reviewer,
        client("beta.analyst") as b,
    ):
        key = str(uuid.uuid4())
        body = {
            "title": "Acceptance: shared idempotency key",
            "items": [item(), item("research"), item(year=2099)],
        }

        def submit(_):
            r = a.post("/api/v2/batches", json=body, headers={"Idempotency-Key": key})
            r.raise_for_status()
            assert r.status_code == 202
            return r.json()["batch_id"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=100) as pool:
            ids = list(pool.map(submit, range(100)))
        assert len(set(ids)) == 1
        check["100_concurrent_same_key"] = "PASS"
        batch = ids[0]
        changed = a.post(
            "/api/v2/batches", json={**body, "title": "changed"}, headers={"Idempotency-Key": key}
        )
        assert changed.status_code == 409
        check["body_conflict"] = "PASS"
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            tasks = (
                a.get("/api/v2/tasks", params={"batch_id": batch})
                .raise_for_status()
                .json()["items"]
            )
            if len(tasks) == 3 and all(
                t["status"] in {"COMPLETED", "FAILED", "UNKNOWN"} for t in tasks
            ):
                break
            time.sleep(1)
        assert all(t["status"] == "COMPLETED" for t in tasks), tasks
        details = [a.get("/api/v2/tasks/" + t["id"]).raise_for_status().json() for t in tasks]
        missing = next(t for t in details if t["request"]["target"]["fiscal_year"] == 2099)
        assert missing["machine_verdict"] == "UNSUPPORTED"
        assert all(t["machine_verdict"] == "SUPPORTED" for t in details if t != missing)
        check["rules_research_and_missing_evidence"] = "PASS"
        for path in [
            "/tasks/" + missing["id"],
            "/tasks/" + missing["id"] + "/events",
            "/batches/" + batch + "/export",
        ]:
            assert b.get("/api/v2" + path).status_code == 404
        check["cross_tenant_task_events_export"] = "PASS"
        raw = httpx.post(
            URL + "/api/v2/batches",
            trust_env=False,
            cookies=a.cookies,
            json=body,
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert raw.status_code == 403
        check["csrf_required"] = "PASS"
        accepted = reviewer.post(
            "/api/v2/tasks/" + missing["id"] + "/review",
            json={
                "expected_revision": missing["revision"],
                "decision": "ACCEPTED",
                "reason": "Accept the missing-evidence refusal; no numeric claim approved.",
            },
        )
        accepted.raise_for_status()
        updated = a.get("/api/v2/tasks/" + missing["id"]).json()
        assert (
            updated["machine_verdict"] == "UNSUPPORTED" and updated["review_status"] == "ACCEPTED"
        )
        delivered = a.get("/api/v2/batches/" + batch + "/export").raise_for_status().json()
        assert [x["id"] for x in delivered["approved_items"]] == [missing["id"]]
        check["human_review_preserves_machine_and_export_scope"] = "PASS"
        with a.stream(
            "GET", "/api/v2/tasks/" + missing["id"] + "/events", headers={"Last-Event-ID": "1"}
        ) as stream:
            stream.raise_for_status()
            seen = []
            for line in stream.iter_lines():
                if line.startswith("id:"):
                    seen.append(int(line.split(":", 1)[1]))
                    if seen[-1] == updated["revision"]:
                        break
            assert seen == list(range(2, updated["revision"] + 1)), seen
        check["sse_cursor_replay"] = "PASS"
        report["batch_id"] = batch
        report["details"] = details
    report["finished_at"] = time.time()
    destination = ROOT / "runs" / ("v2-acceptance-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
    destination.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "checks": check,
                "report": str(destination),
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    main()
