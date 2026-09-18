"""Personalized weekly radar digest.

Turns the latest scan results, impact candidates, actions, and deep-research
briefs into a concise Markdown report a researcher can read in one minute.
This is the "持续关注 + 定时改造" narrative made tangible.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from radar.db import SessionLocal, session_scope
from radar.models import (
    ActionItem,
    Claim,
    ClaimRevision,
    ImpactCandidate,
    ResearchCase,
    ScanRun,
    Source,
    SourceSnapshot,
)


def _fmt(value: Any) -> str:
    return "" if value is None else str(value)


class DigestService:
    def __init__(self, session_factory: sessionmaker[Session] = SessionLocal):
        self.session_factory = session_factory

    def generate_digest(
        self, case_id: str, *, include_sections: list[str] | None = None
    ) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            case = session.get(ResearchCase, case_id)
            if case is None:
                raise LookupError(f"case not found: {case_id}")

            latest_scan = session.scalar(
                select(ScanRun)
                .where(
                    ScanRun.case_id == case_id,
                    ScanRun.status == "completed",
                )
                .order_by(ScanRun.created_at.desc())
            )
            latest_deep = session.scalar(
                select(ScanRun)
                .where(
                    ScanRun.case_id == case_id,
                    ScanRun.mode == "deep_research",
                    ScanRun.status == "completed",
                )
                .order_by(ScanRun.created_at.desc())
            )

            impacts = list(
                session.scalars(
                    select(ImpactCandidate)
                    .join(ClaimRevision, ClaimRevision.id == ImpactCandidate.claim_revision_id)
                    .join(Claim, Claim.id == ClaimRevision.claim_id)
                    .where(Claim.case_id == case_id)
                    .order_by(ImpactCandidate.created_at.desc())
                    .limit(20)
                )
            )
            actions = list(
                session.scalars(
                    select(ActionItem)
                    .where(
                        ActionItem.case_id == case_id,
                        ActionItem.status.in_(["proposed", "open", "in_progress"]),
                    )
                    .order_by(ActionItem.created_at.desc())
                    .limit(20)
                )
            )

            # Resolve source titles for impacts.
            source_ids = {
                impact.source_snapshot_id for impact in impacts if impact.source_snapshot_id
            }
            source_titles: dict[str, str] = {}
            if source_ids:
                rows = session.execute(
                    select(SourceSnapshot.id, Source.title)
                    .join(Source, Source.id == SourceSnapshot.source_id)
                    .where(SourceSnapshot.id.in_(source_ids))
                ).all()
                source_titles = {row.id: row.title for row in rows}

        scan_stats = (latest_scan.stats_json or {}) if latest_scan else {}
        impacts_by_stance: dict[str, int] = {}
        for impact in impacts:
            impacts_by_stance[impact.stance] = impacts_by_stance.get(impact.stance, 0) + 1

        impact_lines: list[str] = []
        for impact in impacts[:10]:
            title = source_titles.get(impact.source_snapshot_id, "unknown paper")
            impact_lines.append(
                f"- **{impact.stance}** · {impact.severity} · {impact.suggested_action} — {title}"
            )

        action_lines: list[str] = []
        for action in actions[:10]:
            action_lines.append(
                f"- [{action.status}] **{action.title}** ({action.action_type}, {action.priority})"
            )

        sections: list[dict[str, Any]] = []
        if latest_scan is not None:
            sections.append(
                {
                    "heading": "最新雷达扫描",
                    "items": [
                        f"扫描模式：{latest_scan.mode}",
                        f"扫描论文：{scan_stats.get('scanned_papers', 0)} 篇",
                        f"路由配对：{scan_stats.get('routed_pairs', 0)} 对",
                        f"影响候选：{scan_stats.get('impact_candidates', 0)} 项",
                        f"完整性告警：{scan_stats.get('integrity_alerts', 0)} 项",
                    ],
                }
            )
        if latest_deep is not None:
            brief = (latest_deep.stats_json or {}).get("research_brief") or {}
            sections.append(
                {
                    "heading": "最近深度调研",
                    "items": [
                        f"主题：{brief.get('title', latest_deep.stats_json.get('question', ''))}",
                        *[f"洞察：{item}" for item in (brief.get("key_insights") or [])[:3]],
                        *[f"建议：{item}" for item in (brief.get("recommended_next_steps") or [])[:3]],
                    ],
                }
            )
        if impact_lines:
            sections.append({"heading": "新影响", "items": impact_lines})
        if action_lines:
            sections.append({"heading": "行动队列", "items": action_lines})
        if not sections:
            sections.append({"heading": "概览", "items": ["还没有可汇总的扫描结果，请先运行一次文献雷达扫描或深度调研。"]})

        if include_sections:
            allowed = set(include_sections)
            key_map = {
                "最新雷达扫描": "scan",
                "最近深度调研": "deep",
                "新影响": "impacts",
                "行动队列": "actions",
                "概览": "overview",
            }
            sections = [
                section
                for section in sections
                if key_map.get(section.get("heading", "")) in allowed
            ]

        markdown_lines = [
            f"# Research Radar 周报 — {case.title}",
            "",
            f"生成时间：{datetime.now(timezone.utc).isoformat()}",
            f"研究问题：{case.research_question}",
            "",
        ]
        for section in sections:
            markdown_lines.append(f"## {section['heading']}")
            markdown_lines.append("")
            markdown_lines.extend(section["items"])
            markdown_lines.append("")

        return {
            "case_id": case_id,
            "title": f"{case.title} 雷达周报",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "scanned_papers": scan_stats.get("scanned_papers", 0),
                "impacts": len(impacts),
                "supports": impacts_by_stance.get("supports", 0),
                "challenges": impacts_by_stance.get("challenges", 0),
                "open_actions": len(actions),
            },
            "sections": sections,
            "markdown": "\n".join(markdown_lines),
        }
