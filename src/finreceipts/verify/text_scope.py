"""Bind a deliberately bounded grammar of asserted annual financial clauses.

Every word in a numeric clause must fit the grammar. Unknown issuer, period,
dimension, basis, modality or tail wording abstains rather than being ignored.
This is a conservative parser, not unrestricted natural-language verification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from finreceipts.verify.numbers import NumberMention
from finreceipts.xbrl.metrics import CONCEPTS, FactLookup, MetricKind, MetricSpec

_ALIASES = {name.casefold(): name for name in CONCEPTS}
_ALIASES.update(
    {
        "total net sales": "revenue",
        "net sales": "revenue",
        "total revenue": "revenue",
        "net revenue": "revenue",
        "net revenues": "revenue",
        "revenues": "revenue",
        "diluted earnings per share": "diluted EPS",
    }
)
_METRIC = re.compile(
    r"(?<!\w)(?:"
    + "|".join(re.escape(x) for x in sorted(_ALIASES, key=len, reverse=True))
    + r")(?!\w)",
    re.I,
)
_YEAR_TEXT = r"(?:19|20)\d{2}"
_YEAR = re.compile(r"(?<!\d)" + _YEAR_TEXT + r"(?!\d)")
_PERIOD = (
    r"(?:in|during|for)\s+(?:fiscal\s+year\s+)?(?:FY\s*)?"
    + _YEAR_TEXT
    + r"(?:\s+(?:compared\s+to|versus|vs\.?)\s+(?:FY\s*)?"
    + _YEAR_TEXT
    + r")?"
)
_LEADING_PERIOD = re.compile(
    r"^(?:" + _PERIOD + r"|(?:FY\s*|fiscal\s+year\s+)" + _YEAR_TEXT + r")\s*[, :]\s*",
    re.I,
)
_TRAILING_PERIOD = re.compile(
    r"(?<=\s)(?:FY\s*|fiscal\s+year\s+)(?P<year>" + _YEAR_TEXT + r")\Z",
    re.I,
)
_COMPARISON = re.compile(
    r"(?P<cur>"
    + _YEAR_TEXT
    + r")\s+(?:compared\s+to|versus|vs\.?)\s+(?:FY\s*)?(?P<prev>"
    + _YEAR_TEXT
    + r")",
    re.I,
)
_DIRECTION = r"(?:increase[ds]?|decrease[ds]?|decline[ds]?|grew|fell|rose|up|down)"
_DECLINE = re.compile(r"^(?:decrease[ds]?|decline[ds]?|fell|down)$", re.I)
_YOY = r"(?:from\s+a\s+year\s+ago|year[ -]over[ -]year|year[ -]on[ -]year|yoy)"
# Numbers are replaced by span-index tokens before matching. The grammar owns
# all text on both sides of the amount, including coordinated metric groups.
_BODY = re.compile(
    r"\s*(?P<before>" + _PERIOD + r")?\s*"
    r"(?:(?P<change>" + _DIRECTION + r")|"
    r"(?:was|were|is|are|totaled|totalled|amounted\s+to|reached)"
    r"(?:\s+(?P<copula_change>up|down))?)?\s*"
    r"N(?P<primary>\d+)"
    r"(?:\s+or\s+N(?P<difference>\d+))?"
    r"(?:\s*,\s*(?P<secondary_change>" + _DIRECTION + r")\s+N(?P<secondary>\d+))?"
    r"\s*(?P<yoy_before>" + _YOY + r")?"
    r"\s*(?P<after>" + _PERIOD + r")?"
    r"\s*(?P<yoy_after>" + _YOY + r")?\s*",
    re.I,
)
_POINTS = re.compile(r"\b(?:percentage\s+points?|pp)\b|个百分点", re.I)


@dataclass(frozen=True)
class TextScope:
    metric: MetricSpec
    fiscal_year: int
    entity: str
    negative_context: bool = False


def _entity_form(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold().replace("’", "'").strip(" ."))


def _cuts(text: str) -> list[int]:
    cuts = [0]
    for separator in re.finditer(r"[;\n]|(?<=[.!?])\s+", text):
        # Longest abbreviation is "Corp." (five characters). Keep one prior
        # character for the word boundary instead of rescanning the whole text
        # prefix at every separator, for every numeric mention.
        tail = text[max(0, separator.start() - 6) : separator.start()]
        if re.search(r"\b(?:Inc|Corp|Ltd|U\.S|vs)\.$", tail, re.I):
            continue
        cuts.append(separator.end())
    return [*cuts, len(text)]


def _prefix(prefix: str, entity: str) -> tuple[bool, bool, list[int]]:
    """Validate the complete subject, without dropping conjunctions or words."""
    prefix = prefix.strip(" ,:")
    period = _LEADING_PERIOD.match(prefix + " ")
    years = [int(m.group()) for m in _YEAR.finditer(period.group())] if period else []
    if period:
        prefix = (prefix + " ")[period.end() :].strip(" ,:")
    else:
        # Accept issuer-before-period only when the entire prefix matches.
        # The remaining subject still has to be an evidence-derived alias;
        # do not search-and-delete fiscal years from arbitrary modifiers.
        # Search only for an anchored fiscal suffix: a wildcard subject followed
        # by whitespace would backtrack quadratically over long whitespace runs.
        subject_period = _TRAILING_PERIOD.search(prefix)
        if subject_period:
            prefix = prefix[: subject_period.start()].rstrip()
            years = [int(subject_period["year"])]
    entity_short = re.sub(
        r"\s+(?:inc\.?|corp\.?|corporation|ltd\.?|limited)$", "", entity, flags=re.I
    )
    explicit = set()
    for alias in (entity, entity_short):
        explicit.update(
            (_entity_form(alias), _entity_form(alias) + "'s", _entity_form(alias + "'s"))
        )
    generic = {"", "the company", "the company's", "our", "its", "the issuer", "the issuer's"}
    form = _entity_form(prefix)
    return form in explicit | generic, form in explicit, years


def _parse_groups(
    text: str,
    start: int,
    end: int,
    metrics: list[re.Match[str]],
    mentions: list[NumberMention],
    prefix_years: list[int],
) -> tuple[list[tuple[str, re.Match[str]]], list[int]] | str:
    groups: list[tuple[str, re.Match[str]]] = []
    years = list(prefix_years)
    local_mentions = [(i, n) for i, n in enumerate(mentions) if start <= n.span[0] < end]
    for index, metric in enumerate(metrics):
        region_start = start + metric.end()
        region_end = start + metrics[index + 1].start() if index + 1 < len(metrics) else end
        cursor = region_start
        parts = []
        indices = []
        for i, number in local_mentions:
            if region_start <= number.span[0] < region_end:
                parts.extend((text[cursor : number.span[0]], f"N{i}"))
                indices.append(i)
                cursor = number.span[1]
        parts.append(text[cursor:region_end])
        body = "".join(parts).strip()
        if index + 1 < len(metrics):
            if not re.search(r"\s+and$", body, re.I):
                return "text scope unresolved: unparsed metric coordination"
            body = re.sub(r"\s+and$", "", body, flags=re.I)
        parsed = _BODY.fullmatch(body)
        if not parsed:
            return "text scope unresolved: unparsed period, issuer, basis, modality, or tail"
        referenced = [int(parsed[k]) for k in ("primary", "difference", "secondary") if parsed[k]]
        if referenced != indices:
            return "text scope unresolved: number tokens do not match original spans"
        periods = [p for p in (parsed["before"], parsed["after"]) if p]
        if len(periods) > 1 or (prefix_years and periods):
            return "text scope unresolved: multiple period attachments"
        years.extend(int(y.group()) for p in periods for y in _YEAR.finditer(p))
        groups.append((_ALIASES[metric.group().casefold()], parsed))
    return groups, years


def scope_for_mention(
    text: str, mention: NumberMention, mentions: list[NumberMention], lookup: FactLookup
) -> TextScope | str:
    """Resolve one mention only after its whole financial clause parses."""
    if mention.unit_conflict:
        return "text scope unresolved: currency and percentage markers conflict on one quantity"
    cuts = _cuts(text)
    start = max(x for x in cuts if x <= mention.span[0])
    end = min(x for x in cuts if x >= mention.span[1])
    # Keep offsets relative to the original text when removing trailing marks.
    end = start + len(text[start:end].rstrip().rstrip(".;!?"))
    clause = text[start:end]
    metrics = list(_METRIC.finditer(clause))
    if not any(m.end() <= mention.span[0] - start for m in metrics):
        return "text scope unresolved: no recognized metric before number"
    if not _YEAR.search(clause):
        return "text scope unresolved: no explicit fiscal year"
    entities = {(f.cik, f.entity_name) for series in lookup.values() for f in series.values()}
    if len(entities) != 1:
        return "text scope unresolved: evidence must identify exactly one entity"
    _, entity = next(iter(entities))
    valid, explicit, prefix_years = _prefix(clause[: metrics[0].start()], entity)
    if not valid:
        return "text scope unresolved: entity or dimensional modifier before metric"
    # Follow generic antecedents through complete parsed clauses, including
    # postfix metadata. An explicit compatible issuer resets that chain.
    if start and not explicit:
        for i in reversed(range(cuts.index(start))):
            prior_start = cuts[i]
            prior_end = prior_start + len(text[prior_start : cuts[i + 1]].rstrip().rstrip(".;!?"))
            previous = text[prior_start:prior_end]
            if not previous.strip():
                continue
            prior_metrics = list(_METRIC.finditer(previous))
            if not prior_metrics:
                return "text scope unresolved: unresolved antecedent in preceding clause"
            valid, prior_explicit, prior_years = _prefix(
                previous[: prior_metrics[0].start()], entity
            )
            prior_groups = _parse_groups(
                text, prior_start, prior_end, prior_metrics, mentions, prior_years
            )
            if not valid or isinstance(prior_groups, str):
                return "text scope unresolved: unresolved antecedent in preceding clause"
            if prior_explicit:
                break
    if mention.unit is None:
        return "text scope unresolved: numeric unit is not explicit"
    if _POINTS.search(mention.text):
        return "text scope unresolved: percentage-point differences are not percent growth"

    parsed_groups = _parse_groups(text, start, end, metrics, mentions, prefix_years)
    if isinstance(parsed_groups, str):
        return parsed_groups
    groups, years = parsed_groups
    unique_years = set(years)
    if len(unique_years) == 1:
        year = years[0]
    elif len(unique_years) == 2 and len(groups) == 1:
        period = groups[0][1]["before"] or groups[0][1]["after"] or ""
        comparison = _COMPARISON.search(period)
        if not comparison or int(comparison["prev"]) != int(comparison["cur"]) - 1:
            return "text scope unresolved: ambiguous or nonannual period comparison"
        year = int(comparison["cur"])
    else:
        return "text scope unresolved: no unique fiscal-year binding"

    target = next(i for i, number in enumerate(mentions) if number is mention)
    for name, parsed in groups:
        primary_direction = parsed["change"] or parsed["copula_change"] or ""
        primary = int(parsed["primary"])
        difference = int(parsed["difference"]) if parsed["difference"] else None
        secondary = int(parsed["secondary"]) if parsed["secondary"] else None
        if difference is not None and not primary_direction:
            return "text scope unresolved: multiple amounts for one metric"
        if secondary is not None and (primary_direction or mentions[primary].unit == "percent"):
            return "text scope unresolved: ambiguous multiple growth operations"
        if target not in (primary, difference, secondary):
            continue
        direction = parsed["secondary_change"] if target == secondary else primary_direction
        if target == difference or (direction and mention.unit != "percent"):
            return (
                "text scope unresolved: absolute changes require an unsupported difference metric"
            )
        if mention.unit == "percent":
            if not direction:
                return "text scope unresolved: percentage has no supported growth operation"
            if len(unique_years) != 2 and not (parsed["yoy_before"] or parsed["yoy_after"]):
                return "text scope unresolved: growth comparison is not explicitly year-over-year"
            declining = bool(_DECLINE.fullmatch(direction))
            if mention.value < 0 and not declining:
                return "text scope unresolved: conflicting growth direction and signed value"
            return TextScope(
                MetricSpec(MetricKind.YOY_GROWTH, CONCEPTS[name]), year, entity, declining
            )
        if len(unique_years) != 1:
            return "text scope unresolved: direct amount has an ambiguous comparison period"
        return TextScope(MetricSpec(MetricKind.DIRECT, CONCEPTS[name]), year, entity)
    return "text scope unresolved: number is outside a parsed metric operation"
