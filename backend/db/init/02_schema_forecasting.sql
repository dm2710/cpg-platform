-- ============================================================
-- CPG Platform — Phase 2 Forecasting Schema
-- Run after schema.sql
-- ============================================================

-- ──────────────────────────────────────────────────────────
-- MODEL REGISTRY
-- Tracks every trained model version with metadata and metrics
-- ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS model_registry (
    model_id          BIGSERIAL PRIMARY KEY,
    model_name        VARCHAR(80)  NOT NULL,          -- "prophet", "lightgbm"
    model_version     VARCHAR(40)  NOT NULL,          -- "v1.0.0", "20240115_143022"
    category_id       INTEGER REFERENCES dim_product_category(category_id),
    region_id         INTEGER REFERENCES dim_region(region_id),
    segment_key       VARCHAR(120) NOT NULL,          -- "cat=1|region=2" or "global"
    status            VARCHAR(20)  NOT NULL DEFAULT 'trained',
                                                      -- training|trained|deployed|retired|failed
    trained_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    deployed_at       TIMESTAMPTZ,
    retired_at        TIMESTAMPTZ,

    -- Training window
    train_start_date  DATE,
    train_end_date    DATE,
    training_rows     INTEGER,

    -- Evaluation metrics (on hold-out set)
    mae               NUMERIC(14,4),                  -- Mean Absolute Error
    mape              NUMERIC(8,4),                   -- Mean Absolute Percentage Error (%)
    rmse              NUMERIC(14,4),                  -- Root Mean Square Error
    smape             NUMERIC(8,4),                   -- Symmetric MAPE
    r2                NUMERIC(8,6),                   -- R-squared

    -- Hyperparameters and feature metadata (JSON)
    hyperparameters   JSONB,
    feature_names     JSONB,
    feature_importance JSONB,

    -- Serialised model artifact path
    artifact_path     TEXT,

    UNIQUE (model_name, segment_key, model_version)
);

CREATE INDEX IF NOT EXISTS idx_model_segment   ON model_registry (segment_key, status);
CREATE INDEX IF NOT EXISTS idx_model_deployed  ON model_registry (status) WHERE status = 'deployed';
CREATE INDEX IF NOT EXISTS idx_model_name      ON model_registry (model_name, status);


-- ──────────────────────────────────────────────────────────
-- FEATURE STORE TABLE
-- Materialised feature matrix per (date, category, region)
-- Rebuilt on each training run; read by both training and prediction
-- ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS feature_store (
    feature_date          DATE    NOT NULL,
    category_id           INTEGER NOT NULL REFERENCES dim_product_category(category_id),
    region_id             INTEGER NOT NULL REFERENCES dim_region(region_id),

    -- Target
    total_revenue         NUMERIC(14,2),
    total_quantity        INTEGER,

    -- Calendar features
    day_of_week           INTEGER,
    day_of_month          INTEGER,
    week_of_year          INTEGER,
    month                 INTEGER,
    quarter               INTEGER,
    year                  INTEGER,
    is_weekend            BOOLEAN,
    is_month_start        BOOLEAN,
    is_month_end          BOOLEAN,
    is_quarter_start      BOOLEAN,
    is_quarter_end        BOOLEAN,
    is_public_holiday     BOOLEAN,
    retail_season         VARCHAR(60),

    -- Lag features (revenue)
    lag_7d                NUMERIC(14,2),
    lag_14d               NUMERIC(14,2),
    lag_28d               NUMERIC(14,2),
    lag_90d               NUMERIC(14,2),
    lag_365d              NUMERIC(14,2),

    -- Rolling window statistics
    rolling_mean_7d       NUMERIC(14,2),
    rolling_mean_14d      NUMERIC(14,2),
    rolling_mean_28d      NUMERIC(14,2),
    rolling_std_7d        NUMERIC(14,2),
    rolling_std_28d       NUMERIC(14,2),
    rolling_min_28d       NUMERIC(14,2),
    rolling_max_28d       NUMERIC(14,2),

    -- Year-over-year
    yoy_revenue           NUMERIC(14,2),
    yoy_growth_pct        NUMERIC(8,4),

    -- Trend / momentum
    revenue_trend_7d      NUMERIC(8,4),              -- % change over 7 days
    revenue_trend_28d     NUMERIC(8,4),              -- % change over 28 days

    -- Promo / campaign signals
    active_promo_count    INTEGER DEFAULT 0,
    max_discount_pct      NUMERIC(5,2),
    daily_campaign_spend  NUMERIC(14,2) DEFAULT 0,

    -- Competitive signal
    competitor_price_index NUMERIC(6,3),

    -- Weather
    avg_temp_c            NUMERIC(5,2),
    precipitation_mm      NUMERIC(7,2),
    is_extreme_weather    BOOLEAN DEFAULT FALSE,

    -- Region demographics (point-in-time)
    median_income_usd     NUMERIC(12,2),
    urban_pct             NUMERIC(5,2),
    population            BIGINT,

    computed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (feature_date, category_id, region_id)
);

