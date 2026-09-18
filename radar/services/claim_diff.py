"""Word-level claim differencing for incremental tracking (Solution 5).

Uses diff-match-patch for precise word-level diffs between claim versions,
identifying exactly which words changed, were added, or deleted.
"""

from typing import Any

try:
    import diff_match_patch as dmp_module
    _DMP_AVAILABLE = True
except ImportError:
    _DMP_AVAILABLE = False


def diff_claims(previous_text: str, current_text: str) -> dict[str, Any]:
    """Compute word-level diff between two claim versions.

    Args:
        previous_text: The old claim statement or source_quote
        current_text: The new claim statement or source_quote

    Returns:
        Dict with word-level change counts and change type classification
    """
    if not _DMP_AVAILABLE:
        return _fallback_diff(previous_text, current_text)

    dmp = dmp_module.diff_match_patch()
    diffs = dmp.diff_main(previous_text, current_text)
    dmp.diff_cleanupSemantic(diffs)

    added = sum(len(text) for op, text in diffs if op == 1)
    deleted = sum(len(text) for op, text in diffs if op == -1)
    unchanged = sum(len(text) for op, text in diffs if op == 0)

    total = max(added + unchanged + deleted, 1)
    change_ratio = (added + deleted) / total

    # Classify change type
    if change_ratio == 0:
        change_type = "unchanged"
    elif change_ratio < 0.1:
        change_type = "minor_edit"
    elif change_ratio < 0.5:
        change_type = "modified"
    else:
        change_type = "major_rewrite"

    return {
        "change_type": change_type,
        "change_ratio": round(change_ratio, 3),
        "chars_added": added,
        "chars_deleted": deleted,
        "chars_unchanged": unchanged,
        "engine": "diff-match-patch",
    }


def diff_sections(
    previous_content: str,
    current_content: str,
) -> list[dict[str, Any]]:
    """Identify which sections of a manuscript changed between versions.

    Used by incremental tracking to determine which claims need revalidation.
    Changes beyond the first 5000 characters of a section count, and a
    renamed section is reported under its CURRENT heading (so incremental
    extraction can match it) with its previous heading attached.

    Returns:
        List of section changes with change ratios
    """
    from radar.services.manuscript_parser import build_document_map

    prev_map = build_document_map(previous_content)
    curr_map = build_document_map(current_content)

    prev_by_heading = {
        s.heading.lower().strip(): s for s in prev_map.sections
    }
    curr_by_heading = {
        s.heading.lower().strip(): s for s in curr_map.sections
    }
    prev_headings = set(prev_by_heading)
    curr_headings = set(curr_by_heading)

    changes: list[dict[str, Any]] = []
    new_sections = curr_headings - prev_headings
    deleted_sections = prev_headings - curr_headings

    for heading in prev_headings & curr_headings:
        s = prev_by_heading[heading]
        match = curr_by_heading[heading]
        diff = diff_claims(s.text, match.text)
        changes.append({
            "section": match.heading,
            "change": diff["change_type"],
            "ratio": diff["change_ratio"],
        })

    for old_heading in deleted_sections:
        # Rename detection: a deleted heading that closely matches a new one
        # is a rename, not a deletion — report it under the CURRENT heading
        # so incremental extraction treats the section as changed.
        old_section = prev_by_heading[old_heading]
        best = max(
            curr_by_heading.items(),
            key=lambda item: _heading_similarity(old_heading, item[0]),
            default=None,
        )
        if best is not None:
            new_heading, new_section = best
            similarity = _heading_similarity(old_heading, new_heading)
            if similarity >= _RENAME_SIMILARITY_THRESHOLD:
                diff = diff_claims(old_section.text, new_section.text)
                changes.append({
                    "section": new_section.heading,
                    "change": diff["change_type"],
                    "ratio": diff["change_ratio"],
                    "previous_heading": old_section.heading,
                    "renamed": True,
                    "heading_similarity": round(similarity, 3),
                })
                continue
        changes.append({"section": old_section.heading, "change": "deleted", "ratio": 1.0})

    for heading in new_sections:
        changes.append({"section": curr_by_heading[heading].heading, "change": "new", "ratio": 1.0})

    return changes


_RENAME_SIMILARITY_THRESHOLD = 0.5


def _heading_similarity(previous: str, current: str) -> float:
    """Normalized heading similarity for rename detection."""
    import difflib

    return difflib.SequenceMatcher(
        None, previous.lower().strip(), current.lower().strip()
    ).ratio()


def _fallback_diff(previous_text: str, current_text: str) -> dict[str, Any]:
    """Simple character-level diff when diff-match-patch is unavailable."""
    if previous_text == current_text:
        return {
            "change_type": "unchanged", "change_ratio": 0.0,
            "chars_added": 0, "chars_deleted": 0,
            "chars_unchanged": len(current_text), "engine": "fallback",
        }
    # Set-based approximation
    prev_words = set(previous_text.lower().split())
    curr_words = set(current_text.lower().split())
    unchanged = len(prev_words & curr_words)
    total = max(len(prev_words | curr_words), 1)
    change_ratio = 1.0 - (unchanged / total)
    return {
        "change_type": "modified", "change_ratio": round(change_ratio, 3),
        "chars_added": len(current_text) - len(previous_text),
        "chars_deleted": max(len(previous_text) - len(current_text), 0),
        "chars_unchanged": min(len(previous_text), len(current_text)),
        "engine": "fallback",
    }
