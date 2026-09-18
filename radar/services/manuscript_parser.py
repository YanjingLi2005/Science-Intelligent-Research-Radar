"""Section-aware manuscript parsing: DocumentMap + ManuscriptOverview + section extraction.

Addresses:
- Solution 1: Section-aware extraction (build document map, extract per section)
- Solution 3: Manuscript Overview (lightweight paper portrait before extraction)
"""

import re
from pathlib import Path
from typing import Any

from radar.llm.base import LLMClient
from radar.llm.factory import build_analysis_llm
from radar.config import Settings, get_settings
from radar.schemas import (
    DocumentMap,
    ManuscriptOverview,
    SectionInfo,
)


# Patterns for identifying sections in academic papers.
# Two complementary patterns:
#  - _HEADING_STRUCTURE: catches any LaTeX \section/\subsection or markdown ## heading
#    (section title content is captured and may be any text)
#  - _HEADING_KEYWORD: catches plain/numbered headings whose title contains a
#    known academic keyword (Introduction, Methods, Results, ...)
_HEADING_STRUCTURE = re.compile(
    r"(?:^|\n)\s*(?:\\sub\w+\s*\{|\\section\*?\s*\{)([^}]*)\}|"
    r"(?:^|\n)\s*\#{1,4}\s+([^\n]+)",
    re.IGNORECASE,
)
_SECTION_KEYWORDS = (
    "Introduction|Related Work|Background|Methodology?|Methods?|Experiment|Results?|Discussion|"
    "Conclusion|Limitations?|Appendix|Abstract|Evaluation|Ablation|Implementation|Setup|Overview|"
    "Preliminary|Future Work|Acknowledgment|Reference|Supplementary"
)
_HEADING_KEYWORD = re.compile(
    r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s+)?(" + _SECTION_KEYWORDS + r")(?=\s*$|\s*\n)",
    re.IGNORECASE,
)


def find_section_headings(content: str) -> list[tuple[int, str]]:
    """Locate section headings, returning (offset, cleaned_heading).

    Handles LaTeX ``\\section{...}``, markdown ``## ...``, numbered and
    plain academic headings. Falls back to an empty list when the document
    has no detectable structure.
    """
    found: dict[int, str] = {}
    for match in _HEADING_STRUCTURE.finditer(content):
        title = (match.group(1) or match.group(2) or "").strip()
        if title:
            found[match.start()] = title
    for match in _HEADING_KEYWORD.finditer(content):
        found.setdefault(match.start(), match.group(1))
    return sorted(found.items())


def _clean_heading(raw: str) -> str:
    """Normalize a heading: strip LaTeX markup, hashes, and numbers."""
    h = raw.strip()
    h = re.sub(r"\\sub\w+\s*\{", "", h)
    h = re.sub(r"\\section\*?\s*\{", "", h)
    h = h.rstrip("}").strip()
    h = re.sub(r"^#+\s*", "", h)
    h = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", h)
    return h.strip()

MAIN_SECTIONS = {
    "abstract": 1, "introduction": 1, "related work": 2, "background": 2,
    "preliminary": 2, "overview": 2,
    "method": 3, "methodology": 3, "approach": 3, "implementation": 3, "setup": 3,
    "experiment": 4, "evaluation": 4, "results": 4, "result": 4,
    "ablation": 4, "analysis": 5,
    "discussion": 6, "limitation": 7, "conclusion": 8,
    "future work": 8, "acknowledgment": 9, "reference": 10, "appendix": 11,
}


def _section_level(heading: str) -> int:
    clean = re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", heading).strip().lower()
    for key, level in MAIN_SECTIONS.items():
        if key in clean:
            return min(level, 4)
    return 4


def _clean_heading(raw: str) -> str:
    """Normalize a heading match: strip LaTeX markup, hashes, and numbers."""
    h = raw.strip()
    # LaTeX: \section{Main Results} -> Main Results
    h = re.sub(r"\\sub\w+\s*\{", "", h)
    h = re.sub(r"\\section\*?\s*\{", "", h)
    h = h.rstrip("}").strip()
    # Markdown: ## Methods -> Methods
    h = re.sub(r"^#+\s*", "", h)
    # Numbered: 1. Introduction -> Introduction
    h = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", h)
    return h.strip()


