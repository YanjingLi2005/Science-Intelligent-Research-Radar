"""User accounts and session tokens for multi-user deployments.

Accounts live in a small global ``data/users.db``; each account's data and
settings live under ``data/users/<username>/`` (see ``radar.tenant``). Passwords
are hashed with PBKDF2-HMAC-SHA256, session tokens are random and stored as
hashes, so a leaked database does not expose credentials.
"""

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from radar.config import PROJECT_ROOT
from radar.tenant import Tenant, tenant_dir

_ACCOUNTS_DB = PROJECT_ROOT / "data" / "users.db"
_PBKDF2_ITERATIONS = 200_000
_USERNAME_RE = r"^[A-Za-z0-9_.-]{2,32}$"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _accounts_conn() -> sqlite3.Connection:
    _ACCOUNTS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_ACCOUNTS_DB)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user'
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()
    }
    if "role" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")
        # Existing installs predate roles: keep the earliest account as admin
        # so admin-only routes remain reachable.
        conn.execute(
            "UPDATE users SET role='admin' WHERE id = (SELECT id FROM users ORDER BY created_at ASC, rowid ASC LIMIT 1)"
        )
    else:
        admin_count = conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin'"
        ).fetchone()[0]
        if admin_count == 0:
            conn.execute(
                "UPDATE users SET role='admin' WHERE id = (SELECT id FROM users ORDER BY created_at ASC, rowid ASC LIMIT 1)"
            )
    conn.commit()
    return conn


def _hash_password(password: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return digest.hex()


def register(username: str, password: str) -> dict:
    """Create an account and its per-user data directory."""
    import re

    username = username.strip()
    if not re.match(_USERNAME_RE, username):
        raise ValueError("用户名只能包含字母、数字、点、下划线或连字符（2-32 位）")
    if len(password) < 8:
        raise ValueError("密码至少 8 位")
    conn = _accounts_conn()
    existing = conn.execute(
        "SELECT 1 FROM users WHERE username = ?", (username,)
    ).fetchone()
    if existing:
        conn.close()
        raise ValueError("用户名已被注册")
    is_first_user = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    role = "admin" if is_first_user else "user"
    user_id = secrets.token_hex(16)
    salt = secrets.token_bytes(16)
    conn.execute(
        "INSERT INTO users (id, username, password_hash, salt, created_at, role) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, username, _hash_password(password, salt), salt.hex(), _now(), role),
    )
    conn.commit()
    conn.close()
    data_dir = tenant_dir(username)
    data_dir.mkdir(parents=True, exist_ok=True)
    return {"id": user_id, "username": username, "role": role}


def login(username: str, password: str) -> str:
    """Verify credentials and mint a session token."""
    conn = _accounts_conn()
    row = conn.execute(
        "SELECT id, username, password_hash, salt, role FROM users WHERE username = ?",
        (username.strip(),),
    ).fetchone()
    if row is None:
        conn.close()
        raise ValueError("用户名或密码错误")
    user_id, stored_username, stored_hash, salt_hex, _role = row
    salt = bytes.fromhex(salt_hex)
    if not hmac.compare_digest(_hash_password(password, salt), stored_hash):
        conn.close()
        raise ValueError("用户名或密码错误")
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at) VALUES (?, ?, ?)",
        (token_hash, user_id, _now()),
    )
    conn.commit()
    conn.close()
    tenant_dir(stored_username).mkdir(parents=True, exist_ok=True)
    return token


def logout(token: str) -> None:
    conn = _accounts_conn()
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
    conn.commit()
    conn.close()


def resolve_tenant(token: str) -> Tenant | None:
    """Map a session token to a Tenant, or None when invalid/expired."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    conn = _accounts_conn()
    row = conn.execute(
        """
        SELECT u.id, u.username FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = ?
        """,
        (token_hash,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    user_id, username = row
    data_dir = tenant_dir(username)
    data_dir.mkdir(parents=True, exist_ok=True)
    return Tenant(user_id=user_id, username=username, data_dir=data_dir)


def me(token: str) -> dict | None:
    tenant = resolve_tenant(token)
    if tenant is None:
        return None
    conn = _accounts_conn()
    row = conn.execute(
        "SELECT id, username, role FROM users WHERE id = ?", (tenant.user_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    user_id, username, role = row
    return {"username": username, "id": user_id, "role": role or "user"}


def list_users() -> list[dict]:
    """Return the global account list for administrators."""
    conn = _accounts_conn()
    rows = conn.execute(
        "SELECT id, username, role, created_at FROM users ORDER BY created_at, username"
    ).fetchall()
    conn.close()
    return [
        {"id": user_id, "username": username, "role": role or "user", "created_at": created_at}
        for user_id, username, role, created_at in rows
    ]


def set_role(user_id: str, role: str) -> dict:
    """Set an account role, retaining at least one administrator."""
    if role not in ("admin", "user"):
        raise ValueError("role must be 'admin' or 'user'")
    conn = _accounts_conn()
    row = conn.execute(
        "SELECT id, username, role, created_at FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if row is None:
        conn.close()
        raise ValueError("用户不存在")
    if row[2] == "admin" and role == "user":
        admin_count = conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin'"
        ).fetchone()[0]
        if admin_count <= 1:
            conn.close()
            raise ValueError("不能降级最后一个管理员")
    conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    conn.commit()
    conn.close()
    return {"id": row[0], "username": row[1], "role": role, "created_at": row[3]}
