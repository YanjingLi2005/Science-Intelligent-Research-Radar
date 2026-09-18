import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from radar.adapters.arxiv import ArxivSearchAdapter
from radar.adapters.crossref import CrossrefIntegrityAdapter
from radar.config import Settings
from radar.llm.provider import ProviderLLMClient
from radar.models import (
    ActionItem,
    AuditEvent,
    ClaimSourceLink,
    ImpactCandidate,
    ModelRun,
    ScanRun,
    Source,
    SourceSnapshot,
)
from radar.services.action_service import ActionService
from radar.schemas import (
    ActionAdviceOutput,
    EvidenceSpan,
    ImpactAssessmentOutput,
    IncomingResult,
    SourceRecord,
)
from radar.services.weekly_radar_service import WeeklyRadarService
from radar.services.report_service import ReportService
from radar.services.review_service import ReviewService


INCOMING_QUOTE = (
    "On the DomainQA unseen-domain split, NewRAG reaches 70.1 exact match while "
    "RadarNet reaches 64.0 using the same BM25 baseline."
)
OWN_QUOTE = (
    "On DomainQA, RadarNet improves exact match from 61.2% to 68.7% over BM25 "
    "under unseen-domain evaluation."
)


class OnePaperSearch:
    def search(self, case_id, watch_query):
        return [
            SourceRecord(
                external_id="arxiv:test-live-1",
                title="A Matched Re-evaluation of RadarNet",
                authors=["Independent Lab"],
                abstract=INCOMING_QUOTE,
                url="https://arxiv.org/abs/test-live-1",
                published_at="2026-07-20T00:00:00Z",
                arxiv_id="test-live-1",
            )
        ]


class MultiQuerySearch(OnePaperSearch):
    def __init__(self):
        self.queries = []

    def search(self, case_id, watch_query):
        self.queries.append(watch_query.query)
        return super().search(case_id, watch_query)


class ScriptedLLM:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.stages = []
        self.prompts = []

    def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
        self.stages.append(stage)
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("injected_llm_failure")
        if stage == "incoming_result":
            return IncomingResult(
                task="open-domain QA",
                dataset="DomainQA",
                split="unseen-domain",
                metric="exact match",
                comparator="BM25",
                scope="RadarNet",
                direction="challenges",
            )
        if stage == "impact_assessment":
            return ImpactAssessmentOutput(
                stance="challenges",
                impact_mode="boundary_condition",
                comparability="compatible",
                condition_differences=[],
                evidence_own=EvidenceSpan(
                    quote=OWN_QUOTE,
                    locator="sec:main-results:p:1",
                ),
                evidence_new=EvidenceSpan(
                    quote=INCOMING_QUOTE,
                    locator="paragraph:1",
                ),
                change_depth=3,
                suggested_action="narrow_claim",
                uncertainty_sources=[],
            )
        if stage == "action_advice":
            return ActionAdviceOutput(
                category="experiment",
                title="复现 NewRAG 对照并定位 RadarNet 差距",
                rationale=(
                    "NewRAG 在 DomainQA unseen-domain split 上报告 70.1 exact match，"
                    "高于同一 BM25 baseline 下 RadarNet 的 64.0；该结果与你的 C1 直接可比，"
                    "建议复现该对照并分析差距来源。"
                ),
                checklist=[
                    "冻结双方 task/dataset/metric/comparator",
                    "复现 NewRAG 报告的关键数字",
                    "在你的实现上运行相同设置",
                ],
            )
        raise AssertionError(f"unexpected stage: {stage}")


class NoChangeLLM(ScriptedLLM):
    def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
        if stage == "incoming_result":
            self.stages.append(stage)
            self.prompts.append(prompt)
            return IncomingResult(
                task="open-domain QA",
                dataset="DomainQA",
                split="unseen-domain",
                metric="exact match",
                comparator="BM25",
                scope="RadarNet",
                direction="neutral",
            )
        if stage == "impact_assessment":
            self.stages.append(stage)
            self.prompts.append(prompt)
            return ImpactAssessmentOutput(
                stance="neutral",
                impact_mode="no_material_change",
                comparability="compatible",
                condition_differences=[],
                evidence_own=EvidenceSpan(
                    quote=OWN_QUOTE,
                    locator="sec:main-results:p:1",
                ),
                evidence_new=EvidenceSpan(
                    quote=INCOMING_QUOTE,
                    locator="paragraph:1",
                ),
                change_depth=0,
                suggested_action="no_action",
                uncertainty_sources=["No result changes the confirmed claim."],
            )
        raise AssertionError(f"unexpected stage: {stage}")


def _settings(**overrides):
    return Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-test-key-000000000000",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
        **overrides,
    )


