"""Deterministic internal-consistency checks over a single manuscript."""

from radar.services.consistency_service import (
    check_manuscript,
    extract_measurements,
    find_numeric_inconsistencies,
)


CONFLICTING = """
## Abstract

Our model reaches 28.4 BLEU on WMT 2014 English-to-German, improving over the
previous best by a wide margin.

## Results

On WMT 2014 English-to-German our model reaches 25.1 BLEU, outperforming all
published baselines.
"""

CONSISTENT = """
## Abstract

Our model reaches 28.4 BLEU on WMT 2014 English-to-German.

## Results

The final system scores 28.4 BLEU on the same benchmark.
"""

ROUNDED = """
## Abstract

The system achieves roughly 28 BLEU on the benchmark.

## Results

Measured accuracy of the final run is 28.4 BLEU.
"""


def test_flags_metric_reported_with_two_values_across_sections():
    findings = find_numeric_inconsistencies(CONFLICTING)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "numeric_conflict"
    assert finding.metric == "bleu"
    # 28.4 vs 25.1 is an 11% spread — worth review, not critical.
    assert finding.severity in {"review", "critical"}
    values = sorted(m.value for m in finding.measurements)
    assert values == [25.1, 28.4]
    # Every occurrence is anchored so a reviewer can jump to the source.
    for measurement in finding.measurements:
        assert measurement.start_offset < measurement.end_offset
        assert CONFLICTING[measurement.start_offset : measurement.end_offset]
        assert measurement.quote


def test_same_value_in_two_sections_is_not_a_conflict():
    """Restating a result is normal writing, not an inconsistency."""

    assert find_numeric_inconsistencies(CONSISTENT) == []


def test_rounding_between_abstract_and_results_is_tolerated():
    """"roughly 28" vs "28.4" must not be reported: precision floors would
    otherwise make every abstract look inconsistent with its own results."""

    assert find_numeric_inconsistencies(ROUNDED) == []


def test_years_and_section_numbers_are_not_measurements():
    text = """
    ## Introduction

    Prior work (Vaswani, 2017) introduced the architecture. See Section 3 and
    Table 2 for details. We follow the 2019 evaluation protocol.
    """

    assert extract_measurements(text) == []


def test_numbers_without_a_metric_name_are_ignored():
    """A bare count is not a reported result and must not be compared."""

    text = """
    ## Method

    We train on 8 GPUs for 12 epochs.

    ## Setup

    Training used 4 GPUs.
    """

    assert find_numeric_inconsistencies(text) == []


def test_values_inside_one_section_are_not_compared():
    """Per-dataset numbers listed together are legitimate, not conflicting."""

    text = """
    ## Results

    Accuracy is 91.2 on the first split and 84.7 accuracy on the second split.
    """

    assert find_numeric_inconsistencies(text) == []


def test_check_manuscript_returns_serialisable_candidates():
    result = check_manuscript(CONFLICTING)

    assert result["finding_count"] == 1
    finding = result["findings"][0]
    # Findings are proposals; confirmation happens through the human gates.
    assert finding["review_state"] == "candidate"
    assert finding["kind"] == "numeric_conflict"
    assert len(finding["occurrences"]) == 2
    assert all("start_offset" in item for item in finding["occurrences"])


def test_empty_manuscript_is_handled():
    assert check_manuscript("") == {
        "checks_run": ["numeric_conflict"],
        "finding_count": 0,
        "findings": [],
    }


# ---------------------------------------------------------------------------
# Regressions from running against a real paper (Attention Is All You Need).
# The first pass extracted 164 "measurements" and produced 6 findings, all of
# them false positives. Each case below is one of the causes.
# ---------------------------------------------------------------------------

def test_metric_acronyms_do_not_match_inside_words():
    """EM/IoU/PPL/WER are substrings of memory, previous, applications, power."""

    text = """
    ## Introduction

    Long short-term memory models power 12 applications, and previous work
    reports 34 similar systems.
    """

    assert extract_measurements(text) == []


def test_citation_markers_are_not_measurements():
    text = """
    ## Background

    Prior accuracy work [13] and later studies [35, 2, 5] motivated this.

    ## Results

    Follow-up accuracy analyses [27, 28] agree.
    """

    assert find_numeric_inconsistencies(text) == []


def test_reference_section_numbers_are_ignored():
    """arXiv ids and page ranges in the bibliography are not results."""

    text = """
    ## Results

    We reach 91.3 F1 on the benchmark.

    ## References

    [1] Someone. Title. arXiv preprint arXiv:1601.07 F1, pages 2440-2448.
    """

    assert find_numeric_inconsistencies(text) == []


