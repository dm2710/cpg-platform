"""
Integration tests for auth and RBAC.

Unlike the rest of the integration suite, these tests deliberately
clear the default admin auth override from the `client` fixture so
they exercise the *real* authentication and authorization path --
real JWTs, real permission checks, real 401/403 responses.
"""

import pytest

from app.main import app
from app.security.deps import get_current_user
from app.tests.conftest import auth_headers, create_test_user


def _clear_auth_override(client):
    """Remove the default-admin override so requests go through real auth."""
    app.dependency_overrides.pop(get_current_user, None)


# -- Login ---------------------------------------------------

class TestLogin:
    def test_login_succeeds_with_correct_password(self, client, db):
        from app.security.rbac import hash_password
        from sqlalchemy import text

        role_id = db.execute(text("SELECT role_id FROM roles WHERE role_name='analyst'")).scalar()
        db.execute(
            text("INSERT INTO users (email, hashed_password, role_id) VALUES (:e, :p, :r)"),
            {"e": "login-test@example.com", "p": hash_password("correct-horse-battery"), "r": role_id},
        )
        db.commit()

        _clear_auth_override(client)
        r = client.post("/api/v1/auth/login", json={
            "email": "login-test@example.com", "password": "correct-horse-battery",
        })
        assert r.status_code == 200
        data = r.json()
        assert "accessToken" in data
        assert "refreshToken" in data
        assert data["role"] == "analyst"

    def test_login_fails_with_wrong_password(self, client, db):
        from app.security.rbac import hash_password
        from sqlalchemy import text

        role_id = db.execute(text("SELECT role_id FROM roles WHERE role_name='viewer'")).scalar()
        db.execute(
            text("INSERT INTO users (email, hashed_password, role_id) VALUES (:e, :p, :r)"),
            {"e": "wrong-pw@example.com", "p": hash_password("the-real-password"), "r": role_id},
        )
        db.commit()

        _clear_auth_override(client)
        r = client.post("/api/v1/auth/login", json={
            "email": "wrong-pw@example.com", "password": "not-the-real-password",
        })
        assert r.status_code == 401

    def test_login_fails_for_unknown_email(self, client, db):
        _clear_auth_override(client)
        r = client.post("/api/v1/auth/login", json={
            "email": "nobody@example.com", "password": "whatever",
        })
        assert r.status_code == 401

    def test_login_fails_for_inactive_user(self, client, db):
        from app.security.rbac import hash_password
        from sqlalchemy import text

        role_id = db.execute(text("SELECT role_id FROM roles WHERE role_name='viewer'")).scalar()
        db.execute(
            text("INSERT INTO users (email, hashed_password, role_id, is_active) VALUES (:e, :p, :r, FALSE)"),
            {"e": "inactive@example.com", "p": hash_password("pw"), "r": role_id},
        )
        db.commit()

        _clear_auth_override(client)
        r = client.post("/api/v1/auth/login", json={"email": "inactive@example.com", "password": "pw"})
        assert r.status_code == 401


# -- Token refresh ---------------------------------------------

class TestRefresh:
    def test_refresh_with_valid_token(self, client, db):
        from app.security.rbac import Role, create_refresh_token

        user_id = create_test_user(db, email="refresh-test@example.com", role="analyst")
        refresh_token = create_refresh_token(user_id, Role.ANALYST)

        _clear_auth_override(client)
        r = client.post("/api/v1/auth/refresh", json={"refreshToken": refresh_token})
        assert r.status_code == 200
        assert "accessToken" in r.json()

    def test_refresh_rejects_access_token(self, client, db):
        """An access token must not work as a refresh token."""
        from app.security.rbac import Role, create_access_token

        user_id = create_test_user(db, email="refresh-reject@example.com", role="viewer")
        access_token = create_access_token(user_id, Role.VIEWER)

        _clear_auth_override(client)
        r = client.post("/api/v1/auth/refresh", json={"refreshToken": access_token})
        assert r.status_code == 401

    def test_refresh_rejects_garbage_token(self, client):
        _clear_auth_override(client)
        r = client.post("/api/v1/auth/refresh", json={"refreshToken": "not-a-real-token"})
        assert r.status_code == 401


# -- /auth/me --------------------------------------------------