class FlaggedIntegrityAdapter:
    """CrossrefIntegrityAdapter stand-in flagging chosen DOIs as retracted."""

    def __init__(self, flagged_dois):
        self.flagged_dois = set(flagged_dois)
        self.checked = []

    def check(self, doi):
        self.checked.append(doi)
        state = "retracted" if doi in self.flagged_dois else "normal"
        return {"doi": doi, "integrity_state": state, "updates": [], "relation": {}}


class DoiPaperSearch:
    def search(self, case_id, watch_query):
        return [
            SourceRecord(
                external_id="arxiv:test-live-doi",
                title="A Matched Re-evaluation of RadarNet",
                authors=["Independent Lab"],
                abstract=INCOMING_QUOTE,
                url="https://arxiv.org/abs/test-live-doi",
                published_at="2026-07-20T00:00:00Z",
                doi="10.5555/retracted.incoming",
                arxiv_id="test-live-doi",
            )
        ]


def _add_doi_backed_support(db_session_factory, source_id, doi):
    """Attach a DOI-backed source to a confirmed claim as supporting evidence."""
    with db_session_factory() as session:
        source = Source(
            id=source_id,
            external_id=f"doi:{doi}",
            title="DOI-backed supporting study",
            authors_json=["External Lab"],
            url=f"https://doi.org/{doi}",
            doi=doi,
            integrity_state="normal",
        )
        session.add(source)
        session.flush()
        session.add(
            SourceSnapshot(
                id=f"{source_id}-snapshot",
                source_id=source_id,
                version_label="v1",
                title=source.title,
                abstract="Supporting evidence abstract.",
                content_text="Supporting evidence abstract.",
                content_hash=f"hash-{source_id}",
            )
        )
        session.add(
            ClaimSourceLink(
                id=f"{source_id}-link",
                claim_revision_id="claim-rev-01",
                source_id=source_id,
                relation_type="cited_by_claim",
                source_locator="sec:evaluation:p:1",
                review_state="confirmed",
            )
        )
        session.commit()


def test_live_weekly_scan_creates_verified_candidate(
    db_session_factory, golden_case
):
    llm = ScriptedLLM()
    scan_id = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=llm,
        settings=_settings(),
    ).run(
        golden_case,
        query="DomainQA RadarNet exact match",
        max_results=5,
        analysis_limit=1,
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        if scan.status == "failed":
            raise AssertionError(f"SCAN FAILED: {scan.error_message}")
        impact = session.scalar(
            select(ImpactCandidate).where(ImpactCandidate.scan_run_id == scan_id)
        )
        runs = session.scalar(
            select(func.count(ModelRun.id)).where(
                ModelRun.input_refs_json.is_not(None),
                ModelRun.provider == "deepseek",
            )
        )

        assert scan.status == "completed"
        assert scan.stats_json["scanned_papers"] == 1
        assert scan.stats_json["impact_candidates"] == 1
        assert impact.review_state == "candidate"
        assert impact.trust_state == "verified"
        assert impact.stance == "challenges"
        assert impact.severity == "critical"
        assert impact.evidence_new_json["quote"] == INCOMING_QUOTE
        assert runs == 3
    # HyDE, reference extraction and the LLM rerank are attempted first and
    # degrade silently: the scripted double only implements the analysis
    # stages. The evidence-fidelity judge runs after assessment but degrades
    # (its stage is recorded, the verdict is never produced).
    assert llm.stages == [
        "hyde_abstract",
        "reference_extraction",
        "retrieval_rerank",
        "incoming_result",
        "impact_assessment",
        "evidence_fidelity",
        "action_advice",
    ]
    assert '"incoming_abstract"' in llm.prompts[3]


def test_failed_scan_preserves_real_counts(db_session_factory, golden_case, monkeypatch):
    """C7 regression: a failure after assessment must not zero the stats."""
    import radar.services.weekly_radar_service as wrs_module

    service = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=ScriptedLLM(),
        settings=_settings(),
    )
    original_finish = WeeklyRadarService._finish_scan
    calls = {"count": 0}

    def flaky_finish(self, scan_id, *, status, stats, error_message):
        calls["count"] += 1
        if calls["count"] == 1 and status == "completed":
            raise RuntimeError("post-completion bookkeeping failure")
        return original_finish(self, scan_id, status=status, stats=stats, error_message=error_message)

    monkeypatch.setattr(wrs_module.WeeklyRadarService, "_finish_scan", flaky_finish)

    with pytest.raises(RuntimeError, match="bookkeeping"):
        service.run(
            golden_case, query="DomainQA RadarNet exact match",
            max_results=5, analysis_limit=1,
        )

    with db_session_factory() as session:
        scan = session.scalar(
            select(ScanRun).where(ScanRun.case_id == golden_case).order_by(ScanRun.created_at.desc())
        )
        assert scan.status == "failed"
        # Real counts survive the failure branch instead of being zeroed.
        assert scan.stats_json["scanned_papers"] == 1
        assert scan.stats_json["impact_candidates"] == 1


