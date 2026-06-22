#!/bin/sh
# Block until PostgreSQL is accepting connections, then exec the main command.

HOST="${POSTGRES_HOST:-postgres}"
PORT="5432"
USER="${POSTGRES_USER:-cpg}"
DB="${POSTGRES_DB:-cpg_platform}"
MAX_TRIES=60
SLEEP=3

echo "[wait-for-postgres] Waiting for PostgreSQL at $HOST:$PORT (db=$DB, user=$USER)..."

i=0
until pg_isready -h "$HOST" -p "$PORT" -U "$USER" -d "$DB" -q 2>/dev/null; do
  i=$((i+1))
  if [ "$i" -ge "$MAX_TRIES" ]; then
    echo "[wait-for-postgres] ERROR: gave up after $((MAX_TRIES * SLEEP))s"
    exit 1
  fi
  echo "[wait-for-postgres] not ready (attempt $i/$MAX_TRIES), sleeping ${SLEEP}s..."
  sleep "$SLEEP"
done

echo "[wait-for-postgres] PostgreSQL is ready -- starting application."

# Seed the default admin user, if one doesn't already exist. Tolerant
# of the users/roles tables not existing yet on a brand-new database
# (e.g. first-ever boot, before Postgres's own init scripts and/or
# this app's create_all_tables() have run) -- retries briefly rather
# than failing the whole container over a seeding step.
if [ "${SEED_ADMIN:-true}" = "true" ]; then
  echo "[seed-admin] Checking for default admin user..."
  seed_tries=0
  until python3 scripts/seed_admin.py 2>&1; do
    seed_tries=$((seed_tries+1))
    if [ "$seed_tries" -ge 10 ]; then
      echo "[seed-admin] WARNING: could not seed admin user after $seed_tries attempts -- continuing startup anyway."
      break
    fi
    sleep 2
  done
fi

exec "$@"
