"""Layer 2 tests.

The pipeline calls a model, so the tests supply fake ones. What is worth
testing here is not the model's judgment — it is everything built around the
model to stop a wrong judgment from reaching the user: which sentences are
eligible, which pairs are even considered, and whether a verdict the model
cannot quote gets discarded.
"""

from __future__ import annotations

from radar.services.semantic_consistency_service import (
    Sentence,
    collect_sentences,
    find_semantic_inconsistencies,
    generate_candidate_pairs,
    is_assertive,
    section_role,
    split_sentences,
)


MANUSCRIPT = """
## Abstract

We present a method that requires no additional annotation to reach strong
performance on entity linking across every benchmark we consider.

## Introduction

Entity linking has been studied for decades and remains difficult [1, 2, 3].

## Method

Our approach fine-tunes a base encoder on the target domain. We annotated
5,000 examples by hand to build the training set used in all experiments.

## Results

We evaluate our model on AIDA-CoNLL and report consistent gains over the
published baselines in this domain.

## References

[1] Someone. A title. In Proceedings, pages 1-10.
"""


class FakeNLI:
    """Returns a fixed contradiction score for every pair."""

    def __init__(self, score: float = 0.9):
        self.score = score
        self.calls: list[tuple[str, str]] = []

    def check(self, claim: str, evidence: str) -> dict:
        self.calls.append((claim, evidence))
        return {
            "label": "contradiction",
            "scores": {"contradiction": self.score, "entailment": 0.0, "neutral": 0.1},
        }


class UnavailableNLI:
    """Stands in for the model failing to load — check() returns None."""

    def check(self, claim: str, evidence: str) -> None:
        return None


class FakeLLM:
    """Returns a scripted verdict, so the test controls what the model 'said'."""

    def __init__(self, verdict: dict):
        self.verdict = verdict
        self.prompts: list[str] = []

    def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
        self.prompts.append(prompt)
        return response_model.model_validate(self.verdict)


# ---------------------------------------------------------------------------
# Section roles: which parts of a paper are even eligible.
# ---------------------------------------------------------------------------

def test_headings_map_to_roles_by_keyword():
    assert section_role("Abstract") == "abstract"
    assert section_role("5.2 Experimental Results") == "results"
    assert section_role("Our Approach") == "method"
    assert section_role("Introduction") == "intro"


def test_related_work_and_references_are_excluded():
    """Claims about other people's papers cannot be self-contradictions."""

    assert section_role("Related Work") is None
    assert section_role("References") is None
    assert section_role("Acknowledgements") is None
    assert section_role("Appendix A") is None


# ---------------------------------------------------------------------------
# Sentence eligibility: the cheapest place to stop a false positive.
# ---------------------------------------------------------------------------

def test_sentence_must_be_about_this_paper():
    assert is_assertive(
        "We present a method that requires no additional annotation to reach "
        "strong performance."
    )
    # Same proposition, but it is someone else's.
    assert not is_assertive(
        "Prior work presents a method that requires no additional annotation "
        "to reach strong performance."
    )


def test_attributed_sentence_is_not_this_papers_claim():
    """"Smith et al. show X" next to "we found not-X" is not a contradiction."""

    assert not is_assertive(
        "Smith et al. show that our task can be solved without any annotation "
        "whatsoever in the general case."
    )


def test_paper_may_name_its_own_system_in_the_third_person():
    """Regression: requiring "we"/"our" left zero eligible sentences on a real
    manuscript, because the authors wrote about their system by name."""

    assert is_assertive(
        "On DomainQA, RadarNet improves exact match from 61.2% to 68.7% over "
        "BM25 under unseen-domain evaluation."
    )


def test_attribution_is_caught_mid_sentence_too():
    assert not is_assertive(
        "The gains hold under unseen-domain evaluation, as Smith et al. report "
        "for a closely related retrieval setting."
    )
    assert not is_assertive(
        "A concurrent reproduction reports a lower result under nominally "
        "matched experimental conditions than we do."
    )


