"""Initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2024-01-01 00:00:00
"""

from pathlib import Path
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA_FILE = Path(__file__).parent.parent.parent / "db" / "schema.sql"


def upgrade() -> None:
    sql = SCHEMA_FILE.read_text()
    op.execute(sa.text(sql))


def downgrade() -> None:
    tables = [
        "competitor_pricing", "promo_windows", "marketing_campaigns",
        "weather_daily", "unit_mappings", "fx_rates", "field_aliases",
        "dq_issues", "late_arrivals", "ingestion_fingerprints",
        "agg_revenue_daily", "fact_transactions",
        "staging_transactions", "dim_source",
        "dim_store", "dim_region_demographics", "dim_region",
        "dim_sku", "dim_product_category", "dim_calendar",
    ]
    for table in tables:
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table} CASCADE"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS upsert_sku CASCADE"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS refresh_agg_revenue_daily CASCADE"))
    op.execute(sa.text("DROP VIEW IF EXISTS dq_summary CASCADE"))
