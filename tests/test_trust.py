from radar.schemas import ImpactAssessmentOutput
from radar.services.evidence_service import EvidenceService, resolve_exact_quote
from radar.services.trust_service import TrustService


def test_exact_evidence_required():
    output = ImpactAssessmentOutput.model_validate(
        {
            "stance":"challenges", "impact_mode":"boundary_condition", "comparability":"compatible",
            "condition_differences":[],
            "evidence_own":{"quote":"own exact", "locator":"own:1"},
            "evidence_new":{"quote":"invented", "locator":"new:1"},
            "change_depth":2, "suggested_action":"add_boundary_discussion",
            "uncertainty_sources":[],
        }
    )
    result = TrustService().verify_impact(output, "own exact", "different incoming")
    assert result.state == "blocked"
    assert "span_failed:new" in result.errors


def test_partial_conditions_block_directional_stance():
    output = ImpactAssessmentOutput.model_validate(
        {
            "stance": "challenges",
            "impact_mode": "boundary_condition",
            "comparability": "partial",
            "condition_differences": [],
            "evidence_own": {"quote": "own exact", "locator": "own:1"},
            "evidence_new": {"quote": "new exact", "locator": "new:1"},
            "change_depth": 2,
            "suggested_action": "add_boundary_discussion",
            "uncertainty_sources": [],
        }
    )

    result = TrustService().verify_impact(output, "own exact", "new exact")

    assert result.state == "blocked"
    assert "condition_not_compatible_for_directional_stance" in result.errors


def test_evidence_extraction_matches_long_claim_at_sentence_level():
    claim = (
        "Robustness scores do not always scale strictly with model size; some "
        "mid-sized generators outperform larger counterparts."
    )
    content = (
        "We introduce a benchmark for retrieval-augmented generation. "
        "Our results show that smaller generators can outperform larger models "
        "on robustness scores under document perturbations. "
        "The benchmark will be released publicly."
    )

    evidence = EvidenceService.extract_relevant_evidence(
        [claim], content, "snapshot-1"
    )

    assert evidence is not None
    assert evidence.quote in content
    assert evidence.quote.startswith("Our results show")
    assert evidence.locator == "sentence:2"


def test_evidence_extraction_rejects_unrelated_text():
    evidence = EvidenceService.extract_relevant_evidence(
        ["RAG robustness under retrieval perturbations"],
        "A recipe for banana bread uses ripe fruit and toasted walnuts.",
        "snapshot-2",
    )

    assert evidence is None


def test_evidence_extraction_handles_collapsed_abstract_sentences():
    content = (
        "RAG systems use dense retrieval models."
        "Existing robustness studies focus on ranking attacks."
        "Experiments show that sentence-level perturbations outperform prior attacks."
    )

    evidence = EvidenceService.extract_relevant_evidence(
        ["RAG systems are sensitive to sentence-level perturbations"],
        content,
        "snapshot-3",
    )

    assert evidence is not None
    assert evidence.quote in content
    assert evidence.quote == (
        "Experiments show that sentence-level perturbations outperform prior attacks."
    )


def test_pdf_wrapping_can_only_resolve_to_one_exact_span():
    content = "A unique hierar-\nchical router improves retrieval."
    resolved = EvidenceService.resolve_exact(
        {"quote": "A unique hierarchical router improves retrieval.", "locator": "model"},
        content,
    )

    assert resolved is not None
    assert resolved.quote == content
    assert resolved.quote in content


def test_ligature_quote_resolves_after_nfkc_normalization():
    """M22 regression: a pypdf-extracted ligature (ﬁ) must match the plain
    "fi" the LLM quotes, with offsets pointing at real source characters."""
    content = "Our study reports significant findings across all domains."
    quote = "signiﬁcant"  # ligature form

    resolved = resolve_exact_quote(quote, content)

    assert resolved is not None
    exact, start, end = resolved
    # The span comes from the source text (ASCII form) and covers the word.
    assert content[start:end] == "significant"
    assert "significant" in exact
    assert "significant" in resolve_exact_quote("significant", content)[0]


def test_hyphen_linebreak_and_soft_hyphen_quotes_resolve():
    """M22 regression: hyphen+newline, hyphen+space and soft-hyphen word
    breaks must not break verbatim matching."""
    content = "The model achieves state-of-the-art results on every benchmark."
    assert resolve_exact_quote("state-of-the-art", content) is not None
    assert resolve_exact_quote("state-of-\nthe-art", content) is not None
    assert resolve_exact_quote("state-of- the-art", content) is not None
    assert resolve_exact_quote("state-of­-the-art", content) is not None


def test_latex_inline_command_quote_resolves():
    """M22 regression: a quote spanning \\emph{...} must match the plain
    words the LLM quotes; \\cite{...} metadata is stripped, not prose."""
    content = (
        "We show that \\emph{RadarNet is robust} under unseen domains, "
        "as \\cite{radar2026} demonstrates."
    )
    assert resolve_exact_quote("RadarNet is robust under unseen domains", content) is not None
    # The citation key is dropped metadata — a quote containing it cannot match.
    assert resolve_exact_quote("as radar2026 demonstrates", content) is None


def test_missing_or_ambiguous_evidence_is_not_substituted():
    assert EvidenceService.resolve_exact(
        {"quote": "invented evidence", "locator": "model"}, "actual source text"
    ) is None
    assert EvidenceService.resolve_exact(
        {"quote": "same sentence", "locator": "model"},
        "same sentence\nother\nsame sentence",
    ) is None
