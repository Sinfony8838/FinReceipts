"""Extract and normalise numbers mentioned in free text.

Handles currency markers (``$``, ``US$``, ``USD``, ``美元``), scale words
(thousand/million/billion/trillion, ``k/m/bn/B``, ``万/亿/万亿``), accounting
negatives ``(1,234)``, unicode minus, percentages and per-share amounts.
Every mention keeps its surface ``text`` and character ``span`` so reports can
highlight it, plus the displayed precision so the verifier can apply a
rounding-aware tolerance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SCALE_WORDS: dict[str, float] = {
    "thousand": 1e3,
    "k": 1e3,
    "million": 1e6,
    "millions": 1e6,
    "mn": 1e6,
    "mm": 1e6,
    "m": 1e6,
    "billion": 1e9,
    "billions": 1e9,
    "bn": 1e9,
    "b": 1e9,
    "trillion": 1e12,
    "tn": 1e12,
    "t": 1e12,
    "千": 1e3,
    "万": 1e4,
    "百万": 1e6,
    "千万": 1e7,
    "亿": 1e8,
    "万亿": 1e12,
}

NEGATIVE_WORDS = (
    "decrease",
    "decreased",
    "decline",
    "declined",
    "fell",
    "fall",
    "drop",
    "dropped",
    "down",
    "lower by",
    "shrank",
    "contracted",
    "loss of",
    "下降",
    "减少",
    "下滑",
    "亏损",
)

_NUM = r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_PATTERN = re.compile(
    r"(?P<neg_paren>\()?"
    r"(?P<sign>[-−–])?\s*"
    r"(?P<cur>US\$|\$|USD\s?|RMB\s?|CNY\s?|¥)?\s*" + _NUM + r"(?P<close_paren>\))?"
    r"(?:\s*(?P<scale>万亿|百万|千万|thousand|millions?|billions?|trillion|mn|bn|tn|mm|[kKmMbBtT](?![a-zA-Z])|千|万|亿))?"
    r"\s*(?P<suffix>%|percent|per\s?cent|个百分点|percentage points?|pp\b|美元|元|dollars?|USD)?",
    re.IGNORECASE,
)

_YEAR = re.compile(r"^(19|20)\d{2}$")

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE = re.compile(
    r"\b\d{4}-\d{1,2}-\d{1,2}\b"  # 2025-09-27
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b"  # 09/27/2025
    r"|\b" + _MONTH + r"\s+\d{1,2}(?:st|nd|rd|th)?\b(?!\.\d)"  # September 27
    r"|\b\d{1,2}(?:st|nd|rd|th)?\s+" + _MONTH + r"(?![a-z])"  # 27 September
    r"|\d{4}年\d{1,2}月(?:\d{1,2}日)?|\d{1,2}月\d{1,2}日",
    re.IGNORECASE,
)


def _date_spans(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in _DATE.finditer(text)]


@dataclass(frozen=True)
class NumberMention:
    """A number found in text, normalised to base units."""

    text: str
    value: float
    unit: str | None
    span: tuple[int, int]
    decimals: int
    scale: float
    negative_context: bool = False

    @property
    def rounding_step(self) -> float:
        """Half of the smallest displayed increment, in base units."""
        return 0.5 * (10.0 ** (-self.decimals)) * self.scale


def _is_ascii_letter(ch: str) -> bool:
    return bool(ch) and ch.isascii() and ch.isalpha()


def _decimals(num: str) -> int:
    return len(num.split(".", 1)[1]) if "." in num else 0


def parse_number(text: str) -> NumberMention | None:
    """Parse a single number expression (e.g. ``"$383.3 billion"``)."""
    found = extract_numbers(text, keep_years=True)
    return found[0] if found else None


def extract_numbers(text: str, *, keep_years: bool = False) -> list[NumberMention]:
    """Find all numeric mentions in ``text``.

    Bare four-digit years (``2023``) are dropped unless ``keep_years``.
    """
    mentions: list[NumberMention] = []
    dates = _date_spans(text)
    for m in _PATTERN.finditer(text):
        num = m.group("num")
        cur = (m.group("cur") or "").strip()
        scale_word = (m.group("scale") or "").strip()
        suffix = (m.group("suffix") or "").strip().lower()
        # Ignore digits that are part of an identifier such as "Q3", "FY23" or "10-K".
        start, end = m.start("num"), m.end("num")
        prev_ch = text[start - 1] if start > 0 else ""
        next_two = text[end : end + 2]
        if not cur and _is_ascii_letter(prev_ch):
            continue
        if (
            not scale_word
            and not suffix
            and (
                _is_ascii_letter(next_two[:1])
                or (next_two[:1] == "-" and _is_ascii_letter(next_two[1:]))
            )
        ):
            continue
        if not keep_years and _YEAR.match(num) and not (cur or scale_word or suffix):
            continue
        # Parts of a date ("December 31", "2025-09-27") are not financial figures.
        if not (cur or scale_word or suffix) and any(a <= start < b for a, b in dates):
            continue
        value = float(num.replace(",", ""))
        scale = (
            SCALE_WORDS.get(scale_word.lower(), SCALE_WORDS.get(scale_word, 1.0))
            if scale_word
            else 1.0
        )
        # A lone lower-case "m"/"t"/"b"/"k" without a currency is ambiguous ("5 m" metres);
        # upper-case "B"/"M" (e.g. "352.6B") is the common finance shorthand.
        dropped_scale = False
        if scale_word in {"m", "t", "b", "k"} and not cur and not suffix:
            scale = 1.0
            scale_word = ""
            dropped_scale = True
        value *= scale
        negative = bool(m.group("sign")) or bool(m.group("neg_paren") and m.group("close_paren"))
        if negative:
            value = -value
        if suffix in {"%", "percent", "per cent", "个百分点", "pp"} or suffix.startswith(
            ("percentage point", "per")
        ):
            unit: str | None = "percent"
        elif cur in {"$", "US$", "USD"} or suffix in {"美元", "dollar", "dollars", "usd"}:
            unit = "USD"
        elif cur in {"RMB", "CNY", "¥"} or suffix == "元":
            unit = "CNY"
        else:
            unit = None
        s, e = m.span()
        if m.group("neg_paren") and not m.group("close_paren"):
            s = m.start("neg_paren") + 1  # "(24.0% of revenue)" is not a negative
        if dropped_scale:
            e = m.end("close_paren") if m.group("close_paren") else m.end("num")
        # Trim leading/trailing whitespace from the surface span.
        surface = text[s:e]
        lstrip = len(surface) - len(surface.lstrip())
        rstrip = len(surface) - len(surface.rstrip())
        s, e = s + lstrip, e - rstrip
        window = text[max(0, s - 40) : s].lower()
        mentions.append(
            NumberMention(
                text=text[s:e],
                value=value,
                unit=unit,
                span=(s, e),
                decimals=_decimals(num),
                scale=scale,
                negative_context=any(w in window for w in NEGATIVE_WORDS),
            )
        )
    return mentions
