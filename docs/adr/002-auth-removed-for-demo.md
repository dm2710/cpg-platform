# ADR 002 — Remove authentication for the demo environment

**Status:** Accepted (demo only — must be reversed before any production deployment)  
**Date:** 2026-06-22  
**Deciders:** Engineering team  

---

## Context

The platform was built with a full JWT-based authentication and RBAC system:
- `POST /api/v1/auth/login` issues access + refresh tokens
- `require_permission(Permission.X)` FastAPI dependencies guard every write endpoint
- Three roles: `admin`, `analyst`, `viewer`
- A `seed_admin.py` script creates the initial admin user on first boot

During demo preparation, authentication caused repeated friction:
1. The `generate_synthetic_data.py` data-loading script needed to log in first, obtain a token, and pass it as a header on every batch request
2. The frontend needed a login page, token storage in localStorage, and an `Authorization` header on every axios request
3. After `docker compose down`, the postgres volume was sometimes reset, wiping the seeded admin user — the next `docker compose up` would leave the API with no valid user, causing every request to return 401 until the seed script was re-run manually
4. The DeepSeek API key issue meant the container restart cycle was already long; adding auth debugging on top made iteration slow

The project is an internal analytics tool being demonstrated to a single audience. There is no sensitive data (all records are synthetic). The risk of unauthenticated access is negligible in this context.

---

## Decision

Replace the `security/deps.py` module with a no-op implementation that returns a synthetic admin user for every request without inspecting any token.

```python
_ANON_ADMIN = CurrentUser(user_id=0, role=Role.ADMIN)

def get_current_user() -> CurrentUser:
    return _ANON_ADMIN

def require_permission(permission: Permission):
    def _check() -> CurrentUser:
        return _ANON_ADMIN
    return _check
```

All endpoint signatures, permission decorators, and RBAC logic remain in place — only the enforcement is bypassed. The full auth system can be restored by reverting `security/deps.py` to its original implementation.

---

## Alternatives considered

| Option | Outcome |
|---|---|
| Keep auth, fix the seed script | Seed script was fixed but `SEED_ADMIN` env var not picked up after volume reset — compounding issue |
| Use a static API key instead of JWT | Simpler than JWT but still requires header injection in every script and axios call |
| Auto-login on frontend startup | Reduces friction for UI but doesn't fix the data-loading script |

---

## Consequences

**Positive:**
- Zero friction for demo data loading, UI exploration, and API testing
- No token expiry during a live presentation
- The `audit_log` table still receives entries (user_id=0) so the logging infrastructure remains exercised

**Negative / risks:**
- Any network-accessible deployment of this build is completely open — **do not deploy to a public URL**
- The `audit_log` entries have `user_id=0` (foreign key to a non-existent user row) — referential integrity is relaxed for the demo
- The login endpoint (`POST /api/v1/auth/login`) still works correctly but is never called

**Reverting auth:**
1. Replace `backend/app/security/deps.py` with the original JWT implementation
2. Set `SEED_ADMIN: "true"`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` in `docker-compose.yml`
3. Update the frontend `App.jsx` to include `AuthProvider` and `AuthGate`
4. Update `client.js` to send `Authorization: Bearer <token>` on every request
5. Update `generate_synthetic_data.py` to call `/auth/login` before pushing data