def test_action_advice_failure_keeps_completed_scan(db_session_factory, golden_case, monkeypatch):
    """C7 regression: advice/action-sync failures after completion must not
    flip the completed scan to failed or clobber its stats."""

    def boom(self, scan_id, *, advice_by_impact=None):
        raise RuntimeError("action sync exploded")

    monkeypatch.setattr(ActionService, "sync_scan_actions", boom)

    scan_id = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=ScriptedLLM(),
        settings=_settings(),
    ).run(
        golden_case, query="DomainQA RadarNet exact match",
        max_results=5, analysis_limit=1,
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "completed"
        assert scan.stats_json["scanned_papers"] == 1
        assert scan.stats_json["impact_candidates"] == 1
        audit = session.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "weekly_scan_action_advice_failed"
            )
        )
        assert audit is not None
        assert audit.payload_json["error"] == "action sync exploded"


def test_live_weekly_scan_records_llm_failure(db_session_factory, golden_case):
    scan_id = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=ScriptedLLM(fail=True),
        settings=_settings(),
    ).run(
        golden_case,
        query="DomainQA RadarNet exact match",
        max_results=5,
        analysis_limit=1,
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        count = session.scalar(
            select(func.count(ImpactCandidate.id)).where(
                ImpactCandidate.scan_run_id == scan_id
            )
        )
        assert scan.status == "failed"
        assert scan.stats_json["failed_pairs"] == 1
        assert "failed" in scan.error_message
        assert count == 0


def test_misconfigured_embeddings_degrade_scan_instead_of_failing(
    db_session_factory, golden_case
):
    """M15 regression: partial embedding config (provider set, base URL
    missing) must not fail every scan at construction — it degrades to
    lexical retrieval with the reason recorded in the scan stats."""
    service = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=ScriptedLLM(),
        settings=_settings(embedding_provider="ollama"),
    )
    assert service.embedding_misconfigured == "embedding_not_configured:ollama"

    scan_id = service.run(
        golden_case, query="DomainQA RadarNet exact match",
        max_results=5, analysis_limit=1,
    )
    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "completed", scan.error_message
        assert scan.stats_json["embedding_misconfigured"] == (
            "embedding_not_configured:ollama"
        )
        assert scan.stats_json["impact_candidates"] == 1


class InventedEvidenceLLM(ScriptedLLM):
    """ScriptedLLM whose incoming evidence quote is NOT in the snapshot text,
    forcing the span gate to block the pair (P0 fidelity measurement)."""

    def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
        if stage == "impact_assessment":
            self.stages.append(stage)
            self.prompts.append(prompt)
            return ImpactAssessmentOutput(
                stance="challenges",
                impact_mode="boundary_condition",
                comparability="compatible",
                condition_differences=[],
                evidence_own=EvidenceSpan(quote=OWN_QUOTE, locator="sec:main-results:p:1"),
                evidence_new=EvidenceSpan(
                    quote="A completely invented sentence not in the incoming paper.",
                    locator="paragraph:1",
                ),
                change_depth=3,
                suggested_action="narrow_claim",
                uncertainty_sources=[],
            )
        return super().generate_structured(
            stage=stage, prompt=prompt, response_model=response_model
        )


class FidelityJudgeLLM(ScriptedLLM):
    """ScriptedLLM that also answers the evidence_fidelity judge stage."""

    def __init__(self, *, faithful=True, **kwargs):
        super().__init__(**kwargs)
        self.faithful = faithful

    def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
        if stage == "evidence_fidelity":
            from radar.schemas import EvidenceFidelityVerdict

            self.stages.append(stage)
            self.prompts.append(prompt)
            return EvidenceFidelityVerdict(
                faithful=self.faithful,
                issues=[] if self.faithful else ["Evidence quote does not support the stance"],
                reason="judge reason",
            )
        return super().generate_structured(
            stage=stage, prompt=prompt, response_model=response_model
        )


