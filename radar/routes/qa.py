"""Literature Q&A (batch & SSE streaming) and Deep Research API routes."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from radar.api_schemas import DeepResearchStartRequest, LiteratureQARequest
from radar.services.literature_qa_service import LiteratureQAService
from radar.services.scan_runner import ScanAlreadyRunningError, start_deep_research
from radar.routes.deps import _service

router = APIRouter(tags=["qa"])


def _run_deep_research(*args, **kwargs):
    try:
        import radar.api as api
        fn = getattr(api, "start_deep_research", start_deep_research)
        return fn(*args, **kwargs)
    except Exception:
        return start_deep_research(*args, **kwargs)


@router.post("/api/cases/{case_id}/deep-research")
def start_deep_research_run(
    case_id: str, body: DeepResearchStartRequest = DeepResearchStartRequest()
) -> dict[str, Any]:
    """Launch a bounded deep-research run for this case."""
    try:
        scan_id = _run_deep_research(
            case_id,
            question=body.question,
            depth=body.depth,
            max_sources=body.max_sources,
            max_subqueries=body.max_subqueries,
        )
    except ScanAlreadyRunningError as exc:
        raise HTTPException(409, str(exc))
    return {"scan_id": scan_id, "status": "running", "mode": "deep_research"}


@router.post("/api/cases/{case_id}/literature-qa")
def literature_qa(case_id: str, body: LiteratureQARequest) -> dict[str, Any]:
    """Answer a question from the papers already collected in this case."""
    try:
        return _service(LiteratureQAService).answer(
            case_id, body.question, top_k=body.top_k, history=body.history
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/cases/{case_id}/literature-qa/stream")
def literature_qa_stream(case_id: str, body: LiteratureQARequest) -> StreamingResponse:
    """Stream an answer with SSE tokens and real-time source retrieval."""
    svc = _service(LiteratureQAService)

    def event_generator():
        try:
            for event in svc.answer_stream(
                case_id, body.question, top_k=body.top_k, history=body.history
            ):
                event_name = event.get("event", "message")
                data_payload = json.dumps(event.get("data", {}), ensure_ascii=False)
                yield f"event: {event_name}\ndata: {data_payload}\n\n"
        except Exception as exc:
            err_payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
            yield f"event: error\ndata: {err_payload}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
