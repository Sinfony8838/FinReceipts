from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta

import pytest

from finreceipts.evals.dataset import read_jsonl
from finreceipts.pit import Catalog, Filing, Policy, TimeEvidence, content_hash, fact_hash
from finreceipts.pit.isolation import Envelope, cache_key
from finreceipts.pit.selection import filter_facts, select_annual
from tests.conftest import make_fact

RAW = b"synthetic original filing"
A = "0000000001-23-000001"
B = "0000000001-24-000001"
C = "0000000001-25-000001"


def instant(raw):
    return TimeEvidence(raw, "exact")


def policy(raw="2023-11-03T21:00:00Z", **kwargs):
    return Policy(datetime.fromisoformat(raw.replace("Z", "+00:00")), **kwargs)


def record(accn=A, raw=RAW, observed="2023-11-03T20:30:00Z", facts=(), **kwargs):
    return Filing(
        accn,
        1,
        "10-K",
        content_hash(raw),
        first_observed=instant(observed) if observed else None,
        observation_source="recorded-collector/receipt-1" if observed else "",
        acceptance=instant("2023-11-03T20:00:00Z"),
        acceptance_source="recorded-SEC-header",
        fact_hashes=tuple(map(fact_hash, facts)),
        **kwargs,
    )


def test_observation_lags_acceptance_same_day_and_equality():
    catalog = Catalog((record(),))
    early = policy("2023-11-03T20:15:00Z")
    assert not catalog.decide(A, RAW, early).allowed
    assert catalog.decide(A, RAW, replace(early, mode="acceptance_proxy")).allowed
    proxy = catalog.decide(A, RAW, replace(early, mode="acceptance_proxy"))
    assert proxy.approximate and proxy.reason == "acceptance_proxy_approximate"
    assert catalog.decide(A, RAW, policy("2023-11-03T20:30:00Z")).allowed
    lagged = replace(early, mode="acceptance_proxy", acceptance_lag=timedelta(minutes=20))
    assert not catalog.decide(A, RAW, lagged).allowed


@pytest.mark.parametrize("mode", ["observed_replay", "acceptance_proxy"])
def test_missing_identity_hash_and_conflict_are_fail_closed(mode):
    p = policy(mode=mode)
    catalog = Catalog((record(),))
    assert catalog.decide(B, RAW, p).status == "insufficient"
    assert catalog.decide(A, None, p).reason == "missing_raw_content"
    assert catalog.decide(A, RAW + b" forged", p).reason == "content_hash_mismatch"
    conflicting = Catalog((record(), replace(record(), sha256=content_hash(b"other"))))
    assert conflicting.decide(A, RAW, p).reason == "conflicting_accession"
    assert Catalog((record(), record())).decide(A, RAW, p).allowed
    assert Catalog((replace(record(), first_observed=None),)).decide(A, RAW, policy()).status == (
        "insufficient"
    )
    assert Catalog((replace(record(), acceptance=None),)).decide(A, RAW, p).allowed == (
        mode == "observed_replay"
    )


def test_provenance_and_impossible_time():
    r = record()
    for field, mode in [
        ("observation_source", "observed_replay"),
        ("acceptance_source", "acceptance_proxy"),
    ]:
        assert Catalog((replace(r, **{field: ""}),)).decide(A, RAW, policy(mode=mode)).status == (
            "insufficient"
        )
    impossible = replace(r, first_observed=instant("2023-11-03T19:59:00Z"))
    assert (
        Catalog((impossible,)).decide(A, RAW, policy()).reason == "observation_precedes_acceptance"
    )


def test_timezone_normalization_raw_preserved_and_precision_conservative():
    t = TimeEvidence("2023-11-03T16:30:00-04:00", "minute")
    assert t.raw.endswith("-04:00") and t.lower_utc == datetime(2023, 11, 3, 20, 30, tzinfo=UTC)
    r = replace(record(), first_observed=t)
    catalog = Catalog((r,))
    assert not catalog.decide(A, RAW, policy("2023-11-03T20:30:30Z")).allowed
    assert catalog.decide(A, RAW, policy("2023-11-03T20:31:00Z")).allowed
    second = replace(r, first_observed=TimeEvidence("2023-11-03T20:30:00Z"))
    assert not Catalog((second,)).decide(A, RAW, policy("2023-11-03T20:30:00Z")).allowed
    day = replace(
        r, acceptance=None, first_observed=TimeEvidence("2023-11-03T00:00:00+00:00", "day")
    )
    assert not Catalog((day,)).decide(A, RAW, policy()).allowed
    assert Catalog((day,)).decide(A, RAW, policy("2023-11-04T00:00:00Z")).allowed
    # Summer/winter SEC source offsets are explicit; naive local time is not guessed.
    assert instant("2023-12-03T16:30:00-05:00").lower_utc.hour == 21


