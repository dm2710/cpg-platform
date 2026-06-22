"""
Reference data endpoints — SKU catalog, stores, demographics, signals.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.reference import (
    BulkSkuRequest, BulkSkuResponse,
    CampaignOut, CampaignUpsertRequest,
    CompetitorPriceOut, CompetitorPriceRequest,
    DemographicsUpsertRequest,
    PromoOut, PromoUpsertRequest,
    SkuHistoryOut, SkuOut, SkuUpsertRequest,
    StoreOut, StoreUpsertRequest,
)
from app.schemas.base import MessageResponse
from app.security.deps import CurrentUser, require_permission
from app.security.rbac import Permission

router = APIRouter()


# ── SKU catalog ───────────────────────────────────────────

def _do_upsert_sku(req: SkuUpsertRequest, db: Session) -> int:
    """Plain Python upsert logic, reused by the route below, bulk upsert, and CSV upload.
    No auth dependency here -- callers are responsible for permission checks."""
    eff_date = req.effective_date or date.today()
    cat_id = _get_or_create(db, "dim_product_category", "category_name", req.category_name, "category_id")

    new_id = db.execute(
        text("""
            SELECT upsert_sku(
                :sku_id, :sku_name, :brand, :cat_id, :sub_cat,
                :pkg_size, NULL, NULL,
                :list_price, :cost_price, :launch, :discontinue,
                :is_active, :eff_date, :reason
            )
        """),
        {
            "sku_id":      req.sku_id,
            "sku_name":    req.sku_name,
            "brand":       req.brand,
            "cat_id":      cat_id,
            "sub_cat":     req.sub_category,
            "pkg_size":    req.package_size,
            "list_price":  req.list_price,
            "cost_price":  req.cost_price,
            "launch":      req.launch_date,
            "discontinue": req.discontinue_date,
            "is_active":   req.is_active,
            "eff_date":    eff_date,
            "reason":      req.change_reason,
        },
    ).scalar()
    db.commit()
    return new_id


@router.post("/catalog/sku", response_model=MessageResponse, summary="Upsert a SKU (SCD2)")
def upsert_sku(
    req: SkuUpsertRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.WRITE_REFERENCE_DATA)),
):
    new_id = _do_upsert_sku(req, db)
    return MessageResponse(message=f"SKU '{req.sku_id}' upserted (surrogate_id={new_id})")


def _do_bulk_upsert_skus(req: BulkSkuRequest, db: Session) -> BulkSkuResponse:
    """Plain Python bulk-upsert logic, reused by the route below and CSV upload.
    No auth dependency here -- callers are responsible for permission checks."""
    inserted = updated = unchanged = errors = 0
    error_details = []

    for sku_req in req.records:
        try:
            sku_req.effective_date = sku_req.effective_date or req.effective_date or date.today()
            sku_req.change_reason  = sku_req.change_reason or req.change_reason

            existing = db.execute(
                text("SELECT sku_surrogate_id FROM dim_sku WHERE sku_id = :id AND is_current = TRUE"),
                {"id": sku_req.sku_id},
            ).first()

            _do_upsert_sku(sku_req, db)

            new_row = db.execute(
                text("SELECT sku_surrogate_id FROM dim_sku WHERE sku_id = :id AND is_current = TRUE"),
                {"id": sku_req.sku_id},
            ).first()

            if not existing:
                inserted += 1
            elif existing[0] != new_row[0]:
                updated += 1
            else:
                unchanged += 1
        except Exception as exc:
            errors += 1
            error_details.append({"sku_id": sku_req.sku_id, "error": str(exc)})

    return BulkSkuResponse(
        inserted=inserted, updated=updated,
        unchanged=unchanged, errors=errors, error_details=error_details,
    )


@router.post("/catalog/sku/bulk", response_model=BulkSkuResponse, summary="Bulk upsert SKUs")
def bulk_upsert_skus(
    req: BulkSkuRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.WRITE_REFERENCE_DATA)),
):
    return _do_bulk_upsert_skus(req, db)


@router.post("/catalog/sku/upload-csv", response_model=BulkSkuResponse, summary="Upload SKU catalog CSV")
async def upload_sku_csv(
    file:           UploadFile     = File(...),
    effective_date: Optional[date] = None,
    db:             Session        = Depends(get_db),
    user:           CurrentUser    = Depends(require_permission(Permission.WRITE_REFERENCE_DATA)),
):
    import pandas as pd, io
    content = await file.read()
    try:
        df = pd.read_csv(io.BytesIO(content))
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    except Exception as exc:
        raise HTTPException(400, f"Cannot parse CSV: {exc}")

    records = []
    for _, row in df.iterrows():
        try:
            records.append(SkuUpsertRequest(
                sku_id=str(row.get("sku_id", "")),
                sku_name=str(row.get("sku_name", "")),
                category_name=str(row.get("category_name", "Uncategorized")),
                brand=row.get("brand") if pd.notna(row.get("brand")) else None,
                list_price=float(row["list_price"]) if "list_price" in row and pd.notna(row["list_price"]) else None,
                effective_date=effective_date,
            ))
        except Exception:
            continue

    req = BulkSkuRequest(records=records, effective_date=effective_date)
    return _do_bulk_upsert_skus(req, db)


@router.get("/catalog/skus", response_model=list[SkuOut], summary="List current SKU catalog")
def list_skus(
    category_name: Optional[str]  = None,
    brand:         Optional[str]  = None,
    is_active:     Optional[bool] = True,
    db:            Session        = Depends(get_db),
):
    filters = ["s.is_current = TRUE"]
    params:  dict = {}
    if category_name:
        filters.append("dpc.category_name ILIKE :cat")
        params["cat"] = f"%{category_name}%"
    if brand:
        filters.append("s.brand ILIKE :brand")
        params["brand"] = f"%{brand}%"
    if is_active is not None:
        filters.append("s.is_active = :active")
        params["active"] = is_active

    rows = db.execute(
        text(f"""
            SELECT s.*, dpc.category_name
            FROM dim_sku s
            LEFT JOIN dim_product_category dpc ON dpc.category_id = s.category_id
            WHERE {' AND '.join(filters)}
            ORDER BY dpc.category_name, s.brand, s.sku_name
        """),
        params,
    ).mappings().all()
    return [SkuOut(**dict(r)) for r in rows]


@router.get("/catalog/sku/{sku_id}/history", response_model=SkuHistoryOut)
def get_sku_history(sku_id: str, db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT s.*, dpc.category_name
            FROM dim_sku s
            LEFT JOIN dim_product_category dpc ON dpc.category_id = s.category_id
            WHERE s.sku_id = :sku_id
            ORDER BY s.valid_from DESC
        """),
        {"sku_id": sku_id},
    ).mappings().all()
    if not rows:
        raise HTTPException(404, f"SKU '{sku_id}' not found")
    return SkuHistoryOut(
        sku_id=sku_id,
        versions=[SkuOut(**dict(r)) for r in rows],
        total=len(rows),
    )


