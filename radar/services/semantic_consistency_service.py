"""Layer 2: semantic self-contradictions inside one manuscript.

Layer 1 (``consistency_service``) compares numbers. It cannot see the other
half of the problem: a paper that claims "requires no additional annotation"
in the introduction and "we annotated 5,000 examples" in the method section
contradicts itself without a single conflicting digit. Only a model that reads
meaning can catch that, so this layer calls one.

Why it is not simply "give the whole paper to an LLM and ask for
contradictions":

* Asked that question, a model almost never answers "none" — it finds
  something, because finding something is what it was asked to do. The
  published tool comparisons for this problem space fail on precision, not
  recall (the strongest citation checker flagged 48 of 84 genuine references).
  Precision has to be engineered in, not hoped for.
* A model given ten thousand words also cannot reliably say *where* a sentence
  was. The product's rule is that no assertion ships without a verbatim quote
  anchored to an offset, and a whole-document prompt cannot honour it.

So the pipeline narrows before it judges, and verifies after:

1. Pair only sections that can meaningfully disagree (abstract vs results,
   introduction vs method, ...). A sentence cannot contradict its own
   neighbours in the way we care about, and most cross-section pairs are
   unrelated.
2. Keep only sentences that assert something. Citations, table rows, headings
   and fragments are dropped before any model sees them.
3. Score every surviving pair with the local cross-encoder NLI model already
   in the codebase (``radar/nli.py``). This is free, offline, and cuts
   thousands of pairs to a few dozen. It over-flags on purpose — it is a
   filter, not a judge.
4. Ask the LLM to judge only those few dozen, one pair at a time, with a
   prompt that defines what does *not* count and defaults to "no".
5. Discard any verdict whose quotes are not verbatim in the source. A model
   that cannot point at the text does not get to make the claim.

Everything here produces *candidates*. Nothing mutates the manuscript, and the
output shape matches layer 1 exactly so the UI treats both the same.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from radar.services.consistency_service import _restore_split_decimals
from radar.services.manuscript_parser import build_document_map

logger = logging.getLogger(__name__)

PROMPT_PATH = (
    Path(__file__).parents[1] / "llm" / "prompts" / "semantic_consistency_judge.txt"
)

# Section roles. Headings vary ("Experiments", "Results and Discussion", "5.2
# Evaluation"), so match on keywords rather than exact titles.
_ROLE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("abstract", ("abstract",)),
    ("conclusion", ("conclusion", "concluding", "summary and outlook")),
    ("limitation", ("limitation", "threats to validity", "future work")),
    ("results", ("result", "experiment", "evaluation", "finding", "ablation")),
    ("method", ("method", "approach", "model", "architecture", "implementation",
                "system", "design", "algorithm")),
    ("intro", ("introduction", "motivation", "overview", "background")),
)

# Sections whose text is never a claim about this paper's own contribution.
_EXCLUDED_KEYWORDS = (
    "reference", "bibliography", "acknowledg", "appendix", "related work",
    "prior work", "author contribution", "funding", "ethic", "checklist",
)

# Which roles can meaningfully contradict each other. Deliberately not the
# full cross product: introduction-vs-introduction is noise, and results-vs-
# limitation is where authors are *supposed* to qualify themselves.
_COMPARABLE_ROLES: frozenset[frozenset[str]] = frozenset(
    frozenset(pair)
    for pair in (
        ("abstract", "results"),
        ("abstract", "method"),
        ("abstract", "conclusion"),
        ("intro", "method"),
        ("intro", "results"),
        ("method", "results"),
        ("method", "conclusion"),
        ("results", "conclusion"),
    )
)

# Sentences end at punctuation, at a blank line, or at a heading. Without the
# heading case a LaTeX \section{...} followed by a single newline glues the
# title onto the first sentence of the section, and that sentence is then
# thrown away as a heading.
_SENTENCE_END = re.compile(
    r"(?<=[.!?])\s+(?=[A-Z(\\])"
    r"|\n{2,}"
    r"|(?<=\})\s*\n"
    r"|\n(?=\s*(?:#{1,6}\s|\\(?:sub)*section\b))"
)

# A sentence shorter than this is a fragment, a heading or a caption; longer
# than this is a parsing failure (a whole paragraph glued together).
_MIN_SENTENCE_CHARS = 40
_MAX_SENTENCE_CHARS = 600

_CITATION_MARKER = re.compile(r"\[\s*\d+(?:\s*[,;–-]\s*\d+)*\s*\]")
_TABLE_ROW = re.compile(r"^\s*\|")
_HEADING_LINE = re.compile(r"^\s*(?:#{1,6}\s|\\(?:sub)*section\b|\d+(?:\.\d+)*\.?\s+\S)")
# Sentences that are mostly symbols, digits or LaTeX are equations or data.
_WORDLIKE = re.compile(r"[A-Za-z]{3,}")

# A sentence about someone else's system cannot contradict a claim about ours:
# "Smith et al. require no annotation" next to "we annotated 5,000 examples"
# is the single most tempting false positive in this whole layer.
#
# The filter is attribution-based rather than self-reference-based. Requiring
# "we"/"our" looks safer but silently discards most of a real paper — plenty of
# authors write about their own system in the third person ("RadarNet improves
# exact match from 61.2% to 68.7%"), and on a real manuscript that rule left
# zero eligible sentences. So: drop what is explicitly credited to someone
# else, keep the rest, and let the prompt handle the residual ambiguity.
_REPORTING_VERB = (
    r"report|reports|reported|show|shows|showed|find|finds|found|claim|claims|"
    r"claimed|argue|argues|argued|observe|observes|observed|note|notes|noted|"
    r"describe|describes|described|propose|proposes|proposed"
)
_THIRD_PARTY_OPENER = re.compile(
    r"^\s*(?:"
    r"[A-Z][A-Za-z.'-]+\s+et\s+al\.?"
    r"|[A-Z][A-Za-z.'-]+\s+and\s+[A-Z][A-Za-z.'-]+\s*\("
    r"|(?:a|an|the)\s+(?:concurrent|recent|prior|previous|independent|"
    r"contemporaneous|follow-?up)\b"
    r"|previous work|prior work|related work|earlier (?:work|studies)"
    r"|existing (?:methods?|systems?|approaches|work)"
    r"|(?:several|many|other|some)\s+(?:authors|works|studies|papers)"
    r"|according to\b"
    rf"|{_CITATION_MARKER.pattern}\s*(?:{_REPORTING_VERB})"
    r")",
    re.IGNORECASE,
)
# Attribution can also arrive mid-sentence: "... , as Smith et al. report".
_THIRD_PARTY_INLINE = re.compile(
    rf"\b(?:as|per)\s+(?:[A-Z][A-Za-z.'-]+\s+et\s+al\.?|{_CITATION_MARKER.pattern})"
    rf"|\bet\s+al\.\s+(?:{_REPORTING_VERB})\b",
)

# Cost and noise ceilings. The NLI pass is free but not instant; the LLM pass
# is neither. Both are capped so a pathological document cannot run away, and
# the caller is told when a cap bit.
_MAX_PAIRS_SCORED = 6000
_MAX_PAIRS_JUDGED = 40
_DEFAULT_NLI_THRESHOLD = 0.55
_MIN_LLM_CONFIDENCE = 0.6

_VALID_KINDS = frozenset(
    {"scope_overreach", "requirement_conflict", "conclusion_conflict",
     "capability_conflict"}
)


@dataclass(frozen=True)
class Sentence:
    """One assertive sentence, anchored to where it was written."""

    text: str
    section: str
    role: str
    start_offset: int
    end_offset: int


@dataclass
class SemanticFinding:
    """One candidate semantic contradiction, always evidence-anchored."""

    kind: str
    severity: str
    summary: str
    confidence: float
    sentences: list[Sentence] = field(default_factory=list)
    quotes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Match layer 1's shape so the UI renders both without branching."""

        return {
            "kind": self.kind,
            "metric": "",
            "severity": self.severity,
            "summary": self.summary,
            "review_state": "candidate",
            "confidence": round(self.confidence, 3),
            "occurrences": [
                {
                    "value": None,
                    "unit": "",
                    "section": sentence.section,
                    "start_offset": start,
                    "end_offset": start + len(quote),
                    "quote": quote,
                    "scope": sentence.role,
                    "source": "semantic",
                }
                for sentence, quote, start in (
                    (s, q, s.start_offset + s.text.find(q))
                    for s, q in zip(self.sentences, self.quotes)
                )
            ],
        }


