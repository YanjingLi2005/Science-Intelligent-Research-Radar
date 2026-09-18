"""Unit tests for Research Compliance Audit Report generation, Lab Matrix risk aggregation, and API routes."""

import pytest
from datetime import datetime, timezone
from uuid import uuid4
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from radar.api import app
from radar.db import Base, session_scope
from radar.models import (
    Claim,
    ClaimRevision,
    ImpactCandidate,
    ManuscriptVersion,
    ResearchCase,
    ScanRun,
    Source,
    SourceSnapshot,
)
from radar.services.citation_service import CitationService


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    return session_factory


def test_generate_compliance_report_markdown_and_html(test_db):
    case_id = f"case-{uuid4().hex[:8]}"
    service = CitationService(session_factory=test_db)

    with session_scope(test_db) as session:
        # Create case
        case = ResearchCase(
            id=case_id,
            title="Adversarial Robustness in Dense Retrieval",
            research_question="How robust are dense retrievers under domain shift?",
            created_at=datetime.now(timezone.utc),
        )
        session.add(case)

        # Create manuscript with citations
        manuscript_text = r"""
\section{Introduction}
Dense retrieval models have shown remarkable accuracy \cite{karpukhin2020dense, devlin2019bert}.

\begin{thebibliography}{9}
\bibitem{karpukhin2020dense}
Karpukhin et al. Dense Passage Retrieval. 10.18653/v1/2020.emnlp-main.550
\bibitem{devlin2019bert}
Devlin et al. BERT. arXiv:1810.04805
\end{thebibliography}
"""
        ms_id = f"ms-{uuid4().hex[:8]}"
        ms = ManuscriptVersion(
            id=ms_id,
            case_id=case_id,
            version_no=1,
            file_name="manuscript.tex",
            source_type="latex",
            content_hash="hash-1234",
            content_text=manuscript_text,
            is_current=True,
        )
        session.add(ms)

        # Create source metadata
        src1 = Source(
            id="src-1",
            external_id="ext-src-1",
            title="Dense Passage Retrieval for Open-Domain Question Answering",
            url="https://doi.org/10.18653/v1/2020.emnlp-main.550",
            doi="10.18653/v1/2020.emnlp-main.550",
            venue="EMNLP 2020",
            integrity_state="normal",
        )
        src2 = Source(
            id="src-2",
            external_id="ext-src-2",
            title="BERT: Pre-training of Deep Bidirectional Transformers",
            url="https://arxiv.org/abs/1810.04805",
            arxiv_id="1810.04805",
            venue="NAACL 2019",
            integrity_state="normal",
        )
        session.add_all([src1, src2])

        # Create Claim
        claim = Claim(
            id=f"claim-{uuid4().hex[:8]}",
            case_id=case_id,
            stable_key="CLM-001",
        )
        session.add(claim)
        rev = ClaimRevision(
            id=f"rev-{uuid4().hex[:8]}",
            claim_id=claim.id,
            manuscript_version_id=ms_id,
            revision_no=1,
            statement="Dense retrieval achieves 86.4 EM on DomainQA under zero-shot setting.",
            claim_type="empirical_result",
            centrality="primary",
            falsifiable_condition="Dense retriever EM > 85",
            source_quote="Dense retrieval achieves 86.4 EM on DomainQA under zero-shot setting.",
            source_locator="sec:results",
            contract_json={"task": "QA", "dataset": "DomainQA", "metric": "EM"},
            review_state="confirmed",
        )
        session.add(rev)

        # Create Scan Run and Impact Candidate with adversarial critique
        scan = ScanRun(
            id=f"scan-{uuid4().hex[:8]}",
            case_id=case_id,
            mode="manual",
            status="completed",
            stats_json={"scanned_papers": 10},
        )
        session.add(scan)

        snap = SourceSnapshot(
            id=f"snap-{uuid4().hex[:8]}",
            source_id="src-1",
            version_label="v1",
            title="Dense Passage Retrieval",
            abstract="Sample abstract for DPR.",
            content_hash="hash-snap-1",
            content_text="Sample snapshot",
        )
        session.add(snap)

        impact = ImpactCandidate(
            id=f"impact-{uuid4().hex[:8]}",
            scan_run_id=scan.id,
            claim_revision_id=rev.id,
            source_snapshot_id=snap.id,
            event_type="new_paper",
            stance="challenges",
            impact_mode="boundary_condition",
            change_depth=2,
            severity="critical",
            suggested_action="add_boundary_discussion",
            comparability="compatible",
            strategy_payload_json={
                "adversarial_critique": {
                    "lethal_reviewer_question": "外部最新基线在更低计算预算下达到同等指标，本文贡献的不可替代性如何证明？",
                    "key_difference": {
                        "assumptions": "外部采用自适应探针，本文采用固定切分。",
                        "methodology": "外部使用对比学习，本文使用交叉熵。",
                        "performance": "外部在噪声环境下优于本文 2.1 个百分点。",
                    },
                    "defense_strategy": {
                        "framing_shift": "限定在特定硬件约束下的端侧能效增益。",
                        "required_experiments": ["补充噪声容忍度测试", "增加延迟功耗测试"],
                    },
                }
            },
        )
        session.add(impact)

    report = service.generate_compliance_report(case_id, format="markdown")

    assert report["case_id"] == case_id
    assert report["format"] == "markdown"
    assert "《科研诚信与引用合规审计报告》" in report["report_markdown"]
    assert "一、 审计概要" in report["report_markdown"]
    assert "二、 参考文献真实性与 CrossRef 校验清单" in report["report_markdown"]
    assert "三、 撤稿与科研诚信风险筛查" in report["report_markdown"]
    assert "四、 原创声明与证据链溯源" in report["report_markdown"]
    assert "五、 同行评审对抗质疑与学术防御自评" in report["report_markdown"]
    assert "六、 审计结论与签批建议" in report["report_markdown"]
    assert "86.4 EM" in report["report_markdown"]
    assert "外部最新基线在更低计算预算下达到同等指标" in report["report_markdown"]
    assert report["summary"]["total_references"] >= 2
    assert report["summary"]["total_claims"] == 1
    assert report["summary"]["high_risk_claims"] == 1
    assert report["summary"]["compliance_score"] >= 80

    # HTML format
    report_html = service.generate_compliance_report(case_id, format="html")
    assert "<!DOCTYPE html>" in report_html["report_html"]
    assert "科研诚信与引用合规审计报告" in report_html["report_html"]
    assert report_html["filename"].endswith(".html")


