"""
Reports API — Phase 4.

GET /reports/pdf/executive-summary  — PDF report with KPIs + 5 insight sections
GET /reports/csv/revenue            — CSV export of daily revenue
GET /reports/csv/category-breakdown — CSV export of category totals
GET /reports/csv/region-breakdown   — CSV export of region totals
GET /reports/csv/forecast           — CSV export of forecast series
"""

import csv
import io
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.logging import get_logger
from app.forecasting.features.engineer import segment_key
from app.insights.cache.insight_cache import cache_get, cache_set
from app.insights.context.builders import resolve_segment_label
from app.insights.engines.insight_engines import (
    ExecutiveSummaryEngine,
    ForecastExplanationEngine,
    RevenueDriverAnalysisEngine,
    TrendSummarizationEngine,
)

router = APIRouter()
log    = get_logger(__name__)


# ── CSV exports ───────────────────────────────────────────

def _csv_response(rows: list[dict], filename: str) -> StreamingResponse:
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/csv/revenue", summary="Export daily revenue as CSV")
def export_revenue_csv(
    category_id: Optional[int] = None,
    region_id:   Optional[int] = None,
    start_date:  Optional[date] = None,
    end_date:    Optional[date] = None,
    db:          Session = Depends(get_db),
):
    start_date = start_date or (date.today() - timedelta(days=90))
    end_date   = end_date   or date.today()

    filters, params = [], {"start": start_date, "end": end_date}
    if category_id is not None:
        filters.append("category_id = :cat"); params["cat"] = category_id
    if region_id is not None:
        filters.append("region_id = :region"); params["region"] = region_id
    extra = (" AND " + " AND ".join(filters)) if filters else ""

    rows = db.execute(
        text(f"""
            SELECT agg_date, total_revenue, total_quantity, txn_count
            FROM agg_revenue_daily
            WHERE agg_date BETWEEN :start AND :end {extra}
            ORDER BY agg_date
        """),
        params,
    ).mappings().all()

    out = [
        {
            "date":     str(r["agg_date"]),
            "revenue":  float(r["total_revenue"]),
            "quantity": int(r["total_quantity"] or 0),
            "txn_count": int(r["txn_count"] or 0),
        }
        for r in rows
    ]
    return _csv_response(out, f"revenue_{start_date}_{end_date}.csv")


@router.get("/csv/category-breakdown", summary="Export category breakdown as CSV")
def export_category_csv(
    start_date: Optional[date] = None,
    end_date:   Optional[date] = None,
    db:         Session = Depends(get_db),
):
    start_date = start_date or (date.today() - timedelta(days=90))
    end_date   = end_date   or date.today()

    rows = db.execute(text("""
        SELECT dpc.category_name, SUM(a.total_revenue) AS revenue, SUM(a.total_quantity) AS quantity
        FROM agg_revenue_daily a
        JOIN dim_product_category dpc ON dpc.category_id = a.category_id
        WHERE a.agg_date BETWEEN :s AND :e
        GROUP BY dpc.category_name ORDER BY revenue DESC
    """), {"s": start_date, "e": end_date}).mappings().all()

    total = sum(float(r["revenue"]) for r in rows) or 1
    out = [
        {
            "category":     r["category_name"],
            "revenue":      float(r["revenue"]),
            "quantity":     int(r["quantity"] or 0),
            "pct_of_total": round(float(r["revenue"]) / total * 100, 2),
        }
        for r in rows
    ]
    return _csv_response(out, f"category_breakdown_{start_date}_{end_date}.csv")


@router.get("/csv/region-breakdown", summary="Export region breakdown as CSV")
def export_region_csv(
    start_date: Optional[date] = None,
    end_date:   Optional[date] = None,
    db:         Session = Depends(get_db),
):
    start_date = start_date or (date.today() - timedelta(days=90))
    end_date   = end_date   or date.today()

    rows = db.execute(text("""
        SELECT dr.region_name, SUM(a.total_revenue) AS revenue, SUM(a.total_quantity) AS quantity
        FROM agg_revenue_daily a
        JOIN dim_region dr ON dr.region_id = a.region_id
        WHERE a.agg_date BETWEEN :s AND :e
        GROUP BY dr.region_name ORDER BY revenue DESC
    """), {"s": start_date, "e": end_date}).mappings().all()

    total = sum(float(r["revenue"]) for r in rows) or 1
    out = [
        {
            "region":       r["region_name"],
            "revenue":      float(r["revenue"]),
            "quantity":     int(r["quantity"] or 0),
            "pct_of_total": round(float(r["revenue"]) / total * 100, 2),
        }
        for r in rows
    ]
    return _csv_response(out, f"region_breakdown_{start_date}_{end_date}.csv")


@router.get("/csv/forecast", summary="Export forecast series as CSV")
def export_forecast_csv(
    category_id: Optional[int] = None,
    region_id:   Optional[int] = None,
    db:          Session = Depends(get_db),
):
    seg = segment_key(category_id, region_id)
    rows = db.execute(text("""
        SELECT forecast_date, model_name, predicted_revenue, lower_80, upper_80, actual_revenue, error_pct
        FROM forecast_results
        WHERE segment_key = :seg
        ORDER BY forecast_date
    """), {"seg": seg}).mappings().all()

    out = [
        {
            "date":              str(r["forecast_date"]),
            "model":             r["model_name"],
            "predicted_revenue": float(r["predicted_revenue"]),
            "lower_80":          float(r["lower_80"]) if r["lower_80"] else None,
            "upper_80":          float(r["upper_80"]) if r["upper_80"] else None,
            "actual_revenue":    float(r["actual_revenue"]) if r["actual_revenue"] else None,
            "error_pct":         float(r["error_pct"]) if r["error_pct"] else None,
        }
        for r in rows
    ]
    return _csv_response(out, f"forecast_{seg.replace('|','_').replace('=','')}.csv")