def section_role(heading: str) -> str | None:
    """Classify a heading into a comparable role, or None to skip it."""

    text = (heading or "").strip().lower()
    if not text:
        return None
    if any(word in text for word in _EXCLUDED_KEYWORDS):
        return None
    for role, keywords in _ROLE_KEYWORDS:
        if any(word in text for word in keywords):
            return role
    return None


def is_assertive(sentence: str) -> bool:
    """True when the sentence states something this paper could contradict."""

    text = sentence.strip()
    if not (_MIN_SENTENCE_CHARS <= len(text) <= _MAX_SENTENCE_CHARS):
        return False
    if _TABLE_ROW.match(text) or _HEADING_LINE.match(text):
        return False
    # Needs real prose, not an equation or a row of numbers.
    if len(_WORDLIKE.findall(text)) < 5:
        return False
    # A sentence that is mostly a citation list asserts nothing of its own.
    without_citations = _CITATION_MARKER.sub("", text)
    if len(without_citations) < len(text) * 0.6:
        return False
    # Must not be credited to someone else — "Smith et al. show X" is not this
    # paper claiming X.
    if _THIRD_PARTY_OPENER.match(text) or _THIRD_PARTY_INLINE.search(text):
        return False
    return True


def split_sentences(text: str, base_offset: int) -> list[tuple[str, int, int]]:
    """Split into sentences, keeping each one's offsets in the whole document."""

    spans: list[tuple[str, int, int]] = []
    cursor = 0
    for match in _SENTENCE_END.finditer(text):
        chunk = text[cursor:match.start()]
        stripped = chunk.strip()
        if stripped:
            lead = len(chunk) - len(chunk.lstrip())
            start = base_offset + cursor + lead
            spans.append((stripped, start, start + len(stripped)))
        cursor = match.end()
    tail = text[cursor:]
    stripped = tail.strip()
    if stripped:
        lead = len(tail) - len(tail.lstrip())
        start = base_offset + cursor + lead
        spans.append((stripped, start, start + len(stripped)))
    return spans


