"""Read-only handoff snapshot of the existing local deployment; no model calls."""

import json
import subprocess
import time

from v2_acceptance import ROOT, client
from v2_load import COMPOSE, hardware, sql


def main():
    with client("alpha.analyst") as c:
        me = c.get("/api/v2/me").raise_for_status().json()
        quota = c.get("/api/v2/quota").raise_for_status().json()
        response = c.get("/v3/api-docs").raise_for_status()
        (ROOT / "runs/v2-openapi.json").write_text(response.text, encoding="utf-8")
    report = {
        "date": time.time(),
        "status": "RUNNING",
        "user": me["username"],
        "hardware": hardware(),
        "alpha_quota": quota,
        "task_states": sql("SELECT status,COUNT(*) FROM tasks GROUP BY status;"),
        "artifacts": sql("SELECT copied,COUNT(*) FROM artifact_copies GROUP BY copied;"),
        "containers": subprocess.run(
            [*COMPOSE, "--profile", "observability", "ps", "--format", "json"],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        ).stdout,
    }
    (ROOT / "runs/v2-runtime-final.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "openapi_saved": True,
                "task_states": report["task_states"],
            }
        )
    )


if __name__ == "__main__":
    main()
