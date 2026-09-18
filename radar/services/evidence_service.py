"""Evidence extraction and exact-span verification helpers."""

import re
import unicodedata

from radar.schemas import EvidenceSpan


TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9-]*", re.IGNORECASE)
# Some source APIs collapse whitespace between abstract sentences ("...model.Existing").
# Split both conventional and collapsed boundaries while preserving exact quotes.
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s*(?=[A-Z])")
RESULT_CUES = re.compile(
    r"\b(result|results|find|finds|found|show|shows|demonstrate|demonstrates|"
    r"outperform|outperforms|lower|higher|improve|improves|reduce|reduces|"
    r"robust|robustness|fragile|sensitive|score|scores)\b",
    re.IGNORECASE,
)
# Markdown decoration dropped during quote normalization: LLM quotes often
# transcribe the plain words without the surrounding emphasis/heading marks.
_MARKDOWN_DECORATIONS = "*_`#~"
STOPWORDS = {
    "about", "after", "again", "against", "also", "among", "and", "are",
    "been", "being", "between", "compared", "consistently", "does", "each",
    "for", "from", "have", "into", "more", "most", "not", "our", "over",
    "paper", "reported", "research", "some", "still", "study", "than", "that",
    "the", "their", "these", "this", "under", "using", "were", "which", "with",
}


