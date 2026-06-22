"""
Authentication/authorisation dependencies — auth disabled.

All endpoints that previously required a bearer token now accept
requests without any credentials. get_current_user and
require_permission return a synthetic admin user so all permission
checks pass transparently.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.security.rbac import Permission, Role, role_has_permission


@dataclass
class CurrentUser:
    user_id: int
    role: Role


# Synthetic admin user returned for every request — no token needed
_ANON_ADMIN = CurrentUser(user_id=0, role=Role.ADMIN)


def get_current_user() -> CurrentUser:
    return _ANON_ADMIN


def require_permission(permission: Permission):
    """No-op permission check — always grants access."""
    def _check() -> CurrentUser:
        return _ANON_ADMIN
    return _check


def require_role(*allowed_roles: Role):
    """No-op role check — always grants access."""
    def _check() -> CurrentUser:
        return _ANON_ADMIN
    return _check
