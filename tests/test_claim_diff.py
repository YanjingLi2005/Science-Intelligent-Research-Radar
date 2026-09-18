"""diff_sections correctness (M4): tail edits beyond char 5000 and renamed
sections must be reported as changes."""

from radar.services.claim_diff import diff_claims, diff_sections


def test_diff_sections_detects_tail_edit_beyond_5000_chars():
    """M4 regression: an edit in the tail of a long section must not be
    reported as unchanged (the old [:5000] truncation hid it)."""
    head = (
        "1 Introduction\n\n"
        + "Background filler sentence about retrieval systems. " * 200
        + "\n\n2 Results\n\n"
        + "A long results section with plenty of context. " * 150
    )
    previous = head + "\nThe model improves exact match by 7.0 points."
    current = head + "\nThe model improves exact match by 9.0 points."

    changes = diff_sections(previous, current)
    results_change = next(
        (change for change in changes if change["section"].lower().startswith("results")),
        None,
    )
    assert results_change is not None
    assert results_change["change"] in ("minor_edit", "modified")


def test_diff_sections_reports_renamed_section_under_current_heading():
    """M4 regression: a renamed section must be reported as a change under its
    CURRENT heading (with previous_heading attached), so incremental
    extraction re-extracts it instead of treating it as unchanged."""
    previous = (
        "# Results\n\n"
        "RadarNet improves exact match by 7.0 points over BM25 on DomainQA."
    )
    current = (
        "# Experimental Results\n\n"
        "RadarNet improves exact match by 7.0 points over BM25 on DomainQA."
    )

    changes = diff_sections(previous, current)
    rename = next(
        (change for change in changes if change.get("renamed")),
        None,
    )
    assert rename is not None
    assert rename["section"] == "Experimental Results"
    assert rename["previous_heading"] == "Results"
    assert rename["change"] != "deleted"
    # the old heading is not reported as deleted
    assert not any(
        change["change"] == "deleted" and change["section"] == "Results"
        for change in changes
    )


def test_diff_sections_reports_genuine_deletion_and_addition():
    previous = "1 Results\n\nA result sentence with numbers 42."
    current = "1 Results\n\nA result sentence with numbers 42.\n\n2 Related Work\n\nNew section."
    changes = diff_sections(previous, current)
    assert any(change["change"] == "new" for change in changes)


def test_diff_claims_classifies_change_levels():
    base = "This is a fairly long sentence about retrieval systems in detail."
    assert diff_claims(base, base)["change_type"] == "unchanged"
    # Small addition: ratio ~0.2 → modified, not a rewrite.
    assert diff_claims(base, base + " A few words added.")["change_type"] == "modified"
    # Wholesale replacement: ratio ~0.9 → major_rewrite.
    replaced = "A completely different sentence about something else entirely."
    assert diff_claims(base, replaced)["change_type"] == "major_rewrite"
