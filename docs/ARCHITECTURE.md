# Architecture

GridPulse simulates smart meters, streams their readings through Redis into a FastAPI service, stores them in TimescaleDB, detects anomalies, and pushes live updates to a Next.js dashboard.

## Data flow

```mermaid
flowchart LR
    SIM[Simulator<br/>meters + solar + faults] -->|JSON on gridpulse:readings| REDIS[(Redis Pub/Sub)]
    REDIS --> CONSUMER[FastAPI consumer<br/>validate, detect, batch]
    CONSUMER -->|one transaction per batch| DB[(TimescaleDB<br/>meter_readings, anomaly_alerts)]
    CONSUMER -->|bounded live queue| BATCHER[WebSocket batcher<br/>100 ms or 100 events]
    BATCHER -->|readings + alerts| UI[Next.js dashboard]
    UI -->|GET history, KPIs, alerts| REST[FastAPI REST]
    REST -->|time_bucket, LEAD, recent alerts| DB
```

1. **Simulator** (`simulator/`): generates readings for each meter from daily demand and solar curves, adds seeded Gaussian noise and occasional injected demand surges (`is_fault`, ground truth), and publishes JSON to Redis. Virtual time by default; `--realtime` paces output.
2. **Redis Pub/Sub**: decouples the simulator from the API. Delivery is fire-and-forget: subscribers that are not connected miss messages, and nothing is replayed.
3. **Consumer** (`backend/app/consumer.py`): a reader task moves messages into a bounded inbox; the consumer takes every message already waiting (up to `CONSUMER_MAX_BATCH`, default 500), validates each with Pydantic, runs the anomaly detector, and saves readings and alerts in one transaction. Only then are they queued for live delivery.
4. **Anomaly detector** (`backend/app/anomaly.py`): per-meter rolling z-score over the last 30 demand values, threshold 4.0, 10-reading warm-up, 0.05 kW std floor, flagged values clipped into the baseline. State is in memory and resets on restart.
5. **Storage** (`backend/app/repository.py`): `meter_readings` and `anomaly_alerts` are TimescaleDB hypertables keyed by `(timestamp, meter_id)`. Readings upsert (`DO UPDATE`); alerts keep the first judgement (`DO NOTHING`). Schema is created at startup.
6. **Live delivery** (`backend/app/websocket.py`): a bounded 1,000-event queue (drops and counts the oldest on overflow; data is already stored) feeds a batcher that broadcasts every 100 ms or 100 events to all clients concurrently, removing clients that fail or exceed a 1 s send timeout.
7. **REST** (`backend/app/main.py`): `/health`, `/readings/latest`, `/readings/count`, `/readings/history` (two-stage `time_bucket` aggregation), `/analytics/kpis` (`LEAD`-based energy integration), `/alerts`. CORS allows only the dashboard origins and `GET`.
8. **Dashboard** (`frontend/`): a reconnecting WebSocket hook (capped exponential backoff) with runtime validation, a 60-point live chart, KPI cards, a historical trends panel (1h/6h/24h), and an alerts panel that merges live and stored alerts. History and alerts refetch every minute and after each reconnect.

## Key design decisions

| Decision | Why | Trade-off |
|---|---|---|
| Redis Pub/Sub between simulator and API | Simple, low-latency decoupling | No persistence or replay; Redis Streams or Kafka would add acknowledgements and backlog |
| Persist before live delivery | Database is the complete record even if the live queue drops | Live updates wait for the commit |
| Natural batching of writes | Many readings share one commit; no added latency at low load | A batch is lost from live delivery if its transaction fails (the consumer stops) |
| Server-side WebSocket batching (100 ms) | Fewer, larger frames | Adds up to 100 ms latency |
| Rolling z-score with clipped baseline | Per-meter, adapts to daily curve; measured better than exclude/include | Warm-up blind spot; in-memory state |
| Runtime validation in browser | TypeScript does not check network data | Duplicated rules with Pydantic |
| Schema created at startup | Simple for a learning project | Production should use versioned migrations |

## Configuration

