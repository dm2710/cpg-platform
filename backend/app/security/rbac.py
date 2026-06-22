"""
Role-based access control core.

Three fixed roles, seeded in the database:
  admin   -- full access, including user management and reference-data writes
  analyst -- can read everything and trigger forecasts/insights/training,
             cannot manage users or write reference data (SKU/store/campaigns)
  viewer  -- read-only across the board

Permissions are expressed as a small fixed set of capability strings
rather than a per-endpoint ACL table, since the surface area here is
stable and a capability model is far easier to reason about and test
than per-route exceptions.

JWT access tokens carry the user's role as a claim, so authorization
checks never hit the database on the hot path -- only authentication
(login) and token refresh touch the users table.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

import bcrypt
from jose import JWTError, jwt

from app.core.config import get_settings

settings = get_settings()


# ── Roles and permissions ─────────────────────────────────

class Role(str, Enum):
    ADMIN   = "admin"
    ANALYST = "analyst"
    VIEWER  = "viewer"


class Permission(str, Enum):
    READ_DATA          = "read_data"           # analytics, forecasts, insights, dashboards
    TRIGGER_FORECAST    = "trigger_forecast"     # train models, generate predictions
    TRIGGER_INSIGHT      = "trigger_insight"      # call DeepSeek insight/Q&A endpoints
    WRITE_REFERENCE_DATA  = "write_reference_data"  # SKU catalog, stores, campaigns, promos
    INGEST_DATA            = "ingest_data"           # push/upload transaction data
    MANAGE_USERS             = "manage_users"          # create/deactivate users, change roles
    VIEW_AUDIT_LOG             = "view_audit_log"        # read the audit trail
    MANAGE_RETRAINING            = "manage_retraining"     # trigger/pause the retraining scheduler


ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.ADMIN: {
        Permission.READ_DATA, Permission.TRIGGER_FORECAST, Permission.TRIGGER_INSIGHT,
        Permission.WRITE_REFERENCE_DATA, Permission.INGEST_DATA,
        Permission.MANAGE_USERS, Permission.VIEW_AUDIT_LOG, Permission.MANAGE_RETRAINING,
    },
    Role.ANALYST: {
        Permission.READ_DATA, Permission.TRIGGER_FORECAST, Permission.TRIGGER_INSIGHT,
        Permission.INGEST_DATA,
    },
    Role.VIEWER: {
        Permission.READ_DATA,
    },
}


def role_has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())


# ── Password hashing ───────────────────────────────────────

def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed hash in the DB -- fail closed, never raise into a 500
        # that might leak details about why verification failed.
        return False


# ── JWT tokens ─────────────────────────────────────────────

class TokenPayload:
    def __init__(self, sub: str, role: str, exp: datetime, token_type: str):
        self.user_id = int(sub)
        self.role = Role(role)
        self.exp = exp
        self.token_type = token_type


def create_access_token(user_id: int, role: Role) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_access_token_minutes)
    payload = {"sub": str(user_id), "role": role.value, "exp": expire, "type": "access"}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: int, role: Role) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_token_days)
    payload = {"sub": str(user_id), "role": role.value, "exp": expire, "type": "refresh"}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> Optional[TokenPayload]:
    """Returns None on any validation failure (expired, malformed, bad signature)."""
    try:
        data = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
        return TokenPayload(
            sub=data["sub"],
            role=data["role"],
            exp=datetime.fromtimestamp(data["exp"], tz=timezone.utc),
            token_type=data.get("type", "access"),
        )
    except (JWTError, KeyError, ValueError):
        return None