# ── Stores ────────────────────────────────────────────────

@router.post("/stores", response_model=MessageResponse, summary="Upsert a store")
def upsert_store(
    req: StoreUpsertRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.WRITE_REFERENCE_DATA)),
):
    region_id = _get_or_create(db, "dim_region", "region_name", req.region_name, "region_id")
    db.execute(
        text("""
            INSERT INTO dim_store
                (store_id, store_name, store_type, region_id, country, city,
                 latitude, longitude, timezone, opened_date, closed_date,
                 is_active, sq_footage)
            VALUES
                (:sid, :name, :type, :rid, :country, :city,
                 :lat, :lon, :tz, :opened, :closed, :active, :sqft)
            ON CONFLICT (store_id) DO UPDATE SET
                store_name  = EXCLUDED.store_name,
                store_type  = EXCLUDED.store_type,
                region_id   = EXCLUDED.region_id,
                country     = EXCLUDED.country,
                city        = EXCLUDED.city,
                latitude    = EXCLUDED.latitude,
                longitude   = EXCLUDED.longitude,
                timezone    = EXCLUDED.timezone,
                is_active   = EXCLUDED.is_active,
                sq_footage  = EXCLUDED.sq_footage,
                updated_at  = now()
        """),
        {
            "sid":     req.store_id, "name": req.store_name,
            "type":    req.store_type, "rid": region_id,
            "country": req.country, "city": req.city,
            "lat":     req.latitude, "lon": req.longitude,
            "tz":      req.timezone, "opened": req.opened_date,
            "closed":  req.closed_date, "active": req.is_active,
            "sqft":    req.sq_footage,
        },
    )
    db.commit()
    return MessageResponse(message=f"Store '{req.store_id}' upserted")


