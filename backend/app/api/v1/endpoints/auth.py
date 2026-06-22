"""
Auth and user management API.

POST /auth/login              -- email + password -> access + refresh tokens
POST /auth/refresh             -- refresh token -> new access token
GET  /auth/me                   -- current user's identity and permissions

User management (admin only):
POST /auth/users                 -- create a user
GET  /auth/users                  -- list users
PATCH /auth/users/{id}/role        -- change a user's role
PATCH /auth/users/{id}/deactivate   -- deactivate a user
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.logging import get_logger
from app.schemas.auth import (
    LoginRequest, MeResponse, RefreshRequest, TokenResponse,
    UserCreateRequest, UserOut, UserUpdateRoleRequest,
)
from app.schemas.base import MessageResponse
from app.security.audit import log_security_event
from app.security.deps import CurrentUser, get_current_user, require_permission
from app.security.rbac import (
    ROLE_PERMISSIONS, Role, create_access_token, create_refresh_token,
    decode_token, hash_password, verify_password,
)

router = APIRouter()
log    = get_logger(__name__)


@router.post("/login", response_model=TokenResponse, summary="Log in with email and password")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    row = db.execute(
        text("""
            SELECT u.user_id, u.hashed_password, u.is_active, r.role_name
            FROM users u JOIN roles r ON r.role_id = u.role_id
            WHERE u.email = :email
        """),
        {"email": req.email},
    ).mappings().first()

    if row is None or not verify_password(req.password, row["hashed_password"]):
        if row is not None:
            log_security_event(db, "login_failed", user_id=row["user_id"], user_email=req.email)
        else:
            log_security_event(db, "login_failed", user_email=req.email, detail={"reason": "no_such_user"})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")

    if not row["is_active"]:
        log_security_event(db, "login_failed", user_id=row["user_id"], user_email=req.email,
                           detail={"reason": "account_inactive"})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This account has been deactivated")

    role = Role(row["role_name"])
    access  = create_access_token(row["user_id"], role)
    refresh = create_refresh_token(row["user_id"], role)

    db.execute(text("UPDATE users SET last_login_at = now() WHERE user_id = :id"), {"id": row["user_id"]})
    log_security_event(db, "login_success", user_id=row["user_id"], user_email=req.email)

    from app.core.config import get_settings
    settings = get_settings()

    return TokenResponse(
        access_token=access, refresh_token=refresh,
        expires_in_minutes=settings.jwt_access_token_minutes,
        role=role.value, user_id=row["user_id"],
    )


@router.post("/refresh", response_model=TokenResponse, summary="Exchange a refresh token for a new access token")
def refresh(req: RefreshRequest, db: Session = Depends(get_db)):
    payload = decode_token(req.refresh_token)
    if payload is None or payload.token_type != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")

    row = db.execute(
        text("SELECT is_active FROM users WHERE user_id = :id"),
        {"id": payload.user_id},
    ).first()
    if row is None or not row[0]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User account is inactive or no longer exists")

    access  = create_access_token(payload.user_id, payload.role)
    new_refresh = create_refresh_token(payload.user_id, payload.role)

    from app.core.config import get_settings
    settings = get_settings()

    return TokenResponse(
        access_token=access, refresh_token=new_refresh,
        expires_in_minutes=settings.jwt_access_token_minutes,
        role=payload.role.value, user_id=payload.user_id,
    )


@router.get("/me", response_model=MeResponse, summary="Current user identity and permissions")
def me(user: CurrentUser = Depends(get_current_user)):
    return MeResponse(
        user_id=user.user_id,
        role=user.role.value,
        permissions=[p.value for p in ROLE_PERMISSIONS.get(user.role, set())],
    )


# ── User management (admin only) ──────────────────────────

from app.security.rbac import Permission


@router.post("/users", response_model=UserOut, summary="Create a user (admin only)")
def create_user(
    req: UserCreateRequest,
    db: Session = Depends(get_db),
    admin: CurrentUser = Depends(require_permission(Permission.MANAGE_USERS)),
):
    try:
        role = Role(req.role)
    except ValueError:
        raise HTTPException(422, f"Invalid role '{req.role}'. Must be one of: admin, analyst, viewer")

    existing = db.execute(text("SELECT 1 FROM users WHERE email = :e"), {"e": req.email}).first()
    if existing:
        raise HTTPException(409, f"A user with email '{req.email}' already exists")

    role_id = db.execute(text("SELECT role_id FROM roles WHERE role_name = :r"), {"r": role.value}).scalar()

    row = db.execute(
        text("""
            INSERT INTO users (email, hashed_password, full_name, role_id)
            VALUES (:email, :pw, :name, :role_id)
            RETURNING user_id, email, full_name, is_active, created_at, last_login_at
        """),
        {
            "email": req.email,
            "pw":    hash_password(req.password),
            "name":  req.full_name,
            "role_id": role_id,
        },
    ).mappings().first()
    db.commit()

    log_security_event(db, "user_created", user_id=admin.user_id,
                       detail={"created_user_email": req.email, "role": role.value})

    return UserOut(**dict(row), role=role.value)


@router.get("/users", response_model=list[UserOut], summary="List all users (admin only)")
def list_users(
    db: Session = Depends(get_db),
    admin: CurrentUser = Depends(require_permission(Permission.MANAGE_USERS)),
):
    rows = db.execute(text("""
        SELECT u.user_id, u.email, u.full_name, u.is_active, u.created_at, u.last_login_at, r.role_name
        FROM users u JOIN roles r ON r.role_id = u.role_id
        ORDER BY u.created_at DESC
    """)).mappings().all()
    return [UserOut(**{**dict(r), "role": r["role_name"]}) for r in rows]


@router.patch("/users/{user_id}/role", response_model=MessageResponse, summary="Change a user's role (admin only)")
def update_user_role(
    user_id: int,
    req: UserUpdateRoleRequest,
    db: Session = Depends(get_db),
    admin: CurrentUser = Depends(require_permission(Permission.MANAGE_USERS)),
):
    try:
        role = Role(req.role)
    except ValueError:
        raise HTTPException(422, f"Invalid role '{req.role}'. Must be one of: admin, analyst, viewer")

    role_id = db.execute(text("SELECT role_id FROM roles WHERE role_name = :r"), {"r": role.value}).scalar()
    result = db.execute(text("UPDATE users SET role_id = :rid WHERE user_id = :id"), {"rid": role_id, "id": user_id})
    if result.rowcount == 0:
        raise HTTPException(404, f"User {user_id} not found")
    db.commit()

    log_security_event(db, "role_changed", user_id=admin.user_id,
                       detail={"target_user_id": user_id, "new_role": role.value})

    return MessageResponse(message=f"User {user_id} role updated to '{role.value}'")


@router.patch("/users/{user_id}/deactivate", response_model=MessageResponse, summary="Deactivate a user (admin only)")
def deactivate_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: CurrentUser = Depends(require_permission(Permission.MANAGE_USERS)),
):
    if user_id == admin.user_id:
        raise HTTPException(400, "You cannot deactivate your own account")

    result = db.execute(text("UPDATE users SET is_active = FALSE WHERE user_id = :id"), {"id": user_id})
    if result.rowcount == 0:
        raise HTTPException(404, f"User {user_id} not found")
    db.commit()

    log_security_event(db, "user_deactivated", user_id=admin.user_id, detail={"target_user_id": user_id})

    return MessageResponse(message=f"User {user_id} deactivated")


# ── Audit log ──────────────────────────────────────────────

from app.schemas.base import PaginatedResponse
from app.schemas.auth import AuditLogOut


@router.get("/audit-log", response_model=PaginatedResponse[AuditLogOut], summary="View audit log (admin only)")
def view_audit_log(
    page: int      = 1,
    page_size: int = 50,
    action:    str | None = None,
    user_id:   int | None = None,
    db: Session = Depends(get_db),
    admin: CurrentUser = Depends(require_permission(Permission.VIEW_AUDIT_LOG)),
):
    filters, params = [], {}
    if action:
        filters.append("action = :action"); params["action"] = action
    if user_id:
        filters.append("user_id = :uid"); params["uid"] = user_id
    where = f"WHERE {' AND '.join(filters)}" if filters else ""

    total = db.execute(text(f"SELECT COUNT(*) FROM audit_log {where}"), params).scalar()
    offset = (page - 1) * page_size
    rows = db.execute(
        text(f"""
            SELECT audit_id, occurred_at, user_id, user_email, action,
                   method, path, status_code, ip_address, duration_ms, detail
            FROM audit_log {where}
            ORDER BY occurred_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {**params, "limit": page_size, "offset": offset},
    ).mappings().all()

    return PaginatedResponse(
        data=[AuditLogOut(**dict(r)) for r in rows],
        total=total, page=page, page_size=page_size,
        pages=-(-total // page_size) if total else 0,
    )
