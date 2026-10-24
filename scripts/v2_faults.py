"""Scoped Compose fault drills, fake models only. Restores every stopped service."""

from __future__ import annotations

import json
import subprocess
import time
import uuid

import httpx
from v2_acceptance import ROOT, URL, client, config, item
from v2_load import COMPOSE, sql


def compose(*args):
    return subprocess.run(
        [*COMPOSE, *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


def database_writes(enabled):
    statement = "SET GLOBAL read_only=" + ("OFF" if enabled else "ON") + ";"
    subprocess.run(
        [
            *COMPOSE,
            "exec",
            "-T",
            "mysql",
            "sh",
            "-c",
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -N -u root',
        ],
        input=statement,
        text=True,
        capture_output=True,
        check=True,
    )


def wait_for(fn, seconds=90):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        result = fn()
        if result:
            return result
        time.sleep(1)
    raise TimeoutError("drill condition not reached")


def main():
    assert config().get("MODEL_PROVIDER", "fake") == "fake"
    assert (
        sql(
            "SELECT COUNT(*) FROM execution_profiles WHERE document->>'$.provider'<>'fake';"
        ).strip()
        == "0"
    )
    checks = {}
    report = {
        "started_at": time.time(),
        "checks": checks,
        "scope": "Only finreceipts-v2 containers, synthetic requests, fake provider.",
    }
    temporary = "finreceipts-v2-fault-worker-" + uuid.uuid4().hex[:10]
    original_quota = int(sql("SELECT max_pending FROM tenants WHERE id='alpha';").strip())
    assert sql("SELECT @@global.read_only;").strip() == "0", "Database must start writable"
    temporary_created = False
    worker_names = ["worker1", "worker2", "worker3", "worker4"]
    with client("alpha.analyst") as alpha, client("beta.analyst") as beta:
        token = (
            alpha.post("/api/v2/tokens", json={"scope": "submit", "days": 1})
            .raise_for_status()
            .json()["token"]
        )

        def submit(c=alpha, research=False):
            r = c.post(
                "/api/v2/batches",
                json={
                    "title": "fault-" + str(uuid.uuid4()),
                    "items": [item("research" if research else "metric")],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            r.raise_for_status()
            return r.json()["batch_id"]

        def task(batch):
            return (
                alpha.get("/api/v2/tasks", params={"batch_id": batch})
                .raise_for_status()
                .json()["items"][0]
            )

        try:
            # An API replica exits: session authentication and writes continue through its peer.
            compose("stop", "api1")
            batch = submit()
            wait_for(lambda: task(batch)["status"] == "COMPLETED")
            checks["api_replica_stopped_shared_session"] = "PASS"
            compose("start", "api1")
            # The accepted transaction must survive an unavailable broker.
            compose("stop", "rabbitmq")
            batch = submit()
            assert task(batch)["status"] in {"QUEUED", "READY"}
            compose("start", "rabbitmq")
            wait_for(lambda: task(batch)["status"] == "COMPLETED", 120)
            checks["committed_outbox_broker_restart"] = "PASS"
            # Redis failure closes new admission; its persisted session survives restart.
            compose("stop", "redis")
            response = httpx.post(
                URL + "/api/v2/batches",
                trust_env=False,
                headers={"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid.uuid4())},
                json={"title": "redis unavailable", "items": [item()]},
                timeout=15,
            )
            assert response.status_code in {500, 503}, response.status_code
            compose("start", "redis")
            wait_for(lambda: alpha.get("/api/v2/me").status_code == 200, 30)
            checks["redis_restart_fail_closed"] = {
                "status": "PASS",
                "rejection_status": response.status_code,
            }
            # The database rejects writes without losing an already accepted task.
            compose("stop", *worker_names)
            batch = submit()
            database_writes(False)
            denied_key = str(uuid.uuid4())
            response = alpha.post(
                "/api/v2/batches",
                headers={"Idempotency-Key": denied_key},
                json={"title": "database readonly", "items": [item()]},
            )
            assert response.status_code in {500, 503}
            database_writes(True)
            assert (
                sql(f"SELECT COUNT(*) FROM batches WHERE request_key='{denied_key}';").strip()
                == "0"
            )
            compose("start", *worker_names)
            wait_for(lambda: task(batch)["status"] == "COMPLETED", 120)
            checks["database_readonly_rollback_and_recovery"] = {
                "status": "PASS",
                "rejection_status": response.status_code,
            }
            # A saturated team cannot consume another team's admission quota.
            sql("UPDATE tenants SET max_pending=0 WHERE id='alpha';")
            response = alpha.post(
                "/api/v2/batches",
                headers={"Idempotency-Key": str(uuid.uuid4())},
                json={"title": "quota denial", "items": [item()]},
            )
            assert response.status_code == 429
            submit(beta)
            sql(f"UPDATE tenants SET max_pending={original_quota} WHERE id='alpha';")
            checks["tenant_quota_isolation"] = "PASS"
            # Kill a real worker after reserving an external-attempt budget.
            compose("stop", *worker_names)
            compose(
                "run",
                "-d",
                "--no-deps",
                "--name",
                temporary,
                "-e",
                "FAKE_DELAY_SECONDS=25",
                "worker1",
            )
            temporary_created = True
            batch = submit(research=True)
            wait_for(
                lambda: (
                    sql(
                        "SELECT COUNT(*) FROM attempts a JOIN tasks t ON t.id=a.task_id "
                        f"WHERE t.batch_id='{batch}' AND a.status='RESERVED';"
                    ).strip()
                    == "1"
                ),
                60,
            )
            current = task(batch)
            before = sql("SELECT reserved_micros FROM tenants WHERE id='alpha';").strip()
            subprocess.run(["docker", "kill", temporary], check=True, capture_output=True)
            compose("start", *worker_names)
            wait_for(lambda: task(batch)["status"] == "UNKNOWN", 80)
            after = sql("SELECT reserved_micros FROM tenants WHERE id='alpha';").strip()
            assert int(after) >= int(before) > 0
            checks["worker_death_after_attempt_reservation"] = {
                "status": "PASS",
                "task_id": current["id"],
                "reserved_before": before,
                "reserved_after": after,
            }
            # Unknown retry requires explicit human confirmation and retains the old reservation.
            current = task(batch)
            response = alpha.post(
                f"/api/v2/tasks/{current['id']}/retry",
                json={
                    "expected_revision": current["revision"],
                    "acknowledge_possible_duplicate": False,
                },
            )
            assert response.status_code == 409
            checks["unknown_retry_confirmation"] = "PASS"
            report["status"] = "PASS"
        finally:
            database_writes(True)
            sql(f"UPDATE tenants SET max_pending={original_quota} WHERE id='alpha';")
            compose("start", "api1", "redis", "rabbitmq", *worker_names)
            # Exact disposable test container; no -v, no project volume cleanup.
            if temporary_created:
                subprocess.run(["docker", "rm", "-f", temporary], capture_output=True)
            report["finished_at"] = time.time()
            destination = ROOT / "runs" / ("v2-faults-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
            destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report))
            print(destination)


if __name__ == "__main__":
    main()
