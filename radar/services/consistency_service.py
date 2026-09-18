"""Layer 1: deterministic internal-consistency checks over a single manuscript.

Scope: contradictions *inside* one paper (abstract says +5%, the results table
says +3%), as opposed to reference validity (``reference_validity_service``)
or manuscript-vs-literature impact (``weekly_radar_service``).

Prior art: statcheck does exactly this for psychology, recomputing p-values
from the reported test statistic and flagging mismatches; running it during
review measurably reduced reporting errors at Psychological Science. It is
built for APA statistical reporting and cannot read CS result metrics, so the
transferable part is the method, not the code: verify only what can be checked
deterministically, and separate "minor" from "changes the conclusion".

Design notes:

* Deterministic first. The published tool comparisons for this problem space
  fail on precision, not recall — the strongest citation checker flagged 48 of
  84 genuine references as suspicious. A numeric check needs no model, cannot
  hallucinate, and is fully reproducible, so it runs before any LLM layer and
  keeps the false-positive floor at zero.
* Findings are candidates. Every finding carries the verbatim quote and the
  character offsets it came from, so a reviewer can jump to the source text
  and confirm or dismiss it. Nothing here mutates the manuscript.
* Two numbers are only ever compared when they measure the same thing under
  the same conditions. Same metric is not enough: 41.8 BLEU on English-French
  and 28.4 BLEU on English-German are both correct. Every measurement
  therefore carries a *scope* (benchmark, language pair, model variant) and a
  *delta* flag ("2 BLEU better" is a difference, not a score).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from radar.services.manuscript_parser import build_document_map


# A measurement is "<number><unit>" optionally preceded/followed by a metric
# name. Percentages and bare decimals cover almost all reported results.
# The leading (?<![A-Za-z]) matters: without it the "1" in "89.5 F1" is read as
# a second measurement worth 1.0, and every paper reporting F1 appears to
# contradict itself.
_MEASUREMENT = re.compile(
    r"(?<![A-Za-z])(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>%|percent|percentage points?|pp\b)?",
    re.IGNORECASE,
)

# Metric vocabulary. Split by length because short acronyms are substrings of
# ordinary English: matching "em" loosely hits m-em-ory, "iou" hits prev-iou-s,
# "ppl" hits a-ppl-ications, "wer" hits po-wer. Long names may appear in any
# case; acronyms must stand alone as their own uppercase token.
_METRIC_WORDS = (
    "accuracy", "bleu", "rouge", "meteor", "exact match", "precision",
    "recall", "perplexity", "error rate", "f-score", "f1 score",
)
_METRIC_ACRONYMS = ("F1", "EM", "AUC", "mAP", "MRR", "nDCG", "PPL", "WER", "CER", "IoU")

_METRIC_WORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(term) for term in _METRIC_WORDS) + r")\b",
    re.IGNORECASE,
)
# Case-sensitive: "EM" is a metric, "em" inside a word is not.
_METRIC_ACRONYM_RE = re.compile(
    r"(?<![A-Za-z])(" + "|".join(_METRIC_ACRONYMS) + r")(?![A-Za-z])"
)

# Numbers that are never results. Years and small integers appear constantly
# as citations, section numbers and counts.
_YEAR = re.compile(r"^(19|20)\d{2}$")

# Bracketed citation markers: [13], [35, 2, 5]. Numbers inside are reference
# ids, not measurements.
_CITATION_SPAN = re.compile(r"\[[\d,\s–-]+\]")

# Sections whose numbers are bibliographic (arXiv ids, page ranges, years).
_EXCLUDED_HEADINGS = ("reference", "bibliography", "appendix", "acknowledg")

# Units that mean the number is a cost, a size or a count — never a score.
# Without this gate "41.8 after training for 3.5 days" hands 3.5 to whichever
# metric name is nearest, and the paper appears to report BLEU 3.5.
_NON_RESULT_UNIT = re.compile(
    r"^\s*(?:day|hour|minute|second|week|month|year|gpu|tpu|cpu|epoch|step|"
    r"layer|head|token|sentence|example|param|parameter|million|billion|"
    r"thousand|time|fold|x)s?\b",
    re.IGNORECASE,
)

# "improving ... by over 2 BLEU", "0.9 BLEU worse" — a difference between two
# systems, which must never be compared against an absolute score.
_DELTA_BEFORE = re.compile(
    r"(?:\bby\b|\bover\b|\bthan\b|[+\-±])\s*"
    r"(?:more than\s+|at least\s+|about\s+|roughly\s+|nearly\s+)?$",
    re.IGNORECASE,
)
_DELTA_AFTER = re.compile(
    r"^[^.]{0,20}?\b(worse|better|higher|lower|improvement|improvements|gain|"
    r"gains|drop|increase|decrease|degradation)\b",
    re.IGNORECASE,
)

# Scope vocabulary: what makes two numbers comparable. A benchmark, a language
# pair or a model variant all pin down "which experiment is this".
_LANGUAGE_CODES = {
    "german": "de", "french": "fr", "chinese": "zh", "czech": "cs",
    "russian": "ru", "spanish": "es", "japanese": "ja", "romanian": "ro",
}
_WMT_RE = re.compile(r"\bWMT\s*'?(\d{2,4})\b", re.IGNORECASE)
_PAIR_LONG_RE = re.compile(
    r"\bEnglish\s*[-–—to ]{1,4}\s*(" + "|".join(_LANGUAGE_CODES) + r")\b",
    re.IGNORECASE,
)
_PAIR_SHORT_RE = re.compile(r"\b(?:en)[-–](de|fr|zh|cs|ru|es|ja|ro)\b", re.IGNORECASE)
_DATASETS = (
    "imagenet", "squad", "glue", "superglue", "mnli", "conll", "coco",
    "cifar", "wikitext", "penn treebank", "librispeech", "mmlu", "gsm8k",
)
_DATASET_RE = re.compile(r"\b(" + "|".join(_DATASETS) + r")\b", re.IGNORECASE)
# A variant label only counts when it modifies a noun: "the base model", not
# "based on", not "a large amount".
_VARIANT_RE = re.compile(
    r"\b(base|big|large|small|tiny|baseline|ensemble)\s+"
    r"(?:model|models|configuration|config|variant|system|systems|setting)\b",
    re.IGNORECASE,
)

# How far around a number to look for a metric name. Tight on purpose: a wide
# window lets an unrelated metric elsewhere in the sentence justify a number.
_CONTEXT_CHARS = 40
# Scope is read from the sentence the number sits in. A character window
# instead of a sentence lets the benchmark named in the *previous* sentence
# claim this number, which is how "41.8 on English-French" ended up looking
# like a conflict with "28.4 on English-German".
_SENTENCE_BREAK = re.compile(r"(?<=[.!?;:])\s+|\n{2,}")
_SENTENCE_WINDOW = 400
# Two values of the same metric are "the same" within this relative tolerance,
# which absorbs rounding between an abstract ("about 28 BLEU") and a table
# ("28.4"). Tightening this raises recall but also false positives.
_REL_TOLERANCE = 0.02


@dataclass(frozen=True)
class Measurement:
    """One reported number, anchored to where it was written."""

    metric: str
    value: float
    unit: str
    section: str
    start_offset: int
    end_offset: int
    quote: str
    scope: frozenset[str] = frozenset()
    is_delta: bool = False
    source: str = "text"

    @property
    def scope_label(self) -> str:
        return "+".join(sorted(self.scope))


@dataclass
class ConsistencyFinding:
    """One candidate internal inconsistency, always evidence-anchored."""

    kind: str
    metric: str
    severity: str
    summary: str
    measurements: list[Measurement] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "metric": self.metric,
            "severity": self.severity,
            "summary": self.summary,
            "review_state": "candidate",
            "occurrences": [
                {
                    "value": m.value,
                    "unit": m.unit,
                    "section": m.section,
                    "start_offset": m.start_offset,
                    "end_offset": m.end_offset,
                    "quote": m.quote,
                    "scope": m.scope_label,
                    "source": m.source,
                }
                for m in self.measurements
            ],
        }


def _nearest_metric(context: str, number_at: int) -> str | None:
    """Return the metric name closest to the number, or None.

    Nearest rather than first: a results sentence often names several metrics,
    and the one adjacent to the number is the one it belongs to.
    """

    best: tuple[int, str] | None = None
    for pattern, normalize in (
        (_METRIC_WORD_RE, str.lower),
        (_METRIC_ACRONYM_RE, str.lower),
    ):
        for match in pattern.finditer(context):
            distance = min(
                abs(match.start() - number_at), abs(match.end() - number_at)
            )
            if best is None or distance < best[0]:
                best = (distance, normalize(match.group(1)))
    return best[1] if best else None


def extract_scope(context: str) -> frozenset[str]:
    """Which experiment a number belongs to, as normalised tokens.

    Two numbers with different non-empty scopes are different measurements and
    are never compared. An empty scope means "not stated here".
    """

    tokens: set[str] = set()
    for match in _WMT_RE.finditer(context):
        year = match.group(1)
        tokens.add("wmt" + (year if len(year) == 4 else "20" + year))
    for match in _PAIR_LONG_RE.finditer(context):
        tokens.add("en-" + _LANGUAGE_CODES[match.group(1).lower()])
    for match in _PAIR_SHORT_RE.finditer(context):
        tokens.add("en-" + match.group(1).lower())
    for match in _DATASET_RE.finditer(context):
        tokens.add(match.group(1).lower())
    for match in _VARIANT_RE.finditer(context):
        tokens.add(match.group(1).lower())
    return frozenset(tokens)


def sentence_at(content: str, position: int) -> str:
    """The sentence containing ``position``, capped to a sane window."""

    left = max(0, position - _SENTENCE_WINDOW)
    start = left
    for match in _SENTENCE_BREAK.finditer(content, left, position):
        start = match.end()
    right = min(len(content), position + _SENTENCE_WINDOW)
    end = right
    match = _SENTENCE_BREAK.search(content, position, right)
    if match is not None:
        end = match.start()
    return content[start:end]


def _is_delta(before: str, after: str) -> bool:
    """True when the number is a difference between systems, not a score."""

    return bool(_DELTA_BEFORE.search(before) or _DELTA_AFTER.match(after))


def _is_citation_number(content: str, position: int) -> bool:
    """True when the number sits inside a [12, 34] citation marker."""

    window_start = max(0, position - 30)
    for match in _CITATION_SPAN.finditer(content, window_start, position + 30):
        if match.start() <= position < match.end():
            return True
    return False


def _line_at(content: str, position: int) -> tuple[int, str]:
    line_start = content.rfind("\n", 0, position) + 1
    line_end = content.find("\n", position)
    if line_end == -1:
        line_end = len(content)
    return line_start, content[line_start:line_end]


def _is_table_row(content: str, position: int) -> bool:
    """True when the number sits on a markdown table row.

    Table cells are still read, but by ``extract_table_measurements``, which
    can attribute each cell to its column header and its row label. The prose
    pass must skip them or every cell inherits whichever metric name happens
    to be nearby.
    """

    return _line_at(content, position)[1].count("|") >= 2


def _is_heading_number(content: str, position: int) -> bool:
    """True for the leading number of a section heading ("5.4 Regularization")."""

    line_start = content.rfind("\n", 0, position) + 1
    prefix = content[line_start:position]
    return bool(re.fullmatch(r"[#*\s]*[\d.]*", prefix))


def _excluded_section(heading: str) -> bool:
    lowered = heading.lower()
    return any(marker in lowered for marker in _EXCLUDED_HEADINGS)


def _restore_split_decimals(content: str) -> str:
    """The PDF pipeline renders decimal points as "28 _._ 4"; restore them."""

    return re.sub(r"(\d)\s*_\._\s*(\d)", r"\1.\2", content)


def _section_lookup(content: str):
    document = build_document_map(content)
    spans = [(s.start_offset, s.end_offset, s.heading) for s in document.sections]

    def section_of(offset: int) -> str:
        for start, end, heading in spans:
            if start <= offset < end:
                return heading
        return "(untitled)"

    return section_of


def extract_measurements(content: str) -> list[Measurement]:
    """Pull every reported measurement out of the prose, with offsets."""

    content = _restore_split_decimals(content)
    section_of = _section_lookup(content)

    measurements: list[Measurement] = []
    for match in _MEASUREMENT.finditer(content):
        raw_value = match.group("value")
        if _YEAR.match(raw_value):
            continue
        position = match.start()
        if (
            _is_citation_number(content, position)
            or _is_table_row(content, position)
            or _is_heading_number(content, position)
        ):
            continue
        section = section_of(position)
        if _excluded_section(section):
            continue

        unit = (match.group("unit") or "").strip().lower()
        if unit in {"percent", "percentage point", "percentage points", "pp"}:
            unit = "%"

        tail = content[match.end() : match.end() + 40]
        if not unit and _NON_RESULT_UNIT.match(tail):
            continue

        start = max(0, position - _CONTEXT_CHARS)
        end = min(len(content), match.end() + _CONTEXT_CHARS)
        context = content[start:end]
        before = context[: position - start]
        # Figure/table/equation/section references are not results.
        if re.search(
            r"(section|figure|fig\.|table|equation|eq\.|chapter)\s*$", before.lower()
        ):
            continue
        metric = _nearest_metric(context, position - start)
        if metric is None:
            continue

        measurements.append(
            Measurement(
                metric=metric,
                value=float(raw_value),
                unit=unit,
                section=section,
                start_offset=position,
                end_offset=match.end(),
                quote=" ".join(context.split()),
                scope=extract_scope(sentence_at(content, position)),
                is_delta=_is_delta(before, tail),
                source="text",
            )
        )
    return measurements


def _split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell)


_CELL_VALUE = re.compile(r"^([<>~≈]?\s*)(\d+(?:\.\d+)?)\s*(%?)$")


def extract_table_measurements(content: str) -> list[Measurement]:
    """Read markdown result tables cell by cell.

    A flattened table row loses its column labels only if you read it as
    prose. Reading the header gives every cell its metric, and the first
    column gives it its row label — which is exactly the scope needed to know
    that the "big" row and the "base" row are not in conflict.
    """

    content = _restore_split_decimals(content)
    section_of = _section_lookup(content)

    measurements: list[Measurement] = []
    offset = 0
    lines = content.split("\n")
    blocks: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for line in lines:
        if line.count("|") >= 2:
            current.append((offset, line))
        else:
            if len(current) >= 2:
                blocks.append(current)
            current = []
        offset += len(line) + 1
    if len(current) >= 2:
        blocks.append(current)

    for block in blocks:
        header_cells = _split_row(block[0][1])
        column_metric: dict[int, str] = {}
        for index, cell in enumerate(header_cells):
            metric = _nearest_metric(cell, 0)
            if metric is not None:
                column_metric[index] = metric
        if not column_metric:
            continue
        caption_start = max(0, block[0][0] - 200)
        table_scope = extract_scope(content[caption_start : block[0][0]])
        if _excluded_section(section_of(block[0][0])):
            continue

        for line_offset, line in block[1:]:
            cells = _split_row(line)
            if _is_separator_row(cells):
                continue
            label = cells[0].lower().strip("*_ ") if cells else ""
            row_scope = table_scope | ({label} if label and not _CELL_VALUE.match(label) else set())
            for index, cell in enumerate(cells):
                metric = column_metric.get(index)
                if metric is None:
                    continue
                match = _CELL_VALUE.match(cell)
                if match is None:
                    continue
                cell_at = line.find(cell, 0)
                start = line_offset + (cell_at if cell_at != -1 else 0)
                measurements.append(
                    Measurement(
                        metric=metric,
                        value=float(match.group(2)),
                        unit="%" if match.group(3) else "",
                        section=section_of(line_offset),
                        start_offset=start,
                        end_offset=start + len(cell),
                        quote=" ".join(line.split()),
                        scope=frozenset(row_scope),
                        is_delta=False,
                        source="table",
                    )
                )
    return measurements


def _values_agree(first: float, second: float) -> bool:
    """Treat two values as consistent within a small relative tolerance."""

    if first == second:
        return True
    largest = max(abs(first), abs(second))
    if largest == 0:
        return True
    return abs(first - second) / largest <= _REL_TOLERANCE


def _comparable(first: Measurement, second: Measurement) -> bool:
    """Whether two measurements describe the same quantity.

    Both scopes known: they must match. Otherwise only prose is compared —
    an unlabelled sentence and a labelled table row are not evidence of a
    conflict, they are just two numbers whose relationship is unknown.

    Differences ("2 BLEU better") are stricter still: a difference is only
    meaningful relative to a baseline the text rarely restates, so two of them
    are compared only when both name the same experiment outright.
    """

    if first.is_delta or second.is_delta:
        return bool(first.scope) and first.scope == second.scope
    if first.scope and second.scope:
        return first.scope == second.scope
    return first.source == "text" and second.source == "text"


def _distinct_places(first: Measurement, second: Measurement) -> bool:
    """A conflict must span sections, or set prose against a table."""

    return first.section != second.section or first.source != second.source


def find_numeric_inconsistencies(content: str) -> list[ConsistencyFinding]:
    """Report metrics that carry conflicting values for the same experiment.

    Two values of one metric inside the same section are left alone: papers
    legitimately list per-dataset or per-baseline numbers side by side. The
    signal this check is after is the abstract/conclusion disagreeing with the
    results it summarises.
    """

    grouped: dict[tuple[str, str, bool], list[Measurement]] = defaultdict(list)
    for measurement in extract_measurements(content) + extract_table_measurements(
        content
    ):
        grouped[(measurement.metric, measurement.unit, measurement.is_delta)].append(
            measurement
        )

    findings: list[ConsistencyFinding] = []
    for (metric, unit, is_delta), measurements in sorted(
        grouped.items(), key=lambda item: item[0][:2]
    ):
        conflicting: list[Measurement] = []
        seen: set[int] = set()
        for index, first in enumerate(measurements):
            for second in measurements[index + 1 :]:
                if not _comparable(first, second):
                    continue
                if not _distinct_places(first, second):
                    continue
                if _values_agree(first.value, second.value):
                    continue
                for item in (first, second):
                    if id(item) not in seen:
                        seen.add(id(item))
                        conflicting.append(item)
        if not conflicting:
            continue

        values = sorted({m.value for m in conflicting})
        spread = (max(values) - min(values)) / max(max(values), 1e-9)
        scopes = {m.scope for m in conflicting}
        # "critical" is reserved for numbers that provably describe the same
        # experiment. When one side never named its benchmark the mismatch may
        # just be two different results, so it stays a review item.
        same_experiment = len(scopes) == 1 and all(scopes)
        severity = "critical" if same_experiment and spread > 0.2 else "review"
        conflicting.sort(key=lambda m: m.start_offset)
        rendered = ", ".join(
            f"{m.value:g}{unit} in {m.section}" for m in conflicting[:6]
        )
        scope_note = ""
        labels = {m.scope_label for m in conflicting if m.scope_label}
        if labels:
            scope_note = f"（同一实验条件 {'/'.join(sorted(labels))}）"
        kind_note = "相对提升值" if is_delta else "结果数值"
        findings.append(
            ConsistencyFinding(
                kind="numeric_conflict",
                metric=metric,
                severity=severity,
                summary=(
                    f"{metric} 的{kind_note}在不同章节不一致{scope_note}：{rendered}"
                ),
                measurements=conflicting,
            )
        )
    return findings


def check_manuscript(content: str, *, semantic: bool = False) -> dict[str, Any]:
    """Run the consistency checks over one manuscript.

    The deterministic numeric check always runs: it is free, offline and safe
    to call on every page load. The semantic check (layer 2) loads a model and
    calls the LLM, so it is opt-in — the caller decides when that cost is
    worth paying, and a layer-2 failure never takes the numeric results with
    it.
    """

    findings = find_numeric_inconsistencies(content)
    result: dict[str, Any] = {
        "checks_run": ["numeric_conflict"],
        "finding_count": len(findings),
        "findings": [finding.to_dict() for finding in findings],
    }
    if not semantic:
        return result

    from radar.services.semantic_consistency_service import (
        find_semantic_inconsistencies,
    )

    try:
        semantic_findings, stats = find_semantic_inconsistencies(content)
    except Exception as exc:  # pragma: no cover - defensive
        result["semantic"] = {"status": "error", "detail": str(exc)}
        return result

    result["checks_run"].append("semantic_conflict")
    result["findings"].extend(f.to_dict() for f in semantic_findings)
    result["finding_count"] = len(result["findings"])
    result["semantic"] = stats
    return result