def test_table_row_is_not_compared_to_an_unlabelled_sentence():
    """A table row names its condition; a bare sentence does not.

    Comparing them would flag every variant in a results table against the
    headline number, so a table cell only meets prose that names the same
    condition.
    """

    text = """
    ## Results

    Our model reaches 27.3 BLEU.

    ## Model Variations

    |model|BLEU|params|
    |---|---|---|
    |base|38.1|65|
    """

    assert find_numeric_inconsistencies(text) == []


# ---------------------------------------------------------------------------
# Regressions from running against the real manuscript in data/. The first
# pass reported one CRITICAL finding that lumped six unrelated numbers
# together; each case below is one of the causes.
# ---------------------------------------------------------------------------

def test_same_metric_on_different_benchmarks_is_not_a_conflict():
    """41.8 on English-French and 28.4 on English-German are both correct."""

    text = """
    ## Abstract

    Our model achieves 28.4 BLEU on the WMT 2014 English-to-German translation
    task. On the WMT 2014 English-to-French translation task, our model
    establishes a state-of-the-art BLEU score of 41.8.

    ## Results

    On WMT 2014 English-to-German we report 28.4 BLEU.
    """

    assert find_numeric_inconsistencies(text) == []


def test_improvement_is_not_compared_against_an_absolute_score():
    """"by over 2 BLEU" is a difference between systems, not a score."""

    text = """
    ## Abstract

    Our model achieves 28.4 BLEU, improving over the best previous results by
    over 2 BLEU.

    ## Results

    The final system reaches 28.4 BLEU.
    """

    assert find_numeric_inconsistencies(text) == []


def test_trailing_comparison_word_marks_a_delta():
    text = """
    ## Model Variations

    Single-head attention is 0.9 BLEU worse than the best setting.

    ## Results

    Our model reaches 27.3 BLEU.
    """

    assert find_numeric_inconsistencies(text) == []


def test_cost_units_are_not_scores():
    """"41.8 after training for 3.5 days" must not report BLEU 3.5."""

    text = """
    ## Abstract

    We reach a BLEU score of 41.8 after training for 3.5 days on eight GPUs.
    """

    values = sorted(m.value for m in extract_measurements(text))
    assert values == [41.8]


def test_table_cell_conflicting_with_matching_prose_is_flagged():
    """The abstract/table disagreement is the case this check exists for."""

    text = """
    ## Abstract

    Our big model reaches 41.0 BLEU.

    ## Results

    |model|BLEU|
    |---|---|
    |big|38.1|
    """

    findings = find_numeric_inconsistencies(text)

    assert len(findings) == 1
    assert findings[0].metric == "bleu"
    sources = {m.source for m in findings[0].measurements}
    assert sources == {"text", "table"}


def test_table_variants_do_not_conflict_with_each_other():
    text = """
    ## Model Variations

    |model|BLEU|
    |---|---|
    |base|38.1|
    |big|41.0|
    """

    assert find_numeric_inconsistencies(text) == []


def test_section_heading_numbers_are_ignored():
    text = """
    ## 5.4 Regularization

    We apply dropout.

    ## 6.1 Machine Translation

    Accuracy is discussed here.
    """

    assert extract_measurements(text) == []


def test_split_decimal_from_pdf_parser_is_rejoined():
    """The parser renders 28.4 as "28 _._ 4"; it must read as one value."""

    text = """
    ## Abstract

    The model achieves 28 _._ 4 BLEU on the benchmark.

    ## Results

    Final BLEU is 28.4 on the same benchmark.
    """

    # One value, restated — not a conflict.
    assert find_numeric_inconsistencies(text) == []


def test_digit_inside_a_metric_name_is_not_a_measurement():
    """"89.5 F1" is one number, not 89.5 and 1."""

    text = """
    ## Abstract

    Our system reaches 89.5 F1 on SQuAD.
    """

    assert sorted(m.value for m in extract_measurements(text)) == [89.5]


def test_conflict_without_a_shared_experiment_stays_a_review_item():
    text = """
    ## Abstract

    Our system reaches 89.5 F1 on SQuAD.

    ## Results

    The final model scores 40.0 F1.
    """

    findings = find_numeric_inconsistencies(text)

    assert len(findings) == 1
    assert findings[0].severity == "review"