@router.get("/stores", response_model=list[StoreOut], summary="List stores")
def list_stores(
    region_name: Optional[str]  = None,
    is_active:   Optional[bool] = True,
    db:          Session        = Depends(get_db),
):
    filters = []
    params: dict = {}
    if region_name:
        filters.append("dr.region_name ILIKE :region")
        params["region"] = f"%{region_name}%"
    if is_active is not None:
        filters.append("s.is_active = :active")
        params["active"] = is_active

    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    rows = db.execute(
        text(f"""
            SELECT s.*, dr.region_name
            FROM dim_store s
            JOIN dim_region dr ON dr.region_id = s.region_id
            {where}
            ORDER BY dr.region_name, s.store_name
        """),
        params,
    ).mappings().all()
    return [StoreOut(**dict(r)) for r in rows]


# ── Demographics ──────────────────────────────────────────

@router.post("/regions/demographics", response_model=MessageResponse)
def upsert_demographics(
    req: DemographicsUpsertRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.WRITE_REFERENCE_DATA)),
):
    region_id = _get_or_create(db, "dim_region", "region_name", req.region_name, "region_id")
    db.execute(
        text("""
            INSERT INTO dim_region_demographics
                (region_id, snapshot_year, population, median_income_usd,
                 urban_pct, age_median, gdp_per_capita_usd, internet_penetration_pct)
            VALUES (:rid, :yr, :pop, :income, :urban, :age, :gdp, :inet)
            ON CONFLICT (region_id, snapshot_year) DO UPDATE SET
                population                = EXCLUDED.population,
                median_income_usd         = EXCLUDED.median_income_usd,
                urban_pct                 = EXCLUDED.urban_pct,
                age_median                = EXCLUDED.age_median,
                gdp_per_capita_usd        = EXCLUDED.gdp_per_capita_usd,
                internet_penetration_pct  = EXCLUDED.internet_penetration_pct
        """),
        {
            "rid":    region_id, "yr": req.snapshot_year,
            "pop":    req.population, "income": req.median_income_usd,
            "urban":  req.urban_pct, "age": req.age_median,
            "gdp":    req.gdp_per_capita_usd, "inet": req.internet_penetration_pct,
        },
    )
    db.commit()
    return MessageResponse(message=f"Demographics for '{req.region_name}' ({req.snapshot_year}) saved")


# ── Campaign signals ──────────────────────────────────────

@router.post("/signals/campaigns", response_model=MessageResponse)
def upsert_campaign(
    req: CampaignUpsertRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.WRITE_REFERENCE_DATA)),
):
    cat_id = _resolve_optional_dim(db, "dim_product_category", "category_name", req.target_category_name, "category_id")
    reg_id = _resolve_optional_dim(db, "dim_region",           "region_name",   req.target_region_name,   "region_id")
    db.execute(
        text("""
            INSERT INTO marketing_campaigns
                (campaign_id, campaign_name, channel, campaign_type, start_date, end_date,
                 budget_usd, target_category_id, target_region_id, target_sku_id,
                 impressions, clicks, conversions)
            VALUES (:id,:name,:ch,:type,:start,:end,:budget,:cat,:reg,:sku,:imp,:clk,:conv)
            ON CONFLICT (campaign_id) DO UPDATE SET
                campaign_name=EXCLUDED.campaign_name, channel=EXCLUDED.channel,
                start_date=EXCLUDED.start_date, end_date=EXCLUDED.end_date,
                budget_usd=EXCLUDED.budget_usd, impressions=EXCLUDED.impressions,
                clicks=EXCLUDED.clicks, conversions=EXCLUDED.conversions, updated_at=now()
        """),
        {"id":req.campaign_id,"name":req.campaign_name,"ch":req.channel,"type":req.campaign_type,
         "start":req.start_date,"end":req.end_date,"budget":req.budget_usd,
         "cat":cat_id,"reg":reg_id,"sku":req.target_sku_id,
         "imp":req.impressions,"clk":req.clicks,"conv":req.conversions},
    )
    db.commit()
    return MessageResponse(message=f"Campaign '{req.campaign_id}' saved")


@router.get("/signals/campaigns", response_model=list[CampaignOut])
def list_campaigns(
    as_of: Optional[date] = None,
    db:    Session        = Depends(get_db),
):
    where = "WHERE :as_of BETWEEN start_date AND end_date" if as_of else ""
    params = {"as_of": as_of} if as_of else {}
    rows = db.execute(
        text(f"SELECT * FROM marketing_campaigns {where} ORDER BY start_date DESC"),
        params,
    ).mappings().all()
    return [CampaignOut(**dict(r)) for r in rows]


# ── Promo signals ─────────────────────────────────────────

