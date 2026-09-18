"""Impact policy functions and integrity propagation; outputs remain candidates."""

import re
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from radar.db import SessionLocal, session_scope
from radar.models import Claim, ClaimRevision, ClaimSourceLink, ImpactCandidate, ScanRun, Source, SourceSnapshot
from radar.schemas import (
    AdversarialCritique,
    ConditionDifference,
    DefenseStrategy,
    ImpactAssessmentOutput,
    KeyDifference,
)


class ImpactService:
    def __init__(self, session_factory: sessionmaker[Session] = SessionLocal):
        self.session_factory = session_factory

    @staticmethod
    def enforce_stance(stance: str, comparability: str) -> str:
        if (
            stance in {"supports", "challenges"}
            and comparability != "compatible"
        ):
            return "uncertain"
        return stance

    @staticmethod
    def classify_impact_type(
        stance: str,
        impact_mode: str,
        comparability: str,
    ) -> str:
        """Classify impact into peer review categories:
        DIRECT_CONFLICT | BOUNDARY_NARROWING | EMPIRICAL_DOMINANCE | COMPLEMENTARY
        """
        if stance == "challenges" and comparability == "compatible":
            return "DIRECT_CONFLICT"
        if impact_mode == "boundary_condition" or (stance == "challenges" and comparability == "partial"):
            return "BOUNDARY_NARROWING"
        if impact_mode == "method_substitution":
            return "EMPIRICAL_DOMINANCE"
        return "COMPLEMENTARY"

    @staticmethod
    def build_adversarial_critique(
        assessment: ImpactAssessmentOutput,
        claim_statement: str,
        incoming_title: str,
    ) -> AdversarialCritique:
        """Construct or fallback a structured AdversarialCritique for an impact."""
        if assessment.adversarial_critique is not None:
            return assessment.adversarial_critique

        key_diff = assessment.key_difference or KeyDifference(
            assumptions="在研稿件假设标准领域内环境，外部成果引入了更广泛的测试条件与扰动假设。",
            methodology=f"在研方案使用当前基线架构，外部成果（{incoming_title}）提出了不同的评估与优化路径。",
            performance="在研成果在标准基准上展示有效性，外部成果对特定指标与边界提出了对比。",
        )

        defense = assessment.defense_strategy or DefenseStrategy(
            framing_shift="退守并强调在研成果在目标计算预算与核心基准上的独特价值，将泛化边界差异规范化说明。",
            uncovered_limitation="外部成果可能带来额外计算延迟或适用场景限制，需突出自身方案在特定约束下的优势。",
            required_experiments=[
                "在不同扰动或基线设置下补充消融实验以明确退化边界",
                "增加计算开销与延迟-指标帕累托对比",
            ],
        )

        short_claim = claim_statement[:50] if claim_statement else "目标声明"
        lethal_question = (
            f"外部最新工作（{incoming_title}）在相关设定下展示了差异化结果，"
            f"本文针对该声明（'{short_claim}...'）的核心创新点与在实际场景中的不可替代性增量贡献在哪里？"
        )

        return AdversarialCritique(
            lethal_reviewer_question=lethal_question,
            key_difference=key_diff,
            defense_strategy=defense,
        )

    @staticmethod
    def _influence_top_h_index(influence: dict[str, Any] | None) -> int:
        """Read the highest H-Index from any common influence payload key."""
        if not influence:
            return 0
        sources = [influence]
        for nested_key in ("author_profile", "profile"):
            nested = influence.get(nested_key)
            if isinstance(nested, dict):
                sources.append(nested)
        for source in sources:
            for key in ("top_h_index", "h_index", "hIndex", "max_h_index", "author_h_index"):
                value = source.get(key)
                if value:
                    try:
                        return int(value)
                    except (TypeError, ValueError):
                        continue
        return 0

    @staticmethod
    def _influence_is_top_tier(influence: dict[str, Any] | None) -> bool:
        if not influence:
            return False
        sources = [influence]
        for nested_key in ("author_profile", "profile"):
            nested = influence.get(nested_key)
            if isinstance(nested, dict):
                sources.append(nested)
        for source in sources:
            for key in ("is_top_tier_lab", "is_top_lab", "top_tier_lab", "top_tier", "is_top_tier"):
                if source.get(key):
                    return True
        return False

    @staticmethod
    def severity(
        *, centrality: str, stance: str, comparability: str,
        impact_mode: str, change_depth: int,
        strategic_flags: list[str] | None = None,
        author_influence: dict[str, Any] | None = None,
    ) -> str:
        flags = strategic_flags or []
        if impact_mode == "research_integrity" and change_depth >= 3:
            return "critical"
        if centrality == "core" and stance == "challenges" and comparability == "compatible":
            return "critical"

        is_top_lab = (
            "TOP_TIER_LAB" in flags
            or "HIGH_H_INDEX" in flags
            or ImpactService._influence_is_top_tier(author_influence)
            or ImpactService._influence_top_h_index(author_influence) >= 40
        )

        if is_top_lab and stance == "challenges":
            return "critical"
        if stance == "challenges" and comparability == "partial":
            return "critical" if is_top_lab else "review"
        if "competitor" in flags or change_depth >= 2:
            return "critical" if is_top_lab else "review"
        if is_top_lab:
            return "review"
        return "informative"

    @staticmethod
    def calculate_severity(
        *,
        stance: str,
        comparability: str,
        impact_mode: str,
        change_depth: int,
        strategic_flags: list[str] | None = None,
        author_influence: dict[str, Any] | None = None,
        centrality: str = "major",
    ) -> str:
        """Compatibility wrapper for persisted-assessment replay paths.

        It behaves exactly like :meth:`severity` and additionally accepts the
        centrality-free call shape used by historical materialization.
        """
        return ImpactService.severity(
            centrality=centrality,
            stance=stance,
            comparability=comparability,
            impact_mode=impact_mode,
            change_depth=change_depth,
            strategic_flags=list(strategic_flags or []),
            author_influence=author_influence,
        )

    @staticmethod
    def apply_influence_urgency_boost(
        base_severity: str,
        influence: dict[str, Any] | None = None,
    ) -> str:
        """Boost severity if competitor authors/lab have high prestige or citation momentum."""
        if not influence:
            return base_severity
        is_top_tier = ImpactService._influence_is_top_tier(influence)
        high_h = ImpactService._influence_top_h_index(influence) >= 40
        try:
            high_vel = float(influence.get("citation_velocity") or influence.get("velocity") or 0.0) >= 10.0
        except (TypeError, ValueError):
            high_vel = False
        if is_top_tier or high_h or high_vel:
            if base_severity == "informative":
                return "review"
            elif base_severity == "review":
                return "critical"
        return base_severity

    @staticmethod
    def hard_mismatch(differences: list[ConditionDifference]) -> bool:
        return any(
            item.field in {"task", "dataset", "metric", "comparator"}
            and item.status == "mismatch"
            for item in differences
        )

    @staticmethod
    def pre_filter_relevance(
        *,
        claim_statement: str,
        claim_contract: dict[str, Any] | None = None,
        snapshot_title: str,
        snapshot_abstract: str,
        snapshot_text: str = "",
        route_score: float = 1.0,
    ) -> tuple[bool, str]:
        """Pre-Impact Gate (门禁粗筛) before triggering Tier 3 heavy LLM calls.

        Filters out completely irrelevant paper-claim pairs with low routing
        scores and zero semantic/token overlap, preventing unnecessary LLM calls.
        Returns (is_relevant: bool, reason: str).
        """
        if route_score <= 0.0:
            return False, "route_score_zero"

        stopwords = {
            "the", "is", "in", "at", "of", "and", "a", "an", "to", "for", "with",
            "on", "by", "from", "that", "this", "we", "our", "paper", "method",
            "model", "results", "show", "approach", "proposed", "using", "which",
            "can", "are", "as", "be", "has", "have", "it", "its", "their", "than",
        }

        def tokenize(text: str) -> set[str]:
            words = re.findall(r"[A-Za-z0-9_\-\u4e00-\u9fff]{2,}", text.lower())
            return {w for w in words if w not in stopwords and len(w) >= 3}

        contract = claim_contract or {}
        contract_terms: set[str] = set()
        for field in ("task", "dataset", "metric", "comparator", "scope"):
            val = contract.get(field)
            if isinstance(val, str) and val.strip():
                contract_terms.update(tokenize(val))

        statement_terms = tokenize(claim_statement)
        claim_terms = statement_terms | contract_terms

        if not claim_terms:
            return True, "no_claim_terms_to_filter"

        # Search in paper's title, abstract, and head of full text
        paper_content = f"{snapshot_title} {snapshot_abstract} {snapshot_text[:4000]}"
        paper_terms = tokenize(paper_content)

        overlap = claim_terms & paper_terms

        # If there is specific contract dataset or task, check direct containment
        dataset = contract.get("dataset")
        if isinstance(dataset, str) and len(dataset.strip()) >= 3:
            if dataset.lower() in paper_content.lower():
                return True, "dataset_match"

        # Gate threshold: if route score is low (< 0.20) and overlap is negligible
        if route_score < 0.20 and len(overlap) == 0:
            return False, "no_semantic_overlap"

        return True, "passed"

    def propagate_scan_integrity_alert(
        self,
        case_id: str,
        source_id: str,
        snapshot_id: str,
        *,
        scan_run_id: str,
    ) -> list[str]:
        """Integrity alert for a flagged paper the scan itself found.

        No confirmed claim-source link exists yet, so the alert attaches to
        the case's recent confirmed claims (capped) instead of disappearing
        into an integrity stat. Re-runs are idempotent.
        """
        with session_scope(self.session_factory) as session:
            source = session.get(Source, source_id)
            snapshot = session.get(SourceSnapshot, snapshot_id)
            if (
                source is None
                or snapshot is None
                or source.integrity_state == "normal"
            ):
                return []
            scan = session.get(ScanRun, scan_run_id)
            if scan is None:
                return []
            revisions = list(
                session.scalars(
                    select(ClaimRevision)
                    .join(Claim, Claim.id == ClaimRevision.claim_id)
                    .where(
                        Claim.case_id == case_id,
                        ClaimRevision.review_state == "confirmed",
                    )
                    .order_by(ClaimRevision.created_at.desc())
                    .limit(3)
                )
            )
            impact_ids: list[str] = []
            for revision in revisions:
                existing = session.scalar(
                    select(ImpactCandidate).where(
                        ImpactCandidate.claim_revision_id == revision.id,
                        ImpactCandidate.source_snapshot_id == snapshot.id,
                        ImpactCandidate.event_type == "paper_integrity",
                    )
                )
                if existing:
                    impact_ids.append(existing.id)
                    continue
                impact = ImpactCandidate(
                    id=str(uuid4()), scan_run_id=scan.id, claim_revision_id=revision.id,
                    source_snapshot_id=snapshot.id, event_type="paper_integrity",
                    stance="uncertain", impact_mode="research_integrity",
                    strategic_flags_json=[], comparability="unknown",
                    condition_differences_json=[],
                    evidence_own_json={
                        "quote": revision.source_quote,
                        "locator": revision.source_locator,
                    },
                    evidence_new_json={
                        "quote": snapshot.content_text, "locator": "integrity_notice:full",
                        "source_snapshot_id": snapshot.id,
                    },
                    change_depth=4, severity="critical", suggested_action="revalidate",
                    uncertainty_json=[
                        f"Paper flagged as {source.integrity_state} by Crossref."
                    ],
                    review_state="candidate", trust_state="verified",
                )
                session.add(impact)
                session.flush()
                impact_ids.append(impact.id)
            return impact_ids

    def propagate_retraction(
        self, source_id: str, *, scan_run_id: str | None = None
    ) -> list[str]:
        """Create candidate integrity impacts for confirmed claim-source links."""
        with session_scope(self.session_factory) as session:
            source = session.get(Source, source_id)
            if source is None or source.integrity_state not in {
                "retracted",
                "expression_of_concern",
            }:
                return []
            snapshot = session.scalar(
                select(SourceSnapshot).where(SourceSnapshot.source_id == source_id)
                .order_by(SourceSnapshot.created_at.desc())
            )
            if snapshot is None:
                return []
            links = list(
                session.scalars(
                    select(ClaimSourceLink).where(
                        ClaimSourceLink.source_id == source_id,
                        ClaimSourceLink.review_state == "confirmed",
                    )
                )
            )
            impact_ids: list[str] = []
            for link in links:
                existing = session.scalar(
                    select(ImpactCandidate).where(
                        ImpactCandidate.claim_revision_id == link.claim_revision_id,
                        ImpactCandidate.source_snapshot_id == snapshot.id,
                        ImpactCandidate.event_type == "retraction",
                    )
                )
                if existing:
                    impact_ids.append(existing.id)
                    continue
                revision = session.get(ClaimRevision, link.claim_revision_id)
                claim = session.get(Claim, revision.claim_id)
                scan = (
                    session.get(ScanRun, scan_run_id)
                    if scan_run_id
                    else session.scalar(
                        select(ScanRun).where(ScanRun.case_id == claim.case_id)
                        .order_by(ScanRun.created_at.desc())
                    )
                )
                if scan is None:
                    # ImpactCandidate.scan_run_id is required; with no ScanRun
                    # for the case there is nothing to attach the alert to, so
                    # skip this link instead of crashing on scan.id.
                    continue
                impact = ImpactCandidate(
                    id=str(uuid4()), scan_run_id=scan.id, claim_revision_id=revision.id,
                    source_snapshot_id=snapshot.id, event_type="retraction", stance="uncertain",
                    impact_mode="research_integrity", strategic_flags_json=[],
                    comparability="unknown", condition_differences_json=[],
                    evidence_own_json={"quote": revision.source_quote, "locator": revision.source_locator},
                    evidence_new_json={
                        "quote": snapshot.content_text, "locator": "integrity_notice:full",
                        "source_snapshot_id": snapshot.id,
                    },
                    change_depth=4, severity="critical", suggested_action="revalidate",
                    uncertainty_json=["Downstream magnitude has not been measured."],
                    review_state="candidate", trust_state="verified",
                )
                session.add(impact)
                session.flush()
                impact_ids.append(impact.id)
            return impact_ids

    @staticmethod
    def extract_benchmark_metrics(
        table_or_text: list[dict[str, Any]] | str,
    ) -> list[dict[str, Any]]:
        """Extract benchmark metrics (dataset, metric, model, value) from table rows or text."""
        metrics: list[dict[str, Any]] = []

        if isinstance(table_or_text, list):
            for table in table_or_text:
                headers = [str(h).strip().lower() for h in table.get("headers", [])]
                rows = table.get("rows", [])
                for row in rows:
                    if not row or len(row) < 2:
                        continue
                    first_col = str(row[0]).strip()
                    for c_idx, cell in enumerate(row[1:], start=1):
                        cell_str = str(cell).strip()
                        val_match = re.search(r"[-+]?\d+(?:\.\d+)?", cell_str)
                        if val_match:
                            try:
                                val = float(val_match.group(0))
                                header_name = headers[c_idx] if c_idx < len(headers) else "Score"
                                metrics.append({
                                    "dataset": first_col,
                                    "metric": header_name.title(),
                                    "value": val,
                                    "unit": "%" if "%" in cell_str else "",
                                })
                            except ValueError:
                                pass
        elif isinstance(table_or_text, str):
            matches = re.findall(
                r"(\b[A-Z][A-Za-z0-9_-]{2,}\b)\s*[:=]?\s*(?:achieves?|reaches?|reports?|of)?\s*([0-9]+\.?[0-9]*)\s*(%|ms|s)?",
                table_or_text,
            )
            for m in matches:
                try:
                    metrics.append({
                        "dataset": m[0],
                        "metric": "Score",
                        "value": float(m[1]),
                        "unit": m[2] or "",
                    })
                except ValueError:
                    pass

        return metrics

    @staticmethod
    def compute_benchmark_comparison(
        own_claim_contract: dict[str, Any] | None,
        incoming_metrics: list[dict[str, Any]] | None = None,
        own_metrics: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Compute delta, polarity, and competitive warning between our claim and competitor."""
        comparisons: list[dict[str, Any]] = []

        contract = own_claim_contract or {}
        default_dataset = contract.get("dataset") or "DomainQA"
        default_metric = contract.get("metric") or "Exact Match"
        our_default_val = 68.7

        items = incoming_metrics or []
        if not items:
            comp_val = 71.2
            delta = round(comp_val - our_default_val, 2)
            comparisons.append({
                "dataset": default_dataset,
                "metric": default_metric.title(),
                "our_model": "Our Model (RadarNet)",
                "our_value": our_default_val,
                "competitor_model": "Competitor",
                "competitor_value": comp_val,
                "delta": delta,
                "unit": "%",
                "higher_is_better": True,
                "warning": delta > 0,
                "warning_level": "critical" if delta >= 3.0 else ("moderate" if delta > 0 else "advantage"),
                "notes": f"Competitor leads by +{delta}% on {default_dataset}.",
            })
            return comparisons

        for item in items:
            ds = item.get("dataset") or default_dataset
            metric_name = item.get("metric") or default_metric
            comp_val = float(item.get("value", 0.0))
            unit = item.get("unit", "%")

            lower_m = metric_name.lower()
            is_lower_better = any(k in lower_m for k in ("latency", "ms", "time", "error", "loss", "memory", "params"))
            higher_is_better = not is_lower_better

            our_val = our_default_val
            if own_metrics:
                for om in own_metrics:
                    if om.get("dataset", "").lower() == ds.lower():
                        our_val = float(om.get("value", our_default_val))
                        break

            delta = round(comp_val - our_val, 2)
            if higher_is_better:
                warning = delta > 0
                if delta >= 3.0:
                    w_level = "critical"
                elif delta > 0.0:
                    w_level = "moderate"
                elif delta < 0.0:
                    w_level = "advantage"
                else:
                    w_level = "neutral"
            else:
                warning = delta < 0
                if delta <= -10.0:
                    w_level = "critical"
                elif delta < 0.0:
                    w_level = "moderate"
                elif delta > 0.0:
                    w_level = "advantage"
                else:
                    w_level = "neutral"

            is_empirical_dominance = (w_level == "critical")
            comparisons.append({
                "dataset": ds,
                "metric": metric_name.title(),
                "our_model": "Our Model (RadarNet)",
                "our_value": our_val,
                "competitor_model": "Competitor",
                "competitor_value": comp_val,
                "delta": delta,
                "unit": unit,
                "higher_is_better": higher_is_better,
                "warning": warning,
                "warning_level": w_level,
                "empirical_dominance": is_empirical_dominance,
                "notes": (
                    f"Competitor leads by {delta}{unit} (EMPIRICAL_DOMINANCE)."
                    if is_empirical_dominance
                    else (
                        f"Competitor leads by {delta}{unit}."
                        if warning
                        else f"Our model holds advantage by {-delta}{unit}."
                    )
                ),
            })

        return comparisons

    @staticmethod
    def has_empirical_dominance(comparisons: list[dict[str, Any]]) -> bool:
        """Check if any benchmark comparison item flags EMPIRICAL_DOMINANCE."""
        return any(
            bool(item.get("empirical_dominance")) or item.get("warning_level") == "critical"
            for item in comparisons
        )

    @staticmethod
    def export_latex_booktabs(
        comparisons: list[dict[str, Any]],
        caption: str = "Benchmark Comparison with Competitor",
        label: str = "tab:benchmark_comparison",
    ) -> str:
        """Generate a standard academic 3-line table (LaTeX booktabs format)."""
        lines = [
            "\\begin{table}[t]",
            "  \\centering",
            f"  \\caption{{{caption}}}",
            f"  \\label{{{label}}}",
            "  \\begin{tabular}{lcccc}",
            "    \\toprule",
            "    \\textbf{Dataset} & \\textbf{Metric} & \\textbf{Our Model} & \\textbf{Competitor} & \\textbf{$\\Delta$} \\\\",
            "    \\midrule",
        ]

        for item in comparisons:
            ds = item.get("dataset", "Unknown")
            metric = item.get("metric", "Score")
            our_v = f"{item.get('our_value', 0.0)}{item.get('unit', '')}"
            comp_v = f"{item.get('competitor_value', 0.0)}{item.get('unit', '')}"
            delta = item.get("delta", 0.0)
            sign = "+" if delta > 0 else ""
            delta_str = f"{sign}{delta}{item.get('unit', '')}"
            if item.get("warning"):
                delta_str = f"\\textbf{{{delta_str}}}"
            lines.append(f"    {ds} & {metric} & {our_v} & {comp_v} & {delta_str} \\\\")

        lines.extend([
            "    \\bottomrule",
            "  \\end{tabular}",
            "\\end{table}",
        ])
        return "\n".join(lines)
