-- ============================================================
-- CPG Platform -- Phase 5: Production Hardening
-- Users, roles, audit log, automated retraining tracking
-- ============================================================

-- ── Roles (fixed set, seeded below) ──────────────────────
CREATE TABLE IF NOT EXISTS roles (
    role_id     SERIAL PRIMARY KEY,
    role_name   VARCHAR(40) NOT NULL UNIQUE,    -- admin | analyst | viewer
    description TEXT
);

INSERT INTO roles (role_name, description) VALUES
    ('admin',   'Full access: manage users, trigger training, write reference data, view everything'),
    ('analyst', 'Read all data, trigger forecasts/insights, cannot manage users or write reference data'),
    ('viewer',  'Read-only access to dashboards, forecasts, and insights')
ON CONFLICT (role_name) DO NOTHING;

-- ── Users ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    user_id         BIGSERIAL    PRIMARY KEY,
    email           VARCHAR(255) NOT NULL UNIQUE,
    hashed_password VARCHAR(255) NOT NULL,
    full_name       VARCHAR(255),
    role_id         INTEGER      NOT NULL REFERENCES roles(role_id),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_login_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);

-- NOTE: the default admin user is NOT seeded here. A hardcoded bcrypt
-- hash in a SQL file can't be verified at write time and is a classic
-- way to ship a broken or (worse) well-known credential. Instead,
-- scripts/seed_admin.py creates the admin account on first boot using
-- the real bcrypt library, with a randomly generated password printed
-- once to the container logs. See that script for details.

-- ── Audit log ─────────────────────────────────────────────
-- Every mutating request (POST/PUT/PATCH/DELETE) gets one row here,
-- plus a few explicit security events (login, login failure, role
-- change) logged directly by the auth endpoints.
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id     BIGSERIAL    PRIMARY KEY,
    occurred_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    user_id      BIGINT       REFERENCES users(user_id),
    user_email   VARCHAR(255),                     -- denormalized, survives user deletion
    action       VARCHAR(80)  NOT NULL,             -- e.g. "http_request", "login_success", "login_failed"
    method       VARCHAR(10),
    path         TEXT,
    status_code  INTEGER,
    ip_address   VARCHAR(64),
    duration_ms  INTEGER,
    detail       JSONB
);
CREATE INDEX IF NOT EXISTS idx_audit_occurred ON audit_log (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user     ON audit_log (user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action   ON audit_log (action, occurred_at DESC);

-- ── Automated retraining run log ─────────────────────────
-- Separate from training_runs (which tracks per-call training
-- pipeline results) -- this tracks the SCHEDULER's decisions: did it
-- run, did it skip, why, and what triggered it (cron vs drift).
CREATE TABLE IF NOT EXISTS retraining_schedule_log (
    schedule_log_id  BIGSERIAL   PRIMARY KEY,
    triggered_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    trigger_reason   VARCHAR(40) NOT NULL,    -- "cron" | "drift" | "manual"
    decision         VARCHAR(20) NOT NULL,    -- "ran" | "skipped"
    skip_reason      TEXT,
    training_run_id  BIGINT REFERENCES training_runs(run_id),
    segments_checked INTEGER,
    segments_drifted INTEGER,
    duration_ms      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_retrain_log_time ON retraining_schedule_log (triggered_at DESC);
