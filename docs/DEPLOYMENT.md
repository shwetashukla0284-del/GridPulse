# Deployment guide

`compose.yaml` is built for running GridPulse on your own machine. This guide lists what it does and what must change before running it anywhere shared or public.

## Local stack

```bash
docker compose up --build        # start everything (first build takes several minutes)
docker compose ps                # health of each service
docker compose logs -f api       # follow API logs
docker compose down              # stop; data is kept in the timescale_data volume
docker compose down -v           # stop and delete stored data
```

Startup order is enforced with health checks: Redis and TimescaleDB healthy → API healthy (it has subscribed to Redis) → simulator and dashboard start. The order matters because Redis Pub/Sub drops messages published before a subscriber connects.

Only the API and dashboard are published, and only on `127.0.0.1`. Redis and TimescaleDB are reachable only on the Compose network.

### Changing ports

If 8000 or 3000 are in use, create a `.env` file next to `compose.yaml`:

```env
API_PORT=18000
WEB_PORT=13000
```

Then rebuild (`docker compose up --build`). The dashboard image must be rebuilt because `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_WS_URL` are compiled into the browser bundle, and the API's `CORS_ORIGINS` is derived from `WEB_PORT`.

## Before deploying beyond your machine

1. **Secrets.** Replace `POSTGRES_PASSWORD` (default `gridpulse_dev`) and keep it out of version control (an untracked `.env` file or the platform's secret store).
2. **Pin images.** `latest-pg16` and `7-alpine` move over time. Pin exact versions, for example `TIMESCALE_IMAGE=timescale/timescaledb:2.31.0-pg16` (the version verified in development) and a specific Redis 7.4 tag, and upgrade deliberately.
3. **TLS and a reverse proxy.** Put the API and dashboard behind a proxy (e.g. Caddy, Nginx, or a cloud load balancer) that terminates HTTPS. Use `wss://` for the WebSocket URL; browsers block `ws://` from `https://` pages.
4. **Public URLs.** Build the dashboard with the public API addresses (`NEXT_PUBLIC_API_URL=https://api.example.com`, `NEXT_PUBLIC_WS_URL=wss://api.example.com/ws/readings`) and set `CORS_ORIGINS` to the dashboard's public origin only.
5. **Authentication.** The API has none. CORS only limits which web pages can read responses in a browser; it does not stop direct requests. Add authentication (e.g. a token checked on REST and WebSocket connect) before exposing it.
6. **Migrations.** The API creates tables at startup with `CREATE ... IF NOT EXISTS`. Use versioned migrations (e.g. Alembic or plain numbered SQL files) so schema changes are reviewed and repeatable.
7. **Durable messaging.** Redis Pub/Sub does not keep messages. If losing readings during an API restart matters, switch to Redis Streams (consumer groups, acknowledgements) or Kafka.
8. **Single instance.** Run one API replica. The WebSocket hub, live queue, and anomaly detector state live in one process. Scaling out needs shared fan-out (e.g. Redis Pub/Sub for broadcasts) and meters partitioned across consumers.
9. **Data retention.** Readings grow without limit. Add a TimescaleDB retention policy (e.g. `SELECT add_retention_policy('meter_readings', INTERVAL '90 days');`) and consider compression.
10. **Backups and monitoring.** Back up the database volume. Monitor `/health` (it returns 503 if the consumer or batcher stops) and watch `websocket_readings_dropped`.

## Running without Docker (development)

```bash
# Redis and TimescaleDB (development containers, localhost only)
docker run -d --name gridpulse-redis-dev -p 127.0.0.1:6379:6379 redis:7-alpine
docker run -d --name gridpulse-timescale-dev -p 127.0.0.1:5432:5432 \
  -e POSTGRES_USER=gridpulse -e POSTGRES_PASSWORD=gridpulse_dev -e POSTGRES_DB=gridpulse \
  -v gridpulse_timescale_data:/var/lib/postgresql/data timescale/timescaledb:latest-pg16

pip install -r requirements.txt
python -m uvicorn backend.app.main:app --port 8000
python -m simulator --meters 3 --steps 300 --interval-seconds 2 --realtime --redis-url redis://127.0.0.1:6379/0

npm --prefix frontend install
npm --prefix frontend run dev      # http://localhost:3000
```