@pytest.mark.parametrize(
    "raw,precision",
    [
        ("2023-11-03", "day"),
        ("2023-11-03T16:30:00", "second"),
        ("2023-11-03T16:30:01Z", "minute"),
        ("2023-11-03T01:00:00Z", "day"),
        ("2023-11-03T16:30:00.1Z", "second"),
        ("2023-11-03T16:30Z", "second"),
        ("2023-11-03T16:30-04:00", "second"),
    ],
)
def test_invalid_time(raw, precision):
    with pytest.raises(ValueError):
        TimeEvidence(raw, precision)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cutoff": date(2023, 11, 3)},
        {"cutoff": datetime(2023, 11, 3)},
        {"mode": "filed"},
        {"version": ""},
        {"acceptance_lag": timedelta(seconds=-1)},
        {"acceptance_lag": timedelta(seconds=1)},
    ],
)
def test_invalid_policy(kwargs):
    with pytest.raises(ValueError):
        Policy(**({"cutoff": datetime(2023, 11, 3, tzinfo=UTC)} | kwargs))


def test_no_backdating_amendment_or_later_comparative_old_period():
    orig = make_fact(100, accn=A)
    amended = make_fact(110, accn=B, form="10-K/A", filed="2024-01-01")
    comparative = make_fact(120, accn=C, filed="2025-01-01")
    rows = [comparative, amended, orig]
    records = [
        record(facts=(orig,)),
        replace(
            record(B, observed="2024-01-01T12:00:00Z", facts=(amended,), revision_of=A),
            form="10-K/A",
        ),
        record(C, observed="2025-01-01T12:00:00Z", facts=(comparative,)),
    ]
    catalog, blobs = Catalog(tuple(records)), {A: RAW, B: RAW, C: RAW}
    assert select_annual(rows, catalog, blobs, policy(), prefer="latest")[2023].value == 100
    later = policy("2024-06-01T00:00:00Z")
    assert select_annual(rows, catalog, blobs, later, prefer="latest")[2023].value == 110
    final = policy("2025-06-01T00:00:00Z")
    assert select_annual(rows, catalog, blobs, final, prefer="original")[2023].accn == A
    assert select_annual(rows, catalog, blobs, final, prefer="latest")[2023].accn == C
    assert records[1].revision_of == A and records[2].revision_of is None
    with pytest.raises(ValueError):
        select_annual(rows, catalog, blobs, final, prefer="anything")


def test_fact_binding_identity_missing_and_changed_value():
    fact = make_fact(100, accn=A)
    p = policy()
    assert Catalog((record(),)).decide_fact(fact, RAW, p).status == "insufficient"
    catalog = Catalog((record(facts=(fact,)),))
    for field, value in [("value", 111), ("end", date(2000, 1, 1)), ("unit", "shares")]:
        forged = fact.model_copy(update={field: value})
        assert catalog.decide_fact(forged, RAW, p).reason == "fact_binding_mismatch"
    forged = fact.model_copy(update={"cik": 2})
    assert catalog.decide_fact(forged, RAW, p).reason == "filing_identity_mismatch"
    good = filter_facts([fact], catalog, {A: RAW}, p)
    good.facts[0].value = 999
    assert fact.value == 100  # copies prevent downstream mutation of caller rows


def test_ties_period_and_stream_conflicts_are_not_input_order_dependent():
    a, b = make_fact(100, accn=A), make_fact(110, accn=B)
    catalog = Catalog((record(facts=(a,)), record(B, facts=(b,))))
    for rows in ([a, b], [b, a]):
        with pytest.raises(ValueError, match="ambiguous"):
            select_annual(rows, catalog, {A: RAW, B: RAW}, policy())
    changed = b.model_copy(update={"concept": "Assets"})
    catalog = Catalog((record(facts=(a,)), record(B, facts=(changed,))))
    with pytest.raises(ValueError, match="stream"):
        select_annual([a, changed], catalog, {A: RAW, B: RAW}, policy())


