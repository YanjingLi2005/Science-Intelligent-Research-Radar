"""Authentication, user administration, and system metrics API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from radar import auth as auth_service
from radar.config import get_settings
from radar.services.metrics_service import collect_metrics
from radar.services.scan_runner import recover_interrupted_scans
from radar.routes.deps import (
    _auth_rate_limited,
    _record_auth_attempt,
    admin_user,
    current_user,
)

router = APIRouter(tags=["auth"])


class AuthRequest(BaseModel):
    username: str
    password: str


class AuthOut(BaseModel):
    token: str | None = None
    username: str
    id: str | None = None
    role: str = "user"


class RoleUpdate(BaseModel):
    role: str


class AdminCreateUserRequest(BaseModel):
    username: str
    password: str


def _get_current_settings():
    try:
        import radar.api as api
        return getattr(api, "get_settings", get_settings)()
    except Exception:
        return get_settings()


@router.post("/api/auth/register", response_model=AuthOut)
def register_account(body: AuthRequest, request: Request) -> AuthOut:
    """Create a new account. Each account gets its own DB + settings."""
    if _auth_rate_limited("register", request):
        raise HTTPException(429, "注册请求过于频繁，请稍后再试")
    if not _get_current_settings().allow_registration:
        raise HTTPException(403, "当前已关闭公开注册，请联系管理员创建账号")
    try:
        user = auth_service.register(body.username, body.password)
        _record_auth_attempt("register", request)
    except ValueError as exc:
        _record_auth_attempt("register", request)
        raise HTTPException(400, str(exc))
    return AuthOut(username=user["username"], id=user["id"], role=user["role"])


@router.post("/api/auth/login", response_model=AuthOut)
def login_account(body: AuthRequest, request: Request) -> AuthOut:
    """Log in and mint a session token for subsequent /api requests."""
    if _auth_rate_limited("login", request):
        raise HTTPException(429, "登录尝试过于频繁，请稍后再试")
    try:
        token = auth_service.login(body.username, body.password)
    except ValueError as exc:
        _record_auth_attempt("login", request)
        raise HTTPException(401, str(exc))
    # A scan interrupted by a process crash in this user's own DB would stay
    # "running" and block new scans; recover it on login.
    tenant = auth_service.resolve_tenant(token)
    if tenant is not None:
        from radar.tenant import tenant_context

        with tenant_context(tenant):
            recover_interrupted_scans()
    user = auth_service.me(token)
    return AuthOut(
        token=token,
        username=user["username"] if user else body.username.strip(),
        id=user["id"] if user else None,
        role=user["role"] if user else "user",
    )


@router.post("/api/auth/logout")
def logout_account(request: Request) -> dict[str, bool]:
    """Invalidate the current session token."""
    auth_header = request.headers.get("Authorization", "")
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() == "bearer" and token:
        auth_service.logout(token)
    return {"ok": True}


@router.get("/api/auth/me")
def me_account(request: Request) -> dict[str, Any]:
    """Return the current user for the presented token."""
    auth_header = request.headers.get("Authorization", "")
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "未登录或登录已过期")
    user = auth_service.me(token)
    if user is None:
        raise HTTPException(401, "未登录或登录已过期")
    return user


@router.get("/api/admin/users")
def admin_users(_: dict[str, Any] = Depends(admin_user)) -> list[dict[str, Any]]:
    return auth_service.list_users()


@router.post("/api/admin/users")
def admin_create_user(
    body: AdminCreateUserRequest,
    _: dict[str, Any] = Depends(admin_user),
) -> dict[str, Any]:
    """Create a regular user account when public registration is closed."""
    try:
        user = auth_service.register(body.username, body.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"username": user["username"], "id": user["id"], "role": user["role"]}


@router.put("/api/admin/users/{user_id}/role")
def update_user_role(
    user_id: str,
    body: RoleUpdate,
    _: dict[str, Any] = Depends(admin_user),
) -> dict[str, Any]:
    try:
        return auth_service.set_role(user_id, body.role)
    except ValueError as exc:
        message = str(exc)
        raise HTTPException(404 if message == "用户不存在" else 400, message)


@router.get("/api/metrics/overview")
def metrics_overview(
    window_days: int = 30,
    _: dict[str, Any] = Depends(admin_user),
) -> dict[str, Any]:
    """Aggregate product metrics across every tenant for the admin dashboard."""
    if window_days < 1 or window_days > 3650:
        raise HTTPException(400, "window_days 必须在 1 到 3650 之间")
    return collect_metrics(window_days=window_days)
