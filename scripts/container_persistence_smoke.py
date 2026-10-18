"""Isolated Compose recreation drill. Leaves named volumes intact for inspection.

Uses a fresh project, fake LLM and offline fixtures. Never reuses a caller's .env.
Exit 0 = verified; exit 2 = unavailable engine or failed check. Config-only is not PASS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path, PurePosixPath
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = "/app/.cache/runtime"


def validate_storage(config: dict, *, project: str | None = None) -> dict:
    actual_project = config.get("name")
    if not actual_project or (project is not None and actual_project != project):
        raise ValueError("unexpected Compose project")
    service = config["services"]["api"]
    database = PurePosixPath(service["environment"]["FINRECEIPTS_RUN_DB"])
    mounts = service.get("volumes", [])
    # A project prefix does not isolate external names or host bind mounts.
    # Check every mount before starting containers, including the cache volume.
    for item in mounts:
        spec = config.get("volumes", {}).get(item.get("source"), {})
        if (
            item["type"] != "volume"
            or spec.get("external")
            or spec.get("name") != f"{actual_project}_{item.get('source')}"
            or spec.get("driver", "local") != "local"
            or spec.get("driver_opts")
        ):
            raise ValueError("drill requires isolated project-scoped local named volumes")
    selected = [m for m in mounts if m["target"] == str(database.parent)]
    if not database.is_absolute() or len(selected) != 1:
        raise ValueError("runtime DB parent must have exactly one persistent mount")
    mount = selected[0]
    if (
        mount["type"] != "volume"
        or mount.get("read_only")
        or mount["source"] not in config.get("volumes", {})
    ):
        raise ValueError("runtime storage must be a declared writable named volume")
    if str(database.parent) != RUNTIME_DIR or database.name != "runs.sqlite3":
        raise ValueError("unexpected runtime path; review migration and drill together")
    for other in mounts:
        if other is mount:
            continue
        target = PurePosixPath(other["target"])
        if (
            target == database.parent
            or database.parent in target.parents
            or target in database.parents
        ):
            raise ValueError("overlapping mount can hide runtime DB or its SQLite sidecars")
        if other.get("source") == mount["source"]:
            raise ValueError("runtime volume must not alias another mount")
    return {"database": str(database), "target": mount["target"], "volume": mount["source"]}


def isolated_env() -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("FINRECEIPTS_", "COMPOSE_"))
        and k not in {"MINIMAX_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"}
    }
    env.update(FINRECEIPTS_PORT="0", FINRECEIPTS_LLM_PROVIDER="fake", FINRECEIPTS_OFFLINE="1")
    return env


def source_hashes() -> dict:
    paths = [ROOT / name for name in ("Dockerfile", "docker-compose.yml", "pyproject.toml")]
    paths += [p for p in (ROOT / "src").rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(paths)
    }


def run_drill(out: Path, *, config_only=False) -> dict:
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    project = "fr-persist-" + uuid.uuid4().hex[:12]
    (out / "empty.env").write_text("", encoding="utf-8")
    override = {"services": {"api": {"image": project + ":drill"}}}
    (out / "override.json").write_text(json.dumps(override), encoding="utf-8")
    base = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--env-file",
        str(out / "empty.env"),
        "-f",
        str(ROOT / "docker-compose.yml"),
        "-f",
        str(out / "override.json"),
    ]
    result = {
        "status": "FAILED",
        "project": project,
        "config_only": config_only,
        "source_hashes": source_hashes(),
        "phases": [],
        "volumes_removed": False,
    }
    env, owned, step = isolated_env(), False, 0

    def command(args, *, timeout=120):
        nonlocal step
        step += 1
        process = subprocess.run(
            args,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        with (out / f"command-{step:02d}.log").open("x", encoding="utf-8") as stream:
            stream.write(process.stdout + "\n" + process.stderr)
        if process.returncode:
            raise RuntimeError(f"command {step} failed (exit {process.returncode}); see saved log")
        return process.stdout.strip()

    def phase(name):
        result["phases"].append(name)
        print(name, flush=True)

    def container():
        container_id = command([*base, "ps", "-q", "api"])
        if not container_id or "\n" in container_id:
            raise AssertionError("expected one isolated container")
        details = json.loads(
            command(["docker", "inspect", "--format", "{{json .Mounts}}", container_id])
        )
        matches = [m for m in details if m["Destination"] == RUNTIME_DIR]
        assert len(matches) == 1 and matches[0]["Type"] == "volume" and matches[0]["RW"]
        assert matches[0]["Name"] == project + "_finreceipts_runtime"
        actual = json.loads(
            command(
                [
                    *base,
                    "exec",
                    "-T",
                    "api",
                    "python",
                    "-c",
                    "import json,os; from finreceipts.runtime.codec import implementation_id; "
                    "print(json.dumps(dict(uid=os.getuid(),db=os.environ['FINRECEIPTS_RUN_DB'],"
                    "implementation_id=implementation_id())))",
                ]
            )
        )
        assert actual["uid"] == 10001 and actual["db"] == result["storage"]["database"]
        address = command([*base, "port", "api", "8000"])
        assert address.startswith("127.0.0.1:") and "\n" not in address
        image = command(["docker", "inspect", "--format", "{{.Image}}", container_id])
        return {"id": container_id, "address": address, "image": image, **actual}

    opener = build_opener(ProxyHandler({}))

    def api(instance, route, body=None, *, key=None):
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Idempotency-Key"] = key
        request = Request(
            "http://" + instance["address"] + route,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
        )
        with opener.open(request, timeout=30) as response:
            return json.load(response)

    try:
        phase("validate_compose_storage")
        config = json.loads(command([*base, "config", "--format", "json"]))
        result["storage"] = validate_storage(config, project=project)
        assert config["services"]["api"]["environment"]["FINRECEIPTS_LLM_PROVIDER"] == "fake"
        if config_only:
            result["status"] = "CONFIG_VALIDATED_ONLY"
            return result
        phase("engine_preflight")
        try:
            result["engine"] = json.loads(
                command(["docker", "version", "--format", "{{json .Server}}"], timeout=30)
            )
            if not result["engine"]:
                raise RuntimeError("no Docker server")
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            result["status"] = "BLOCKED_UNRUN"
            raise
        assert not command([*base, "ps", "-a", "-q"])
        assert not command(
            [
                "docker",
                "volume",
                "ls",
                "--filter",
                "name=" + project + "_",
                "--format",
                "{{.Name}}",
            ]
        )
        assert not command(
            ["docker", "image", "ls", "--filter", "reference=" + project + ":drill", "-q"]
        )
        owned = True
        phase("build_and_start")
        command([*base, "build"], timeout=900)
        command([*base, "up", "-d", "--wait", "--wait-timeout", "90"], timeout=120)
        first = container()
        body = {"question": "Apple revenue in 2023?", "ticker": "AAPL", "fiscal_years": [2023]}
        answer = api(first, "/api/runs", body, key="drill-answer")
        answer = api(
            first,
            "/api/runs/" + answer["run_id"] + "/advance",
            {"expected_revision": answer["revision"]},
        )
        assert answer["next_node"] == "verify" and answer["dispatches"] == 1
        cancelled = api(first, "/api/runs", body, key="drill-cancelled")
        cancelled = api(
            first,
            "/api/runs/" + cancelled["run_id"] + "/cancel",
            {"expected_revision": cancelled["revision"]},
        )
        running = api(first, "/api/runs", body, key="drill-running")
        # Simulate a worker lost after reservation; fixed clock makes its lease
        # expired, without changing the server clock or editing raw database rows.
        reserve = (
            "import json,os,sys; from pathlib import Path; "
            "from finreceipts.runtime.store import RunStore; "
            "s=RunStore(Path(os.environ['FINRECEIPTS_RUN_DB']),clock=lambda:100); "
            "d=s.get(sys.argv[1]); d,t=s.claim(d['run_id'],d['revision']); "
            "print(json.dumps(s.reserve(d['run_id'],t,'dispatches',6)))"
        )
        running = json.loads(
            command([*base, "exec", "-T", "api", "python", "-c", reserve, running["run_id"]])
        )
        originals = {
            d["run_id"]: api(first, "/api/runs/" + d["run_id"] + "/export")
            for d in (answer, cancelled, running)
        }
        (out / "original-exports.json").write_text(
            json.dumps(originals, indent=2), encoding="utf-8"
        )
        result["containers"] = [first]
        for name, operation in [
            (
                "force_recreate",
                ["up", "-d", "--no-build", "--force-recreate", "--wait", "--wait-timeout", "90"],
            ),
            ("remove_then_create", ["up", "-d", "--no-build", "--wait", "--wait-timeout", "90"]),
        ]:
            phase(name)
            if name == "remove_then_create":
                command([*base, "down"], timeout=90)
            command(base + operation, timeout=120)
            restored = container()
            assert restored["id"] not in {c["id"] for c in result["containers"]}
            assert restored["image"] == first["image"]
            assert restored["implementation_id"] == first["implementation_id"]
            copies = {
                run_id: api(restored, "/api/runs/" + run_id + "/export") for run_id in originals
            }
            assert copies == originals
            result["containers"].append(restored)
            (out / f"{name}-exports.json").write_text(
                json.dumps(copies, indent=2), encoding="utf-8"
            )
        verified = api(
            restored,
            "/api/runs/" + answer["run_id"] + "/advance",
            {"expected_revision": answer["revision"]},
        )
        assert verified["dispatches"] == answer["dispatches"]
        resumed = api(
            restored,
            "/api/runs/" + running["run_id"] + "/resume",
            {"expected_revision": running["revision"], "acknowledge_possible_duplicate": True},
        )
        assert resumed["dispatches"] == 1 and resumed["status"] == "ready"
        assert source_hashes() == result["source_hashes"], "source changed during experiment"
        result.update(
            status="PASS",
            preserved_runs=3,
            identical_exports_after_both_recreations=True,
            reservation_after_resume=resumed["dispatches"],
            verification_dispatches=verified["dispatches"],
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if owned:
            try:
                phase("stop_isolated_project_keep_volumes")
                command([*base, "logs", "--no-color"], timeout=30)
                command([*base, "down"], timeout=90)
                result["retained_volumes"] = [
                    project + "_finreceipts_runtime",
                    project + "_finreceipts_cache",
                ]
            except Exception as exc:
                result["cleanup_error"] = f"{type(exc).__name__}: {exc}"
                result["status"] = "FAILED"
        result["finished_at"] = time.time()
        with (out / "summary.json").open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--config-only", action="store_true")
    args = parser.parse_args()
    report = run_drill(args.out, config_only=args.config_only)
    print(json.dumps({k: report[k] for k in ("status", "project", "phases")}, indent=2))
    raise SystemExit(0 if report["status"] in {"PASS", "CONFIG_VALIDATED_ONLY"} else 2)