@pytest.mark.parametrize("prefer", ["original", "latest"])
@pytest.mark.parametrize("reverse", [False, True])
def test_overlapping_availability_intervals_are_ambiguous(prefer, reverse):
    a, b = make_fact(100, accn=A), make_fact(110, accn=B)
    records = (
        replace(record(facts=(a,)), first_observed=TimeEvidence("2023-11-03T20:30:00Z", "minute")),
        record(B, observed="2023-11-03T20:30:30Z", facts=(b,)),
    )
    rows = [b, a] if reverse else [a, b]
    with pytest.raises(ValueError, match="ambiguous"):
        select_annual(rows, Catalog(records), {A: RAW, B: RAW}, policy(), prefer=prefer)


@pytest.mark.parametrize("prefer", ["original", "latest"])
@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_nonselected_accession_is_refused(prefer, reverse):
    a = make_fact(100, accn=A)
    b = make_fact(110, accn=B)
    conflict = b.model_copy(update={"value": 111})
    c = make_fact(120, accn=C)
    records = (
        record(facts=(a,)),
        record(B, observed="2023-11-03T20:40:00Z", facts=(b, conflict)),
        record(C, observed="2023-11-03T20:50:00Z", facts=(c,)),
    )
    rows = [c, conflict, b, a] if reverse else [a, b, conflict, c]
    with pytest.raises(ValueError, match="conflicting"):
        select_annual(rows, Catalog(records), {A: RAW, B: RAW, C: RAW}, policy(), prefer=prefer)