def build_document_map(content: str) -> DocumentMap:
    """Parse manuscript into sections, extracting tables/figures references.

    Args:
        content: Full manuscript text

    Returns:
        DocumentMap with sections, table/figure captions, and metadata
    """
    sections: list[SectionInfo] = []
    heading_offsets = find_section_headings(content)

    if not heading_offsets:
        # No structured headings found; create one flat section
        sections.append(SectionInfo(
            section_id="full_text",
            heading="Full Text",
            level=1,
            start_offset=0,
            end_offset=len(content),
            text=content,
        ))
    else:
        for i, (offset, raw_heading) in enumerate(heading_offsets):
            heading = _clean_heading(raw_heading) or raw_heading.strip()
            # Section content starts after the heading line
            next_offset = heading_offsets[i + 1][0] if i + 1 < len(heading_offsets) else len(content)
            start = content.find("\n", offset)
            start = start + 1 if start >= 0 else offset + len(raw_heading)
            level = _section_level(heading)
            sections.append(SectionInfo(
                section_id=f"sec-{i:02d}",
                heading=heading,
                level=level,
                start_offset=offset,
                end_offset=next_offset,
                text=content[start:next_offset],
            ))

    # Extract table and figure captions
    table_captions = re.findall(r"\\caption\{([^}]*)\}", content)
    table_captions += re.findall(r"Table\s+\d+[.:]\s*(.+)", content, re.I)
    figure_captions = re.findall(r"Figure\s+\d+[.:]\s*(.+)", content, re.I)

    language = "zh" if re.search(r"[\u4e00-\u9fff]", content) else "en"

    return DocumentMap(
        sections=sections,
        tables=table_captions,
        figures=figure_captions,
        total_length=len(content),
        language=language,
    )


def _manuscript_overview_prompt() -> str:
    path = Path(__file__).parents[1] / "llm" / "prompts" / "manuscript_overview.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return (
        "Generate a lightweight overview of the supplied manuscript. "
        "Identify: research_problem, domain, main_methods, datasets, key_variables, "
        "likely_contributions, author_terms (our, proposed, this work), "
        "cited_work_markers (et al., previous, existing). "
        "Return valid JSON matching the schema."
    )


def generate_manuscript_overview(
    content: str,
    *,
    llm_client: LLMClient | None = None,
    settings: Settings | None = None,
) -> ManuscriptOverview:
    """Generate a lightweight paper portrait (Solution 3).

    Called before claim extraction to help the LLM understand:
    - Which terms refer to the authors' own work (disambiguate "our"/"proposed")
    - Which terms refer to cited work
    - The paper's domain, methods, and key variables
    """
    client = llm_client or build_analysis_llm(settings or get_settings())
    if client is None:
        return ManuscriptOverview()

    instructions = _manuscript_overview_prompt()
    import json
    prompt = (
        f"{instructions}\n\nMANUSCRIPT (first 20K chars):\n{content[:20000]}"
    )
    try:
        output = client.generate_structured(
            stage="manuscript_overview",
            prompt=prompt,
            response_model=ManuscriptOverview,
        )
        return output
    except Exception:
        return ManuscriptOverview()