def collect_sentences(content: str) -> list[Sentence]:
    """Every assertive sentence in a comparable section, with offsets."""

    document = build_document_map(content)
    sentences: list[Sentence] = []
    for section in document.sections:
        role = section_role(section.heading)
        if role is None:
            continue
        body = content[section.start_offset:section.end_offset]
        for text, start, end in split_sentences(body, section.start_offset):
            if is_assertive(text):
                sentences.append(
                    Sentence(
                        text=text,
                        section=section.heading,
                        role=role,
                        start_offset=start,
                        end_offset=end,
                    )
                )
    return sentences


def generate_candidate_pairs(
    sentences: list[Sentence],
) -> list[tuple[Sentence, Sentence]]:
    """Cross-section sentence pairs whose roles can meaningfully disagree."""

    pairs: list[tuple[Sentence, Sentence]] = []
    for i, first in enumerate(sentences):
        for second in sentences[i + 1:]:
            if first.section == second.section:
                continue
            if frozenset((first.role, second.role)) not in _COMPARABLE_ROLES:
                continue
            pairs.append((first, second))
            if len(pairs) >= _MAX_PAIRS_SCORED:
                return pairs
    return pairs


def _contradiction_score(checker: Any, first: Sentence, second: Sentence) -> float:
    """NLI contradiction probability of the later sentence against the earlier.

    premise = the earlier sentence, hypothesis = the later one, matching the
    checker's (evidence, claim) argument order.
    """

    verdict = checker.check(claim=second.text, evidence=first.text)
    if not verdict:
        return 0.0
    return float(verdict.get("scores", {}).get("contradiction", 0.0))


def _verbatim(quote: str, sentence: Sentence) -> bool:
    """A quote counts only if it appears character-for-character in the source."""

    cleaned = (quote or "").strip()
    return bool(cleaned) and cleaned in sentence.text


def _judge_pair(
    llm_client: Any, instructions: str, first: Sentence, second: Sentence
) -> dict[str, Any] | None:
    """Ask the LLM about one pair; None on any failure, which drops the pair."""

    payload = {
        "SENTENCE_A": {"section": first.section, "text": first.text},
        "SENTENCE_B": {"section": second.section, "text": second.text},
    }
    try:
        from radar.schemas import SemanticContradictionOutput

        output = llm_client.generate_structured(
            stage="semantic_consistency_judge",
            prompt=(
                f"{instructions}\n\nINPUT JSON:\n"
                f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
            ),
            response_model=SemanticContradictionOutput,
        )
    except Exception as exc:
        logger.warning("semantic consistency judge failed: %s", exc)
        return None
    if hasattr(output, "model_dump"):
        return output.model_dump()
    if isinstance(output, dict):
        return output
    return None