def test_scan_records_evidence_fidelity_stats_for_span_failures(
    db_session_factory, golden_case
):
    """P0: pairs blocked at the verbatim-span gate must be measured in the
    scan's fidelity stats — the 'extracted information accuracy' loop."""
    scan_id = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=InventedEvidenceLLM(),
        settings=_settings(),
    ).run(
        golden_case, query="DomainQA RadarNet exact match",
        max_results=5, analysis_limit=1,
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "completed"
        fidelity = scan.stats_json["fidelity"]
        assert fidelity["pairs_assessed"] == 1
        assert fidelity["span_failed"] == 1
        assert fidelity["verified"] == 0
        assert fidelity["evidence_pass_rate"] == 0.0


def test_scan_fidelity_judge_marks_faithful_impact_and_records_run(
    db_session_factory, golden_case
):
    """P0: verified directional impacts are sampled by the evidence-fidelity
    judge; a faithful verdict records a ModelRun and scan stats."""
    llm = FidelityJudgeLLM(faithful=True)
    scan_id = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=llm,
        settings=_settings(),
    ).run(
        golden_case, query="DomainQA RadarNet exact match",
        max_results=5, analysis_limit=1,
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "completed"
        fidelity = scan.stats_json["fidelity"]
        assert fidelity["verified"] == 1
        assert fidelity["judged"] == 1
        assert fidelity["faithful"] == 1
        assert fidelity["faithfulness_rate"] == 1.0
        run = session.scalar(
            select(ModelRun).where(ModelRun.stage == "evidence_fidelity")
        )
        assert run is not None
        assert run.parsed_output_json["faithful"] is True
    assert "evidence_fidelity" in llm.stages


def test_scan_fidelity_judge_audits_unfaithful_impact(
    db_session_factory, golden_case
):
    """P0: an unfaithful verdict must leave an audit trail without altering
    the impact itself."""
    scan_id = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=FidelityJudgeLLM(faithful=False),
        settings=_settings(),
    ).run(
        golden_case, query="DomainQA RadarNet exact match",
        max_results=5, analysis_limit=1,
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        fidelity = scan.stats_json["fidelity"]
        assert fidelity["judged"] == 1
        assert fidelity["unfaithful"] == 1
        audit = session.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "evidence_fidelity_unfaithful"
            )
        )
        assert audit is not None
        assert audit.payload_json["issues"]
        # the impact itself is untouched by the judge
        impact = session.scalar(
            select(ImpactCandidate).where(ImpactCandidate.scan_run_id == scan_id)
        )
        assert impact.review_state == "candidate"
        assert impact.trust_state == "verified"


class ScriptedNLI:
    """NLI double returning a fixed verdict for every pair."""

    def __init__(self, label):
        self.label = label
        self.calls = []

    def check(self, claim, evidence):
        self.calls.append((claim, evidence))
        if self.label == "unavailable":
            return None
        return {
            "label": self.label,
            "scores": {"contradiction": 0.1, "entailment": 0.1, "neutral": 0.1},
        }


def _run_scan_with_nli(db_session_factory, golden_case, nli_label):
    service = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=ScriptedLLM(),  # returns stance="challenges"
        settings=_settings(),
        nli_checker=ScriptedNLI(nli_label),
    )
    scan_id = service.run(
        golden_case, query="DomainQA RadarNet exact match",
        max_results=5, analysis_limit=1,
    )
    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        impact = session.scalar(
            select(ImpactCandidate).where(ImpactCandidate.scan_run_id == scan_id)
        )
        return scan, impact


def test_nli_agreement_keeps_llm_stance(db_session_factory, golden_case):
    """P2: NLI contradiction agrees with the LLM's challenges — stance kept."""
    scan, impact = _run_scan_with_nli(db_session_factory, golden_case, "contradiction")
    assert scan.status == "completed"
    assert impact.stance == "challenges"
    assert impact.trust_state == "verified"
    assert scan.stats_json["fidelity"]["nli_checked"] == 1
    assert scan.stats_json["fidelity"]["nli_disagreement"] == 0


def test_nli_disagreement_downgrades_stance_to_uncertain(
    db_session_factory, golden_case
):
    """P2: NLI entailment vs LLM challenges is a direct disagreement — the
    stance is downgraded to uncertain and audited (hallucination guard)."""
    scan, impact = _run_scan_with_nli(db_session_factory, golden_case, "entailment")
    assert impact.stance == "uncertain"
    assert "nli:disagreement(entailment)" in impact.uncertainty_json
    assert scan.stats_json["fidelity"]["nli_disagreement"] == 1
    with db_session_factory() as session:
        audit = session.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "nli_stance_disagreement"
            )
        )
        assert audit is not None
        assert audit.payload_json["llm_stance"] == "challenges"
        assert audit.payload_json["nli_label"] == "entailment"


def test_nli_neutral_flags_weak_evidence_but_keeps_stance(
    db_session_factory, golden_case
):
    """P2: neutral evidence is a weak-signal flag, not a contradiction —
    the LLM stance stands with an uncertainty marker."""
    scan, impact = _run_scan_with_nli(db_session_factory, golden_case, "neutral")
    assert impact.stance == "challenges"
    assert "nli:neutral_evidence" in impact.uncertainty_json
    assert scan.stats_json["fidelity"]["nli_neutral"] == 1