def test_latex_section_heading_does_not_swallow_the_next_sentence():
    """Regression: "\\section{Main Results}" is followed by a single newline,
    so the title glued onto the first sentence and the whole thing was then
    discarded as a heading."""

    text = "\\section{Main Results}\nOur system reaches 68.7% exact match on the benchmark."
    spans = split_sentences(text, base_offset=0)

    assert any(s[0].startswith("Our system reaches") for s in spans)
    assert all(not s[0].startswith("\\section") or "Our system" not in s[0] for s in spans)


def test_fragments_headings_and_table_rows_are_rejected():
    assert not is_assertive("We show this.")            # too short
    assert not is_assertive("## Results")               # heading
    assert not is_assertive("| our model | 41.0 | 65 |")  # table row
    assert not is_assertive("x_{i+1} = f(x_i) + 3.14 * w")  # equation


def test_citation_only_sentence_asserts_nothing():
    assert not is_assertive(
        "Our setting follows [1], [2], [3], [4], [5], [6], [7], [8], [9], [10]."
    )


# ---------------------------------------------------------------------------
# Offsets: a finding that cannot be located is not reportable.
# ---------------------------------------------------------------------------

def test_split_sentences_offsets_point_at_the_original_text():
    text = "We do X.  We also do Y."
    spans = split_sentences(text, base_offset=100)

    assert [s[0] for s in spans] == ["We do X.", "We also do Y."]
    for sentence, start, end in spans:
        assert text[start - 100:end - 100] == sentence


def test_collected_sentences_can_be_sliced_back_out_of_the_manuscript():
    for sentence in collect_sentences(MANUSCRIPT):
        assert MANUSCRIPT[sentence.start_offset:sentence.end_offset] == sentence.text


# ---------------------------------------------------------------------------
# Pairing: what the model is allowed to be asked about.
# ---------------------------------------------------------------------------

def test_sentences_in_the_same_section_are_never_paired():
    sentences = collect_sentences(MANUSCRIPT)
    for first, second in generate_candidate_pairs(sentences):
        assert first.section != second.section


def test_abstract_and_method_are_paired():
    sentences = collect_sentences(MANUSCRIPT)
    roles = {
        frozenset((first.role, second.role))
        for first, second in generate_candidate_pairs(sentences)
    }

    assert frozenset(("abstract", "method")) in roles


def test_reference_section_text_never_reaches_the_model():
    sentences = collect_sentences(MANUSCRIPT)

    assert all("In Proceedings" not in s.text for s in sentences)


# ---------------------------------------------------------------------------
# The verdict gate. This is the part that exists because models hallucinate.
# ---------------------------------------------------------------------------

def _run(verdict: dict, *, nli=None):
    return find_semantic_inconsistencies(
        MANUSCRIPT,
        llm_client=FakeLLM(verdict),
        nli_checker=nli if nli is not None else FakeNLI(),
        max_pairs_judged=3,
    )


def test_contradiction_with_verbatim_quotes_is_reported():
    """The scripted verdict is applied to every judged pair, so only the pair
    whose sentences actually contain those quotes survives — which is the gate
    doing its job, not a coincidence."""

    findings, stats = _run(
        {
            "is_contradiction": True,
            "kind": "requirement_conflict",
            "confidence": 0.9,
            "quote_a": "requires no additional annotation",
            "quote_b": "We annotated",
            "reason": "The paper both disclaims and describes manual annotation.",
        }
    )

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "requirement_conflict"
    assert finding.quotes[0] in finding.sentences[0].text
    assert finding.quotes[1] in finding.sentences[1].text
    # The other judged pairs were rejected precisely because the quotes were
    # not in them.
    assert stats["rejected_unquoted"] == stats["pairs_judged"] - 1


def test_paraphrased_quote_is_discarded():
    """The model 'quoted' something that is not in the text — drop the verdict."""

    findings, stats = _run(
        {
            "is_contradiction": True,
            "kind": "requirement_conflict",
            "confidence": 0.95,
            "quote_a": "the method needs no labelled data at all",
            "quote_b": "We annotated",
            "reason": "Invented quote.",
        }
    )

    assert findings == []
    assert stats["rejected_unquoted"] > 0


