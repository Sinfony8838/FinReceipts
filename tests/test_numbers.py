import pytest

from finreceipts.verify.numbers import extract_numbers, parse_number


@pytest.mark.parametrize(
    ("text", "value", "unit"),
    [
        ("$383.3 billion", 383.3e9, "USD"),
        ("$383,285 million", 383_285e6, "USD"),
        ("US$1.2 trillion", 1.2e12, "USD"),
        ("383,285 million USD", 383_285e6, "USD"),
        ("$29.9bn", 29.9e9, "USD"),
        ("352.6B", 352.6e9, None),
        ("$6.13", 6.13, "USD"),
        ("44.1%", 44.1, "percent"),
        ("44.1 percent", 44.1, "percent"),
        ("−5.5%", -5.5, "percent"),
        ("-2.8%", -2.8, "percent"),
        ("($1,234) million", -1_234e6, "USD"),
        ("3832.85亿美元", 3832.85e8, "USD"),
        ("1.2万亿元", 1.2e12, "CNY"),
        ("2023", 2023.0, None),
    ],
)
def test_parse_number(text, value, unit):
    m = parse_number(text)
    assert m is not None
    assert m.value == pytest.approx(value)
    assert m.unit == unit


def test_years_and_identifiers_are_skipped():
    found = extract_numbers("In fiscal 2023 the 10-K for Q3 and FY23 showed $5 million.")
    assert [m.text for m in found] == ["$5 million"]


def test_spans_point_at_surface_text():
    text = "Revenue was $383.3 billion, down 2.8%."
    for m in extract_numbers(text):
        assert text[m.span[0] : m.span[1]] == m.text


def test_negative_context_detected():
    (pct,) = extract_numbers("revenue declined 2.8% year over year")
    assert pct.negative_context
    assert pct.value == 2.8


def test_rounding_step_reflects_displayed_precision():
    m = parse_number("$383.3 billion")
    assert m.rounding_step == pytest.approx(0.05e9)
    assert parse_number("$383,285 million").rounding_step == pytest.approx(0.5e6)


def test_lowercase_single_letter_scale_is_ignored_without_currency():
    m = parse_number("5 m")
    assert m.value == 5 and m.text == "5"


def test_parenthetical_percent_is_not_negative():
    text = "Net income was $93.7 billion (24.0% of revenue)."
    pct = extract_numbers(text)[1]
    assert pct.text == "24.0%" and pct.value == 24.0


def test_dates_are_not_numbers():
    from finreceipts.verify.numbers import extract_numbers

    for text in (
        "for the fiscal year ended December 31, 2025",
        "fiscal year ended Sept. 27, 2025",
        "as of 31 December 2024",
        "period ending 2025-09-27",
        "filed 10/31/2025",
        "截至2025年12月31日",
    ):
        assert extract_numbers(text) == [], text
    found = extract_numbers("For the year ended December 31, 2025, revenue was $637.9 billion.")
    assert [m.text for m in found] == ["$637.9 billion"]
    # a scaled or currency figure right after a month is still a number
    assert [m.text for m in extract_numbers("In May $5 billion was repaid")] == ["$5 billion"]
