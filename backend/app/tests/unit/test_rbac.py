"""
Unit tests for app.security.rbac -- no database, no HTTP, pure logic.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.security.rbac import (
    Permission,
    Role,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    role_has_permission,
    verify_password,
)


# -- Permission matrix -----------------------------------------

class TestPermissionMatrix:
    def test_admin_has_all_permissions(self):
        for perm in Permission:
            assert role_has_permission(Role.ADMIN, perm), f"admin missing {perm}"

    def test_viewer_only_has_read_data(self):
        assert role_has_permission(Role.VIEWER, Permission.READ_DATA)
        assert not role_has_permission(Role.VIEWER, Permission.TRIGGER_FORECAST)
        assert not role_has_permission(Role.VIEWER, Permission.WRITE_REFERENCE_DATA)
        assert not role_has_permission(Role.VIEWER, Permission.MANAGE_USERS)

    def test_analyst_can_trigger_but_not_manage(self):
        assert role_has_permission(Role.ANALYST, Permission.TRIGGER_FORECAST)
        assert role_has_permission(Role.ANALYST, Permission.TRIGGER_INSIGHT)
        assert role_has_permission(Role.ANALYST, Permission.INGEST_DATA)
        assert not role_has_permission(Role.ANALYST, Permission.MANAGE_USERS)
        assert not role_has_permission(Role.ANALYST, Permission.WRITE_REFERENCE_DATA)
        assert not role_has_permission(Role.ANALYST, Permission.VIEW_AUDIT_LOG)

    def test_only_admin_manages_users(self):
        assert role_has_permission(Role.ADMIN, Permission.MANAGE_USERS)
        assert not role_has_permission(Role.ANALYST, Permission.MANAGE_USERS)
        assert not role_has_permission(Role.VIEWER, Permission.MANAGE_USERS)


# -- Password hashing --------------------------------------------

class TestPasswordHashing:
    def test_hash_then_verify_succeeds(self):
        hashed = hash_password("correct-horse-battery-staple")
        assert verify_password("correct-horse-battery-staple", hashed)

    def test_wrong_password_fails(self):
        hashed = hash_password("correct-horse-battery-staple")
        assert not verify_password("wrong-password", hashed)

    def test_hash_is_not_plaintext(self):
        hashed = hash_password("my-password")
        assert hashed != "my-password"
        assert len(hashed) > 20

    def test_same_password_different_hashes(self):
        """bcrypt salts each hash differently."""
        h1 = hash_password("same-password")
        h2 = hash_password("same-password")
        assert h1 != h2
        assert verify_password("same-password", h1)
        assert verify_password("same-password", h2)

    def test_malformed_hash_fails_closed(self):
        """Verification against a garbage hash must return False, not raise."""
        assert verify_password("anything", "not-a-real-bcrypt-hash") is False


# -- JWT tokens ----------------------------------------------------

class TestJWTTokens:
    def test_access_token_roundtrip(self):
        token = create_access_token(user_id=42, role=Role.ANALYST)
        payload = decode_token(token)
        assert payload is not None
        assert payload.user_id == 42
        assert payload.role == Role.ANALYST
        assert payload.token_type == "access"

    def test_refresh_token_roundtrip(self):
        token = create_refresh_token(user_id=7, role=Role.ADMIN)
        payload = decode_token(token)
        assert payload is not None
        assert payload.user_id == 7
        assert payload.role == Role.ADMIN
        assert payload.token_type == "refresh"

    def test_garbage_token_returns_none(self):
        assert decode_token("not-a-real-token") is None

    def test_empty_token_returns_none(self):
        assert decode_token("") is None

    def test_tampered_token_rejected(self):
        token = create_access_token(user_id=1, role=Role.VIEWER)
        tampered = token[:-4] + "abcd"
        assert decode_token(tampered) is None

    def test_different_roles_distinguishable(self):
        admin_token = create_access_token(user_id=1, role=Role.ADMIN)
        viewer_token = create_access_token(user_id=1, role=Role.VIEWER)
        assert decode_token(admin_token).role == Role.ADMIN
        assert decode_token(viewer_token).role == Role.VIEWER
