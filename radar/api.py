"""FastAPI application entry point for Research Radar.

Routes are modularized under ``radar.routes.*`` and mounted via FastAPI APIRouters.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from radar import auth as auth_service
from radar.api_schemas import *  # Re-export for backwards compatibility
from radar.config import (
    Settings,
    get_settings,
    save_local_settings,
)
from radar.llm.factory import describe_fallback_setup, describe_llm_setup
from radar.db import SessionLocal, get_session_factory, init_database, session_scope
from radar.errors import ErrorCode, format_error_envelope
from radar.models import ResearchCase
from radar.schemas import CitationSupportInput, ReferenceEntry, ReferenceMatch
from radar.services.action_service import ActionService
from radar.services.case_service import CaseService, ScanActiveError
from radar.services.citation_service import CitationService
from radar.services.citation_support_service import CitationSupportService
from radar.services.claim_service import ClaimService
from radar.services.digest_service import DigestService
from radar.services.literature_qa_service import LiteratureQAService
from radar.services.manuscript_understanding_service import ManuscriptUnderstandingService
from radar.services.patch_service import PatchService
from radar.services.reference_validity_service import ReferenceValidityService
from radar.services.report_service import ReportService
from radar.services.review_service import ReviewService
from radar.services.skill_execution_service import SkillExecutionService
from radar.services.skill_pipeline_service import SkillPipelineService
from radar.services.source_import_service import SourceImportService
from radar.services.scan_runner import (
    ScanAlreadyRunningError,
    recover_interrupted_scans,
    request_cancel,
    start,
    start_deep_research,
)
from radar.tracing import Timer, get_trace_id, set_trace_id
from radar.routes.auth import (
    AdminCreateUserRequest,
    AuthOut,
    AuthRequest,
    RoleUpdate,
    router as auth_router,
)
from radar.routes.cases import router as cases_router
from radar.routes.citations import (
    CitationSupportRequest,
    ReferenceCheckRequest,
    router as citations_router,
)
from radar.routes.claims import router as claims_router
from radar.routes.deps import (
    _AUTH_ATTEMPTS,
    _AUTH_RATE_MAX_ATTEMPTS,
    _AUTH_RATE_WINDOW_SECONDS,
    _auth_rate_limited,
    _client_ip,
    _parse_json_field,
    _record_auth_attempt,
    _service,
    _set_case_source_tags,
    _source_tags,
    admin_user,
    current_user,
)
from radar.routes.patches import router as patches_router
from radar.routes.qa import router as qa_router
from radar.routes.scans import router as scans_router
from radar.routes.settings import (
    _ALLOWED_SETTINGS_KEYS,
    _MODEL_CATALOG,
    router as settings_router,
)
from radar.routes.skills import router as skills_router
from radar.routes.sources import router as sources_router
from radar.routes.builders import (
    _action_to_out,
    _build_actions,
    _build_audit,
    _build_citation_health,
    _build_claims,
    _build_competitors,
    _build_papers,
    _build_profile,
    _build_project,
    _build_retrieval_receipts,
    _build_rewrite,
    _build_summary,
    _build_versions,
    _case_topics,
    _diff_status,
    _impact_to_paper_out,
    _latest_fidelity,
    _revision_to_claim_out,
    _stance_evidence_kind,
)

logger = logging.getLogger("radar.api")


# ---------------------------------------------------------------------------
# Application Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_: FastAPI):
    # A fresh install has no tables until init_database runs: do it once at
    # startup so the first write cannot fail with "no such table".
    init_database()
    # A scan interrupted by a process crash would otherwise block its case
    # forever ("scan already running"); recover it at startup.
    recover_interrupted_scans(session_factory=SessionLocal)
    # Continuous monitoring: a daemon scheduler starts due auto-scans while
    # the web process is alive. Tests disable it with RADAR_SCHEDULER_DISABLED.
    if os.environ.get("RADAR_SCHEDULER_DISABLED") != "1":
        from radar.services.scheduler_service import start_scheduler

        start_scheduler(session_factory=SessionLocal)
    yield


app = FastAPI(
    title="Research Radar API",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middlewares
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:8501"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def tracing_middleware(request: Request, call_next):
    """Assign an end-to-end trace ID to every incoming request and record timing."""
    incoming_trace_id = request.headers.get("x-trace-id") or request.headers.get("traceparent")
    trace_id = set_trace_id(incoming_trace_id)

    t = Timer()
    response = await call_next(request)
    elapsed_ms = t.stop()

    response.headers["X-Trace-Id"] = trace_id
    response.headers["X-Response-Time-Ms"] = str(elapsed_ms)
    return response


@app.middleware("http")
async def tenant_middleware(request: Request, call_next):
    """Resolve the bearer token and bind the per-user tenant."""
    from radar.tenant import tenant_context

    if os.environ.get("RADAR_AUTH_DISABLED") == "1":
        return await call_next(request)

    path = request.url.path
    if (
        path.startswith("/api/")
        and path != "/api/health"
        and not path.startswith("/api/auth/")
    ):
        auth_header = request.headers.get("Authorization", "")
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return JSONResponse(status_code=401, content={"detail": "未登录或登录已过期"})
        tenant = auth_service.resolve_tenant(token)
        if tenant is None:
            return JSONResponse(status_code=401, content={"detail": "未登录或登录已过期"})
        with tenant_context(tenant):
            return await call_next(request)
    return await call_next(request)


# ---------------------------------------------------------------------------
# Exception Handlers
# ---------------------------------------------------------------------------

@app.exception_handler(HTTPException)
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_: Request, exc: HTTPException | StarletteHTTPException):
    error_code = getattr(exc, "error_code", None)
    retryable = getattr(exc, "retryable", None)
    envelope = format_error_envelope(
        detail=exc.detail if isinstance(exc.detail, (str, list, dict)) else str(exc.detail),
        status_code=exc.status_code,
        error_code=error_code,
        retryable=retryable,
    )
    headers = dict(exc.headers or {})
    headers["X-Trace-Id"] = envelope["trace_id"]
    return JSONResponse(
        status_code=exc.status_code,
        content=envelope,
        headers=headers,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    trace_id = get_trace_id()
    logger.exception("[%s] Unhandled error on %s %s: %s", trace_id, request.method, request.url.path, exc)
    envelope = format_error_envelope(
        detail=f"服务器内部错误: {str(exc)}",
        status_code=500,
        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        retryable=False,
        trace_id=trace_id,
    )
    return JSONResponse(
        status_code=500,
        content=envelope,
        headers={"X-Trace-Id": trace_id},
    )


# ---------------------------------------------------------------------------
# Health Route
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "research-radar", "version": "0.1.0"}


# ---------------------------------------------------------------------------
# Register Domain Routers
# ---------------------------------------------------------------------------

app.include_router(auth_router)
app.include_router(cases_router)
app.include_router(sources_router)
app.include_router(claims_router)
app.include_router(scans_router)
app.include_router(patches_router)
app.include_router(qa_router)
app.include_router(citations_router)
app.include_router(skills_router)
app.include_router(settings_router)


# ---------------------------------------------------------------------------
# SPA Static Files Mount
# ---------------------------------------------------------------------------

_STATIC_DIRS = [
    Path("/app/static"),                                      # Docker image
    Path(__file__).resolve().parent.parent / "app" / "dist",  # local dev
]
for _dir in _STATIC_DIRS:
    if _dir.exists():
        from fastapi.staticfiles import StaticFiles

        class _SpaStaticFiles(StaticFiles):
            """Serve index.html for unknown paths so client routes deep-link."""

            async def _fallback(self, path: str, scope):
                if path.startswith("api/") or path == "api":
                    raise StarletteHTTPException(status_code=404)
                return await super().get_response("index.html", scope)

            async def get_response(self, path: str, scope):
                try:
                    response = await super().get_response(path, scope)
                except StarletteHTTPException as exc:
                    if exc.status_code != 404:
                        raise
                    return await self._fallback(path, scope)
                if response.status_code == 404:
                    return await self._fallback(path, scope)
                return response

        app.mount("/", _SpaStaticFiles(directory=str(_dir), html=True), name="static")
        break