# ── PDF executive report ──────────────────────────────────

@router.get("/pdf/executive-summary", summary="Generate executive summary PDF")
async def export_executive_pdf(
    category_id:   Optional[int] = None,
    region_id:     Optional[int] = None,
    lookback_days: int = Query(default=90, ge=14, le=365),
    horizon_days:  int = Query(default=30, ge=7, le=365),
    db:            Session = Depends(get_db),
):
    """
    Generates a board-ready PDF combining KPIs, the executive summary,
    trend narrative, top revenue drivers, and forecast explanation —
    each pulled from cache if available, generated fresh otherwise.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    )

    seg = segment_key(category_id, region_id)
    seg_label = resolve_segment_label(db, category_id, region_id)

    # ── Gather the four insight sections (cache-first) ────
    async def _get_or_generate(itype, engine_cls, params, *args):
        hit = cache_get(db, itype, seg, params)
        if hit:
            return hit.insight_text, hit.confidence
        result = await engine_cls(db).generate(*args)
        cache_set(db, itype, seg, params, result, category_id=category_id, region_id=region_id)
        return result.insight_text, result.confidence

    exec_text, exec_conf = await _get_or_generate(
        "executive", ExecutiveSummaryEngine,
        {"lookback": lookback_days, "horizon": horizon_days},
        category_id, region_id, lookback_days, horizon_days,
    )
    trend_text, trend_conf = await _get_or_generate(
        "trend", TrendSummarizationEngine,
        {"lookback_days": lookback_days},
        category_id, region_id, lookback_days,
    )
    driver_text, driver_conf = await _get_or_generate(
        "driver", RevenueDriverAnalysisEngine,
        {"lookback": lookback_days},
        category_id, region_id, lookback_days,
    )
    forecast_text, forecast_conf = await _get_or_generate(
        "forecast", ForecastExplanationEngine,
        {"horizon": horizon_days},
        category_id, region_id, horizon_days,
    )

    # ── KPI numbers ─────────────────────────────────────────
    end   = date.today()
    start = end - timedelta(days=lookback_days)
    flt, params = [], {"s": start, "e": end}
    if category_id: flt.append("category_id=:cat");    params["cat"]    = category_id
    if region_id:   flt.append("region_id=:region");   params["region"] = region_id
    where = f"AND {' AND '.join(flt)}" if flt else ""

    kpi = db.execute(text(f"""
        SELECT COALESCE(SUM(total_revenue),0) AS total_revenue,
               COALESCE(SUM(total_quantity),0) AS total_quantity,
               COUNT(DISTINCT agg_date) AS days
        FROM agg_revenue_daily
        WHERE agg_date BETWEEN :s AND :e {where}
    """), params).mappings().first()

    # ── Build PDF ────────────────────────────────────────────
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.75*inch, bottomMargin=0.75*inch)
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle("ReportTitle", parent=styles["Title"], fontSize=20, spaceAfter=4)
    sub_style   = ParagraphStyle("ReportSub", parent=styles["Normal"], fontSize=10, textColor=colors.grey, spaceAfter=18)
    h2_style    = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=13, spaceBefore=16, spaceAfter=8, textColor=colors.HexColor("#1a1a1a"))
    body_style  = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10, leading=15)
    conf_style  = ParagraphStyle("Conf", parent=styles["Normal"], fontSize=8, textColor=colors.grey, spaceBefore=4)

    story = []
    story.append(Paragraph("CPG revenue intelligence report", title_style))
    story.append(Paragraph(
        f"Segment: {seg_label} &nbsp;|&nbsp; Period: {start} to {end} &nbsp;|&nbsp; "
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}",
        sub_style,
    ))

    # KPI table
    kpi_data = [
        ["Total revenue", f"${float(kpi['total_revenue']):,.0f}"],
        ["Total units sold", f"{int(kpi['total_quantity']):,}"],
        ["Days covered", f"{int(kpi['days'])}"],
        ["Avg daily revenue", f"${float(kpi['total_revenue']) / max(int(kpi['days']),1):,.0f}"],
    ]
    kpi_table = Table(kpi_data, colWidths=[2.5*inch, 3*inch])
    kpi_table.setStyle(TableStyle([
        ("FONTSIZE", (0,0), (-1,-1), 10),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("LINEBELOW", (0,0), (-1,-2), 0.5, colors.HexColor("#dddddd")),
        ("TEXTCOLOR", (0,0), (0,-1), colors.grey),
        ("FONTNAME", (1,0), (1,-1), "Helvetica-Bold"),
    ]))
    story.append(kpi_table)

    def _section(heading: str, text_body: str, confidence: float):
        story.append(Paragraph(heading, h2_style))
        for line in text_body.split("\n"):
            if line.strip():
                story.append(Paragraph(_escape(line), body_style))
        story.append(Paragraph(f"Confidence: {confidence:.0%}", conf_style))

    _section("Executive summary", exec_text, exec_conf)
    _section("Revenue trend", trend_text, trend_conf)
    _section("Top revenue drivers", driver_text, driver_conf)
    _section("Forecast outlook", forecast_text, forecast_conf)

    doc.build(story)
    buf.seek(0)

    filename = f"executive_report_{seg.replace('|','_').replace('=','')}_{end}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _escape(text_in: str) -> str:
    return (text_in.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
