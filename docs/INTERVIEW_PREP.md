# Interview preparation

These are initial study notes, not claims about a completed or benchmarked system. Answers should be revised as the project is implemented and measured.

## Why use a monorepo for this project?

A monorepo keeps the simulator, API, frontend, infrastructure, tests, and documentation together. That makes a small learning project easier to navigate and run. Separate repositories can make sense when teams need independent ownership, release cycles, or access controls.

## Is the system running yet?

The simulator can generate multi-meter readings, write JSON Lines, and publish readings to a local Redis Pub/Sub channel. FastAPI consumes and validates those messages, persists them to TimescaleDB, exposes database-backed latest readings and a count, and broadcasts batched events over `/ws/readings`. A Next.js frontend connects with a reconnecting WebSocket hook and displays live KPIs, a chart, and meter status. Historical analytics and anomaly detection still need to be implemented.

## How will you report throughput or latency?

Only after running a repeatable load test and recording the measurement method and results. No throughput or latency numbers have been measured yet.

## How do you make simulated data reproducible?

The generator accepts a `random.Random` instance instead of using global random state. Tests create it with a fixed seed, so the same inputs produce the same reading sequence. A real run can use an unseeded generator for variation.

## What is the difference between an injected fault and an anomaly?

An injected fault is a known surge placed into synthetic data and marked as ground truth. An anomaly is a value flagged by a detector based on observed behavior. The detector should evaluate the measurements, not use the simulator's fault marker as its answer.

## Why use virtual time and a lazy iterator in the simulator?

Virtual time lets us generate readings across many intervals without sleeping for every interval. A lazy iterator yields readings one at a time, which keeps memory use proportional to the current reading rather than the whole simulation and lets us later connect the generator to a stream publisher.

## How can the simulator run quickly or approximately in real time?

By default it advances virtual timestamps without waiting. The optional `--realtime` mode flushes each JSONL reading and sleeps between timestamp batches. Sleep timing is approximate, so this is useful for demos, not a hard real-time guarantee.

## Why use Redis Pub/Sub here, and what happens if a consumer is offline?

Redis Pub/Sub is a simple, low-latency way to decouple the simulator from the API consumer. A publisher writes to a channel without knowing its subscribers. Delivery is ephemeral: a consumer that is offline when a message is published misses it, because Pub/Sub does not persist or replay messages. If replay, acknowledgements, or durable backlogs become necessary, Redis Streams or Kafka would be better candidates.

## Why use an async Redis client in FastAPI?

The API serves HTTP requests while it waits for incoming Redis messages. `redis.asyncio` lets the subscription yield control to the event loop while waiting on network I/O, so it does not block the server's thread from handling requests. This helps I/O concurrency; it does not make CPU-heavy work faster.

## Why use TimescaleDB for these readings?

TimescaleDB keeps PostgreSQL's relational model and SQL while adding time-series hypertables partitioned by time. It fits timestamped meter readings and preserves the option to use ordinary PostgreSQL queries and constraints.

## How do duplicate meter/timestamp messages behave?

The readings table uses `(timestamp, meter_id)` as its primary key, and the insert uses `ON CONFLICT ... DO UPDATE`. Reprocessing a message for the same meter and timestamp updates that row rather than increasing the row count. The time column must be part of a Timescale hypertable's unique keys.

## What are the remaining delivery limitations?

Redis Pub/Sub is ephemeral. If the API or database is unavailable, messages published while no healthy subscriber is processing them can be lost. The current consumer logs a database failure and reports unhealthy status rather than silently pretending to save the message, but it does not yet reconnect or replay. A durable queue such as Redis Streams/Kafka plus retry and dead-letter handling would address that in a production design.

## How does the WebSocket server handle slow clients?

It puts validated readings into a bounded queue and groups up to 100 readings or waits at most 100 ms before broadcasting. Each connected client is sent the batch concurrently with a one-second timeout. If the queue fills, the oldest live update is dropped and counted; the reading was already persisted, so the historical database remains complete. A failed or slow client is removed.

## How does the dashboard recover from a WebSocket disconnect?

The React hook watches the browser WebSocket's close event and reconnects with capped exponential backoff. It cleans up the connection and timer when the component unmounts. Reconnection restores future live messages but does not replay missed readings; historical REST queries will be used for recovery and chart backfill.

## Why limit the browser's chart history?

Keeping a fixed rolling window (60 time points) bounds memory and rendering work as the stream continues. The backend persists the complete history, so a bounded live chart does not imply discarding database records.

## How does the history endpoint aggregate readings, and why in two stages?

It first sums all meters at each timestamp to get system load at that instant, then uses TimescaleDB's `time_bucket` to average those totals within each bucket. Bucketing raw rows directly would double-count a meter that reports several times per bucket (if summed) or give a per-meter average (if averaged). The result was checked against an independent Python calculation for a known seeded run.

