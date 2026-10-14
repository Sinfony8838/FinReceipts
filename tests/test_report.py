from finreceipts.report.html import render_answer_html, render_report
from finreceipts.verify.verifier import Verifier


def test_render_marks_supported_and_unsupported(aapl_lookup):
    text = "Revenue in 2023 was $383.3 billion. <b>and</b> 161,000 employees."
    audit = Verifier(aapl_lookup).audit_text(text, fiscal_years={2023})
    html = render_answer_html(text, audit, verification_mode="text_scope")
    assert '<span class="num ok" tabindex="0">$383.3 billion' in html
    assert '<span class="num bad" tabindex="0">161,000' in html
    assert "&lt;b&gt;and&lt;/b&gt;" in html  # user text is escaped
    assert "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/" in html


def test_render_report_page(aapl_lookup):
    text = "Revenue in 2023 was $383.3 billion."
    audit = Verifier(aapl_lookup).audit_text(text, fiscal_years={2023})
    page = render_report(
        title="t", question="q?", answer=text, audit=audit, verification_mode="text_scope"
    )
    assert page.startswith("<!doctype html>") and "1/1 numbers supported" in page
    assert "Not investment advice" in page


def test_numeric_collision_report_defaults_to_no_semantic_claim(aapl_lookup):
    text = "Microsoft revenue was $383.3 billion."
    audit = Verifier(aapl_lookup).audit_text(text, numeric_only=True)
    assert audit[0].ok  # Historical value-only receipt is retained.
    page = render_report(title="t", question="q?", answer=text, audit=audit)
    assert "numeric matches by value only" in page
    assert "semantic attribution unchecked" in page
    assert 'class="num match"' in page
    assert 'class="num ok"' not in page