def test_low_confidence_verdict_is_discarded():
    findings, stats = _run(
        {
            "is_contradiction": True,
            "kind": "scope_overreach",
            "confidence": 0.3,
            "quote_a": "every benchmark we consider",
            "quote_b": "We evaluate",
            "reason": "Guessing.",
        }
    )

    assert findings == []
    assert stats["rejected_low_confidence"] > 0


def test_no_contradiction_verdict_produces_nothing():
    findings, _ = _run(
        {"is_contradiction": False, "kind": "none", "confidence": 0.0,
         "quote_a": "", "quote_b": "", "reason": ""}
    )

    assert findings == []


def test_unknown_kind_is_discarded():
    findings, _ = _run(
        {
            "is_contradiction": True,
            "kind": "none",
            "confidence": 0.9,
            "quote_a": "requires no additional annotation",
            "quote_b": "We annotated",
            "reason": "Kind missing.",
        }
    )

    assert findings == []


# ---------------------------------------------------------------------------
# Degradation: the layer must fail quietly, never loudly or expensively.
# ---------------------------------------------------------------------------

def test_low_nli_scores_mean_the_llm_is_never_called():
    """The prefilter is the cost control; below threshold, nothing is judged."""

    llm = FakeLLM({"is_contradiction": True, "kind": "requirement_conflict",
                   "confidence": 0.9, "quote_a": "", "quote_b": "", "reason": ""})
    findings, stats = find_semantic_inconsistencies(
        MANUSCRIPT, llm_client=llm, nli_checker=FakeNLI(score=0.01)
    )

    assert findings == []
    assert stats["pairs_judged"] == 0
    assert llm.prompts == []


def test_unavailable_nli_model_is_reported_not_bypassed():
    """Without the filter we do not fall back to sending everything to the LLM."""

    llm = FakeLLM({"is_contradiction": True, "kind": "requirement_conflict",
                   "confidence": 0.9, "quote_a": "", "quote_b": "", "reason": ""})
    findings, stats = find_semantic_inconsistencies(
        MANUSCRIPT, llm_client=llm, nli_checker=UnavailableNLI()
    )

    assert findings == []
    assert stats["status"] == "nli_unavailable"
    assert llm.prompts == []


def test_empty_manuscript_is_handled():
    findings, stats = find_semantic_inconsistencies("")

    assert findings == []
    assert stats["status"] == "empty_manuscript"


def test_judged_pairs_are_capped():
    _, stats = _run(
        {"is_contradiction": False, "kind": "none", "confidence": 0.0,
         "quote_a": "", "quote_b": "", "reason": ""}
    )

    assert stats["pairs_judged"] <= 3


# ---------------------------------------------------------------------------
# Output shape: layer 1 and layer 2 findings must render identically.
# ---------------------------------------------------------------------------

def test_finding_dict_matches_layer_one_shape_and_offsets():
    findings, _ = _run(
        {
            "is_contradiction": True,
            "kind": "requirement_conflict",
            "confidence": 0.9,
            "quote_a": "requires no additional annotation",
            "quote_b": "We annotated",
            "reason": "Both disclaims and performs annotation.",
        }
    )
    payload = findings[0].to_dict()

    assert set(payload) >= {
        "kind", "metric", "severity", "summary", "review_state", "occurrences",
    }
    assert payload["review_state"] == "candidate"
    assert len(payload["occurrences"]) == 2
    for occurrence in payload["occurrences"]:
        start, end = occurrence["start_offset"], occurrence["end_offset"]
        assert MANUSCRIPT[start:end] == occurrence["quote"]


def test_numeric_check_is_unaffected_when_semantic_is_off():
    from radar.services.consistency_service import check_manuscript

    assert check_manuscript(MANUSCRIPT)["checks_run"] == ["numeric_conflict"]