def test_nli_unavailable_leaves_llm_judgment_untouched(
    db_session_factory, golden_case
):
    """P2: model unavailable (not downloaded / no network) — no-op, counted."""
    scan, impact = _run_scan_with_nli(db_session_factory, golden_case, "unavailable")
    assert impact.stance == "challenges"
    assert scan.stats_json["fidelity"]["nli_unavailable"] == 1
    assert scan.stats_json["fidelity"]["nli_checked"] == 0


def test_crossref_classifies_updates_by_type(monkeypatch):
    """M21 regression: integrity state must come from the update entry's
    TYPE — substring search misflagged DOIs containing "correct" and missed
    withdrawal/erratum entirely."""
    adapter = CrossrefIntegrityAdapter()

    def fake_message(doi):
        return {
            "update-to": [{"type": "retraction", "DOI": "10.1000/x"}],
            "relation": {},
        }

    monkeypatch.setattr(adapter, "_message", fake_message)
    assert adapter.check("10.1000/x")["integrity_state"] == "retracted"

    def withdrawal_message(doi):
        return {
            "updated-by": [{"type": "withdrawal", "DOI": "10.1000/y"}],
            "relation": {},
        }

    monkeypatch.setattr(adapter, "_message", withdrawal_message)
    assert adapter.check("10.1000/y")["integrity_state"] == "retracted"

    def erratum_message(doi):
        return {"update-to": [{"type": "erratum"}], "relation": {}}

    monkeypatch.setattr(adapter, "_message", erratum_message)
    assert adapter.check("10.1000/z")["integrity_state"] == "corrected"

    def harmless_message(doi):
        # "correct" appears only inside a DOI string — must stay normal.
        return {"update-to": [{"type": "new-edition", "DOI": "10.1000/correct-x"}], "relation": {}}

    monkeypatch.setattr(adapter, "_message", harmless_message)
    assert adapter.check("10.1000/correct-x")["integrity_state"] == "normal"