@pytest.mark.parametrize("mode", ["observed_replay", "acceptance_proxy"])
@pytest.mark.parametrize("prefer", ["original", "latest"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("overlap", [True, False])
@pytest.mark.parametrize("precision", ["second", "minute", "day"])
def test_interval_order_in_both_modes_including_explicit_proxy_lag(
    mode,
    prefer,
    reverse,
    overlap,
    precision,
):
    a, b = make_fact(100, accn=A), make_fact(110, accn=B)
    field = "first_observed" if mode == "observed_replay" else "acceptance"
    bucket = TimeEvidence("2023-11-03T00:00:00Z", precision)
    width = {
        "second": timedelta(seconds=1),
        "minute": timedelta(minutes=1),
        "day": timedelta(days=1),
    }[precision]
    point = instant((bucket.lower_utc + (width / 2 if overlap else width)).isoformat())
    records = (
        replace(
            record(facts=(a,)),
            **{
                field: bucket,
                "first_observed": bucket,
                "acceptance": bucket if mode == "acceptance_proxy" else None,
            },
        ),
        replace(
            record(B, facts=(b,)),
            **{
                field: point,
                "first_observed": point,
                "acceptance": point if mode == "acceptance_proxy" else None,
            },
        ),
    )
    p = policy(
        "2023-11-05T00:00:00Z",
        mode=mode,
        acceptance_lag=timedelta(seconds=7) if mode == "acceptance_proxy" else timedelta(),
    )
    decision = Catalog(records).decide(A, RAW, p)
    lag = p.acceptance_lag
    assert decision.available_lower_utc == bucket.lower_utc + lag
    assert decision.available_upper_utc == bucket.upper_utc + lag
    rows = [b, a] if reverse else [a, b]
    if overlap:
        with pytest.raises(ValueError, match="ambiguous"):
            select_annual(rows, Catalog(records), {A: RAW, B: RAW}, p, prefer=prefer)
    else:
        selected = select_annual(rows, Catalog(records), {A: RAW, B: RAW}, p, prefer=prefer)
        assert selected[2023].accn == (A if prefer == "original" else B)


@pytest.mark.parametrize("prefer", ["original", "latest"])
@pytest.mark.parametrize("precision", ["second", "minute", "day"])
def test_overlap_at_closed_endpoint_is_ambiguous_even_with_equal_values(prefer, precision):
    a, b = make_fact(100, accn=A), make_fact(100, accn=B)
    minute = TimeEvidence("2023-11-03T00:00:00Z", precision)
    records = (
        replace(record(facts=(a,)), first_observed=minute, acceptance=None),
        replace(record(B, observed=minute.upper_utc.isoformat(), facts=(b,)), acceptance=None),
    )
    with pytest.raises(ValueError, match="ambiguous"):
        select_annual(
            [a, b],
            Catalog(records),
            {A: RAW, B: RAW},
            policy("2023-11-05T00:00:00Z"),
            prefer=prefer,
        )


@pytest.mark.parametrize("prefer", ["original", "latest"])
@pytest.mark.parametrize("reverse", [False, True])
def test_nonselected_overlap_does_not_obscure_definite_extreme(prefer, reverse):
    a, b, c = make_fact(100, accn=A), make_fact(110, accn=B), make_fact(120, accn=C)
    # B and C overlap but A is definitely earliest/latest; only that requested
    # extreme is determined. Same-value rows with distinct receipt identities
    # still cannot be arbitrarily chosen when they are competing extremes.
    extreme = "2023-11-03T20:20:00Z" if prefer == "original" else "2023-11-03T20:40:00Z"
    records = (
        record(observed=extreme, facts=(a,)),
        replace(
            record(B, facts=(b,)), first_observed=TimeEvidence("2023-11-03T20:30:00Z", "minute")
        ),
        record(C, observed="2023-11-03T20:30:30Z", facts=(c,)),
    )
    rows = [c, b, a] if reverse else [a, b, c]
    result = select_annual(
        rows, Catalog(records), {A: RAW, B: RAW, C: RAW}, policy(), prefer=prefer
    )
    assert result[2023].accn == A


def test_duplicate_identical_fact_rows_do_not_create_order_ambiguity():
    a = make_fact(100, accn=A)
    catalog = Catalog((record(facts=(a,)),))
    assert select_annual([a, a.model_copy(deep=True)], catalog, {A: RAW}, policy())[2023].accn == A


def test_revision_policy_namespace_rejects_first_release_checkpoint():
    p, catalog = policy(), Catalog((record(),))
    assert p.version == "pit-v2"
    old = replace(p, version="pit-v1")
    envelope = Envelope.seal(old, catalog, "checkpoint:run1", {"selected": A})
    with pytest.raises(ValueError, match="mismatch"):
        envelope.open(p, catalog, "checkpoint:run1")


def test_policy_cache_checkpoint_isolation_and_integrity():
    p, catalog = policy(), Catalog((record(),))
    req = {"ticker": "SYN"}
    envelope = Envelope.seal(p, catalog, "checkpoint:run1", {"verified": [A]})
    assert envelope.open(p, catalog, "checkpoint:run1") == {"verified": [A]}
    altered = [
        replace(p, cutoff=p.cutoff + timedelta(seconds=1)),
        replace(p, mode="acceptance_proxy"),
        replace(p, version="different-policy-version"),
        replace(p, mode="acceptance_proxy", acceptance_lag=timedelta(seconds=1)),
    ]
    for other in altered:
        assert cache_key(p, catalog, req) != cache_key(other, catalog, req)
        with pytest.raises(ValueError, match="mismatch"):
            envelope.open(other, catalog, "checkpoint:run1")
    other_catalog = Catalog((replace(record(), observation_source="different collector"),))
    assert cache_key(p, catalog, req) != cache_key(p, other_catalog, req)
    with pytest.raises(ValueError):
        envelope.open(p, other_catalog, "checkpoint:run1")
    with pytest.raises(ValueError):
        envelope.open(p, catalog, "cache")
    with pytest.raises(ValueError, match="integrity"):
        replace(envelope, payload_json='{"verified":["forged"]}').open(
            p, catalog, "checkpoint:run1"
        )
    with pytest.raises(FrozenInstanceError):
        p.cutoff = datetime(2099, 1, 1, tzinfo=UTC)


def test_legacy_eval_schema_does_not_supply_strict_availability():
    from pathlib import Path

    items = read_jsonl(Path(__file__).parents[1] / "evals/datasets/xbrl_numeric_v0.jsonl")
    assert len(items) == 100 and isinstance(items[0].as_of, date)
    with pytest.raises(ValueError):
        Policy(items[0].as_of)
    assert Catalog(()).decide(items[0].evidence[0].accn, b"", policy()).status == "insufficient"
