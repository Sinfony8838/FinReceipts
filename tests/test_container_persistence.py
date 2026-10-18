"""Deployment topology and drill safety; actual Docker is a separate CI gate."""

import copy
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "container_drill", Path(__file__).parents[1] / "scripts/container_persistence_smoke.py"
)
drill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drill)


def config():
    return {
        "name": "fr-persist-unit",
        "services": {
            "api": {
                "environment": {
                    "FINRECEIPTS_RUN_DB": "/app/.cache/runtime/runs.sqlite3",
                    "FINRECEIPTS_LLM_PROVIDER": "fake",
                },
                "volumes": [
                    {
                        "type": "volume",
                        "source": "finreceipts_runtime",
                        "target": "/app/.cache/runtime",
                    },
                    {"type": "volume", "source": "finreceipts_cache", "target": "/app/.cache/sec"},
                ],
            }
        },
        "volumes": {
            "finreceipts_runtime": {"name": "fr-persist-unit_finreceipts_runtime"},
            "finreceipts_cache": {"name": "fr-persist-unit_finreceipts_cache"},
        },
    }


@pytest.mark.parametrize(
    "failure",
    [
        "no_db",
        "file_mount",
        "read_only",
        "bind",
        "alias",
        "overlap",
        "undeclared",
        "relative_path",
        "external",
        "global_name",
        "host_driver",
    ],
)
def test_invalid_storage_cannot_pass_topology_gate(failure):
    candidate = config()
    service = candidate["services"]["api"]
    mount = service["volumes"][0]
    if failure == "no_db":
        service["environment"].pop("FINRECEIPTS_RUN_DB")
    elif failure == "file_mount":
        mount["target"] += "/runs.sqlite3"
    elif failure == "read_only":
        mount["read_only"] = True
    elif failure == "bind":
        mount["type"] = "bind"
    elif failure == "alias":
        service["volumes"][1]["source"] = mount["source"]
    elif failure == "overlap":
        candidate["volumes"]["nested"] = {"name": "fr-persist-unit_nested"}
        service["volumes"].append(
            {"type": "volume", "source": "nested", "target": mount["target"] + "/runs.sqlite3-wal"}
        )
    elif failure == "undeclared":
        candidate["volumes"].pop("finreceipts_runtime")
    elif failure == "relative_path":
        service["environment"]["FINRECEIPTS_RUN_DB"] = ".cache/runtime/runs.sqlite3"
    elif failure == "external":
        candidate["volumes"]["finreceipts_cache"]["external"] = True
    elif failure == "global_name":
        candidate["volumes"]["finreceipts_cache"]["name"] = "production-cache"
    else:
        candidate["volumes"]["finreceipts_runtime"]["driver_opts"] = {"device": "/host/data"}
    with pytest.raises(
        (KeyError, ValueError), match="overlapping" if failure == "overlap" else None
    ):
        drill.validate_storage(candidate)


def test_directory_mount_covers_database_and_sidecars_without_modifying_config():
    candidate = config()
    original = copy.deepcopy(candidate)
    assert drill.validate_storage(candidate) == {
        "database": "/app/.cache/runtime/runs.sqlite3",
        "target": "/app/.cache/runtime",
        "volume": "finreceipts_runtime",
    }
    assert candidate == original


def test_drill_cannot_inherit_real_provider_or_compose_target(monkeypatch):
    for name in [
        "MINIMAX_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "COMPOSE_FILE",
        "COMPOSE_PROJECT_NAME",
        "FINRECEIPTS_LLM_PROVIDER",
        "FINRECEIPTS_LLM_API_KEY_ENV",
    ]:
        monkeypatch.setenv(name, "must-not-propagate")
    env = drill.isolated_env()
    assert "must-not-propagate" not in env.values()
    assert env["FINRECEIPTS_LLM_PROVIDER"] == "fake"
    assert env["FINRECEIPTS_OFFLINE"] == "1" and env["FINRECEIPTS_PORT"] == "0"


def test_compiled_project_cannot_override_the_generated_drill_project():
    with pytest.raises(ValueError, match="project"):
        drill.validate_storage(config(), project="fr-persist-different")


@pytest.mark.parametrize(
    "scenario",
    ["config_only", "engine_absent", "existing_container", "existing_volume", "build_failure"],
)
def test_drill_never_reports_unrun_as_pass_or_cleans_unclaimed_resources(
    tmp_path, monkeypatch, scenario
):
    commands = []

    def execute(args, **_kwargs):
        commands.append(args)
        output, code = "", 0
        if "config" in args:
            candidate = config()
            project = args[args.index("--project-name") + 1]
            candidate["name"] = project
            for name, volume in candidate["volumes"].items():
                volume["name"] = project + "_" + name
            output = json.dumps(candidate)
        elif "version" in args:
            if scenario == "engine_absent":
                code, output = 1, "engine is unavailable"
            else:
                output = '{"Version":"test-engine"}'
        elif scenario == "existing_container" and "ps" in args:
            output = "protected-container"
        elif scenario == "existing_volume" and "volume" in args:
            output = "protected-volume"
        elif "build" in args:
            code = 1
        return subprocess.CompletedProcess(args, code, output, "")

    monkeypatch.setattr(drill.subprocess, "run", execute)
    monkeypatch.setattr(drill, "source_hashes", lambda: {"test": "fixture"})
    result = drill.run_drill(tmp_path / "experiment", config_only=scenario == "config_only")
    assert result["status"] == {
        "config_only": "CONFIG_VALIDATED_ONLY",
        "engine_absent": "BLOCKED_UNRUN",
    }.get(scenario, "FAILED")
    assert result["volumes_removed"] is False
    assert not any("--volumes" in args or "-v" in args for args in commands)
    assert any("down" in args for args in commands) == (scenario == "build_failure")
    assert (
        json.loads((tmp_path / "experiment/summary.json").read_text())["status"] == result["status"]
    )
    if scenario == "config_only":
        assert len(commands) == 1
