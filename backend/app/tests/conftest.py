"""
Pytest fixtures shared across all tests.
"""

import os
import pytest
from datetime import date
from typing import Generator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://cpg:cpg_secret@localhost:5433/cpg_platform_test",
)

from app.core.database import Base, get_db
from app.main import app

# ── Test engine ───────────────────────────────────────────

TEST_DB_URL = os.environ["DATABASE_URL"]
test_engine = create_engine(TEST_DB_URL, pool_pre_ping=True)
TestSessionLocal = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """
    Create all tables once per session, drop them after.

    Loads every schema file in the same order as db/init/00_init.sql
    (Phase 1 base schema, then forecasting, insights, security) so the
    test database has the full table set -- previously this fixture
    only loaded db/schema.sql, silently leaving forecast_results,
    model_registry, insight_cache, users, audit_log, etc. missing
    unless Base.metadata.create_all()'s fallback happened to fire,
    which it never did since db/schema.sql always exists.
    """
    backend_root = __file__.replace("app/tests/conftest.py", "").replace("//", "/")
    schema_files = [
        "db/schema.sql",
        "db/schema_forecasting.sql",
        "db/schema_insights.sql",
        "db/schema_security.sql",
    ]

    loaded_any = False
    with test_engine.connect() as conn:
        for rel_path in schema_files:
            full_path = backend_root + rel_path
            try:
                with open(full_path) as f:
                    sql = f.read()
                conn.execute(text(sql))
                conn.commit()
                loaded_any = True
            except FileNotFoundError:
                continue

    if not loaded_any:
        # Fallback to SQLAlchemy create_all if no schema files were found at all
        Base.metadata.create_all(bind=test_engine)

    yield

    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(autouse=True)
def clean_tables(db: "Session"):
    """Truncate transactional tables before each test."""
    tables = [
        "competitor_pricing", "promo_windows", "marketing_campaigns",
        "dq_issues", "late_arrivals", "ingestion_fingerprints",
        "agg_revenue_daily", "fact_transactions", "staging_transactions",
        "dim_store", "dim_sku", "dim_region_demographics",
        "audit_log", "retraining_schedule_log",
        "conversation_messages", "conversation_sessions",
        "insight_cache", "insight_log",
        "forecast_results", "forecast_accuracy", "training_runs",
        "model_registry", "feature_store",
    ]
    for table in tables:
        db.execute(text(f"TRUNCATE {table} RESTART IDENTITY CASCADE"))
    db.commit()
    # users/roles are seeded data, not transactional -- truncated
    # separately and only down to the seeded roles, never per-test,
    # since individual tests create/use users via the auth fixtures
    # below and rely on roles existing throughout the session.
    db.execute(text("DELETE FROM users"))
    db.commit()


@pytest.fixture
def db() -> Generator[Session, None, None]:
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client(db: Session) -> TestClient:
    """
    TestClient with get_db overridden (as before) AND get_current_user
    overridden to return a real, seeded admin user by default.

    This means existing tests that call write/trigger endpoints don't
    need to thread auth headers through every request -- they get an
    authenticated admin user "for free", matching how most of this
    test suite was originally written before RBAC existed. The user
    is a real row in the test DB (not a fake id), so audit-log inserts
    that foreign-key against users.user_id succeed normally.

    Tests that specifically exercise RBAC (permission denial, role
    differences) should override get_current_user again with a
    different role's CurrentUser before making their request -- see
    test_security.py for the pattern.
    """
    from app.security.deps import CurrentUser, get_current_user
    from app.security.rbac import Role

    admin_user_id = create_test_user(db, email="default-test-admin@example.com", role="admin")

    def override_get_db():
        yield db

    def override_get_current_user():
        return CurrentUser(user_id=admin_user_id, role=Role.ADMIN)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


# ── Data factories ────────────────────────────────────────

def make_transaction_record(**overrides) -> dict:
    defaults = {
        "transaction_date": str(date.today()),
        "category_name":    "Electronics",
        "region_name":      "North America",
        "revenue":          150.00,
        "quantity":         2,
        "currency":         "USD",
        "source_name":      "test_push",
    }
    return {**defaults, **overrides}


def make_sku_record(**overrides) -> dict:
    defaults = {
        "sku_id":        "TEST-SKU-001",
        "sku_name":      "Test Product",
        "category_name": "Electronics",
        "brand":         "TestBrand",
        "list_price":    99.99,
        "is_active":     True,
    }
    return {**defaults, **overrides}


def seed_category(db: Session, name: str = "Electronics") -> int:
    row = db.execute(
        text("INSERT INTO dim_product_category (category_name) VALUES (:n) "
             "ON CONFLICT (category_name) DO UPDATE SET category_name=EXCLUDED.category_name "
             "RETURNING category_id"),
        {"n": name},
    ).first()
    db.commit()
    return row[0]


def seed_region(db: Session, name: str = "North America") -> int:
    row = db.execute(
        text("INSERT INTO dim_region (region_name) VALUES (:n) "
             "ON CONFLICT (region_name) DO UPDATE SET region_name=EXCLUDED.region_name "
             "RETURNING region_id"),
        {"n": name},
    ).first()
    db.commit()
    return row[0]


# -- Auth / RBAC test helpers --------------------------------

def create_test_user(db: Session, email: str = "test@example.com", role: str = "admin") -> int:
    """Create a user with the given role and return their user_id."""
    from app.security.rbac import hash_password

    role_id = db.execute(
        text("SELECT role_id FROM roles WHERE role_name = :r"), {"r": role}
    ).scalar()
    if role_id is None:
        raise ValueError(f"Role '{role}' not found -- has schema_security.sql been loaded?")

    row = db.execute(
        text("""
            INSERT INTO users (email, hashed_password, full_name, role_id)
            VALUES (:email, :pw, :name, :role_id)
            ON CONFLICT (email) DO UPDATE SET role_id = EXCLUDED.role_id
            RETURNING user_id
        """),
        {"email": email, "pw": hash_password("test-password-123"), "name": "Test User", "role_id": role_id},
    ).first()
    db.commit()
    return row[0]


def auth_headers(db: Session, role: str = "admin", email: str = None) -> dict:
    """
    Create a test user with the given role and return Authorization
    headers carrying a valid access token for them. Use as:

        r = client.post("/api/v1/reference/catalog/sku", json=..., headers=auth_headers(db, "admin"))
    """
    from app.security.rbac import Role, create_access_token

    email = email or f"test-{role}@example.com"
    user_id = create_test_user(db, email=email, role=role)
    token = create_access_token(user_id, Role(role))
    return {"Authorization": f"Bearer {token}"}
