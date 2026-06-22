-- ============================================================
-- CPG Platform — Phase 3: AI Insights Infrastructure
-- Engines: Trend Summarization, Root Cause Analysis,
--          Forecast Explanation, Revenue Driver Analysis,
--          Executive Summary Generation
-- LLM provider: DeepSeek (deepseek-chat)
-- ============================================================

-- ── Insight cache ─────────────────────────────────────────
-- Avoids redundant LLM calls for identical (type, segment, params).
-- SHA-256 keyed, per-type TTL.
CREATE TABLE IF NOT EXISTS insight_cache (
    cache_id        BIGSERIAL    PRIMARY KEY,
    cache_key       VARCHAR(64)  NOT NULL UNIQUE,
    insight_type    VARCHAR(60)  NOT NULL,
    segment_key     VARCHAR(200) NOT NULL,
    category_id     INTEGER REFERENCES dim_product_category(category_id),
    region_id       INTEGER REFERENCES dim_region(region_id),
    question        TEXT,
    insight_text    TEXT         NOT NULL,
    structured_data JSONB,
    confidence      NUMERIC(4,3),
    model_used      VARCHAR(80),
    tokens_total    INTEGER,
    latency_ms      INTEGER,
    generated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ  NOT NULL,
    hit_count       INTEGER      NOT NULL DEFAULT 0,
    last_hit_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_cache_key     ON insight_cache (cache_key);
CREATE INDEX IF NOT EXISTS idx_cache_segment ON insight_cache (segment_key, insight_type);
CREATE INDEX IF NOT EXISTS idx_cache_expiry  ON insight_cache (expires_at);

-- ── Insight log ───────────────────────────────────────────
-- Permanent audit trail of every LLM call (cached or not).
CREATE TABLE IF NOT EXISTS insight_log (
    log_id            BIGSERIAL    PRIMARY KEY,
    insight_type      VARCHAR(60)  NOT NULL,
    segment_key       VARCHAR(200),
    category_id       INTEGER REFERENCES dim_product_category(category_id),
    region_id         INTEGER REFERENCES dim_region(region_id),
    question          TEXT,
    system_prompt     TEXT,
    user_prompt       TEXT,
    insight_text      TEXT,
    structured_data   JSONB,
    confidence        NUMERIC(4,3),
    model_used        VARCHAR(80),
    tokens_prompt     INTEGER,
    tokens_completion INTEGER,
    tokens_total      INTEGER,
    latency_ms        INTEGER,
    from_cache        BOOLEAN      NOT NULL DEFAULT FALSE,
    status            VARCHAR(20)  NOT NULL DEFAULT 'success',
    error_detail      TEXT,
    requested_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    triggered_by      VARCHAR(80)  DEFAULT 'api'
);
CREATE INDEX IF NOT EXISTS idx_log_type    ON insight_log (insight_type, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_log_segment ON insight_log (segment_key, requested_at DESC);

-- ── Cleanup helper ────────────────────────────────────────
CREATE OR REPLACE FUNCTION purge_expired_insights() RETURNS INTEGER AS $$
DECLARE deleted INTEGER;
BEGIN
    DELETE FROM insight_cache WHERE expires_at < now();
    GET DIAGNOSTICS deleted = ROW_COUNT;
    RETURN deleted;
END;
$$ LANGUAGE plpgsql;

-- ── Conversational analytics (Phase 4) ───────────────────
CREATE TABLE IF NOT EXISTS conversation_sessions (
    session_id      VARCHAR(36)  PRIMARY KEY,
    title           VARCHAR(255),
    segment_key     VARCHAR(200),
    category_id     INTEGER REFERENCES dim_product_category(category_id),
    region_id       INTEGER REFERENCES dim_region(region_id),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_active_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    message_count   INTEGER      NOT NULL DEFAULT 0,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    message_id      BIGSERIAL    PRIMARY KEY,
    session_id      VARCHAR(36)  NOT NULL REFERENCES conversation_sessions(session_id),
    role            VARCHAR(20)  NOT NULL,
    content         TEXT         NOT NULL,
    structured_data JSONB,
    confidence      NUMERIC(4,3),
    tokens          INTEGER,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_conv_msg_session ON conversation_messages (session_id, created_at);
