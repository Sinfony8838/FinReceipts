"""Package local acceptance evidence without credentials, databases or container disks."""

import gzip
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/evidence/v2-20261024"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    entries = []

    def copy(source, target, compress=False):
        raw = source.read_bytes()
        destination = OUT / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(gzip.compress(raw, mtime=0) if compress else raw)
        entries.append(
            {
                "original": source.relative_to(ROOT).as_posix(),
                "copy": destination.relative_to(OUT).as_posix(),
                "original_bytes": len(raw),
                "original_sha256": hashlib.sha256(raw).hexdigest(),
                "copy_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            }
        )

    # This explicit allowlist avoids private environment files and broad runtime directories.
    for pattern in (
        "v2-acceptance-*.json",
        "v2-faults-*.json",
        "v2-archive-*.json",
        "v2-observability-report.json",
        "v2-runtime-final.json",
        "v2-openapi.json",
        "v2-regression-final.log",
        "v2-rules-evaluation.json",
    ):
        for source in sorted((ROOT / "runs").glob(pattern)):
            copy(source, Path("checks") / source.name)
    java = ROOT / "server/target/surefire-reports/io.finreceipts.PlatformIntegrationTest.txt"
    if java.exists():
        copy(java, Path("checks/java-integration.txt"))
    for name in ("v2-browser-1792852158170", "v2-browser-cases-1792852911946"):
        for source in sorted((ROOT / "runs" / name).glob("*")):
            if source.suffix in {".json", ".png"}:
                copy(source, Path("browser") / name / source.name)
    for source in sorted((ROOT / "runs/platform-v2-live").glob("*")):
        if source.suffix in {".json", ".jsonl"}:
            copy(source, Path("models") / source.name)
    capacity = []
    for source in sorted((ROOT / "runs").glob("v2-load-*.json")):
        raw = json.loads(source.read_text(encoding="utf-8"))
        brief = {k: v for k, v in raw.items() if k not in {"samples", "streams"}}
        if raw.get("streams"):
            streams = raw["streams"]
            brief["stream_summary"] = {
                "connections": len(streams),
                "complete": sum(s.get("complete", False) for s in streams),
                "statuses": {
                    str(c): sum(s["status"] == c for s in streams)
                    for c in {s["status"] for s in streams}
                },
                "min_reconnects": min(s["reconnects"] for s in streams),
                "max_reconnects": max(s["reconnects"] for s in streams),
            }
        brief["original_report"] = source.relative_to(ROOT).as_posix()
        brief["original_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        capacity.append(brief)
        copy(source, Path("capacity/raw") / (source.name + ".gz"), compress=True)
    (OUT / "capacity").mkdir(exist_ok=True)
    (OUT / "capacity/summary.json").write_text(json.dumps(capacity, indent=2), encoding="utf-8")
    sources = []
    for folder in ("server/src", "src/finreceipts/platform", "deploy/v2"):
        for source in sorted((ROOT / folder).rglob("*")):
            if source.is_file() and "__pycache__" not in source.parts:
                sources.append(
                    {
                        "path": source.relative_to(ROOT).as_posix(),
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    }
                )
    manifest = {
        "packaged_at": time.time(),
        "scope": (
            "Local measured evidence. Includes failed runs; consult each acceptance verdict. "
            "UI work in another thread is not silently included."
        ),
        "git_head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True
        ).stdout.strip(),
        "files": entries,
        "backend_source_files": sources,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    archive = shutil.make_archive(str(ROOT / "runs/finreceipts-v2-evidence-20261024"), "zip", OUT)
    print(
        json.dumps(
            {
                "files": len(entries),
                "archive": archive,
                "sha256": hashlib.sha256(Path(archive).read_bytes()).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    main()
