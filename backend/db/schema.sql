-- ============================================================
-- CPG Predictive Intelligence Platform
-- Master Schema — Phase 1 Data Foundation
-- ============================================================

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";   -- fuzzy text matching for alias resolution

-- ──────────────────────────────────────────────────────────
-- DIMENSION TABLES
-- ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS dim_product_category (
    category_id     SERIAL PRIMARY KEY,
    category_name   VARCHAR(120) NOT NULL UNIQUE,
    category_group  VARCHAR(120)
);

CREATE TABLE IF NOT EXISTS dim_sku (
    sku_surrogate_id    BIGSERIAL PRIMARY KEY,
    sku_id              VARCHAR(80)   NOT NULL,
    sku_name            VARCHAR(255)  NOT NULL,
    brand               VARCHAR(120),
    category_id         INTEGER REFERENCES dim_product_category(category_id),
    sub_category        VARCHAR(120),
    package_size        VARCHAR(80),
    package_size_units  NUMERIC(10,3),
    package_unit        VARCHAR(40),
    list_price          NUMERIC(12,2),
    cost_price          NUMERIC(12,2),
    launch_date         DATE,
    discontinue_date    DATE,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from          DATE    NOT NULL DEFAULT CURRENT_DATE,
    valid_to            DATE    NOT NULL DEFAULT '9999-12-31',
    is_current          BOOLEAN NOT NULL DEFAULT TRUE,
    change_reason       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sku_id        ON dim_sku (sku_id);
CREATE INDEX IF NOT EXISTS idx_sku_current   ON dim_sku (sku_id) WHERE is_current = TRUE;
CREATE INDEX IF NOT EXISTS idx_sku_validity  ON dim_sku (sku_id, valid_from, valid_to);
CREATE INDEX IF NOT EXISTS idx_sku_category  ON dim_sku (category_id) WHERE is_current = TRUE;

CREATE TABLE IF NOT EXISTS dim_region (
    region_id   SERIAL PRIMARY KEY,
    region_name VARCHAR(120) NOT NULL UNIQUE,
    country     VARCHAR(80),
    sub_region  VARCHAR(120)
);

CREATE TABLE IF NOT EXISTS dim_store (
    store_id     VARCHAR(80)  PRIMARY KEY,
    store_name   VARCHAR(255) NOT NULL,
    store_type   VARCHAR(60),
    region_id    INTEGER REFERENCES dim_region(region_id),
    country      VARCHAR(80),
    city         VARCHAR(120),
    latitude     NUMERIC(9,6),
    longitude    NUMERIC(9,6),
    timezone     VARCHAR(60),
    opened_date  DATE,
    closed_date  DATE,
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    sq_footage   INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_store_region ON dim_store (region_id);

CREATE TABLE IF NOT EXISTS dim_region_demographics (
    id                       SERIAL PRIMARY KEY,
    region_id                INTEGER NOT NULL REFERENCES dim_region(region_id),
    snapshot_year            INTEGER NOT NULL,
    population               BIGINT,
    median_income_usd        NUMERIC(12,2),
    urban_pct                NUMERIC(5,2),
    age_median               NUMERIC(4,1),
    gdp_per_capita_usd       NUMERIC(12,2),
    internet_penetration_pct NUMERIC(5,2),
    UNIQUE (region_id, snapshot_year)
);

CREATE TABLE IF NOT EXISTS dim_calendar (
    cal_date          DATE PRIMARY KEY,
    year              INTEGER NOT NULL,
    quarter           INTEGER NOT NULL,
    month             INTEGER NOT NULL,
    week_of_year      INTEGER NOT NULL,
    day_of_week       INTEGER NOT NULL,
    is_weekend        BOOLEAN NOT NULL,
    is_public_holiday BOOLEAN NOT NULL DEFAULT FALSE,
    holiday_name      VARCHAR(120),
    retail_season     VARCHAR(60),
    fiscal_week       INTEGER,
    fiscal_quarter    INTEGER,
    fiscal_year       INTEGER
);

CREATE TABLE IF NOT EXISTS dim_source (
    source_id   SERIAL PRIMARY KEY,
    source_name VARCHAR(80) NOT NULL UNIQUE,
    source_type VARCHAR(60)
);

-- ──────────────────────────────────────────────────────────
-- BRONZE — STAGING LAYER
-- ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS staging_transactions (
    staging_id       BIGSERIAL PRIMARY KEY,
    source_name      VARCHAR(80)  NOT NULL,
    raw_payload      JSONB        NOT NULL,
    transaction_date DATE,
    sku_id           VARCHAR(80),
    category_name    VARCHAR(120),
    region_name      VARCHAR(120),
    store_id         VARCHAR(80),
    revenue          NUMERIC(14,2),
    quantity         INTEGER,
    currency         VARCHAR(3)   NOT NULL DEFAULT 'USD',
    unit             VARCHAR(40),
    processed        BOOLEAN      NOT NULL DEFAULT FALSE,
    ingested_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    processed_at     TIMESTAMPTZ,
    error_message    TEXT
);
CREATE INDEX IF NOT EXISTS idx_staging_source       ON staging_transactions (source_name);
CREATE INDEX IF NOT EXISTS idx_staging_date         ON staging_transactions (transaction_date);
CREATE INDEX IF NOT EXISTS idx_staging_unprocessed  ON staging_transactions (processed) WHERE processed = FALSE;

-- ──────────────────────────────────────────────────────────
-- SILVER — FACT TABLE
-- ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS fact_transactions (
    transaction_id    BIGSERIAL PRIMARY KEY,
    transaction_date  DATE         NOT NULL,
    sku_surrogate_id  BIGINT       REFERENCES dim_sku(sku_surrogate_id),
    category_id       INTEGER      REFERENCES dim_product_category(category_id),
    region_id         INTEGER      REFERENCES dim_region(region_id),
    store_id          VARCHAR(80)  REFERENCES dim_store(store_id),
    source_id         INTEGER      REFERENCES dim_source(source_id),
    staging_id        BIGINT       REFERENCES staging_transactions(staging_id),
    revenue           NUMERIC(14,2) NOT NULL,
    revenue_original  NUMERIC(14,2),
    currency_original VARCHAR(3),
    fx_rate           NUMERIC(14,6),
    quantity          INTEGER       NOT NULL DEFAULT 1,
    unit_price        NUMERIC(12,2),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fact_date         ON fact_transactions (transaction_date);
CREATE INDEX IF NOT EXISTS idx_fact_cat_region   ON fact_transactions (category_id, region_id, transaction_date);
CREATE INDEX IF NOT EXISTS idx_fact_sku          ON fact_transactions (sku_surrogate_id, transaction_date);
CREATE INDEX IF NOT EXISTS idx_fact_store        ON fact_transactions (store_id, transaction_date);

-- ──────────────────────────────────────────────────────────
-- GOLD — AGGREGATED SERVING LAYER
-- ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agg_revenue_daily (
    agg_date       DATE    NOT NULL,
    category_id    INTEGER NOT NULL REFERENCES dim_product_category(category_id),
    region_id      INTEGER NOT NULL REFERENCES dim_region(region_id),
    total_revenue  NUMERIC(14,2) NOT NULL,
    total_quantity INTEGER       NOT NULL,
    txn_count      INTEGER       NOT NULL,
    refreshed_at   TIMESTAMPTZ   NOT NULL DEFAULT now(),
    PRIMARY KEY (agg_date, category_id, region_id)
);
CREATE INDEX IF NOT EXISTS idx_agg_date ON agg_revenue_daily (agg_date);

-- ──────────────────────────────────────────────────────────
-- DATA QUALITY TABLES
-- ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ingestion_fingerprints (
    fingerprint   VARCHAR(64)  PRIMARY KEY,
    source_name   VARCHAR(80)  NOT NULL,
    staging_id    BIGINT       REFERENCES staging_transactions(staging_id),
    first_seen_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fingerprint_source ON ingestion_fingerprints (source_name);

CREATE TABLE IF NOT EXISTS late_arrivals (
    id               SERIAL PRIMARY KEY,
    staging_id       BIGINT REFERENCES staging_transactions(staging_id),
    transaction_date DATE         NOT NULL,
    ingested_at      TIMESTAMPTZ  NOT NULL,
    lateness_days    INTEGER      NOT NULL,
    severity         VARCHAR(20)  NOT NULL,
    source_name      VARCHAR(80)  NOT NULL,
    resolved         BOOLEAN      NOT NULL DEFAULT FALSE,
    resolved_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_late_unresolved ON late_arrivals (resolved) WHERE resolved = FALSE;
CREATE INDEX IF NOT EXISTS idx_late_date       ON late_arrivals (transaction_date);

CREATE TABLE IF NOT EXISTS dq_issues (
    id              SERIAL PRIMARY KEY,
    staging_id      BIGINT,
    source_name     VARCHAR(80) NOT NULL,
    issue_type      VARCHAR(80) NOT NULL,
    issue_detail    TEXT,
    raw_value       TEXT,
    corrected_value TEXT,
    severity        VARCHAR(20) NOT NULL DEFAULT 'warning',
    auto_corrected  BOOLEAN     NOT NULL DEFAULT FALSE,
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dq_source  ON dq_issues (source_name);
CREATE INDEX IF NOT EXISTS idx_dq_type    ON dq_issues (issue_type);
CREATE INDEX IF NOT EXISTS idx_dq_recent  ON dq_issues (detected_at DESC);

CREATE TABLE IF NOT EXISTS field_aliases (
    id              SERIAL PRIMARY KEY,
    source_name     VARCHAR(80)  NOT NULL,
    source_field    VARCHAR(120) NOT NULL,
    canonical_field VARCHAR(80)  NOT NULL,
    UNIQUE (source_name, source_field)
);

CREATE TABLE IF NOT EXISTS fx_rates (
    id          SERIAL PRIMARY KEY,
    rate_date   DATE       NOT NULL,
    currency    VARCHAR(3) NOT NULL,
    rate_to_usd NUMERIC(14,6) NOT NULL,
    UNIQUE (rate_date, currency)
);
CREATE INDEX IF NOT EXISTS idx_fx_currency ON fx_rates (currency, rate_date DESC);

CREATE TABLE IF NOT EXISTS unit_mappings (
    source_unit    VARCHAR(40) PRIMARY KEY,
    canonical_unit VARCHAR(40) NOT NULL,
    multiplier     NUMERIC(14,6) NOT NULL
);

-- ──────────────────────────────────────────────────────────
-- SECONDARY SIGNALS
-- ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS marketing_campaigns (
    campaign_id        VARCHAR(80)  PRIMARY KEY,
    campaign_name      VARCHAR(255) NOT NULL,
    channel            VARCHAR(60),
    campaign_type      VARCHAR(60),
    start_date         DATE         NOT NULL,
    end_date           DATE         NOT NULL,
    budget_usd         NUMERIC(14,2),
    target_category_id INTEGER REFERENCES dim_product_category(category_id),
    target_region_id   INTEGER REFERENCES dim_region(region_id),
    target_sku_id      VARCHAR(80),
    impressions        BIGINT,
    clicks             BIGINT,
    conversions        INTEGER,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_campaign_dates ON marketing_campaigns (start_date, end_date);

CREATE TABLE IF NOT EXISTS promo_windows (
    promo_id        VARCHAR(80)  PRIMARY KEY,
    promo_name      VARCHAR(255) NOT NULL,
    promo_type      VARCHAR(60),
    discount_pct    NUMERIC(5,2),
    start_date      DATE         NOT NULL,
    end_date        DATE         NOT NULL,
    sku_id          VARCHAR(80),
    category_id     INTEGER REFERENCES dim_product_category(category_id),
    region_id       INTEGER REFERENCES dim_region(region_id),
    min_order_value NUMERIC(12,2),
    channel         VARCHAR(60),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_promo_dates ON promo_windows (start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_promo_sku   ON promo_windows (sku_id);

CREATE TABLE IF NOT EXISTS competitor_pricing (
    id                   SERIAL PRIMARY KEY,
    snapshot_date        DATE         NOT NULL,
    competitor_name      VARCHAR(120) NOT NULL,
    sku_id               VARCHAR(80),
    category_id          INTEGER REFERENCES dim_product_category(category_id),
    our_price_usd        NUMERIC(12,2),
    competitor_price_usd NUMERIC(12,2),
    price_index          NUMERIC(6,3),
    region_id            INTEGER REFERENCES dim_region(region_id),
    data_source          VARCHAR(80),
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_comp_date ON competitor_pricing (snapshot_date, category_id);

-- ──────────────────────────────────────────────────────────
-- WEATHER
-- ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS weather_daily (
    id                 SERIAL PRIMARY KEY,
    weather_date       DATE    NOT NULL,
    region_id          INTEGER NOT NULL REFERENCES dim_region(region_id),
    avg_temp_c         NUMERIC(5,2),
    max_temp_c         NUMERIC(5,2),
    min_temp_c         NUMERIC(5,2),
    precipitation_mm   NUMERIC(7,2),
    snowfall_mm        NUMERIC(7,2),
    is_extreme_weather BOOLEAN NOT NULL DEFAULT FALSE,
    weather_source     VARCHAR(80),
    UNIQUE (weather_date, region_id)
);
CREATE INDEX IF NOT EXISTS idx_weather_date ON weather_daily (weather_date, region_id);

-- ──────────────────────────────────────────────────────────
-- FUNCTIONS
-- ──────────────────────────────────────────────────────────

-- SCD2 upsert for dim_sku
CREATE OR REPLACE FUNCTION upsert_sku(
    p_sku_id            TEXT, p_sku_name TEXT, p_brand TEXT,
    p_category_id       INTEGER, p_sub_category TEXT,
    p_package_size      TEXT, p_package_size_units NUMERIC, p_package_unit TEXT,
    p_list_price        NUMERIC, p_cost_price NUMERIC,
    p_launch_date       DATE, p_discontinue_date DATE, p_is_active BOOLEAN,
    p_effective_date    DATE, p_change_reason TEXT
) RETURNS BIGINT AS $$
DECLARE
    v_current dim_sku%ROWTYPE;
    v_new_id  BIGINT;
BEGIN
    SELECT * INTO v_current FROM dim_sku WHERE sku_id = p_sku_id AND is_current = TRUE;
    IF NOT FOUND THEN
        INSERT INTO dim_sku (sku_id, sku_name, brand, category_id, sub_category,
            package_size, package_size_units, package_unit, list_price, cost_price,
            launch_date, discontinue_date, is_active, valid_from, valid_to, is_current, change_reason)
        VALUES (p_sku_id, p_sku_name, p_brand, p_category_id, p_sub_category,
            p_package_size, p_package_size_units, p_package_unit, p_list_price, p_cost_price,
            p_launch_date, p_discontinue_date, p_is_active, p_effective_date, '9999-12-31', TRUE, p_change_reason)
        RETURNING sku_surrogate_id INTO v_new_id;
        RETURN v_new_id;
    END IF;
    IF COALESCE(v_current.sku_name,'')     != COALESCE(p_sku_name,'')
    OR COALESCE(v_current.brand,'')         != COALESCE(p_brand,'')
    OR COALESCE(v_current.category_id,0)   != COALESCE(p_category_id,0)
    OR COALESCE(v_current.list_price,0)    != COALESCE(p_list_price,0)
    OR COALESCE(v_current.is_active,TRUE)  != COALESCE(p_is_active,TRUE)
    THEN
        UPDATE dim_sku SET valid_to = p_effective_date - 1, is_current = FALSE, updated_at = now()
        WHERE sku_surrogate_id = v_current.sku_surrogate_id;
        INSERT INTO dim_sku (sku_id, sku_name, brand, category_id, sub_category,
            package_size, package_size_units, package_unit, list_price, cost_price,
            launch_date, discontinue_date, is_active, valid_from, valid_to, is_current, change_reason)
        VALUES (p_sku_id, p_sku_name, p_brand, p_category_id, p_sub_category,
            p_package_size, p_package_size_units, p_package_unit, p_list_price, p_cost_price,
            p_launch_date, p_discontinue_date, p_is_active, p_effective_date, '9999-12-31', TRUE, p_change_reason)
        RETURNING sku_surrogate_id INTO v_new_id;
        RETURN v_new_id;
    END IF;
    RETURN v_current.sku_surrogate_id;
END;
$$ LANGUAGE plpgsql;

-- Refresh gold aggregates
CREATE OR REPLACE FUNCTION refresh_agg_revenue_daily(p_since DATE DEFAULT NULL)
RETURNS VOID AS $$
BEGIN
    DELETE FROM agg_revenue_daily WHERE p_since IS NULL OR agg_date >= p_since;
    INSERT INTO agg_revenue_daily (agg_date, category_id, region_id, total_revenue, total_quantity, txn_count)
    SELECT
        transaction_date, category_id, region_id,
        SUM(revenue), SUM(quantity), COUNT(*)
    FROM fact_transactions
    WHERE category_id IS NOT NULL AND region_id IS NOT NULL
      AND (p_since IS NULL OR transaction_date >= p_since)
    GROUP BY transaction_date, category_id, region_id
    ON CONFLICT (agg_date, category_id, region_id)
    DO UPDATE SET
        total_revenue  = EXCLUDED.total_revenue,
        total_quantity = EXCLUDED.total_quantity,
        txn_count      = EXCLUDED.txn_count,
        refreshed_at   = now();
END;
$$ LANGUAGE plpgsql;

-- ──────────────────────────────────────────────────────────
-- VIEWS
-- ──────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW dq_summary AS
SELECT
    source_name,
    issue_type,
    severity,
    COUNT(*)                               AS issue_count,
    COUNT(*) FILTER (WHERE auto_corrected) AS auto_corrected_count,
    MAX(detected_at)                       AS last_seen
FROM dq_issues
GROUP BY source_name, issue_type, severity
ORDER BY issue_count DESC;

-- ──────────────────────────────────────────────────────────
-- SEED DATA
-- ──────────────────────────────────────────────────────────

-- dim_source
INSERT INTO dim_source (source_name, source_type) VALUES
    ('csv_upload',    'manual'),
    ('api_push',      'api'),
    ('pos_legacy',    'pos'),
    ('shopify',       'ecommerce'),
    ('crm_export',    'crm'),
    ('synthetic',     'test')
ON CONFLICT DO NOTHING;

-- fx_rates (static bootstrap; replace with live feed in production)
INSERT INTO fx_rates (rate_date, currency, rate_to_usd) VALUES
    ('2024-01-01','USD',1.000000),('2024-01-01','EUR',1.085000),
    ('2024-01-01','GBP',1.270000),('2024-01-01','JPY',0.006700),
    ('2024-01-01','INR',0.012000),('2024-01-01','AUD',0.650000),
    ('2024-01-01','CAD',0.740000),('2024-01-01','SGD',0.750000),
    ('2024-01-01','AED',0.272000),('2024-01-01','BRL',0.200000),
    ('2024-01-01','MXN',0.058000),('2024-01-01','CNY',0.140000)
ON CONFLICT DO NOTHING;

-- unit_mappings
INSERT INTO unit_mappings (source_unit, canonical_unit, multiplier) VALUES
    ('each','unit',1),('ea','unit',1),('pc','unit',1),
    ('piece','unit',1),('unit','unit',1),('units','unit',1),
    ('dozen','unit',12),('dz','unit',12),
    ('gross','unit',144),('pair','unit',2),
    ('pack','unit',1),('box','unit',1),('case','unit',1)
ON CONFLICT DO NOTHING;

-- field_aliases (common source → canonical mappings)
INSERT INTO field_aliases (source_name, source_field, canonical_field) VALUES
    ('shopify','created_at','transaction_date'),
    ('shopify','total_price','revenue'),
    ('shopify','product_type','category_name'),
    ('shopify','shipping_country','region_name'),
    ('shopify','product_id','sku_id'),
    ('pos_legacy','sale_date','transaction_date'),
    ('pos_legacy','net_amount','revenue'),
    ('pos_legacy','dept','category_name'),
    ('pos_legacy','store_region','region_name'),
    ('pos_legacy','qty','quantity'),
    ('pos_legacy','item_code','sku_id'),
    ('crm_export','close_date','transaction_date'),
    ('crm_export','amount_usd','revenue'),
    ('crm_export','product_family','category_name'),
    ('crm_export','territory','region_name')
ON CONFLICT DO NOTHING;

-- dim_calendar (2020-2027)
INSERT INTO dim_calendar (
    cal_date, year, quarter, month, week_of_year,
    day_of_week, is_weekend, fiscal_week, fiscal_quarter, fiscal_year
)
SELECT
    d::date,
    EXTRACT(YEAR FROM d)::int,
    EXTRACT(QUARTER FROM d)::int,
    EXTRACT(MONTH FROM d)::int,
    EXTRACT(WEEK FROM d)::int,
    EXTRACT(ISODOW FROM d)::int - 1,
    EXTRACT(ISODOW FROM d) IN (6,7),
    EXTRACT(WEEK FROM d)::int,
    EXTRACT(QUARTER FROM d)::int,
    EXTRACT(YEAR FROM d)::int
FROM generate_series('2020-01-01'::date,'2027-12-31'::date,'1 day') d
ON CONFLICT DO NOTHING;

UPDATE dim_calendar SET retail_season = CASE
    WHEN month IN (11,12) THEN 'Holiday'
    WHEN month IN (6,7,8) THEN 'Summer'
    WHEN month IN (3,4,5) THEN 'Spring'
    WHEN month = 7 OR (month = 8 AND week_of_year <= 33) THEN 'Back-to-School'
    ELSE 'Off-Peak'
END;
