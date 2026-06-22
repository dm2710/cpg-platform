"""
Analytics endpoints — all reads from the Gold (agg_revenue_daily) layer.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.analytics import (
    CategoryOut,
    RegionOut,
    RevenueBreakdownItem,
    RevenuePeriod,
    SummaryKpi,
)
from app.schemas.base import BreakdownDimension, Granularity

router = APIRouter()


@router.get("/summary", response_model=SummaryKpi, summary="Platform-wide KPIs")
def get_summary(db: Session = Depends(get_db)):
    row = db.execute(text("""
        SELECT
            COALESCE(SUM(total_revenue), 0)   AS total_revenue,
            COALESCE(SUM(total_quantity), 0)   AS total_quantity,
            MIN(agg_date)                       AS earliest_date,
            MAX(agg_date)                       AS latest_date,
            COUNT(DISTINCT category_id)         AS category_count,
            COUNT(DISTINCT region_id)           AS region_count
        FROM agg_revenue_daily
    """)).mappings().first()
    return SummaryKpi(**dict(row))


@router.get("/revenue", response_model=list[RevenuePeriod], summary="Revenue time series")
def get_revenue(
    start_date:  Optional[date] = None,
    end_date:    Optional[date] = None,
    category_id: Optional[int]  = None,
    region_id:   Optional[int]  = None,
    granularity: Granularity    = Query(default=Granularity.DAY),
    db:          Session        = Depends(get_db),
):
    """
    Revenue time series bucketed by day / week / month.
    Filterable by category and region.
    """
    filters = []
    params:  dict = {"bucket": granularity.value}

    if start_date:
        filters.append("agg_date >= :start_date")
        params["start_date"] = start_date
    if end_date:
        filters.append("agg_date <= :end_date")
        params["end_date"] = end_date
    if category_id is not None:
        filters.append("category_id = :category_id")
        params["category_id"] = category_id
    if region_id is not None:
        filters.append("region_id = :region_id")
        params["region_id"] = region_id

    where = f"WHERE {' AND '.join(filters)}" if filters else ""

    rows = db.execute(
        text(f"""
            SELECT
                date_trunc(:bucket, agg_date)::date AS period,
                SUM(total_revenue)  AS revenue,
                SUM(total_quantity) AS quantity,
                SUM(txn_count)      AS txn_count
            FROM agg_revenue_daily
            {where}
            GROUP BY 1
            ORDER BY 1
        """),
        params,
    ).mappings().all()

    return [RevenuePeriod(**dict(r)) for r in rows]


@router.get(
    "/breakdown",
    response_model=list[RevenueBreakdownItem],
    summary="Revenue breakdown by dimension",
)
def get_breakdown(
    dimension:  BreakdownDimension = Query(...),
    start_date: Optional[date]     = None,
    end_date:   Optional[date]     = None,
    db:         Session            = Depends(get_db),
):
    """Revenue totals grouped by category, region, store, or source."""
    dim_map = {
        BreakdownDimension.CATEGORY: (
            "dim_product_category", "category_id", "category_id", "category_name"
        ),
        BreakdownDimension.REGION: (
            "dim_region", "region_id", "region_id", "region_name"
        ),
    }

    if dimension not in dim_map:
        # store / source breakdowns query fact_transactions directly
        return _breakdown_from_fact(dimension, start_date, end_date, db)

    dim_table, dim_fk, dim_pk, dim_name_col = dim_map[dimension]
    filters = []
    params: dict = {}

    if start_date:
        filters.append("a.agg_date >= :start_date")
        params["start_date"] = start_date
    if end_date:
        filters.append("a.agg_date <= :end_date")
        params["end_date"] = end_date

    where = f"WHERE {' AND '.join(filters)}" if filters else ""

    rows = db.execute(
        text(f"""
            SELECT
                d.{dim_name_col}            AS label,
                SUM(a.total_revenue)        AS revenue,
                SUM(a.total_quantity)       AS quantity
            FROM agg_revenue_daily a
            JOIN {dim_table} d ON d.{dim_pk} = a.{dim_fk}
            {where}
            GROUP BY d.{dim_name_col}
            ORDER BY revenue DESC
        """),
        params,
    ).mappings().all()

    total = sum(r["revenue"] for r in rows) or 1
    return [
        RevenueBreakdownItem(**dict(r), pct=round(float(r["revenue"]) / float(total) * 100, 2))
        for r in rows
    ]


def _breakdown_from_fact(dimension, start_date, end_date, db) -> list[RevenueBreakdownItem]:
    """Fallback for store / source breakdowns — queries fact_transactions."""
    label_col = "store_id" if dimension == BreakdownDimension.STORE else "ds.source_name"
    join_clause = (
        "JOIN dim_source ds ON ds.source_id = f.source_id"
        if dimension == BreakdownDimension.SOURCE else ""
    )

    filters = []
    params: dict = {}
    if start_date:
        filters.append("f.transaction_date >= :start_date")
        params["start_date"] = start_date
    if end_date:
        filters.append("f.transaction_date <= :end_date")
        params["end_date"] = end_date

    where = f"WHERE {' AND '.join(filters)}" if filters else ""

    rows = db.execute(
        text(f"""
            SELECT
                {label_col} AS label,
                SUM(f.revenue)  AS revenue,
                SUM(f.quantity) AS quantity
            FROM fact_transactions f
            {join_clause}
            {where}
            GROUP BY 1
            ORDER BY revenue DESC
        """),
        params,
    ).mappings().all()

    total = sum(r["revenue"] or 0 for r in rows) or 1
    return [
        RevenueBreakdownItem(
            label=str(r["label"] or "Unknown"),
            revenue=r["revenue"] or 0,
            quantity=r["quantity"] or 0,
            pct=round(float(r["revenue"] or 0) / float(total) * 100, 2),
        )
        for r in rows
    ]


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(db: Session = Depends(get_db)):
    rows = db.execute(
        text("SELECT category_id, category_name FROM dim_product_category ORDER BY category_name")
    ).mappings().all()
    return [CategoryOut(**dict(r)) for r in rows]


@router.get("/regions", response_model=list[RegionOut])
def list_regions(db: Session = Depends(get_db)):
    rows = db.execute(
        text("SELECT region_id, region_name FROM dim_region ORDER BY region_name")
    ).mappings().all()
    return [RegionOut(**dict(r)) for r in rows]
