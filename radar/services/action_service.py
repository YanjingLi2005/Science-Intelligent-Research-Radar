"""Turn verified research impacts into concrete project actions."""

from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from radar.db import SessionLocal, session_scope
from radar.models import (
    ActionItem,
    AuditEvent,
    Claim,
    ClaimRevision,
    ImpactCandidate,
    ScanRun,
    Source,
    SourceSnapshot,
)
from radar.schemas import ActionAdviceOutput, ActionRecommendation


PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
ACTIVE_STATUSES = {"proposed", "open", "in_progress"}
SEVERITY_PRIORITY = {"critical": "high", "review": "medium", "informative": "low"}
CATEGORY_DUE_LABEL = {
    "revalidation": "48_hours",
    "cite": "before_next_draft",
    "writing": "before_next_draft",
}

# Action type → CCF-A skill routing
ACTION_SKILL_MAP: dict[str, str] = {
    "writing": "ccf_paper_writer",
    "cite": "ccf_paper_writer",
    "experiment": "ccf_experiment_designer",
    "revalidation": "ccf_integrity_auditor",
    "data": "ccf_experiment_designer",
    "competitor_response": "ccf_rebuttal_writer",
    "team_decision": "ccf_paper_reviewer",
}


class ActionService:
    def __init__(self, session_factory: sessionmaker[Session] = SessionLocal):
        self.session_factory = session_factory

    @staticmethod
    def _recommendations(
        impact: ImpactCandidate,
        claim: Claim,
        revision: ClaimRevision,
        source: Source,
        advice: ActionAdviceOutput | None = None,
    ) -> list[ActionRecommendation]:
        # Integrity events keep the deterministic safety templates; every other
        # impact prefers the concrete LLM-drafted advice when one was generated
        # during the scan.
        is_integrity = (
            impact.impact_mode == "research_integrity"
            or impact.event_type == "retraction"
        )
        if advice is not None and not is_integrity:
            return [
                ActionRecommendation(
                    action_type=advice.category,
                    priority=SEVERITY_PRIORITY.get(impact.severity, "medium"),
                    title=advice.title,
                    rationale=advice.rationale,
                    checklist=advice.checklist,
                    due_label=CATEGORY_DUE_LABEL.get(advice.category, "this_week"),
                    initial_status="proposed",
                    advice_source="llm",
                )
            ]

        recommendations: list[ActionRecommendation] = []

        def add(
            action_type: str,
            priority: str,
            title: str,
            rationale: str,
            checklist: list[str],
            due_label: str,
            initial_status: str = "proposed",
        ) -> None:
            if any(item.action_type == action_type for item in recommendations):
                return
            recommendations.append(
                ActionRecommendation(
                    action_type=action_type,
                    priority=priority,
                    title=title,
                    rationale=rationale,
                    checklist=checklist,
                    due_label=due_label,
                    initial_status=initial_status,
                )
            )

        claim_label = claim.stable_key
        source_label = source.title
        is_core_risk = (
            impact.stance == "challenges"
            and revision.centrality == "core"
            and impact.comparability == "compatible"
        )
        unknown_fields = [
            item["field"]
            for item in impact.condition_differences_json
            if item.get("status") == "unknown"
        ]

        if impact.impact_mode == "research_integrity" or impact.event_type == "retraction":
            add(
                "revalidation",
                "critical",
                f"重新验证 {claim_label}：关键来源出现完整性事件",
                f"{source_label} 出现撤稿/更正信号，依赖该来源的结果不能继续按原状态使用。",
                [
                    "定位所有使用该来源的表格、实验和段落",
                    "确定可替代数据、标注或基准版本",
                    "重新运行受影响评估并记录差异",
                    "在方法、限制和引用中更新完整性说明",
                ],
                "48_hours",
                "open",
            )
            add(
                "writing",
                "high",
                f"暂缓依赖 {claim_label} 的强表述",
                "在重新验证完成前，需要在文稿中标注受影响引用和结果范围。",
                ["标记受影响段落", "准备引用替换方案", "草拟 revalidation note"],
                "this_week",
            )
            return recommendations

        if "competitor" in impact.strategic_flags_json:
            add(
                "competitor_response",
                "high",
                f"竞争预警：72 小时内决定加速还是调整 {claim_label} 的角度",
                f"监控团队在 {source_label} 中发布了与你项目相邻的方法或定位。",
                [
                    "逐项比较方法、数据、指标和实验完成度",
                    "列出你仍然独有的贡献与时间优势",
                    "决定加速关键实验、调整主张或改变投稿叙事",
                    "为团队分配负责人和一周内的交付物",
                ],
                "72_hours",
                "open",
            )
            add(
                "experiment",
                "high",
                f"补做与竞争方法的最小 head-to-head 对比（{claim_label}）",
                "竞争关系不能替代科学比较，需要一个条件匹配的最小实验确定差异。",
                ["锁定共同数据集和指标", "实现或复用公开基线", "运行最小对比", "记录优势和失败边界"],
                "this_week",
            )
            add(
                "writing",
                "medium",
                f"更新 related work 与差异化定位（{claim_label}）",
                "新竞争工作会改变论文的 novelty 和 positioning，即使它不构成挑战。",
                ["加入新引用", "写一段方法差异", "明确你的独特贡献"],
                "this_week",
            )

        if impact.stance == "challenges":
            if is_core_risk or impact.change_depth >= 4:
                add(
                    "team_decision",
                    "critical",
                    f"召开团队决策会：{claim_label} 出现可比反向证据",
                    f"{source_label} 对核心主张提供了条件可比的反向结果，不能只通过补引用处理。",
                    [
                        "会前复核双方精确证据和 condition delta",
                        "决定主张保持、收窄、拆分还是暂时撤回",
                        "指定复现实验负责人和截止时间",
                        "记录对投稿时间线和主线叙事的影响",
                    ],
                    "48_hours",
                    "open",
                )
            if impact.comparability == "compatible":
                add(
                    "experiment",
                    "high" if is_core_risk else "medium",
                    f"复现反向结果并做条件匹配实验（{claim_label}）",
                    "需要用相同数据、指标、baseline 和 split 判断差异来自方法还是设置。",
                    [
                        "冻结双方 task/dataset/metric/comparator",
                        "复现新论文报告的关键数字",
                        "在你的实现上运行相同设置",
                        "分析随机种子、实现和数据处理差异",
                    ],
                    "this_week",
                    "open" if is_core_risk else "proposed",
                )
            if unknown_fields:
                add(
                    "data",
                    "high" if is_core_risk else "medium",
                    f"补齐 {claim_label} 的可比性信息：{', '.join(unknown_fields)}",
                    "缺失条件使当前证据不能直接裁决主张，需要补数据或核对论文附录/代码。",
                    [
                        f"核对缺失字段：{', '.join(unknown_fields)}",
                        "检查补充材料、代码和数据卡",
                        "必要时联系作者确认评估设置",
                        "更新 Claim contract 后重新评估",
                    ],
                    "this_week",
                )

        if impact.stance in {"neutral", "uncertain"} and impact.comparability != "compatible":
            # Non-comparable does not mean irrelevant: turn adjacent-task
            # evidence into a positioning/watch suggestion instead of jargon
            # about missing condition fields.
            add(
                "cite",
                "medium" if impact.severity == "review" else "low",
                f"区分定位并关注：{source_label} 与 {claim_label} 条件不可直接比较",
                f"{source_label} 在不同任务、数据或指标设置下考察了与 {claim_label} 相邻的假设；"
                "当前证据不能直接支持或反驳你的主张，建议在 related work 中明确区分定位，"
                "并评估是否值得增补一个条件匹配的对照实验。",
                [
                    "核对双方任务、数据集、指标与 split 的差异",
                    "在 related work 中说明设置差异与定位区别",
                    "评估是否增补一个条件匹配的对照实验",
                    "持续关注该工作的后续可比结果",
                ],
                "before_next_draft",
            )

        if impact.impact_mode == "method_substitution" and impact.stance != "challenges":
            add(
                "experiment",
                "medium",
                f"测试替代方法是否改变 {claim_label} 的结论",
                f"{source_label} 提供了可能替代当前方法的路径，值得做最小增量实验。",
                ["确定可替换模块", "设置等预算对比", "比较主指标和成本", "决定是否纳入主实验"],
                "next_sprint",
            )

        if (
            impact.suggested_action == "run_comparison"
            and impact.stance != "challenges"
        ):
            add(
                "experiment",
                "medium",
                f"补做跨设置对比，验证 {claim_label} 的边界",
                f"{source_label} 使用了不同数据或指标，不能直接反驳你的主张，但提出了值得纳入的鲁棒性设置。",
                [
                    "提取新论文的扰动与评估设置",
                    f"映射到 {claim_label} 可复现的最小实验",
                    "在一个代表性模型和数据域上运行",
                    "根据结果决定加入主实验、附录或 Limitations",
                ],
                "next_sprint",
            )

        writing_needed = (
            impact.suggested_action
            in {"cite", "add_boundary_discussion", "narrow_claim", "team_review"}
            or impact.impact_mode in {"boundary_condition", "prior_art"}
            or impact.stance == "supports"
        )
        if writing_needed:
            if impact.stance == "supports":
                title = f"把新的支持证据加入 {claim_label} 的讨论与引用"
                rationale = f"{source_label} 为该主张提供了可追溯的独立支持证据。"
            elif impact.impact_mode == "boundary_condition":
                title = f"为 {claim_label} 增加适用边界和限制"
                rationale = f"{source_label} 暴露了主张成立条件之外的重要边界。"
            else:
                title = f"更新 {claim_label} 的 related work 与定位"
                rationale = f"{source_label} 构成需要引用或区分的相关工作。"
            add(
                "writing",
                "high" if is_core_risk else "medium",
                title,
                rationale,
                ["加入精确引用", "说明条件差异", "避免把跨条件结果写成直接支持或反驳", "更新 Discussion/Limitations"],
                "before_next_draft",
            )

        return recommendations

    def sync_scan_actions(
        self,
        scan_run_id: str,
        advice_by_impact: dict[str, ActionAdviceOutput] | None = None,
    ) -> list[str]:
        with session_scope(self.session_factory) as session:
            impact_ids = list(
                session.scalars(
                    select(ImpactCandidate.id).where(
                        ImpactCandidate.scan_run_id == scan_run_id,
                        ImpactCandidate.trust_state == "verified",
                        ImpactCandidate.review_state != "dismissed",
                    )
                )
            )
        advice_by_impact = advice_by_impact or {}
        action_ids: list[str] = []
        for impact_id in impact_ids:
            action_ids.extend(
                self.sync_impact_actions(
                    impact_id, advice=advice_by_impact.get(impact_id)
                )
            )
        return action_ids

    def sync_impact_actions(
        self,
        impact_id: str,
        session: Session | None = None,
        advice: ActionAdviceOutput | None = None,
    ) -> list[str]:
        if session is not None:
            # Caller owns the transaction (e.g. review decisions sync actions
            # atomically with the decision itself).
            return self._sync_impact_actions(session, impact_id, advice)
        with session_scope(self.session_factory) as session:
            return self._sync_impact_actions(session, impact_id, advice)

    def _sync_impact_actions(
        self,
        session: Session,
        impact_id: str,
        advice: ActionAdviceOutput | None = None,
    ) -> list[str]:
        impact = session.get(ImpactCandidate, impact_id)
        if impact is None or impact.trust_state != "verified":
            return []
        revision = session.get(ClaimRevision, impact.claim_revision_id)
        claim = session.get(Claim, revision.claim_id) if revision else None
        scan = session.get(ScanRun, impact.scan_run_id)
        snapshot = session.get(SourceSnapshot, impact.source_snapshot_id)
        source = session.get(Source, snapshot.source_id) if snapshot else None
        if not all([revision, claim, scan, source]):
            return []
        recommendations = self._recommendations(
            impact, claim, revision, source, advice=advice
        )
        action_ids: list[str] = []
        created_types: list[str] = []
        for recommendation in recommendations:
            # One open action per (claim revision, action type): several
            # impacts of the same claim merge into it, and a later scan
            # updates the still-open action instead of opening a duplicate.
            existing = session.scalar(
                select(ActionItem)
                .where(
                    ActionItem.claim_revision_id == revision.id,
                    ActionItem.action_type == recommendation.action_type,
                    ActionItem.status.in_(ACTIVE_STATUSES),
                )
                .order_by(ActionItem.created_at.desc(), ActionItem.id)
            )
            if existing is None:
                base_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"research-radar:action:{revision.id}:{recommendation.action_type}",
                    )
                )
                action_id = base_id
                if session.get(ActionItem, base_id) is not None:
                    # A closed (done/dismissed) action from an earlier scan
                    # keeps this deterministic id; reopen the topic as a fresh
                    # row keyed by the current scan.
                    action_id = str(
                        uuid5(
                            NAMESPACE_URL,
                            f"research-radar:action:{revision.id}:"
                            f"{recommendation.action_type}:{scan.id}",
                        )
                    )
                existing = ActionItem(
                    id=action_id,
                    case_id=scan.case_id,
                    scan_run_id=scan.id,
                    impact_candidate_id=impact.id,
                    claim_revision_id=revision.id,
                    action_type=recommendation.action_type,
                    priority=recommendation.priority,
                    title=recommendation.title,
                    rationale=recommendation.rationale,
                    checklist_json=recommendation.checklist,
                    due_label=recommendation.due_label,
                    status=(
                        "open"
                        if impact.review_state in {"confirmed", "edited"}
                        and recommendation.initial_status == "proposed"
                        else recommendation.initial_status
                    ),
                    advice_source=recommendation.advice_source,
                )
                session.add(existing)
                created_types.append(recommendation.action_type)
            else:
                self._merge_recommendation(existing, recommendation, scan, impact)
            action_ids.append(existing.id)
        if created_types:
            session.add(
                AuditEvent(
                    id=str(uuid4()),
                    case_id=scan.case_id,
                    event_type="action_items_created",
                    object_type="ImpactCandidate",
                    object_id=impact.id,
                    payload_json={"action_types": created_types},
                    actor_type="system",
                    actor_id="action_service",
                )
            )
        return action_ids

    @staticmethod
    def _merge_recommendation(
        existing: ActionItem,
        recommendation: ActionRecommendation,
        scan: ScanRun,
        impact: ImpactCandidate,
    ) -> None:
        """Fold another impact's recommendation into the open per-claim action.

        Keeps the highest priority, deduplicates checklist items, and never
        overwrites model-written advice with a rule-template re-sync.
        """

        existing.scan_run_id = scan.id
        existing.impact_candidate_id = impact.id
        if (
            impact.review_state in {"confirmed", "edited"}
            and existing.status == "proposed"
        ):
            existing.status = "open"
        if PRIORITY_ORDER.get(recommendation.priority, 9) < PRIORITY_ORDER.get(
            existing.priority, 9
        ):
            existing.priority = recommendation.priority
        if existing.advice_source == "llm" and recommendation.advice_source != "llm":
            return
        existing.title = recommendation.title
        existing.rationale = recommendation.rationale
        existing.due_label = recommendation.due_label
        merged = list(existing.checklist_json)
        for item in recommendation.checklist:
            if item not in merged:
                merged.append(item)
        existing.checklist_json = merged
        existing.advice_source = recommendation.advice_source

    def list_actions(
        self,
        case_id: str,
        *,
        scan_run_id: str | None = None,
        include_closed: bool = False,
    ) -> list[ActionItem]:
        with session_scope(self.session_factory) as session:
            statement = select(ActionItem).where(ActionItem.case_id == case_id)
            if scan_run_id is not None:
                statement = statement.where(ActionItem.scan_run_id == scan_run_id)
            if not include_closed:
                statement = statement.where(ActionItem.status.in_(ACTIVE_STATUSES))
            actions = list(session.scalars(statement))
            actions.sort(
                key=lambda item: (
                    PRIORITY_ORDER.get(item.priority, 9),
                    item.created_at,
                    item.id,
                )
            )
            for action in actions:
                session.expunge(action)
            return actions

    def update_status(self, action_id: str, status: str) -> ActionItem:
        if status not in {"proposed", "open", "in_progress", "done", "dismissed"}:
            raise ValueError(f"unsupported action status: {status}")
        with session_scope(self.session_factory) as session:
            action = session.get(ActionItem, action_id)
            if action is None:
                raise LookupError(f"action not found: {action_id}")
            action.status = status
            session.add(
                AuditEvent(
                    id=str(uuid4()),
                    case_id=action.case_id,
                    event_type="action_status_changed",
                    object_type="ActionItem",
                    object_id=action.id,
                    payload_json={"status": status},
                    actor_type="human",
                    actor_id="local_user",
                )
            )
            session.flush()
            session.expunge(action)
            return action

    def claim_attention_state(self, claim_id: str) -> str:
        with session_scope(self.session_factory) as session:
            revision_ids = list(
                session.scalars(
                    select(ClaimRevision.id).where(ClaimRevision.claim_id == claim_id)
                )
            )
            impacts = (
                list(
                    session.scalars(
                        select(ImpactCandidate).where(
                            ImpactCandidate.claim_revision_id.in_(revision_ids),
                            ImpactCandidate.trust_state == "verified",
                            ImpactCandidate.review_state != "dismissed",
                        )
                    )
                )
                if revision_ids
                else []
            )
            if any(
                item.impact_mode == "research_integrity"
                or item.event_type == "retraction"
                for item in impacts
            ):
                return "revalidation_required"
            if any(
                item.stance == "challenges"
                and item.severity == "critical"
                and item.comparability in {"compatible", "partial"}
                for item in impacts
            ):
                return "disputed"
            if any("competitor" in item.strategic_flags_json for item in impacts):
                return "competitor_pressure"
            if any(item.stance == "challenges" for item in impacts):
                return "needs_review"
            if any(item.stance == "supports" for item in impacts):
                return "new_support"
            return "stable"
