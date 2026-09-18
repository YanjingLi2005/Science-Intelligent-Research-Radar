"""Deterministic parsers for the small reference formats used by the MVP."""

from __future__ import annotations

import re

from radar.schemas import ReferenceEntry


_DOI_RE = re.compile(
    r"(?i)(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?"
    r"(10\.\d{4,9}/[-._;()/:A-Z0-9]+)"
)
_ARXIV_RE = re.compile(
    r"(?i)(?:https?://arxiv\.org/(?:abs|pdf)/|arxiv:\s*)?"
    r"((?:\d{4}\.\d{4,5}(?:v\d+)?)|(?:[a-z][a-z-]+(?:\.[A-Z]{2})?/\d{7}))"
)
_YEAR_RE = re.compile(r"\b(18|19|20|21)\d{2}\b")
_BIB_FIELD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_REFERENCE_IDENTIFIER_TAIL_RE = re.compile(
    r"(?i)\s*(?:[.,;]\s*)?(?:doi\s*:|https?://(?:dx\.)?doi\.org/|"
    r"arxiv\s*:|https?://arxiv\.org/(?:abs|pdf)/).*"
)


def _strip_outer_braces(value: str) -> str:
    """Remove balanced outer BibTeX braces while preserving inner braces."""

    value = value.strip()
    while value.startswith("{") and value.endswith("}"):
        depth = 0
        quoted = False
        escaped = False
        closes_at_end = True
        for index, character in enumerate(value):
            if escaped:
                escaped = False
                continue
            if character == "\\":
                escaped = True
                continue
            if character == '"':
                quoted = not quoted
                continue
            if quoted:
                continue
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0 and index != len(value) - 1:
                    closes_at_end = False
                    break
        if not closes_at_end or depth != 0:
            break
        value = value[1:-1].strip()
    return value


def _clean_value(value: str | None) -> str:
    if not value:
        return ""
    value = _strip_outer_braces(value)
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        value = value[1:-1]
    value = value.replace("\\&", "&")
    return " ".join(value.split()).strip(" ,")


def _normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    match = _DOI_RE.search(value.strip())
    if not match:
        return None
    return match.group(1).rstrip(".,;:)").lower()


def _extract_arxiv(value: str | None) -> str | None:
    if not value:
        return None
    match = _ARXIV_RE.search(value.strip())
    return match.group(1).rstrip(".,;:)") if match else None


def _extract_year(value: str | None) -> int | None:
    if not value:
        return None
    match = _YEAR_RE.search(value)
    return int(match.group(0)) if match else None


def _split_top_level(text: str, delimiter: str = ",") -> tuple[str, str] | None:
    depth = 0
    quoted = False
    escaped = False
    for index, character in enumerate(text):
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character == '"':
            quoted = not quoted
        elif not quoted and character == "{":
            depth += 1
        elif not quoted and character == "}":
            depth = max(depth - 1, 0)
        elif not quoted and depth == 0 and character == delimiter:
            return text[:index], text[index + 1 :]
    return None


def _parse_bib_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    position = 0
    length = len(text)
    while position < length:
        while position < length and text[position] in " \t\r\n,":
            position += 1
        field_match = _BIB_FIELD_RE.match(text, position)
        if not field_match:
            break
        name = field_match.group(0).lower()
        position = field_match.end()
        while position < length and text[position].isspace():
            position += 1
        if position >= length or text[position] != "=":
            break
        position += 1
        while position < length and text[position].isspace():
            position += 1
        start = position
        if position < length and text[position] == "{":
            depth = 0
            escaped = False
            while position < length:
                character = text[position]
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == "{":
                    depth += 1
                elif character == "}":
                    depth -= 1
                    if depth == 0:
                        position += 1
                        break
                position += 1
        elif position < length and text[position] == '"':
            position += 1
            escaped = False
            while position < length:
                character = text[position]
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    position += 1
                    break
                position += 1
        else:
            while position < length and text[position] != ",":
                position += 1
        fields[name] = _clean_value(text[start:position])
    return fields


def _bibtex_blocks(text: str) -> list[tuple[str, str, str]]:
    blocks: list[tuple[str, str, str]] = []
    entry_start_re = re.compile(r"@[A-Za-z][A-Za-z0-9_-]*\s*\{")
    for match in entry_start_re.finditer(text):
        type_match = re.match(r"@([A-Za-z][A-Za-z0-9_-]*)", match.group(0))
        if not type_match:
            continue
        entry_type = type_match.group(1).lower()
        opening = text.find("{", match.start(), match.end())
        depth = 0
        quoted = False
        escaped = False
        closing = None
        for index in range(opening, len(text)):
            character = text[index]
            if escaped:
                escaped = False
                continue
            if character == "\\":
                escaped = True
                continue
            if character == '"':
                quoted = not quoted
                continue
            if quoted:
                continue
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    closing = index
                    break
        if closing is None:
            continue
        blocks.append((entry_type, text[match.start() : closing + 1], text[opening + 1 : closing]))
    return blocks


def _authors_from_bib(value: str) -> list[str]:
    return [
        author
        for author in (_clean_value(item) for item in re.split(r"\s+and\s+", value, flags=re.I))
        if author
    ]