def _severity(kind: str, confidence: float) -> str:
    """Only a high-confidence hard conflict is worth interrupting the writer."""

    if kind in {"requirement_conflict", "capability_conflict"} and confidence >= 0.8:
        return "critical"
    return "review"


def find_semantic_inconsistencies(
    content: str,
    *,
    llm_client: Any | None = None,
    nli_checker: Any | None = None,
    nli_threshold: float = _DEFAULT_NLI_THRESHOLD,
    max_pairs_judged: int = _MAX_PAIRS_JUDGED,
) -> tuple[list[SemanticFinding], dict[str, Any]]:
    """Run the layer-2 pipeline, returning findings and what it cost to get them.

    The stats are part of the contract, not debug output: a reviewer deciding
    whether to trust a finding needs to know how many pairs were considered and
    how many verdicts were thrown away for failing the quote check.
    """

    stats: dict[str, Any] = {
        "sentences": 0,
        "pairs_generated": 0,
        "pairs_scored": 0,
        "pairs_judged": 0,
        "rejected_unquoted": 0,
        "rejected_low_confidence": 0,
        "truncated": False,
        "nli_available": False,
        "status": "ok",
    }

    content = _restore_split_decimals(content or "")
    if not content.strip():
        stats["status"] = "empty_manuscript"
        return [], stats

    sentences = collect_sentences(content)
    stats["sentences"] = len(sentences)
    pairs = generate_candidate_pairs(sentences)
    stats["pairs_generated"] = len(pairs)
    if not pairs:
        stats["status"] = "no_candidate_pairs"
        return [], stats

    if nli_checker is None:
        from radar.nli import NLIContradictionChecker

        nli_checker = NLIContradictionChecker()

    scored: list[tuple[float, Sentence, Sentence]] = []
    for first, second in pairs:
        score = _contradiction_score(nli_checker, first, second)
        stats["pairs_scored"] += 1
        if score >= nli_threshold:
            scored.append((score, first, second))
    stats["nli_available"] = any(score > 0.0 for score, _, _ in scored) or bool(scored)

    if not scored:
        # Either nothing looks contradictory, or the NLI model never loaded.
        # These are different situations and the caller must be able to tell
        # them apart: without the filter, sending everything to the LLM would
        # be both expensive and exactly the noisy behaviour we are avoiding.
        probe = nli_checker.check(claim="The sky is green.", evidence="The sky is blue.")
        if probe is None:
            stats["status"] = "nli_unavailable"
            stats["nli_available"] = False
        return [], stats

    scored.sort(key=lambda row: row[0], reverse=True)
    if len(scored) > max_pairs_judged:
        stats["truncated"] = True
        scored = scored[:max_pairs_judged]

    if llm_client is None:
        from radar.llm.factory import build_analysis_llm

        llm_client = build_analysis_llm()
    instructions = PROMPT_PATH.read_text(encoding="utf-8")

    findings: list[SemanticFinding] = []
    seen: set[tuple[int, int]] = set()
    for score, first, second in scored:
        stats["pairs_judged"] += 1
        verdict = _judge_pair(llm_client, instructions, first, second)
        if not verdict or not verdict.get("is_contradiction"):
            continue
        kind = str(verdict.get("kind") or "")
        if kind not in _VALID_KINDS:
            continue
        confidence = float(verdict.get("confidence") or 0.0)
        if confidence < _MIN_LLM_CONFIDENCE:
            stats["rejected_low_confidence"] += 1
            continue
        quote_a = str(verdict.get("quote_a") or "").strip()
        quote_b = str(verdict.get("quote_b") or "").strip()
        if not (_verbatim(quote_a, first) and _verbatim(quote_b, second)):
            # The model asserted something it could not point at. This is the
            # hallucination case the whole layer is built to survive.
            stats["rejected_unquoted"] += 1
            continue
        key = (first.start_offset, second.start_offset)
        if key in seen:
            continue
        seen.add(key)
        findings.append(
            SemanticFinding(
                kind=kind,
                severity=_severity(kind, confidence),
                summary=str(verdict.get("reason") or "").strip()
                or f"{first.section} 与 {second.section} 的表述可能自相矛盾",
                confidence=confidence,
                sentences=[first, second],
                quotes=[quote_a, quote_b],
            )
        )

    findings.sort(key=lambda f: (f.severity != "critical", -f.confidence))
    return findings, stats