## How do you stop a history query from returning too much data?

The API validates the window before querying: the end must be after the start, buckets are 1 second to 1 day, and a request may produce at most 2,000 buckets. Invalid requests get HTTP 422 with a message suggesting a larger bucket. At larger scale, TimescaleDB continuous aggregates could precompute common bucket sizes.

## Why did the REST endpoint need CORS when the WebSocket did not?

The dashboard (port 3000) and API (port 8000) are different origins. Browsers refuse to let a page read cross-origin `fetch` responses unless the server sends `Access-Control-Allow-Origin`. The browser's same-origin rules do not apply CORS to WebSocket connections. The API allows only the dashboard origins and `GET`, configurable via `CORS_ORIGINS`. CORS is a browser protection, not authentication: tools like curl still receive responses.

## How does the dashboard recover history after a disconnect?

Each successful WebSocket connect increments a counter; the history panel uses it as an effect dependency, so it refetches stored history from TimescaleDB after every reconnect. It also refreshes every minute and cancels outdated requests with `AbortController`.

## How do you compute energy (kWh) from power samples?

Energy is power × time. The KPI query uses `LEAD(timestamp) OVER (PARTITION BY meter_id ORDER BY timestamp)` to find each meter's next sample and multiplies power by that duration in hours (a left Riemann sum). Gaps longer than 15 minutes count as missing data so an offline meter is not credited with its last value for hours. Results were checked against an independent Python calculation, including a deliberate 31-minute gap.

## Why split import and export per meter instead of netting system totals?

Netting first lets one home's export cancel another's import, understating both. Splitting each reading into `GREATEST(net, 0)` (import) and `GREATEST(-net, 0)` (export) before summing reports what actually flowed in each direction at each meter.

## What does solar coverage mean here?

(demand − grid import) / demand: the share of demand met by on-site solar. It differs from solar ÷ demand, which can exceed 100% when surplus solar is exported.

## How does the anomaly detector work, and how did you evaluate it?

Each meter keeps a rolling window of its last 30 demand readings; a new reading's z-score is (value − mean) / std, flagged above a threshold, with a warm-up and a std floor. The simulator's `is_fault` flag is ground truth used only for scoring. On 21,600 simulated readings with 229 injected faults (seed 2026), the default (clip, threshold 3.0) had precision 0.465 and recall 1.000; at threshold 4.0, precision was 0.927 with recall 1.000. Simulated surges are much larger than the noise, so these numbers overstate real-world performance.

## What went wrong in the first detector version?

Excluding flagged values from the baseline seemed safer, but measured 1,412 false positives at threshold 3.0. Hypotheses were tested in turn: false-positive runs were short (not a frozen baseline) and std was not collapsing; instead the false positives clustered in the night-time demand decline and 85% were below baseline. Dropping flagged lows during a downward trend kept the mean too high, a one-sided feedback loop. Clipping flagged values to mean ± threshold·std fixed it (263 false positives) while still detecting every injected fault, unlike including raw values, which missed 6.

## Why not tune the threshold on the evaluation data?

Choosing a threshold from the same data used to report results overfits and overstates performance. The threshold should be chosen on one seed and confirmed on a held-out seed.

## How are anomalies processed in the live pipeline?

For each valid reading the consumer saves it, runs the per-meter detector, saves any alert to an `anomaly_alerts` hypertable, then queues the reading and alert for WebSocket delivery. Alerts travel as an extra `alerts` field on the existing batch message so older clients keep working. `GET /alerts` serves stored alerts. Verified live: the WebSocket, the REST endpoint, and an offline detector run on the same seeded readings produced the same two alerts.

## What happens to anomaly detection when the API restarts or messages are replayed?

Detector state is in memory, so a restart clears baselines and each meter needs a 10-reading warm-up; a fault in that window is missed (observed once on a held-out seed). Alerts are inserted with `ON CONFLICT DO NOTHING`, so a replay keeps the original judgement. The detector does not yet reject out-of-order readings. Restoring baselines from recent database readings at startup would shorten the blind spot.

## How did you choose the anomaly threshold?

On seed 2026 threshold 4.0 had the best precision with full recall, then it was confirmed on held-out seeds 7 and 99: precision 0.953 / 0.913, recall 1.000 / 0.995. Simulated surges are much larger than real-world faults, so these figures are optimistic.

## How does the dashboard avoid duplicate or lost alerts?

It merges two sources: alerts pushed live over the WebSocket and stored alerts fetched from `GET /alerts`. Each alert is identified by meter plus parsed timestamp (the database primary key), so an alert seen both live and in history appears once. History is refetched after every reconnect, so alerts raised while disconnected reappear. The merge is a pure, bounded function verified against real backend responses.

## What happens if the alert history endpoint is unavailable?

