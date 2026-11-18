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

## Free public demo: Hugging Face Spaces (single container)

Hugging Face Spaces hosts a Docker app for free with no credit card, but runs **one container on one port** (7860). The root `Dockerfile` therefore packages every service as a process in one image:

| Compose service | All-in-one equivalent |
|---|---|
| `redis` | `redis-server` on 127.0.0.1 (in memory only) |
| `timescale` | `postgres` with `timescaledb` preloaded, data under `$HOME/pgdata` |
| `api` | `uvicorn` on 127.0.0.1:8000 |
| `simulator` | `python3 -m simulator --realtime` |
| `frontend` | `next start` on 127.0.0.1:3000 |
| (new) | **Caddy** on port 7860 — the only public port |

`deploy/huggingface/start.sh` starts them in the same order Compose's health checks enforce and exits if any process stops, so the host restarts the whole container. `deploy/huggingface/Caddyfile` sends `/health`, `/readings/*`, `/analytics/*`, `/alerts`, `/ws/*`, `/docs`, and `/openapi.json` to FastAPI and everything else to the dashboard. Because the browser sees one origin, the dashboard is built with empty `NEXT_PUBLIC_API_URL` / `NEXT_PUBLIC_WS_URL` (same origin, see `frontend/src/lib/endpoints.ts`) and CORS is not needed.

Run it locally first:

```bash
docker build -t gridpulse-allinone .
docker run --rm -p 7860:7860 gridpulse-allinone   # http://localhost:7860
```

Publish it:

1. Create a Space at <https://huggingface.co/new-space>: SDK **Docker**, template **Blank**, hardware **CPU basic (free)**.
2. Create a Hugging Face access token with **write** permission at <https://huggingface.co/settings/tokens>. Git uses it as the password.
3. Push the repository to the Space (the YAML block at the top of `README.md` tells Spaces to use Docker on port 7860):

   ```bash
   git remote add space https://huggingface.co/spaces/<username>/GridPulse
   git push space main
   ```

4. Watch the **Logs** tab while it builds. The site is at `https://<username>-gridpulse.hf.space`.

Free-tier trade-offs:

- **Temporary storage.** The database starts empty after every restart or rebuild; the simulator refills it, so history covers only the time since the last start.
- **Sleeps when idle.** A Space with no visitors for about 48 hours sleeps; the next visitor waits for it to start.
- **Database without a password.** PostgreSQL uses `trust` authentication but listens only on 127.0.0.1 inside the container, and only Caddy's port is reachable. Do not publish other ports from this image.
- **Python 3.12.** The image uses the TimescaleDB base image's Alpine Python (3.12) rather than the 3.13 used locally.

## Free public demo: Render (single container, no card)

Hugging Face Spaces later asked for payment, so the free demo now targets Render's free web service. It reuses the same all-in-one image; `render.yaml` (a Render Blueprint) describes one Docker web service on the free plan with `/health` as its health check. Render sets `PORT` (10000 by default) and the Caddyfile listens on `$PORT`, so no code differs between hosts.

1. Sign in at <https://dashboard.render.com> with GitHub (no card needed for the free plan).
2. **New → Blueprint**, pick the `GridPulse` repository, and apply `render.yaml`.
3. Watch the deploy logs. The first build takes a long time (it built in about 30 minutes on the development laptop; Render's time was not measured). The site is at `https://gridpulse-<suffix>.onrender.com`.

Free-plan trade-offs:

- **512 MB RAM for all six processes.** `start.sh` lowers Postgres `shared_buffers` to 32 MB, caps connections at 20, and limits the Next.js heap to 160 MB. Measured locally under `docker run --memory 512m -e PORT=10000`: about 205 MiB idle and 210 MiB after 100 dashboard/API requests (Next.js about 113 MB RSS, uvicorn about 55 MB), no OOM kill. Render itself has not been measured; if the service restarts with an out-of-memory event, reduce `SIM_METERS` first.
- **Sleeps after 15 minutes without traffic.** The next visitor waits for a cold start, and the database starts empty (storage is temporary), as on Spaces.
- **Same security limits as the Spaces image** (trust auth on 127.0.0.1 only, Python 3.12).

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
