"""
Audit logging middleware.

Records every mutating HTTP request (POST/PUT/PATCH/DELETE) to the
audit_log table: who, what endpoint, when, from where, how long it
took, and the resulting status code. Read-only GET requests are not
logged here to keep the table from growing unboundedly on dashboard
traffic -- explicit security events (login, login failure, role
change) are logged directly by the auth endpoints instead, regardless
of HTTP method.

The user identity is read from the same bearer token the request
already carries (if any) -- this middleware does not require auth,
so unauthenticated requests are still logged with a null user_id,
which is itself useful signal (e.g. repeated failed/anonymous writes).

Failures to write the audit row never block or fail the actual
request -- audit logging is best-effort. A database hiccup on the
audit insert must not take down the API.
"""

from __future__ import annotations

import time

from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.security.rbac import decode_token

log = get_logger(__name__)

_AUDITED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Paths that are noisy or not meaningful to audit even though they're
# mutating in shape (e.g. token refresh happens constantly and is
# already covered by explicit login/refresh event logging elsewhere).
_EXCLUDED_PATH_PREFIXES = ("/metrics", "/docs", "/openapi.json")


class AuditLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        t0 = time.monotonic()
        response = await call_next(request)
        duration_ms = int((time.monotonic() - t0) * 1000)

        if request.method in _AUDITED_METHODS and not request.url.path.startswith(_EXCLUDED_PATH_PREFIXES):
            self._write_audit_row(request, response.status_code, duration_ms)

        return response

    def _write_audit_row(self, request: Request, status_code: int, duration_ms: int) -> None:
        user_id = None
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            payload = decode_token(auth_header[7:])
            if payload is not None:
                user_id = payload.user_id

        client_ip = request.client.host if request.client else None

        db = SessionLocal()
        try:
            db.execute(
                text("""
                    INSERT INTO audit_log
                        (user_id, action, method, path, status_code, ip_address, duration_ms)
                    VALUES (:user_id, 'http_request', :method, :path, :status, :ip, :duration)
                """),
                {
                    "user_id":  user_id,
                    "method":   request.method,
                    "path":     request.url.path,
                    "status":   status_code,
                    "ip":       client_ip,
                    "duration": duration_ms,
                },
            )
            db.commit()
        except Exception as exc:
            # Best-effort: never let audit logging break the actual request.
            log.warning("audit_log.write_failed", error=str(exc))
            db.rollback()
        finally:
            db.close()


def log_security_event(
    db,
    action: str,
    user_id: int | None = None,
    user_email: str | None = None,
    detail: dict | None = None,
) -> None:
    """
    Explicit security event logger for auth endpoints -- login success/
    failure, token refresh, role changes. Called directly (not via the
    middleware) so these are logged even though some are GET-adjacent
    or need richer detail than the generic HTTP audit row captures.
    """
    import json
    db.execute(
        text("""
            INSERT INTO audit_log (user_id, user_email, action, detail)
            VALUES (:user_id, :email, :action, CAST(:detail AS jsonb))
        """),
        {
            "user_id": user_id,
            "email":   user_email,
            "action":  action,
            "detail":  json.dumps(detail or {}),
        },
    )
    db.commit()