@router.post("/signals/promos", response_model=MessageResponse)
def upsert_promo(
    req: PromoUpsertRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.WRITE_REFERENCE_DATA)),
):
    cat_id = _resolve_optional_dim(db, "dim_product_category", "category_name", req.category_name, "category_id")
    reg_id = _resolve_optional_dim(db, "dim_region",           "region_name",   req.region_name,   "region_id")
    db.execute(
        text("""
            INSERT INTO promo_windows
                (promo_id, promo_name, promo_type, discount_pct, start_date, end_date,
                 sku_id, category_id, region_id, min_order_value, channel)
            VALUES (:id,:name,:type,:disc,:start,:end,:sku,:cat,:reg,:min_ord,:ch)
            ON CONFLICT (promo_id) DO UPDATE SET
                promo_name=EXCLUDED.promo_name, discount_pct=EXCLUDED.discount_pct,
                start_date=EXCLUDED.start_date, end_date=EXCLUDED.end_date, updated_at=now()
        """),
        {"id":req.promo_id,"name":req.promo_name,"type":req.promo_type,"disc":req.discount_pct,
         "start":req.start_date,"end":req.end_date,"sku":req.sku_id,
         "cat":cat_id,"reg":reg_id,"min_ord":req.min_order_value,"ch":req.channel},
    )
    db.commit()
    return MessageResponse(message=f"Promo '{req.promo_id}' saved")


@router.get("/signals/promos", response_model=list[PromoOut])
def list_promos(as_of: Optional[date] = None, db: Session = Depends(get_db)):
    where = "WHERE :as_of BETWEEN start_date AND end_date" if as_of else ""
    params = {"as_of": as_of} if as_of else {}
    rows = db.execute(
        text(f"SELECT * FROM promo_windows {where} ORDER BY discount_pct DESC"),
        params,
    ).mappings().all()
    return [PromoOut(**dict(r)) for r in rows]


# ── Competitor pricing ────────────────────────────────────

@router.post("/signals/competitor-pricing", response_model=MessageResponse)
def upsert_competitor_price(
    req: CompetitorPriceRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.WRITE_REFERENCE_DATA)),
):
    cat_id = _resolve_optional_dim(db, "dim_product_category", "category_name", req.category_name, "category_id")
    reg_id = _resolve_optional_dim(db, "dim_region",           "region_name",   req.region_name,   "region_id")
    price_idx = round(float(req.competitor_price_usd) / float(req.our_price_usd), 4)
    db.execute(
        text("""
            INSERT INTO competitor_pricing
                (snapshot_date, competitor_name, sku_id, category_id,
                 our_price_usd, competitor_price_usd, price_index, region_id, data_source)
            VALUES (:date,:comp,:sku,:cat,:ours,:theirs,:idx,:reg,:src)
        """),
        {"date":req.snapshot_date,"comp":req.competitor_name,"sku":req.sku_id,
         "cat":cat_id,"ours":req.our_price_usd,"theirs":req.competitor_price_usd,
         "idx":price_idx,"reg":reg_id,"src":req.data_source},
    )
    db.commit()
    return MessageResponse(message="Competitor price snapshot saved")


@router.get("/signals/competitor-pricing", response_model=list[CompetitorPriceOut])
def list_competitor_prices(
    category_id: Optional[int] = None,
    days:        int           = Query(default=30, ge=1, le=365),
    db:          Session       = Depends(get_db),
):
    filters = [f"snapshot_date >= CURRENT_DATE - INTERVAL '{days} days'"]
    params: dict = {}
    if category_id:
        filters.append("category_id = :cat")
        params["cat"] = category_id

    rows = db.execute(
        text(f"""
            SELECT * FROM competitor_pricing
            WHERE {' AND '.join(filters)}
            ORDER BY snapshot_date DESC
        """),
        params,
    ).mappings().all()
    return [CompetitorPriceOut(**dict(r)) for r in rows]


# ── Helpers ───────────────────────────────────────────────

def _get_or_create(db: Session, table: str, name_col: str, value: str, id_col: str) -> int:
    row = db.execute(
        text(f"SELECT {id_col} FROM {table} WHERE {name_col} = :v"), {"v": value}
    ).first()
    if row:
        return row[0]
    new_id = db.execute(
        text(f"INSERT INTO {table} ({name_col}) VALUES (:v) RETURNING {id_col}"), {"v": value}
    ).scalar()
    db.commit()
    return new_id


def _resolve_optional_dim(
    db: Session, table: str, name_col: str,
    value: Optional[str], id_col: str,
) -> Optional[int]:
    if not value:
        return None
    row = db.execute(
        text(f"SELECT {id_col} FROM {table} WHERE {name_col} ILIKE :v"), {"v": value}
    ).first()
    return row[0] if row else None