def test_same_paper_from_arxiv_and_openalex_is_deduped_by_title(
    db_session_factory, golden_case
):
    """M13 regression: a paper found by arXiv (no DOI yet) and by OpenAlex
    (DOI assigned) must produce ONE snapshot, not two assessments."""
    title = "A Shared Paper Between Two Sources"
    abstract = (
        "On the DomainQA unseen-domain split, NewRAG reaches 70.1 exact match "
        "while RadarNet reaches 64.0 using the same BM25 baseline."
    )
    arxiv_record = SourceRecord(
        external_id="arxiv:shared-paper",
        title=title,
        authors=["Independent Lab"],
        abstract=abstract,
        url="https://arxiv.org/abs/shared-paper",
        published_at="2026-07-20T00:00:00Z",
        arxiv_id="shared-paper",
    )
    openalex_record = SourceRecord(
        external_id="openalex:Wshared",
        title=title,  # same paper, same title
        authors=["Independent Lab"],
        abstract=abstract,
        url="https://openalex.org/Wshared",
        published_at="2026-07-20T00:00:00Z",
        doi="10.5555/shared.paper",
    )

    class TwoSourceSearch:
        def __init__(self):
            self.queries = []

        def search(self, case_id, watch_query):
            self.queries.append(watch_query.query)
            return [arxiv_record, openalex_record]

    scan_id = WeeklyRadarService(
        db_session_factory,
        search_adapter=TwoSourceSearch(),
        llm_client=ScriptedLLM(),
        settings=_settings(),
    ).run(
        golden_case, query="DomainQA RadarNet exact match",
        max_results=5, analysis_limit=1,
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        if scan.status == "failed":
            raise AssertionError(f"SCAN FAILED: {scan.error_message}")
        assert scan.status == "completed"
        assert scan.stats_json["scanned_papers"] == 1
        assert scan.stats_json["impact_candidates"] == 1
        sources = list(
            session.scalars(
                select(Source).where(Source.external_id.in_(
                    ["arxiv:shared-paper", "openalex:Wshared"]
                ))
            )
        )
        assert len(sources) == 1
        assert sources[0].doi == "10.5555/shared.paper"  # DOI-bearing record wins


def test_scan_flags_retracted_supporting_source_and_creates_integrity_impact(
    db_session_factory, golden_case
):
    _add_doi_backed_support(
        db_session_factory, "source-retracted-support", "10.9999/retracted.support"
    )
    integrity = FlaggedIntegrityAdapter({"10.9999/retracted.support"})

    scan_id = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=ScriptedLLM(),
        settings=_settings(),
        integrity_adapter=integrity,
    ).run(
        golden_case,
        query="DomainQA RadarNet exact match",
        max_results=5,
        analysis_limit=1,
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        source = session.get(Source, "source-retracted-support")
        impact = session.scalar(
            select(ImpactCandidate).where(
                ImpactCandidate.scan_run_id == scan_id,
                ImpactCandidate.impact_mode == "research_integrity",
            )
        )
        actions = list(
            session.scalars(
                select(ActionItem).where(ActionItem.impact_candidate_id == impact.id)
            )
        )
        assert scan.status == "completed"
        # The confirmed claim's supporting DOI and the golden case retracted
        # DOI are both checked; only the new support is flagged now.
        assert scan.stats_json["integrity_checked"] == 2
        assert scan.stats_json["integrity_flagged"] == 1
        assert scan.stats_json["integrity_failures"] == 0
        assert source.integrity_state == "retracted"
        assert impact is not None
        assert impact.event_type == "retraction"
        assert impact.claim_revision_id == "claim-rev-01"
        assert impact.severity == "critical"
        # One open action per (claim, type): the integrity impact owns the new
        # revalidation action and the shared claim-level writing action.
        claim_actions = list(
            session.scalars(
                select(ActionItem).where(
                    ActionItem.claim_revision_id == "claim-rev-01",
                    ActionItem.scan_run_id == scan_id,
                )
            )
        )
        assert {action.action_type for action in claim_actions} >= {
            "revalidation",
            "writing",
        }
        assert {action.action_type for action in actions} == {
            "revalidation",
            "writing",
        }
    assert set(integrity.checked) == {
        "10.9999/retracted.support",
        "10.0000/cleaneval.retraction",
    }


def test_scan_flags_retracted_incoming_paper_without_forcing_impact(
    db_session_factory, golden_case
):
    integrity = FlaggedIntegrityAdapter({"10.5555/retracted.incoming"})

    scan_id = WeeklyRadarService(
        db_session_factory,
        search_adapter=DoiPaperSearch(),
        llm_client=ScriptedLLM(),
        settings=_settings(),
        integrity_adapter=integrity,
    ).run(
        golden_case,
        query="DomainQA RadarNet exact match",
        max_results=5,
        analysis_limit=1,
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        source = session.scalar(
            select(Source).where(Source.doi == "10.5555/retracted.incoming")
        )
        integrity_impacts = list(
            session.scalars(
                select(ImpactCandidate).where(
                    ImpactCandidate.scan_run_id == scan_id,
                    ImpactCandidate.impact_mode == "research_integrity",
                )
            )
        )
        assert scan.status == "completed"
        assert scan.stats_json["integrity_checked"] == 2
        assert scan.stats_json["integrity_flagged"] == 1
        assert scan.stats_json["integrity_alerts"] >= 1
        assert source.integrity_state == "retracted"
        # M14: a retracted paper the scan found is user-visible — a
        # paper_integrity alert attached to confirmed claims, not just a stat.
        assert integrity_impacts
        assert all(item.event_type == "paper_integrity" for item in integrity_impacts)
        assert all(item.review_state == "candidate" for item in integrity_impacts)
        # ... and the retracted paper was not LLM-assessed as a normal pair.
        assert scan.stats_json["impact_candidates"] == 0


def test_auto_scan_uses_multiple_manuscript_topics_and_deduplicates_sources(
    db_session_factory, golden_case
):
    search = MultiQuerySearch()
    service = WeeklyRadarService(
        db_session_factory,
        search_adapter=search,
        llm_client=ScriptedLLM(),
        settings=_settings(),
    )
    service.suggested_queries = lambda case_id: [
        "retrieval robustness evaluation",
        "unseen domain retrieval",
    ]

    scan_id = service.run_auto(golden_case, max_results=10, analysis_limit=1)

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.mode == "auto_public_paper_radar"
        assert scan.stats_json["scanned_papers"] == 1
        assert scan.stats_json["search_queries"] == search.queries
        assert scan.stats_json["newest_publication"].startswith("2026-07-20")
        assert scan.stats_json["full_text_papers"] == 0
        assert scan.stats_json["analysis_context"] == "full_manuscript_and_public_paper"
        assert len(scan.stats_json["routed_source_snapshot_ids"]) == 1
    assert search.queries == [
        "retrieval robustness evaluation",
        "unseen domain retrieval",
    ]


def test_remote_demo_selects_deepseek_and_sends_full_comparison_context(
    db_session_factory, golden_case
):
    llm = ScriptedLLM()
    service = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=llm,
        settings=_settings(),
    )
    service.run(
        golden_case,
        query="DomainQA RadarNet exact match",
        max_results=5,
        analysis_limit=1,
    )

    # prompts[0..2] are the degradable enhancement stages (HyDE, reference
    # extraction, LLM rerank); the comparison context lives in [3] and [4].
    assert '"own_full_manuscript_text"' not in llm.prompts[3]
    assert OWN_QUOTE not in llm.prompts[3]
    assert INCOMING_QUOTE in llm.prompts[3]
    assert '"own_full_manuscript_text"' in llm.prompts[4]
    assert '"incoming_public_paper_text"' in llm.prompts[4]


def test_scan_progress_callback_reports_completion(db_session_factory, golden_case):
    events = []
    WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=ScriptedLLM(),
        settings=_settings(),
    ).run(
        golden_case,
        query="DomainQA RadarNet exact match",
        max_results=5,
        analysis_limit=1,
        progress_callback=lambda value, message: events.append((value, message)),
    )

    assert events[0][0] < events[-1][0]
    assert events[-1][0] == 1.0
    assert any("1/1" in message for _, message in events)