The panel reports that history is unavailable and continues to show live alerts. Batches from servers that do not send an `alerts` field are still accepted, so frontend and backend can be upgraded independently.

## How is the frontend tested?

Vitest runs 17 unit tests on pure logic: runtime validators for WebSocket and REST data, alert merge/de-duplication, and per-timestamp chart aggregation (`npm --prefix frontend test`). To confirm the tests can fail, a deliberate bug in the alert key was introduced and caught by two tests. Component and hook tests are a planned addition. Python has a separate `unittest` suite (`python -m unittest discover -s tests`).

## What throughput and latency did you measure?

On an Intel Core i3-6006U laptop running the client, API, Redis, and TimescaleDB together (Docker Desktop, WSL 2), two runs of the load test gave a sustainable rate of about 65–76 readings/s. At 50 readings/s, p50 latency from Redis publish to WebSocket receipt was 80–91 ms and p95 156–315 ms; the 100 ms batching window accounts for most of the median. Above capacity, latency grew to seconds, but all readings were still persisted and delivered. These are single-machine development numbers, not production capacity.

## How did you find the bottleneck?

By ruling things out with measurements: a single message took ~135 ms (no fixed delay); API CPU stayed near 15% of a core and database connections were idle (waiting, not computing); timing the same INSERT without the API showed the per-commit cost, which varied between 5.6 ms and 69 ms median across runs, and roughly halved with `synchronous_commit=off` in a test session. The consumer commits one reading at a time, so its rate is about 1 / commit time. The planned fix is batching inserts so many readings share one commit.

## Why report ranges instead of one number?

Results on this laptop varied substantially between runs because of disk latency and other workloads. Two runs per rate with ranges are more honest than a single best run, and the methodology and hardware are recorded so results can be reproduced.

## How did you improve throughput, and by how much?

The consumer committed one reading per transaction, so its rate was bounded by commit/fsync latency. It now uses natural batching: it takes all messages already waiting (up to 500) and saves them in one transaction. In an alternating A/B test on the same laptop, one-commit-per-reading delivered 36–77 readings/s and built multi-second backlogs at 100/s and above. Batching kept up with every rate the load generator could produce (up to 385/s delivered), with p50 79–151 ms and p95 at most 703 ms. The batched ceiling was not reached because the single-process load generator topped out first.

## Doesn't batching add latency?

Not with natural batching: it never waits for more messages, so at low load batches are about one reading and latency matches the unbatched path. Batches grow only when a backlog already exists, which is exactly when sharing a commit helps. Measured p50 at 50/s was 81–84 ms batched versus 115–3,663 ms unbatched on this machine.

## How do you run the whole system, and what did containerizing it teach you?

`docker compose up --build` starts Redis, TimescaleDB, the API, a simulator, and the dashboard. Health checks order startup so the API subscribes before the simulator publishes (Pub/Sub drops earlier messages). Two lessons came from verification: a Postgres health check over the Unix socket passed while the image's temporary init server was running, so the API was refused over TCP until the check was changed to `pg_isready -h 127.0.0.1`; and Next.js `NEXT_PUBLIC_*` URLs are compiled in at build time and must be the browser's addresses (host ports), not Compose service names.

## What would you change to deploy this for real?

Secrets out of the Compose defaults, pinned image versions, TLS via a reverse proxy with `wss://`, authentication on REST and WebSocket, versioned migrations, a durable queue (Redis Streams or Kafka) instead of Pub/Sub, a retention policy, backups, and monitoring of `/health`. Scaling beyond one API instance needs shared WebSocket fan-out and partitioned detector state. These are listed in `docs/DEPLOYMENT.md`.

## How is the public demo hosted?

On Hugging Face Spaces, which is free but runs one container on one port. The root `Dockerfile` starts from the TimescaleDB image, adds Redis, Python, Node.js, and Caddy, and a start script launches the services in the same order Compose's health checks enforce. Caddy is the only public port and routes API and WebSocket paths to FastAPI, everything else to Next.js, so the dashboard is built for "same origin" and needs no CORS. Trade-offs: storage is temporary (data restarts with the container), and Postgres uses trust authentication, which is acceptable only because it listens on 127.0.0.1 inside the container. Local testing caught one bug: the base image sets `PGDATA` to a directory the non-root user cannot write, so `initdb` failed until `PGDATA` was set explicitly.

## Why move the demo to Render, and what changed?

Hugging Face Spaces started asking for payment, and Render's free web service needs no card. Render has no TimescaleDB, so I kept the all-in-one image; only a `render.yaml` Blueprint and memory limits were added (Postgres `shared_buffers` 32 MB, 20 connections, Next.js heap 160 MB) because all six processes share 512 MB. The trade-offs are cold starts after 15 idle minutes and a database that resets on restart, which is fine for a demo driven by a simulator.