| Variable | Default | Used by |
|---|---|---|
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | API |
| `DATABASE_URL` | `postgresql://gridpulse:gridpulse_dev@127.0.0.1:5432/gridpulse` | API |
| `READINGS_CHANNEL` | `gridpulse:readings` | API (load tests use `gridpulse:loadtest`) |
| `CONSUMER_MAX_BATCH` | `500` (`1` = one commit per reading) | API |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | API |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Dashboard (build time) |
| `NEXT_PUBLIC_WS_URL` | `ws://localhost:8000/ws/readings` | Dashboard (build time) |

`compose.yaml` additionally reads `API_PORT`, `WEB_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `REDIS_IMAGE`, `TIMESCALE_IMAGE`, `SIM_METERS`, and `SIM_INTERVAL_SECONDS`.

## Measured performance

Measured on the development laptop (Intel Core i3-6006U, 2 cores / 4 threads, 7.9 GB RAM, Windows 10, Docker Desktop with WSL 2), with the load generator, API, Redis, and TimescaleDB all on that machine. Latency is Redis publish → WebSocket receipt. Full method and tables: `docs/LEARNING_LOG.md`, Phase 7 steps 2–3.

| Configuration | Sustained delivery | Latency |
|---|---|---|
| One commit per reading (`CONSUMER_MAX_BATCH=1`) | 36–77 readings/s | Multi-second backlog at ≥ 100/s offered |
| Batched (`CONSUMER_MAX_BATCH=500`) | Kept up with every offered rate, up to 385/s delivered | p50 79–151 ms, p95 ≤ 703 ms at 50–400/s offered |

The batched ceiling was not reached: the single-process load generator topped out at 277–388 published/s. No readings were lost in any run. These are single-machine development numbers, not production capacity.

Anomaly detection, scored against injected faults (5 meters × 3 simulated days, 21,600 readings per seed, threshold 4.0): seed 7 precision 0.953 / recall 1.000; seed 99 precision 0.913 / recall 0.995 (threshold chosen on seed 2026). Simulated surges are much larger than the noise, so these figures are optimistic.

## Deployment variants

- **Compose (`compose.yaml`)**: one container per service, for local development and servers.
- **All-in-one (`Dockerfile`, `deploy/huggingface/`)**: for hosts that run a single container on one port (Render free web service via `render.yaml`; originally Hugging Face Spaces). Every service runs as a process; Caddy on port 7860 routes `/health`, `/readings/*`, `/analytics/*`, `/alerts`, `/ws/*`, `/docs`, and `/openapi.json` to FastAPI and the rest to Next.js. The dashboard is built with empty `NEXT_PUBLIC_*` URLs, meaning same origin (`frontend/src/lib/endpoints.ts`). Storage is temporary on free hosts. See `docs/DEPLOYMENT.md`.

## Known limitations

- Redis Pub/Sub loses messages published while the API is down; there is no replay.
- Detector state resets on restart (10-reading warm-up per meter); out-of-order readings are not rejected; `/health` `anomalies_detected` counts detections, not unique stored alerts.
- One process holds the WebSocket hub, live queue, and detector; multiple API replicas would need shared fan-out (e.g. Redis) and partitioned detector state.
- No authentication or TLS; CORS is not access control.
- The simulator models time of day only (no seasons, weather, or timezones); night-time solar averages slightly above zero because noise is clamped at zero.

## Repository layout

- `simulator/` — reading model, profiles, generator, virtual-time simulation, Redis publisher, CLI (`python -m simulator`).
- `backend/app/` — FastAPI app, consumer, detector, repository (SQL), WebSocket hub, models, time-window rules.
- `frontend/` — Next.js App Router dashboard (`src/components`, `src/hooks`, `src/lib`, `src/types`) and Vitest tests (`src/**/*.test.ts`).
- `tests/` — Python unit tests, anomaly evaluation (`python -m tests.evaluate_anomaly`), load test (`python -m tests.load_test`).
- `compose.yaml`, `backend/Dockerfile`, `frontend/Dockerfile` — container setup.
- `docs/` — learning log, concepts, architecture, deployment, interview preparation.
