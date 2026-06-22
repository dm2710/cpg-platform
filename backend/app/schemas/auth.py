"""Auth and user management schemas."""

from datetime import datetime
from typing import Optional

from pydantic import EmailStr, Field

from app.schemas.base import CamelBase


class LoginRequest(CamelBase):
    email:    EmailStr
    password: str = Field(..., min_length=1)


class TokenResponse(CamelBase):
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    expires_in_minutes: int
    role:          str
    user_id:       int


class RefreshRequest(CamelBase):
    refresh_token: str


class UserCreateRequest(CamelBase):
    email:     EmailStr
    password:  str           = Field(..., min_length=8, max_length=128)
    full_name: Optional[str] = None
    role:      str           = Field(..., description="admin | analyst | viewer")


class UserOut(CamelBase):
    user_id:       int
    email:         str
    full_name:     Optional[str]
    role:          str
    is_active:     bool
    created_at:    datetime
    last_login_at: Optional[datetime]


class UserUpdateRoleRequest(CamelBase):
    role: str = Field(..., description="admin | analyst | viewer")


class MeResponse(CamelBase):
    user_id:   int
    role:      str
    permissions: list[str]


class AuditLogOut(CamelBase):
    audit_id:    int
    occurred_at: datetime
    user_id:     Optional[int]
    user_email:  Optional[str]
    action:      str
    method:      Optional[str]
    path:        Optional[str]
    status_code: Optional[int]
    ip_address:  Optional[str]
    duration_ms: Optional[int]
    detail:      Optional[dict]