CREATE INDEX IF NOT EXISTS idx_feature_date    ON feature_store (feature_date);
CREATE INDEX IF NOT EXISTS idx_feature_segment ON feature_store (category_id, region_id);


-- ──────────────────────────────────────────────────────────
-- FORECAST RESULTS
-- One row per (model, segment, horizon date)
-- ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS forecast_results (
    forecast_id           BIGSERIAL PRIMARY KEY,
    model_id              BIGINT    NOT NULL REFERENCES model_registry(model_id),
    model_name            VARCHAR(80)  NOT NULL,
    segment_key           VARCHAR(120) NOT NULL,
    category_id           INTEGER REFERENCES dim_product_category(category_id),
    region_id             INTEGER REFERENCES dim_region(region_id),
    forecast_date         DATE      NOT NULL,
    generated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Point forecast
    predicted_revenue     NUMERIC(14,2) NOT NULL,

    -- Confidence intervals
    lower_80              NUMERIC(14,2),
    upper_80              NUMERIC(14,2),
    lower_95              NUMERIC(14,2),
    upper_95              NUMERIC(14,2),

    -- Decomposition (Prophet)
    trend_component       NUMERIC(14,2),
    seasonal_weekly       NUMERIC(14,2),
    seasonal_yearly       NUMERIC(14,2),
    holiday_component     NUMERIC(14,2),

    -- Actuals (backfilled once available)
    actual_revenue        NUMERIC(14,2),
    error_pct             NUMERIC(8,4),

    UNIQUE (model_id, segment_key, forecast_date)
);

CREATE INDEX IF NOT EXISTS idx_forecast_segment  ON forecast_results (segment_key, forecast_date);
CREATE INDEX IF NOT EXISTS idx_forecast_date     ON forecast_results (forecast_date);
CREATE INDEX IF NOT EXISTS idx_forecast_model    ON forecast_results (model_id);


-- ──────────────────────────────────────────────────────────
-- TRAINING RUN LOG
-- One row per pipeline execution (covers all segments)
-- ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS training_runs (
    run_id            BIGSERIAL PRIMARY KEY,
    run_key           VARCHAR(40)  NOT NULL UNIQUE,   -- "20240115_143022"
    triggered_by      VARCHAR(80)  DEFAULT 'manual',  -- manual|schedule|api
    status            VARCHAR(20)  NOT NULL DEFAULT 'running',
                                                      -- running|completed|failed|partial
    model_names       JSONB        NOT NULL,           -- ["prophet","lightgbm"]
    horizon_days      INTEGER      NOT NULL,
    started_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    completed_at      TIMESTAMPTZ,
    duration_seconds  NUMERIC(10,2),

    -- Segment counts
    segments_total    INTEGER DEFAULT 0,
    segments_trained  INTEGER DEFAULT 0,
    segments_failed   INTEGER DEFAULT 0,
    segments_skipped  INTEGER DEFAULT 0,

    -- Aggregate metrics across all segments
    avg_mape          NUMERIC(8,4),
    avg_mae           NUMERIC(14,4),

    error_detail      TEXT,
    run_metadata      JSONB
);

CREATE INDEX IF NOT EXISTS idx_run_status ON training_runs (status, started_at DESC);


-- ──────────────────────────────────────────────────────────
-- FORECAST ACCURACY TRACKING
-- Backfilled once actuals arrive; drives model selection
-- ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS forecast_accuracy (
    id                BIGSERIAL PRIMARY KEY,
    model_id          BIGINT   NOT NULL REFERENCES model_registry(model_id),
    segment_key       VARCHAR(120) NOT NULL,
    evaluation_date   DATE     NOT NULL,
    horizon_days      INTEGER  NOT NULL,     -- how far out the forecast was
    mae               NUMERIC(14,4),
    mape              NUMERIC(8,4),
    rmse              NUMERIC(14,4),
    bias              NUMERIC(14,4),         -- mean(predicted - actual)
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (model_id, segment_key, evaluation_date, horizon_days)
);

CREATE INDEX IF NOT EXISTS idx_accuracy_model   ON forecast_accuracy (model_id, segment_key);
CREATE INDEX IF NOT EXISTS idx_accuracy_date    ON forecast_accuracy (evaluation_date);
