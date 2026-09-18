"""Modular APIRouter packages for Research Radar."""

from __future__ import annotations

from radar.routes.auth import router as auth_router
from radar.routes.cases import router as cases_router
from radar.routes.citations import router as citations_router
from radar.routes.claims import router as claims_router
from radar.routes.patches import router as patches_router
from radar.routes.qa import router as qa_router
from radar.routes.scans import router as scans_router
from radar.routes.settings import router as settings_router
from radar.routes.skills import router as skills_router
from radar.routes.sources import router as sources_router

__all__ = [
    "auth_router",
    "cases_router",
    "citations_router",
    "claims_router",
    "patches_router",
    "qa_router",
    "scans_router",
    "settings_router",
    "skills_router",
    "sources_router",
]
