from finreceipts.report.html import render_answer_html, render_report
from finreceipts.verify.verifier import Verifier


def test_render_marks_supported_and_unsupported(aapl_lookup):
    text = "Revenue was $383.3 billion <b>and</b> 161,000 employees."
    audit = Verifier(aapl_lookup).audit_text(text, fiscal_years={2023})
    html = render_answer_html(text, audit)
    assert '<span class="num ok" tabindex="0">$383.3 billion' in html
    assert '<span class="num bad" tabindex="0">161,000' in html
    assert "&lt;b&gt;and&lt;/b&gt;" in html  # user text is escaped
    assert "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/" in html


def test_render_report_page(aapl_lookup):
    text = "Revenue was $383.3 billion."
    audit = Verifier(aapl_lookup).audit_text(text, fiscal_years={2023})
    page = render_report(title="t", question="q?", answer=text, audit=audit)
    assert page.startswith("<!doctype html>") and "1/1 numbers verified" in page
    assert "Not investment advice" in page