def test_no_material_comparison_is_saved_and_can_be_promoted_to_writing_action(
    db_session_factory, golden_case
):
    scan_id = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=NoChangeLLM(),
        settings=_settings(),
    ).run(
        golden_case,
        query="DomainQA RadarNet exact match",
        max_results=5,
        analysis_limit=1,
    )

    with db_session_factory() as session:
        impact = session.scalar(
            select(ImpactCandidate).where(ImpactCandidate.scan_run_id == scan_id)
        )
        assert impact is not None
        assert impact.review_state == "informative"
        assert impact.impact_mode == "no_material_change"

    summary = ReportService(db_session_factory).get_weekly_summary(scan_id)
    assert summary["related_papers"] == 0

    ReviewService(db_session_factory).edit_impact(
        impact.id,
        {"impact_mode": "prior_art", "suggested_action": "cite"},
        "Relevant positioning work.",
    )
    with db_session_factory() as session:
        writing_action = session.scalar(
            select(ActionItem).where(
                ActionItem.impact_candidate_id == impact.id,
                ActionItem.action_type == "writing",
            )
        )
        assert writing_action is not None
        assert writing_action.status == "open"


def test_remote_demo_constructs_provider_client(db_session_factory):
    service = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        settings=_settings(),
    )
    assert isinstance(service.llm_client, ProviderLLMClient)
    assert service._remote_full_context_enabled() is True


def test_pair_ranking_prefers_distinct_papers_without_forcing_claim_coverage():
    claims = [SimpleNamespace(id=f"claim-{index}") for index in range(1, 6)]

    class DiverseRetrieval:
        def rank_sources(self, query, top_k, snapshot_ids):
            return [
                (f"source-{index}", 1.1 - 0.1 * index, "ranked")
                for index in range(1, 6)
            ]

        def route_claims(self, snapshot_id, claim_revisions, top_k, **kwargs):
            source_index = int(snapshot_id.rsplit("-", 1)[-1])
            return [
                (claim.id, 1.0 - 0.05 * index - 0.01 * source_index, "route")
                for index, claim in enumerate(claim_revisions)
            ][:top_k]

    service = object.__new__(WeeklyRadarService)
    service.retrieval = DiverseRetrieval()
    pairs = service._rank_pairs(
        [f"source-{index}" for index in range(1, 6)],
        # More candidate papers than the analysis budget.
        claims,
        5,
        query="RAG robustness",
    )

    assert len(pairs) == 5
    assert len({snapshot_id for snapshot_id, _, _ in pairs}) == 5


def test_own_paper_title_is_excluded_from_incoming_work():
    own_titles = [
        "RARE: Retrieval-Aware Robustness Evaluation for Retrieval-Augmented Generation Systems"
    ]
    assert WeeklyRadarService._is_own_work(own_titles[0], own_titles)
    assert not WeeklyRadarService._is_own_work(
        "Salience Induction against Multi-Hop RAG Agents", own_titles
    )


