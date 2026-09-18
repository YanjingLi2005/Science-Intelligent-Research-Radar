"""Phase D tests: demo-case predicate, audit export limit, watch entities."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from radar.db import session_scope
from radar.models import AuditEvent, ResearchCase, WatchEntity
from radar.services.case_service import CaseService
from radar.services.report_service import ReportService


def _case(**overrides) -> ResearchCase:
    research_case = ResearchCase(
        id=str(uuid4()),
        title=overrides.pop("title", "My project"),
        research_question="Q?",
        settings_json=overrides.pop("settings_json", {}),
    )
    for key, value in overrides.items():
        setattr(research_case, key, value)
    return research_case


def test_is_demo_case_flags_sample_and_rare_prefix() -> None:
    assert CaseService.is_demo_case(_case(settings_json={"is_sample": True}))
    assert CaseService.is_demo_case(_case(title="RARE: Something"))
    assert not CaseService.is_demo_case(_case(settings_json={"is_sample": False}))
    assert not CaseService.is_demo_case(_case())
    # Missing settings_json (None) must not raise.
    assert not CaseService.is_demo_case(_case(settings_json=None))


def test_audit_export_respects_limit(db_session_factory) -> None:
    case_id = str(uuid4())
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with session_scope(db_session_factory) as session:
        session.add(
            ResearchCase(
                id=case_id, title="Audit case", research_question="Q?",
            )
        )
        session.flush()
        for index in range(5):
            session.add(
                AuditEvent(
                    id=f"event-{index}",
                    case_id=case_id,
                    event_type="test_event",
                    object_type="ResearchCase",
                    object_id=case_id,
                    payload_json={"index": index},
                    actor_type="system",
                    actor_id="test",
                    created_at=base + timedelta(minutes=index),
                )
            )

    service = ReportService(db_session_factory)
    import json

    limited = json.loads(service.audit_export(case_id, limit=2))
    assert [event["payload"]["index"] for event in limited] == [4, 3]

    full = json.loads(service.audit_export(case_id, limit=None))
    assert len(full) == 5


def test_watch_entity_add_and_remove(db_session_factory, golden_case) -> None:
    service = CaseService(db_session_factory)
    watch_id = service.add_watch_entity(
        golden_case,
        entity_type="lab",
        canonical_name="Test Lab",
        aliases=["TL", "TestLab"],
    )
    with session_scope(db_session_factory) as session:
        watch = session.get(WatchEntity, watch_id)
        assert watch is not None
        assert watch.canonical_name == "Test Lab"
        assert watch.aliases_json == ["TL", "TestLab"]
        events = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.case_id == golden_case,
                    AuditEvent.event_type == "watch_entity_added",
                )
            )
        )
        assert events

    service.remove_watch_entity(watch_id)
    with session_scope(db_session_factory) as session:
        assert session.get(WatchEntity, watch_id) is None


def test_watch_entity_errors(db_session_factory, golden_case) -> None:
    service = CaseService(db_session_factory)
    with pytest.raises(LookupError):
        service.add_watch_entity(
            "missing-case", entity_type="lab", canonical_name="X"
        )
    with pytest.raises(LookupError):
        service.remove_watch_entity("missing-watch")
