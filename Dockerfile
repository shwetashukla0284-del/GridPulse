# All-in-one GridPulse image for single-container hosts (Hugging Face Spaces).
#
# Hosts like Spaces run ONE container that exposes ONE port, so this image runs
# every service from compose.yaml as a process instead of a container:
#   Redis, PostgreSQL + TimescaleDB, FastAPI, the simulator, Next.js, and Caddy.
# Caddy is the only public entry point (port 7860): it routes API and WebSocket
# paths to FastAPI and everything else to the dashboard, so the browser talks
# to one origin. For local development and normal servers, use compose.yaml.
#
#   docker build -t gridpulse-allinone .
#   docker run --rm -p 7860:7860 gridpulse-allinone     # http://localhost:7860

# ---- 1. Build the dashboard ------------------------------------------------
FROM node:24-alpine AS web-build
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Empty values mean "same origin" (see frontend/src/lib/endpoints.ts): the
# browser calls /readings/... and /ws/readings on whatever address it loaded.
ENV NEXT_PUBLIC_API_URL="" \
    NEXT_PUBLIC_WS_URL="" \
    NEXT_TELEMETRY_DISABLED=1
# Build, then drop dev-only packages (TypeScript, ESLint, Vitest) from node_modules.
RUN npm run build && npm prune --omit=dev

# ---- 2. Runtime: TimescaleDB image plus the other services -------------------
# Start from the database image because PostgreSQL + TimescaleDB is the hardest
# part to install; Redis, Python, and Node.js are plain Alpine packages.
FROM timescale/timescaledb:latest-pg16

RUN apk add --no-cache bash redis nodejs py3-pip
# Caddy is a single static binary; copy it from the official image.
COPY --from=caddy:2.10-alpine /usr/bin/caddy /usr/local/bin/caddy
# The official binary carries the cap_net_bind_service file capability (to bind
# ports below 1024). Hosts that drop that capability, such as Render, then refuse
# to execute it ("Operation not permitted", exit 126). Caddy listens on a high
# port here, so re-copy the file: busybox cp does not keep the capability.
RUN cp /usr/local/bin/caddy /tmp/caddy && mv -f /tmp/caddy /usr/local/bin/caddy

WORKDIR /app
COPY requirements.txt .
# This image's Python belongs to Alpine; --break-system-packages allows pip to
# install into it (acceptable in a single-purpose container). Retries and a
# longer timeout ride out dropped connections on slow networks.
RUN pip install --no-cache-dir --break-system-packages \
        --retries 10 --timeout 60 -r requirements.txt

COPY backend ./backend
COPY simulator ./simulator
COPY deploy/huggingface ./deploy/huggingface
COPY --from=web-build /web/.next ./web/.next
COPY --from=web-build /web/node_modules ./web/node_modules
COPY --from=web-build /web/package.json /web/next.config.ts ./web/

# Spaces runs the container as user ID 1000; everything writable lives in its home.
RUN adduser -D -u 1000 user
USER user
# PGDATA overrides the base image's /var/lib/postgresql/data, which this user cannot write.
ENV HOME=/home/user \
    PGDATA=/home/user/pgdata \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production

EXPOSE 7860
# The TimescaleDB image's entrypoint is for running Postgres alone; replace it.
ENTRYPOINT []
CMD ["bash", "/app/deploy/huggingface/start.sh"]
