"""Build sdist -> wheel, install locked dependencies, and exercise installed entrypoints.

Only fresh output directories are accepted. No publishing or editable installation.
Build/install may use the network; the application probe uses offline fixtures and FakeLLM.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def isolated_env() -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("FINRECEIPTS_", "PYTHON", "UV_"))
        and k not in {"VIRTUAL_ENV", "MINIMAX_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"}
    }
    env.update(FINRECEIPTS_LLM_PROVIDER="fake", FINRECEIPTS_OFFLINE="1", PYTHONUTF8="1")
    return env


def source_hashes() -> dict:
    files = [ROOT / p for p in ("pyproject.toml", "uv.lock", "scripts/package_install_smoke.py")]
    files += [p for p in (ROOT / "src").rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    return {p.relative_to(ROOT).as_posix(): sha(p.read_bytes()) for p in sorted(files)}


def inspect_archives(wheel: Path, sdist: Path, expected: dict) -> dict:
    """Verify actual package bytes, not merely a successful backend exit status."""
    with zipfile.ZipFile(wheel) as z, tarfile.open(sdist) as t:
        wheel_names, source_names = z.namelist(), t.getnames()
        for name in wheel_names + source_names:
            parts = Path(name).parts
            if any(p in {".git", ".venv", ".cache", "runs", "__pycache__"} for p in parts):
                raise ValueError(f"unexpected runtime/development artifact: {name}")
            if any(p == ".env" or (p.startswith(".env.") and p != ".env.example") for p in parts):
                raise ValueError(f"unexpected environment file: {name}")
        prefix = source_names[0].split("/")[0] + "/"
        package = {p.removeprefix("src/"): h for p, h in expected.items() if p.startswith("src/")}
        actual = {p: sha(z.read(p)) for p in wheel_names if p.startswith("finreceipts/")}
        if actual != package:
            raise ValueError("wheel package bytes differ from source inventory")
        for name, digest in expected.items():
            stream = t.extractfile(prefix + name)
            if stream is None or sha(stream.read()) != digest:
                raise ValueError(f"sdist source mismatch: {name}")
        return {"wheel_files": wheel_names, "sdist_files": source_names, "package_hashes": package}


def probe(out: Path) -> None:
    # Imported only by the fresh venv's isolated interpreter, never by the build driver.
    import finreceipts
    from finreceipts.runtime.codec import implementation_id
    from finreceipts.runtime.store import verify_export

    origin = Path(finreceipts.__file__).resolve()
    assert origin.is_relative_to(Path(sys.prefix).resolve()), origin
    assert not any(Path(p).resolve() == ROOT / "src" for p in sys.path if p)
    version = importlib.metadata.version("finreceipts")
    assert version == finreceipts.__version__
    inventory = json.loads((out / "archive-inventory.json").read_text())
    for name, digest in inventory["package_hashes"].items():
        assert sha((origin.parent.parent / name).read_bytes()) == digest, name
    entry_dir = Path(sys.executable).parent
    suffix = ".exe" if os.name == "nt" else ""
    cli, server = (str(entry_dir / (name + suffix)) for name in ("finreceipts", "finreceipts-api"))
    env = isolated_env()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env.update(
        FINRECEIPTS_HOST="127.0.0.1",
        FINRECEIPTS_PORT=str(port),
        FINRECEIPTS_CACHE_DIR=str(out / "work/cache"),
        FINRECEIPTS_RUN_DB=str(out / "work/runs.sqlite3"),
    )
    opener = build_opener(ProxyHandler({}))

    def api(route, body=None, key=None):
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Idempotency-Key"] = key
        req = Request(
            f"http://127.0.0.1:{port}" + route,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
        )
        with opener.open(req, timeout=15) as response:
            return response.read()

    processes = []
    for cycle in range(2):
        with (out / f"server-{cycle}.log").open("xb") as log:
            process = subprocess.Popen([server], cwd=out / "work", env=env, stdout=log, stderr=log)
            processes.append(process.pid)
            try:
                for _ in range(100):
                    assert process.poll() is None, "installed API entrypoint exited"
                    try:
                        health = json.loads(api("/health"))
                        break
                    except (URLError, TimeoutError):
                        time.sleep(0.1)
                else:
                    raise AssertionError("installed API did not become ready")
                assert health["provider"] == "fake" and health["offline"] is True
                if cycle == 0:
                    assert json.loads(api("/openapi.json"))["info"]["version"] == version
                    for route, local in [("/", "index.html"), ("/workbench", "runs.html")]:
                        # HTML routes read text, which normalizes Windows checkout CRLF.
                        assert api(route).decode() == (
                            origin.parent / "app/static" / local
                        ).read_text(encoding="utf-8")
                    for name, digest in inventory["package_hashes"].items():
                        if "/static/" in name:
                            assert sha(api("/static/" + name.split("/static/")[1])) == digest
                    scope_checks = []
                    for issuer, expected in [
                        ("Apple", "supported"),
                        ("Another Issuer", "unsupported"),
                    ]:
                        audited = json.loads(
                            api(
                                "/api/audit",
                                {
                                    "ticker": "AAPL",
                                    "fiscal_years": [2023],
                                    "text": f"{issuer} FY2023 revenue was $383.285 billion.",
                                },
                            )
                        )
                        assert audited["audit_mode"] == "text_scope"
                        assert audited["verification_level"] == "bounded_annual_clause"
                        assert audited["all_numbers_supported"] is (expected == "supported")
                        assert len(audited["audit"]) == 1
                        assert audited["audit"][0]["verdict"] == expected
                        if expected == "unsupported":
                            assert audited["audit"][0]["evidence"] is None
                        else:
                            assert audited["audit"][0]["claim"]["entity"] == "Apple Inc."
                        scope_checks.append(audited)
                    (out / "subject-period-audit.json").write_text(
                        json.dumps(scope_checks, indent=2), encoding="utf-8"
                    )
                    numeric_inputs = []
                    for label, text, code in [
                        ("characters", "x" * 65537, "audit_input_limit"),
                        ("mentions", "$1 " * 257, "audit_input_limit"),
                        ("overflow", "$" + "9" * 400, "numeric_out_of_range"),
                    ]:
                        try:
                            api("/api/audit", {"text": text, "ticker": "AAPL"})
                        except HTTPError as exc:
                            rejected = json.load(exc)
                            assert exc.code == 422 and rejected["code"] == code
                            assert "audit" not in rejected
                        else:
                            raise AssertionError(f"numeric input {label} was not rejected")
                        numeric_inputs.append({"case": label, "response": rejected})
                    empty = json.loads(
                        api("/api/audit", {"text": "USD" + " " * 32000 + "x", "ticker": "AAPL"})
                    )
                    assert empty["empty_extraction"] and not empty["all_numbers_supported"]
                    assert json.loads(api("/health"))["execution"]["active"] == 0
                    (out / "numeric-input-errors.json").write_text(
                        json.dumps(numeric_inputs, indent=2), encoding="utf-8"
                    )
                    body = {
                        "question": "Apple revenue for 2023?",
                        "ticker": "AAPL",
                        "fiscal_years": [2023],
                    }
                    doc = json.loads(api("/api/runs", body, "wheel-smoke-key"))
                    url = "/api/runs/" + doc["run_id"]
                    assert json.loads(api("/api/runs", body, "wheel-smoke-key")) == doc
                    for _ in range(15):
                        if doc["status"] != "ready":
                            break
                        doc = json.loads(
                            api(url + "/advance", {"expected_revision": doc["revision"]})
                        )
                    assert doc["status"] == "completed" and doc["dispatches"] > 0
                    original = api(url + "/export")
                    (out / "export.json").write_bytes(original)
                    verify_export(json.loads(original))
                else:
                    assert api(url + "/export") == original
                    assert json.loads(api("/api/runs"))["items"][0]["run_id"] == doc["run_id"]
            finally:
                if os.name == "nt" and process.poll() is None:
                    # Windows console-script launchers own a Python child. Stop both.
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        check=True,
                        capture_output=True,
                        timeout=15,
                    )
                else:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
    audit = subprocess.run(
        [
            cli,
            "audit",
            "--ticker",
            "AAPL",
            "--years",
            "2023",
            "--text",
            "Apple FY2023 revenue was $383.285 billion.",
        ],
        cwd=out / "work",
        env=env,
        capture_output=True,
        timeout=30,
    )
    (out / "cli-audit.log").write_bytes(audit.stdout + audit.stderr)
    assert audit.returncode == 0 and b"OK " in audit.stdout, "installed CLI audit failed"
    (out / "probe.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "origin": str(origin),
                "version": version,
                "implementation_id": implementation_id(),
                "python": sys.version,
                "server_pids": processes,
                "run_id": doc["run_id"],
                "dispatches": doc["dispatches"],
                "export_sha256": sha(original),
                "static_assets_verified": True,
                "restart_export_unchanged": True,
                "cli_audit": "PASS",
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def run(out: Path, uv: str) -> dict:
    out.mkdir(parents=True, exist_ok=False)
    env, initial, step = isolated_env(), source_hashes(), 0
    result = {"status": "FAILED", "source_hashes": initial}

    def command(args, cwd=ROOT):
        nonlocal step
        step += 1
        with (out / f"command-{step:02d}.log").open("xb") as log:
            completed = subprocess.run(args, cwd=cwd, env=env, stdout=log, stderr=log, timeout=300)
        if completed.returncode:
            raise RuntimeError(f"command {step} exited {completed.returncode}; see saved log")

    try:
        command([uv, "--version"])
        command([uv, "build", "--out-dir", str(out / "dist")])
        wheel, sdist = next((out / "dist").glob("*.whl")), next((out / "dist").glob("*.tar.gz"))
        inventory = inspect_archives(wheel, sdist, initial)
        (out / "archive-inventory.json").write_text(
            json.dumps(inventory, indent=2), encoding="utf-8"
        )
        # Negative control: a missing browser asset must fail even though the wheel opens.
        bad = out / "missing-asset.whl"
        with zipfile.ZipFile(wheel) as source, zipfile.ZipFile(bad, "w") as target:
            for item in source.infolist():
                if item.filename != "finreceipts/app/static/runs.js":
                    target.writestr(item, source.read(item.filename))
        try:
            inspect_archives(bad, sdist, initial)
        except ValueError:
            result["missing_asset_negative_control"] = "REJECTED"
        else:
            raise AssertionError("archive checker accepted a missing browser asset")
        command(
            [
                uv,
                "export",
                "--locked",
                "--extra",
                "web",
                "--no-dev",
                "--no-emit-project",
                "--output-file",
                str(out / "dependencies.txt"),
            ]
        )
        command([uv, "venv", "--python", sys.executable, str(out / "venv")])
        python = out / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        command(
            [
                uv,
                "pip",
                "sync",
                "--python",
                str(python),
                "--require-hashes",
                str(out / "dependencies.txt"),
            ]
        )
        command([uv, "pip", "install", "--python", str(python), "--no-deps", str(wheel)])
        command([uv, "pip", "check", "--python", str(python)])
        work = out / "work"
        (work / "cache").mkdir(parents=True)
        # Explicitly provision only the replay dataset from the sdist. No source imports.
        with tarfile.open(sdist) as archive:
            fixtures = [
                m for m in archive.getmembers() if "/tests/fixtures/sec/" in m.name and m.isfile()
            ]
            assert len(fixtures) == 3
            for member in fixtures:
                stream = archive.extractfile(member)
                assert stream is not None
                (work / "cache" / Path(member.name).name).write_bytes(stream.read())
        command(
            [str(python), "-I", str(Path(__file__).resolve()), "--probe", "--out", str(out)],
            cwd=work,
        )
        assert source_hashes() == initial, "source changed during package drill"
        result.update(status="PASS", probe=json.loads((out / "probe.json").read_text()))
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["artifacts"] = {
            p.relative_to(out).as_posix(): sha(p.read_bytes())
            for p in out.rglob("*")
            if p.is_file() and not p.is_relative_to(out / "venv")
        }
        (out / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="new evidence directory")
    parser.add_argument("--uv", default=shutil.which("uv") or "uv")
    parser.add_argument("--probe", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.probe:
        probe(args.out.resolve())
    else:
        summary = run(args.out.resolve(), args.uv)
        print(
            json.dumps(
                {k: v for k, v in summary.items() if k != "source_hashes" and k != "artifacts"}
            )
        )
        raise SystemExit(0 if summary["status"] == "PASS" else 2)
