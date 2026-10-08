from finreceipts.cli import build_parser, main
from tests.conftest import FIXTURES


def test_parser_has_commands():
    p = build_parser()
    for cmd in ("record", "record-ashare", "gen-eval", "gen-eval-cn", "audit", "eval"):
        argv = [cmd, "--ticker", "AAPL"] if cmd == "audit" else [cmd]
        assert p.parse_args(argv).cmd == cmd


def test_audit_command_offline(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FINRECEIPTS_CACHE_DIR", str(FIXTURES))
    monkeypatch.setenv("FINRECEIPTS_OFFLINE", "1")
    out = tmp_path / "r.html"
    code = main(
        [
            "audit",
            "--ticker",
            "AAPL",
            "--years",
            "2023",
            "--text",
            "Revenue was $383.3 billion.",
            "--html",
            str(out),
        ]
    )
    assert code == 0 and out.exists()
    assert "supported" in capsys.readouterr().out
    code = main(["audit", "--ticker", "AAPL", "--years", "2023", "--text", "It was $1.0 billion."])
    assert code == 1


def test_eval_command_with_fake_llm(monkeypatch, tmp_path, capsys, edgar):
    from finreceipts.evals.dataset import write_jsonl
    from finreceipts.evals.generate import generate

    monkeypatch.setenv("FINRECEIPTS_CACHE_DIR", str(FIXTURES))
    monkeypatch.setenv("FINRECEIPTS_OFFLINE", "1")
    monkeypatch.setenv("FINRECEIPTS_LLM_PROVIDER", "fake")
    ds = tmp_path / "d.jsonl"
    write_jsonl(
        generate(edgar, tickers=["AAPL"], fiscal_years=[2023], n_direct=2, n_growth=0, n_ratio=0),
        ds,
    )
    assert (
        main(["eval", "--dataset", str(ds), "--model", "fake", "--out-dir", str(tmp_path / "run")])
        == 0
    )
    assert (tmp_path / "run" / "summary.json").exists()
    monkeypatch.delenv("FINRECEIPTS_SMALL_MODEL", raising=False)
    assert main(["eval", "--dataset", str(ds)]) == 2


def test_eval_command_cascade_with_prices(monkeypatch, tmp_path, edgar):
    import json

    from finreceipts.evals.dataset import write_jsonl
    from finreceipts.evals.generate import generate

    monkeypatch.setenv("FINRECEIPTS_CACHE_DIR", str(FIXTURES))
    monkeypatch.setenv("FINRECEIPTS_OFFLINE", "1")
    monkeypatch.setenv("FINRECEIPTS_LLM_PROVIDER", "fake")
    monkeypatch.delenv("FINRECEIPTS_LARGE_MODEL", raising=False)
    ds = tmp_path / "d.jsonl"
    write_jsonl(
        generate(edgar, tickers=["AAPL"], fiscal_years=[2023], n_direct=1, n_growth=0, n_ratio=0),
        ds,
    )
    base = ["eval", "--dataset", str(ds), "--model", "s", "--mode", "cascade"]
    assert main(base) == 2  # no escalate model
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps({"s": [1, 2], "l": [10, 20]}))
    out = tmp_path / "run"
    args = [*base, "--escalate-model", "l", "--max-revisions", "0", "--prices", str(prices)]
    assert main([*args, "--out-dir", str(out)]) == 0
    summary = json.loads((out / "summary.json").read_text())
    assert summary["config"]["escalate_model"] == "l"
    # the offline fake answers "I don't know." (no number) -> cascade escalates
    assert summary["escalation_rate"] == 1.0 and summary["cost"] > 0