_LATEX_COMMAND = re.compile(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?")
# LaTeX inline commands whose content is kept (emphasis, text styles). The
# content inside the braces stays in the normalized text; only the markup is
# dropped, so a plain-words quote still matches \emph{...} content.
_LATEX_KEEP_COMMANDS = {
    "emph", "textbf", "textit", "textrm", "textsf", "textsc", "texttt",
    "underline", "footnote",
}
# Commands whose content is citation/ref metadata, not prose.
_LATEX_DROP_COMMANDS = {
    "cite", "citep", "citet", "ref", "label", "eqref", "url", "href",
}


def _latex_command_span(text: str, index: int) -> tuple[int, int, bool] | None:
    """If ``text[index:]`` starts a LaTeX command with braced content,
    return (inner_start, close_index, keep_inner) of the matching braces."""
    match = _LATEX_COMMAND.match(text, index)
    if not match:
        return None
    name = match.group(0)
    brace_index = match.end()
    if brace_index >= len(text) or text[brace_index] != "{":
        return None
    depth = 0
    for cursor in range(brace_index, len(text)):
        if text[cursor] == "{":
            depth += 1
        elif text[cursor] == "}":
            depth -= 1
            if depth == 0:
                keep = name[1:].split("[", 1)[0] in _LATEX_KEEP_COMMANDS
                return brace_index + 1, cursor, keep
    return None


def _normalize_characters_with_offsets(text: str) -> tuple[str, list[int]]:
    """NFKC-normalize per character and strip LaTeX inline markup.

    Offsets always point at real source characters: ligatures (``ﬁ`` →
    ``fi``) map both output chars to the source index, and LaTeX commands
    are removed while their inner prose keeps its own offsets.
    """
    output: list[str] = []
    offsets: list[int] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\":
            span = _latex_command_span(text, index)
            if span is not None:
                inner_start, close_index, keep_inner = span
                if keep_inner:
                    inner, inner_offsets = _normalize_characters_with_offsets(
                        text[inner_start:close_index]
                    )
                    output.extend(inner)
                    offsets.extend(inner_start + offset for offset in inner_offsets)
                index = close_index + 1
                continue
        normalized_char = unicodedata.normalize("NFKC", char)
        for normalized in normalized_char:
            output.append(normalized)
            offsets.append(index)
        index += 1
    return "".join(output), offsets


def _normalized_text_with_offsets(text: str) -> tuple[str, list[int]]:
    """Normalize PDF line wrapping while retaining exact source offsets.

    Markdown decoration characters (emphasis, headings, code ticks), LaTeX
    inline commands and NFKC-equivalent characters (ligatures ``ﬁ``/``ﬂ``,
    soft hyphens, fullwidth forms) are normalized so a quote transcribed
    without them still matches; offsets always point at real source
    characters.
    """
    base_text, base_offsets = _normalize_characters_with_offsets(text)
    normalized: list[str] = []
    offsets: list[int] = []
    index = 0
    while index < len(base_text):
        char = base_text[index]
        if char == "\x00" or char in _MARKDOWN_DECORATIONS:
            index += 1
            continue
        if char == "\u00ad":
            # Soft hyphen: mid-word PDF artifact — drop it unconditionally
            # so both sides merge.
            index += 1
            continue
        if char == "-" and index + 1 < len(base_text) and base_text[index + 1].isspace():
            # Hyphen joining a broken word across a line wrap ("signifi- cant",
            # "signifi-\ncant"): drop the joiner when both sides are letters.
            # Plain hyphens between letters ("state-of-the-art") stay.
            next_index = index + 1
            while next_index < len(base_text) and base_text[next_index].isspace():
                next_index += 1
            if (
                index > 0
                and base_text[index - 1].isalpha()
                and next_index < len(base_text)
                and base_text[next_index].isalpha()
            ):
                index = next_index
                continue
        if char.isspace():
            if normalized and normalized[-1] != " ":
                normalized.append(" ")
                offsets.append(base_offsets[index])
            index += 1
            while index < len(base_text) and base_text[index].isspace():
                index += 1
            continue
        normalized.append(char.lower())
        offsets.append(base_offsets[index])
        index += 1
    return "".join(normalized).strip(), offsets


def _strip_hyphens(
    text: str, offsets: list[int]
) -> tuple[str, list[int]]:
    """Drop hyphens from a normalized string, keeping the offset mapping."""
    stripped: list[str] = []
    stripped_offsets: list[int] = []
    for character, offset in zip(text, offsets):
        if character != "-":
            stripped.append(character)
            stripped_offsets.append(offset)
    return "".join(stripped), stripped_offsets


def resolve_exact_quote(quote: str, content: str) -> tuple[str, int, int] | None:
    """Return one exact source span; reject missing or ambiguous normalized matches."""
    exact_offset = content.find(quote)
    if exact_offset >= 0:
        if content.find(quote, exact_offset + 1) >= 0:
            return None
        return quote, exact_offset, exact_offset + len(quote)
    normalized_content, offsets = _normalized_text_with_offsets(content)
    normalized_quote, _ = _normalized_text_with_offsets(quote)
    if not normalized_quote or not offsets:
        return None
    match = normalized_content.find(normalized_quote)
    if match < 0 or normalized_content.find(normalized_quote, match + 1) >= 0:
        # M22 fallback: a line-break hyphen and a real hyphen disagree between
        # quote and content ("state-of-\\nthe-art" vs "state-of-the-art").
        # Compare both sides without hyphens; offsets still map to real chars.
        stripped_content, stripped_offsets = _strip_hyphens(normalized_content, offsets)
        stripped_quote = normalized_quote.replace("-", "")
        if not stripped_quote:
            return None
        match = stripped_content.find(stripped_quote)
        if match < 0 or stripped_content.find(stripped_quote, match + 1) >= 0:
            return None
        start = stripped_offsets[match]
        end = stripped_offsets[match + len(stripped_quote) - 1] + 1
        return content[start:end], start, end
    start = offsets[match]
    end = offsets[match + len(normalized_quote) - 1] + 1
    return content[start:end], start, end


def _canonical_token(token: str) -> str:
    """Collapse a few common English inflections without changing source text."""

    value = token.lower().strip("-")
    if len(value) > 6 and value.endswith("ness"):
        value = value[:-4]
    elif len(value) > 5 and value.endswith("ies"):
        value = value[:-3] + "y"
    elif len(value) > 5 and value.endswith("ing"):
        value = value[:-3]
    elif len(value) > 4 and value.endswith("ed"):
        value = value[:-2]
    elif len(value) > 4 and value.endswith("s"):
        value = value[:-1]
    return value


def _keywords(values: list[str]) -> set[str]:
    return {
        normalized
        for value in values
        for raw in TOKEN_RE.findall(value)
        if len(raw) > 2 and raw.lower() not in STOPWORDS
        if len(normalized := _canonical_token(raw)) > 2
    }


class EvidenceService:
    @staticmethod
    def verify_exact(span: EvidenceSpan | dict, content: str) -> bool:
        evidence = span if isinstance(span, EvidenceSpan) else EvidenceSpan.model_validate(span)
        return evidence.quote in content
    
    @staticmethod
    def resolve_exact_quote_present(quote: str, content: str) -> bool:
        """True when a quote resolves to exactly one span in the content."""
        if not quote.strip():
            return False
        return resolve_exact_quote(quote, content) is not None

    @staticmethod
    def resolve_exact(
        span: EvidenceSpan | dict, content: str
    ) -> EvidenceSpan | None:
        evidence = (
            span if isinstance(span, EvidenceSpan) else EvidenceSpan.model_validate(span)
        )
        resolved = resolve_exact_quote(evidence.quote, content)
        if resolved is None:
            return None
        quote, start, end = resolved
        return evidence.model_copy(
            update={"quote": quote, "locator": f"offset:{start}-{end}"}
        )

    @staticmethod
    def rank_relevant_evidence(
        query_terms: list[str],
        content: str,
        source_snapshot_id: str,
        *,
        limit: int = 5,
        embedding_client=None,
    ) -> list[EvidenceSpan]:
        """Return up to ``limit`` relevant spans, best first.

        Scoring is unchanged from the single-span path; only the number of
        returned spans differs. One paper usually reports its conditions
        across several paragraphs, so condition extraction needs more than
        the single highest-scoring span.
        """
        query_keywords = _keywords(query_terms)
        if not query_keywords:
            return []

        units: list[tuple[str, str]] = []
        sentence_number = 0
        for paragraph_number, raw_paragraph in enumerate(content.split("\n"), start=1):
            paragraph = raw_paragraph.strip()
            if not paragraph:
                continue
            sentences = [part.strip() for part in SENTENCE_BREAK.split(paragraph) if part.strip()]
            if len(sentences) == 1:
                units.append((f"paragraph:{paragraph_number}", paragraph))
                continue
            for sentence in sentences:
                sentence_number += 1
                units.append((f"sentence:{sentence_number}", sentence))

        ranked: list[tuple[float, int, str, str]] = []
        for locator, quote in units:
            evidence_keywords = _keywords([quote])
            overlap = len(query_keywords & evidence_keywords)
            if overlap == 0:
                continue
            empirical_bonus = 1.0 if RESULT_CUES.search(quote) else 0.0
            numeric_bonus = 0.5 if re.search(r"\d", quote) else 0.0
            score = overlap * 3.0 + empirical_bonus + numeric_bonus
            ranked.append((score, -len(quote), locator, quote))

        ranked.sort(reverse=True)
        # Keyword overlap misses paragraphs that report the same conditions in
        # different words. When an embedding client is available, re-score the
        # keyword head by semantic similarity to the claim; any failure keeps
        # the keyword order untouched.
        head = ranked[: max(limit * 4, 20)]
        if embedding_client is not None and len(head) > 1:
            try:
                import numpy as np

                query_text = " ".join(term for term in query_terms if term)
                documents = [quote for _, _, _, quote in head]
                vectors = embedding_client.embed([query_text, *documents])
                if vectors.shape[0] != len(documents) + 1:
                    raise RuntimeError("embedding_batch_shape_mismatch")
                similarities = vectors[1:] @ vectors[0]
                maximum = max(score for score, _, _, _ in head) or 1.0
                blended = [
                    (
                        0.5 * (item[0] / maximum) + 0.5 * float(similarity),
                        item[1],
                        item[2],
                        item[3],
                    )
                    for item, similarity in zip(head, similarities)
                ]
                blended.sort(reverse=True)
                head = blended
            except Exception:
                # Semantic re-ranking is an improvement, never a requirement.
                pass
        return [
            EvidenceSpan(
                quote=quote,
                locator=locator,
                source_snapshot_id=source_snapshot_id,
            )
            for _, _, locator, quote in head[:limit]
        ]

    @staticmethod
    def extract_relevant_evidence(
        query_terms: list[str], content: str, source_snapshot_id: str
    ) -> EvidenceSpan | None:
        """Best single span; kept for callers that need one citation anchor."""
        spans = EvidenceService.rank_relevant_evidence(
            query_terms, content, source_snapshot_id, limit=1
        )
        return spans[0] if spans else None
    