class TestMe:
    def test_me_requires_auth(self, client):
        _clear_auth_override(client)
        r = client.get("/api/v1/auth/me")
        assert r.status_code == 401

    def test_me_returns_role_and_permissions(self, client, db):
        _clear_auth_override(client)
        headers = auth_headers(db, role="analyst")
        r = client.get("/api/v1/auth/me", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data["role"] == "analyst"
        assert "triggerForecast" in data["permissions"]
        assert "manageUsers" not in data["permissions"]


# -- RBAC enforcement on a real write endpoint -----------------

class TestRoleBasedAccess:
    def test_viewer_cannot_write_reference_data(self, client, db):
        _clear_auth_override(client)
        headers = auth_headers(db, role="viewer")
        r = client.post("/api/v1/reference/catalog/sku", json={
            "sku_id": "TEST-1", "sku_name": "Test", "category_name": "Electronics",
        }, headers=headers)
        assert r.status_code == 403

    def test_analyst_cannot_write_reference_data(self, client, db):
        _clear_auth_override(client)
        headers = auth_headers(db, role="analyst")
        r = client.post("/api/v1/reference/catalog/sku", json={
            "sku_id": "TEST-2", "sku_name": "Test", "category_name": "Electronics",
        }, headers=headers)
        assert r.status_code == 403

    def test_admin_can_write_reference_data(self, client, db):
        _clear_auth_override(client)
        headers = auth_headers(db, role="admin")
        r = client.post("/api/v1/reference/catalog/sku", json={
            "sku_id": "TEST-3", "sku_name": "Test", "category_name": "Electronics",
        }, headers=headers)
        assert r.status_code == 200

    def test_no_token_returns_401_not_403(self, client, db):
        """Missing auth should be 401 (unauthenticated), not 403 (forbidden)."""
        _clear_auth_override(client)
        r = client.post("/api/v1/reference/catalog/sku", json={
            "sku_id": "TEST-4", "sku_name": "Test", "category_name": "Electronics",
        })
        assert r.status_code == 401

    def test_analyst_can_trigger_forecast(self, client, db):
        _clear_auth_override(client)
        headers = auth_headers(db, role="analyst")
        r = client.post(
            "/api/v1/forecasting/train?run_sync=true",
            json={"model_names": ["lightgbm"], "horizon_days": 14},
            headers=headers,
        )
        # 200 regardless of whether any segment actually trains (no data
        # seeded here) -- the point is RBAC let the request through.
        assert r.status_code == 200

    def test_viewer_cannot_trigger_forecast(self, client, db):
        _clear_auth_override(client)
        headers = auth_headers(db, role="viewer")
        r = client.post(
            "/api/v1/forecasting/train?run_sync=true",
            json={"model_names": ["lightgbm"], "horizon_days": 14},
            headers=headers,
        )
        assert r.status_code == 403


# -- User management (admin only) -------------------------------

class TestUserManagement:
    def test_admin_can_create_user(self, client, db):
        _clear_auth_override(client)
        headers = auth_headers(db, role="admin")
        r = client.post("/api/v1/auth/users", json={
            "email": "new-user@example.com", "password": "a-secure-password-123", "role": "viewer",
        }, headers=headers)
        assert r.status_code == 200
        assert r.json()["role"] == "viewer"

    def test_non_admin_cannot_create_user(self, client, db):
        _clear_auth_override(client)
        headers = auth_headers(db, role="analyst")
        r = client.post("/api/v1/auth/users", json={
            "email": "blocked-user@example.com", "password": "a-secure-password-123", "role": "viewer",
        }, headers=headers)
        assert r.status_code == 403

    def test_admin_can_list_users(self, client, db):
        _clear_auth_override(client)
        headers = auth_headers(db, role="admin")
        r = client.get("/api/v1/auth/users", headers=headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_admin_cannot_deactivate_self(self, client, db):
        from app.security.rbac import Role, create_access_token

        _clear_auth_override(client)
        user_id = create_test_user(db, email="self-deactivate@example.com", role="admin")
        token = create_access_token(user_id, Role.ADMIN)

        r = client.patch(
            f"/api/v1/auth/users/{user_id}/deactivate",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400

    def test_invalid_role_rejected(self, client, db):
        _clear_auth_override(client)
        headers = auth_headers(db, role="admin")
        r = client.post("/api/v1/auth/users", json={
            "email": "bad-role@example.com", "password": "a-secure-password-123", "role": "superuser",
        }, headers=headers)
        assert r.status_code == 422


# -- Audit log ---------------------------------------------------

class TestAuditLog:
    def test_mutating_request_creates_audit_row(self, client, db):
        from sqlalchemy import text

        _clear_auth_override(client)
        headers = auth_headers(db, role="admin")
        client.post("/api/v1/reference/catalog/sku", json={
            "sku_id": "AUDIT-TEST", "sku_name": "Test", "category_name": "Electronics",
        }, headers=headers)

        count = db.execute(
            text("SELECT COUNT(*) FROM audit_log WHERE path LIKE '%catalog/sku%'")
        ).scalar()
        assert count >= 1

    def test_admin_can_view_audit_log(self, client, db):
        _clear_auth_override(client)
        headers = auth_headers(db, role="admin")
        r = client.get("/api/v1/auth/audit-log", headers=headers)
        assert r.status_code == 200
        assert "data" in r.json()

    def test_non_admin_cannot_view_audit_log(self, client, db):
        _clear_auth_override(client)
        headers = auth_headers(db, role="analyst")
        r = client.get("/api/v1/auth/audit-log", headers=headers)
        assert r.status_code == 403