def test_crossref_enrich_failures_are_counted_in_scan_stats(
    db_session_factory, golden_case, monkeypatch
):
    def fail_metadata(self, doi):
        raise RuntimeError("crossref_check_failed: injected")

    monkeypatch.setattr(CrossrefIntegrityAdapter, "metadata", fail_metadata)

    scan_id = WeeklyRadarService(
        db_session_factory,
        search_adapter=DoiPaperSearch(),
        llm_client=ScriptedLLM(),
        settings=_settings(),
    ).run(
        golden_case,
        query="DomainQA RadarNet exact match",
        max_results=5,
        analysis_limit=1,
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "completed"
        assert scan.stats_json["crossref_enrich_failures"] == 1


def test_public_pdf_enrichment_appends_version_label_once(
    db_session_factory, golden_case, tmp_path, monkeypatch
):
    adapter = ArxivSearchAdapter(tmp_path / "arxiv-cache")
    full_text = "Full public PDF text. " * 200
    monkeypatch.setattr(
        adapter, "fetch_full_text", lambda arxiv_id: full_text
    )
    service = WeeklyRadarService(
        db_session_factory,
        search_adapter=adapter,
        llm_client=ScriptedLLM(),
        settings=_settings(),
    )
    with db_session_factory() as session:
        session.add(
            Source(
                id="source-pdf",
                external_id="arxiv:pdf-1",
                title="PDF paper",
                authors_json=["PDF Lab"],
                url="https://arxiv.org/abs/pdf-1",
                arxiv_id="pdf-1",
                integrity_state="normal",
            )
        )
        session.flush()
        session.add(
            SourceSnapshot(
                id="snapshot-pdf",
                source_id="source-pdf",
                version_label="2026-07-01",
                title="PDF paper",
                abstract="Short abstract.",
                content_text="Short abstract.",
                content_hash="hash-pdf",
            )
        )
        session.commit()

    stats = service._enrich_public_full_text(["snapshot-pdf"])

    assert stats == {"full_text_papers": 1, "full_text_failures": 0}
    with db_session_factory() as session:
        snapshot = session.get(SourceSnapshot, "snapshot-pdf")
        assert snapshot.version_label == "2026-07-01:public-pdf"
        assert snapshot.content_text == full_text
        # content_hash is the immutable abstract identity; the full-text hash
        # goes to its own column (C2: snapshot id must not churn across scans).
        assert snapshot.content_hash == "hash-pdf"
        assert snapshot.full_text_hash == hashlib.sha256(full_text.encode()).hexdigest()

    # Second pass: already enriched and no fuller cache exists, so the
    # snapshot is skipped without appending the label again.
    service._enrich_public_full_text(["snapshot-pdf"])
    with db_session_factory() as session:
        snapshot = session.get(SourceSnapshot, "snapshot-pdf")
        assert snapshot.version_label == "2026-07-01:public-pdf"
        assert snapshot.content_text == full_text

    # A fuller cached parse than the snapshot holds triggers re-enrichment
    # (legacy truncated write), still without duplicating the label.
    fuller_text = full_text + " Extra cached section."
    cache_path = adapter.cache_dir / "full_text" / "pdf-1.txt"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(fuller_text, encoding="utf-8")
    monkeypatch.setattr(
        adapter, "fetch_full_text", lambda arxiv_id: fuller_text
    )
    service._enrich_public_full_text(["snapshot-pdf"])
    with db_session_factory() as session:
        snapshot = session.get(SourceSnapshot, "snapshot-pdf")
        assert snapshot.version_label == "2026-07-01:public-pdf"
        assert snapshot.content_text == fuller_text


class AdviceFailLLM(ScriptedLLM):
    """Analysis succeeds but the action-advice stage is unavailable."""

    def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
        if stage == "action_advice":
            self.stages.append(stage)
            self.prompts.append(prompt)
            raise RuntimeError("advice_llm_failure")
        return super().generate_structured(
            stage=stage, prompt=prompt, response_model=response_model
        )


def test_scan_generates_llm_action_advice(db_session_factory, golden_case):
    scan_id = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=ScriptedLLM(),
        settings=_settings(),
    ).run(
        golden_case,
        query="DomainQA RadarNet exact match",
        max_results=5,
        analysis_limit=1,
    )

    with db_session_factory() as session:
        actions = list(
            session.scalars(
                select(ActionItem).where(ActionItem.scan_run_id == scan_id)
            )
        )
        advice_run = session.scalar(
            select(ModelRun).where(
                ModelRun.stage == "action_advice",
                ModelRun.scan_run_id == scan_id,
            )
        )
    llm_actions = [action for action in actions if action.advice_source == "llm"]
    assert any(
        action.action_type == "experiment"
        and action.title == "复现 NewRAG 对照并定位 RadarNet 差距"
        and "70.1" in action.rationale
        for action in llm_actions
    )
    assert advice_run is not None
    assert advice_run.case_id == golden_case
    assert advice_run.parsed_output_json["category"] == "experiment"


def test_scan_falls_back_to_rule_templates_when_advice_fails(
    db_session_factory, golden_case
):
    scan_id = WeeklyRadarService(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=AdviceFailLLM(),
        settings=_settings(),
    ).run(
        golden_case,
        query="DomainQA RadarNet exact match",
        max_results=5,
        analysis_limit=1,
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        actions = list(
            session.scalars(
                select(ActionItem).where(ActionItem.scan_run_id == scan_id)
            )
        )
    assert scan.status == "completed"
    assert actions
    assert all(action.advice_source == "rule" for action in actions)
    assert any("复现反向结果并做条件匹配实验" in action.title for action in actions)
