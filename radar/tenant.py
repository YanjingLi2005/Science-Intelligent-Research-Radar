"""Per-user tenancy context.

Multi-user deployments give every account its own SQLite database and its own
settings file, so one person's LLM key and cases are never visible to another.
The active tenant is carried in a ``contextvars.ContextVar`` set by the API's
auth dependency for the duration of each request. ``get_settings()`` and the DB
factory both consult it, which lets every existing service pick up the right
settings/database without per-service changes.
"""

from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from contextlib import contextmanager

from radar.config import PROJECT_ROOT


@dataclass(frozen=True)
class Tenant:
    user_id: str
    username: str
    data_dir: Path

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'research_radar.db'}"

    @property
    def settings_file(self) -> Path:
        return self.data_dir / "settings.local.env"


_tenant_var: ContextVar[Tenant | None] = ContextVar("radar_tenant", default=None)


def current_tenant() -> Tenant | None:
    """Return the tenant active for this request, or None outside a request."""
    return _tenant_var.get()


def _users_root() -> Path:
    return PROJECT_ROOT / "data" / "users"


def tenant_dir(username: str) -> Path:
    return _users_root() / username


@contextmanager
def tenant_context(tenant: Tenant | None):
    """Bind a tenant for the duration of a request scope."""
    token = _tenant_var.set(tenant)
    try:
        yield
    finally:
        _tenant_var.reset(token)
