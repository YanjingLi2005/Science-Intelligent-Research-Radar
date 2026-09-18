"""Shared dependency injection, authentication helpers, and utility functions for API routers."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from radar import auth as auth_service
from radar.db import get_session_factory
from radar.models import ResearchCase


# ---------------------------------------------------------------------------
# Service & Parsing Helpers
# ---------------------------------------------------------------------------

def _service(klass):
    """Resolve a domain service instance bound to the active tenant's DB factory."""
    return klass(get_session_factory())


def _parse_json_field(value: str, fallback):
    """Parse a JSON Form field defensively: malformed input is a 400."""
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        raise HTTPException(400, f"malformed JSON field: {value[:80]}")


def _source_tags(case: ResearchCase, source_id: str) -> list[str]:
    """Return normalized per-case tags for one source."""
    source_tags = (case.settings_json or {}).get("source_tags")
    if not isinstance(source_tags, dict):
        return []
    tags = source_tags.get(source_id)
    if not isinstance(tags, list):
        return []
    return [tag for tag in tags if isinstance(tag, str)]


def _set_case_source_tags(
    case: ResearchCase, source_ids: list[str], tags: list[str]
) -> list[str]:
    """Set the same normalized tag list for the given case source IDs."""
    settings = dict(case.settings_json or {})
    source_tags = settings.get("source_tags")
    if not isinstance(source_tags, dict):
        source_tags = {}
    normalized_tags = list(tags)
    for source_id in source_ids:
        source_tags[source_id] = list(normalized_tags)
    settings["source_tags"] = source_tags
    case.settings_json = settings
    return normalized_tags


# ---------------------------------------------------------------------------
# Auth Rate Limiting & User Resolution
# ---------------------------------------------------------------------------

_AUTH_RATE_MAX_ATTEMPTS = 10
_AUTH_RATE_WINDOW_SECONDS = 600
_AUTH_ATTEMPTS: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _get_auth_rate_max_attempts() -> int:
    try:
        import radar.api as api
        return getattr(api, "_AUTH_RATE_MAX_ATTEMPTS", _AUTH_RATE_MAX_ATTEMPTS)
    except Exception:
        return _AUTH_RATE_MAX_ATTEMPTS


def _get_auth_rate_window_seconds() -> int:
    try:
        import radar.api as api
        return getattr(api, "_AUTH_RATE_WINDOW_SECONDS", _AUTH_RATE_WINDOW_SECONDS)
    except Exception:
        return _AUTH_RATE_WINDOW_SECONDS


def _auth_rate_limited(prefix: str, request: Request) -> bool:
    key = f"{prefix}:{_client_ip(request)}"
    now = time.time()
    window = _get_auth_rate_window_seconds()
    attempts = [t for t in _AUTH_ATTEMPTS.get(key, []) if now - t < window]
    _AUTH_ATTEMPTS[key] = attempts
    return len(attempts) >= _get_auth_rate_max_attempts()


def _record_auth_attempt(prefix: str, request: Request) -> None:
    key = f"{prefix}:{_client_ip(request)}"
    now = time.time()
    window = _get_auth_rate_window_seconds()
    attempts = [t for t in _AUTH_ATTEMPTS.get(key, []) if now - t < window]
    attempts.append(now)
    _AUTH_ATTEMPTS[key] = attempts


def current_user(request: Request) -> dict[str, Any]:
    """Return the authenticated account for route-level authorization."""
    if os.environ.get("RADAR_AUTH_DISABLED") == "1":
        return {"id": "test-admin", "username": "test", "role": "admin"}
    auth_header = request.headers.get("Authorization", "")
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "未登录或登录已过期")
    user = auth_service.me(token)
    if user is None:
        raise HTTPException(401, "未登录或登录已过期")
    return user


def admin_user(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if user.get("role") != "admin":
        raise HTTPException(403, "仅管理员可访问")
    return user