def extract_tables_from_pdf(pdf_path: Path | str, max_tables: int = 5) -> list[dict[str, Any]]:
    """Extract structured 2D tables, bounding boxes, and captions from a paper PDF.

    Uses pdfplumber or PyMuPDF (fitz) with graceful degradation to empty list.
    """
    path = Path(pdf_path)
    if not path.is_file():
        return []

    tables: list[dict[str, Any]] = []

    # 1. Try pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                extracted = page.extract_tables()
                if not extracted:
                    continue
                text = page.extract_text() or ""
                captions = re.findall(r"(Table\s+\d+[:.][^\n]+)", text, re.IGNORECASE)

                for t_idx, raw_table in enumerate(extracted):
                    if not raw_table or len(raw_table) < 2:
                        continue
                    cleaned_rows = [
                        [str(cell or "").strip() for cell in row]
                        for row in raw_table
                        if any(bool(cell and str(cell).strip()) for cell in row)
                    ]
                    if len(cleaned_rows) < 2:
                        continue
                    headers = cleaned_rows[0]
                    rows = cleaned_rows[1:]
                    caption = captions[t_idx] if t_idx < len(captions) else f"Table on Page {page_idx + 1}"

                    tables.append({
                        "caption": caption.strip(),
                        "page_num": page_idx + 1,
                        "headers": headers,
                        "rows": rows,
                        "bbox": None,
                    })
                    if len(tables) >= max_tables:
                        return tables
        if tables:
            return tables
    except Exception:
        pass

    # 2. Try PyMuPDF (fitz)
    try:
        import fitz
        doc = fitz.open(str(path))
        for page_idx, page in enumerate(doc):
            tabs = page.find_tables()
            if not tabs or not tabs.tables:
                continue
            text = page.get_text() or ""
            captions = re.findall(r"(Table\s+\d+[:.][^\n]+)", text, re.IGNORECASE)
            for t_idx, tab in enumerate(tabs.tables):
                df = tab.extract()
                if not df or len(df) < 2:
                    continue
                headers = [str(c or "").strip() for c in df[0]]
                rows = [
                    [str(c or "").strip() for c in r]
                    for r in df[1:]
                    if any(bool(c and str(c).strip()) for c in r)
                ]
                caption = captions[t_idx] if t_idx < len(captions) else f"Table on Page {page_idx + 1}"
                tables.append({
                    "caption": caption.strip(),
                    "page_num": page_idx + 1,
                    "headers": headers,
                    "rows": rows,
                    "bbox": list(tab.bbox) if hasattr(tab, "bbox") else None,
                })
                if len(tables) >= max_tables:
                    break
            if len(tables) >= max_tables:
                break
        doc.close()
    except Exception:
        pass

    return tables


def extract_tables_from_text(text: str) -> list[dict[str, Any]]:
    """Extract Markdown or LaTeX tabular data from plain text or source content."""
    if not text:
        return []

    tables: list[dict[str, Any]] = []

    lines = text.splitlines()
    table_block: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            table_block.append(stripped)
        else:
            if len(table_block) >= 3:
                rows = [
                    [c.strip() for c in row.strip("|").split("|")]
                    for row in table_block
                    if not re.match(r"^\|?\s*[-:]+[-| :]*\|?$", row)
                ]
                if len(rows) >= 2:
                    tables.append({
                        "caption": "Extracted Table",
                        "page_num": 1,
                        "headers": rows[0],
                        "rows": rows[1:],
                        "bbox": None,
                    })
            table_block = []

    if len(table_block) >= 3:
        rows = [
            [c.strip() for c in row.strip("|").split("|")]
            for row in table_block
            if not re.match(r"^\|?\s*[-:]+[-| :]*\|?$", row)
        ]
        if len(rows) >= 2:
            tables.append({
                "caption": "Extracted Table",
                "page_num": 1,
                "headers": rows[0],
                "rows": rows[1:],
                "bbox": None,
            })

    return tables


def parse_table_with_multimodal_llm(
    table_image_or_text: bytes | str,
    *,
    llm_client: LLMClient | None = None,
    caption: str = "",
) -> dict[str, Any] | None:
    """Invoke vision/multimodal LLM to transcribe a table into structured 2D format."""
    if not llm_client:
        return None

    prompt = (
        "You are an academic benchmark table parser. Extract this experimental table into structured JSON with:\n"
        f"Caption: {caption}\n"
        "- caption: string\n"
        "- headers: list of string column names\n"
        "- rows: 2D list of row strings\n"
        "Return valid JSON matching the schema."
    )
    try:
        output = llm_client.generate_structured(
            stage="table_multimodal_transcription",
            prompt=prompt,
            response_model=dict,
        )
        return output
    except Exception:
        return None
