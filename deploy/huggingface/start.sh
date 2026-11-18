#!/usr/bin/env bash
# Starts every GridPulse service inside one container, in dependency order:
# Redis and PostgreSQL -> API (the Redis subscriber) -> simulator, dashboard, Caddy.
# If any process exits, the script exits too, so the host restarts the container
# instead of leaving it half-working.
set -euo pipefail

PGDATA="${PGDATA:-$HOME/pgdata}"
DB_USER=gridpulse
DB_NAME=gridpulse

log() { echo "[start] $*"; }

wait_for() {  # wait_for <description> <command...>
  local what=$1; shift
  for _ in $(seq 1 120); do
    if "$@" >/dev/null 2>&1; then log "$what is ready"; return 0; fi
    sleep 1
  done
  log "$what did not become ready"; return 1
}

# Stop all child processes when the container is asked to stop.
trap 'kill $(jobs -p) 2>/dev/null || true' TERM INT

# --- Redis: in-memory only, reachable only inside the container.
redis-server --bind 127.0.0.1 --port 6379 --save '' --appendonly no &
wait_for "Redis" redis-cli -h 127.0.0.1 ping

# --- PostgreSQL + TimescaleDB. Storage on free Spaces is temporary, so the
# database starts empty after each restart (the simulator refills it).
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  log "initializing database in $PGDATA"
  # trust auth is acceptable only because Postgres listens on 127.0.0.1 inside
  # the container and that port is never published; only Caddy's port is public.
  initdb -D "$PGDATA" -U "$DB_USER" --auth=trust >/dev/null
fi
postgres -D "$PGDATA" \
  -c listen_addresses=127.0.0.1 \
  -c unix_socket_directories=/tmp \
  -c shared_preload_libraries=timescaledb \
  -c timescaledb.telemetry_level=off \
  -c shared_buffers=32MB \
  -c max_connections=20 \
  -c timescaledb.max_background_workers=2 &
wait_for "PostgreSQL" pg_isready -h 127.0.0.1 -U "$DB_USER" -d postgres
if ! psql -h 127.0.0.1 -U "$DB_USER" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'" | grep -q 1; then
  createdb -h 127.0.0.1 -U "$DB_USER" "$DB_NAME"
fi

# --- FastAPI. Same-origin through Caddy, so CORS is not needed.
cd /app
export REDIS_URL=redis://127.0.0.1:6379/0
export DATABASE_URL="postgresql://$DB_USER@127.0.0.1:5432/$DB_NAME"
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 &
wait_for "API" wget -q -O /dev/null http://127.0.0.1:8000/health

# --- Simulator: starts after the API because Redis Pub/Sub drops messages
# published before a subscriber is connected.
python3 -m simulator \
  --meters "${SIM_METERS:-5}" \
  --interval-seconds "${SIM_INTERVAL_SECONDS:-2}" \
  --steps 1000000 \
  --realtime \
  --redis-url "$REDIS_URL" > /dev/null &

# --- Dashboard and the public entry point.
# A heap cap keeps Next.js within small hosts' memory (Render free: 512 MB for everything).
(cd /app/web && exec node --max-old-space-size=160 node_modules/next/dist/bin/next start -H 127.0.0.1 -p 3000) &
caddy run --config /app/deploy/huggingface/Caddyfile --adapter caddyfile &
log "GridPulse is starting on port ${PORT:-7860}"

# Exit as soon as any service stops.
wait -n
status=$?
log "a service exited (status $status); stopping container"
kill $(jobs -p) 2>/dev/null || true
exit "$status"