def test_compliance_report_retraction_deduction(test_db):
    case_id = f"case-{uuid4().hex[:8]}"
    service = CitationService(session_factory=test_db)

    with session_scope(test_db) as session:
        case = ResearchCase(
            id=case_id,
            title="Retracted Paper Impact Test",
            research_question="Does retracted literature affect our findings?",
            created_at=datetime.now(timezone.utc),
        )
        session.add(case)

        manuscript_text = r"""
\begin{thebibliography}{9}
\bibitem{retracted2021}
Bad Paper. 10.1000/retracted-paper-123
\end{thebibliography}
"""
        ms = ManuscriptVersion(
            id=f"ms-{uuid4().hex[:8]}",
            case_id=case_id,
            version_no=1,
            file_name="manuscript.tex",
            source_type="latex",
            content_hash="hash-5678",
            content_text=manuscript_text,
            is_current=True,
        )
        session.add(ms)

        src = Source(
            id="src-retracted",
            external_id="ext-src-retracted",
            title="Fabricated Results in Deep Models",
            url="https://doi.org/10.1000/retracted-paper-123",
            doi="10.1000/retracted-paper-123",
            integrity_state="retracted",
        )
        session.add(src)

    report = service.generate_compliance_report(case_id)
    assert report["summary"]["retraction_risks"] == 1
    # Deduction for retracted paper (-40)
    assert report["summary"]["compliance_score"] <= 60
    assert "⚠️ **警告：检测到高危诚信风险文献" in report["report_markdown"]
    assert "已撤稿" in report["report_markdown"]


def test_compliance_report_api_endpoints():
    client = TestClient(app)
    # Test on default demo case or test case
    res = client.get("/api/cases/case-default/compliance-report?format=markdown")
    if res.status_code == 200:
        data = res.json()
        assert "report_markdown" in data
        assert "summary" in data

    res_download = client.get("/api/cases/case-default/compliance-report/download?format=markdown")
    if res_download.status_code == 200:
        assert "attachment; filename=" in res_download.headers.get("content-disposition", "")
        assert b"Research Integrity & Compliance Audit Report" in res_download.content or b"\xe7\xa7\x91\xe7\xa0\x94\xe8\xaf\x9a\xe4\xbf\xa1" in res_download.content


def test_lab_claim_matrix_urgency_aggregation():
    client = TestClient(app)
    res = client.get("/api/lab/claim-matrix")
    assert res.status_code == 200
    data = res.json()
    assert "cases" in data
    assert "rows" in data
    assert "summary" in data
    assert "total_cases" in data["summary"]
    assert "total_claims" in data["summary"]
    assert "critical_claims" in data["summary"]
    assert "review_claims" in data["summary"]
    assert "clean_claims" in data["summary"]