def parse_bibtex(text: str) -> list[ReferenceEntry]:
    """Parse common BibTeX entries without expanding macros or nested syntax."""

    entries: list[ReferenceEntry] = []
    for entry_type, raw, body in _bibtex_blocks(text or ""):
        if entry_type in {"comment", "preamble", "string"}:
            continue
        key_and_fields = _split_top_level(body)
        if key_and_fields is None:
            key, field_text = None, body
        else:
            key, field_text = key_and_fields
            key = key.strip() or None
        fields = _parse_bib_fields(field_text)
        title = _clean_value(fields.get("title"))
        authors = _authors_from_bib(fields.get("author", ""))
        doi = _normalize_doi(fields.get("doi")) or _normalize_doi(fields.get("url"))
        arxiv_id = (
            _extract_arxiv(fields.get("eprint"))
            or _extract_arxiv(fields.get("arxiv"))
            or _extract_arxiv(fields.get("url"))
            or _extract_arxiv(fields.get("note"))
        )
        entries.append(
            ReferenceEntry(
                raw=raw.strip(),
                bibtex_key=key,
                title=title,
                authors=authors,
                year=_extract_year(fields.get("year")),
                venue=_clean_value(fields.get("journal") or fields.get("booktitle")) or None,
                doi=doi,
                arxiv_id=arxiv_id,
                format="bibtex",
            )
        )
    return entries


def _split_plain_entries(text: str) -> list[str]:
    numbered = list(
        re.finditer(r"(?m)^\s*(?:\[\s*\d+\s*\]|\d+\s*[.)])\s*", text or "")
    )
    if numbered:
        return [
            text[match.start() : numbered[index + 1].start() if index + 1 < len(numbered) else len(text)].strip()
            for index, match in enumerate(numbered)
            if text[match.start() : numbered[index + 1].start() if index + 1 < len(numbered) else len(text)].strip()
        ]
    return [part.strip() for part in re.split(r"\n\s*\n+", text or "") if part.strip()]


def _plain_author_list(value: str) -> list[str]:
    value = re.sub(r"\bet\s+al\.?", "", value, flags=re.I).strip(" .,;")
    if not value:
        return []
    parts = re.split(r"\s*(?:;|&|\band\b)\s*", value, flags=re.I)
    if len(parts) == 1:
        parts = re.split(
            r"\s*,\s*(?=[A-Z][a-zA-Z'’-]{1,}(?:\s|,))", value
        )
    return [part.strip(" .,;") for part in parts if part.strip(" .,;")]


def _plain_entry_fields(raw: str) -> ReferenceEntry:
    cleaned = re.sub(r"^\s*(?:\[\s*\d+\s*\]|\d+\s*[.)])\s*", "", raw).strip()
    year_match = _YEAR_RE.search(cleaned)
    year = int(year_match.group(0)) if year_match else None
    doi = _normalize_doi(cleaned)
    arxiv_id = _extract_arxiv(cleaned)

    quoted_title = re.search(r'["“]([^"”]+)["”]', cleaned)
    title = quoted_title.group(1).strip() if quoted_title else ""
    authors: list[str] = []
    venue: str | None = None

    if year_match:
        prefix = cleaned[: year_match.start()].strip(" .,")
        suffix = cleaned[year_match.end() :].strip(" .,")
        if cleaned[year_match.start() - 1 : year_match.start()] == "(":
            prefix = cleaned[: year_match.start() - 1].strip(" .,")
        if prefix and not title:
            authors = _plain_author_list(prefix)
        if suffix and not title:
            suffix = suffix.lstrip("). ")
            pieces = re.split(r"\.\s+", suffix, maxsplit=1)
            title = pieces[0].strip(" .,")
            venue = pieces[1].strip(" .,") if len(pieces) > 1 else None
        elif title and suffix:
            remainder = suffix
            if remainder.startswith(")"):
                remainder = remainder[1:].strip(" .,")
            venue = re.split(r"\.\s+", remainder, maxsplit=1)[0].strip(" .,") or None
        if venue:
            venue = _REFERENCE_IDENTIFIER_TAIL_RE.sub("", venue).strip(" .,") or None
            if venue and venue.lower().startswith("arxiv"):
                venue = "arXiv"
        elif arxiv_id and re.search(r"(?i)arxiv", suffix):
            venue = "arXiv"
    if not authors and year_match:
        before_year = cleaned[: year_match.start()].strip(" .,")
        sentence_parts = re.split(r"\.\s+", before_year)
        if len(sentence_parts) > 1:
            authors = _plain_author_list(sentence_parts[0])
            if not title:
                title = sentence_parts[-1].strip(" .,")
        elif before_year and not title:
            # This covers compact entries such as ``Author. Title, 2020``.
            first_period = before_year.find(".")
            if first_period >= 0:
                authors = _plain_author_list(before_year[:first_period])
                title = before_year[first_period + 1 :].strip(" .,")
    if not title:
        fallback = re.sub(_DOI_RE, "", cleaned)
        fallback = re.sub(_ARXIV_RE, "", fallback)
        fallback = re.sub(r"https?://\S+", "", fallback)
        title = fallback.strip(" .,")
    if not authors and title:
        title_start = cleaned.find(title)
        if title_start > 0:
            authors = _plain_author_list(cleaned[:title_start])

    return ReferenceEntry(
        raw=raw.strip(),
        title=" ".join(title.split()),
        authors=authors,
        year=year,
        venue=venue,
        doi=doi,
        arxiv_id=arxiv_id,
        format="text",
    )


def parse_plain_references(text: str) -> list[ReferenceEntry]:
    """Parse numbered or blank-line-separated bibliography entries heuristically."""

    return [_plain_entry_fields(raw) for raw in _split_plain_entries(text)]


def parse_references(text: str, fmt: str = "auto") -> list[ReferenceEntry]:
    """Parse references in the requested format, detecting BibTeX by default."""

    normalized_format = (fmt or "auto").lower()
    if normalized_format == "bibtex" or (
        normalized_format == "auto" and re.search(r"^\s*@", text or "")
    ):
        return parse_bibtex(text)
    return parse_plain_references(text)
