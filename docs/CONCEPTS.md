# Concepts glossary

## Git repository

A Git repository is a project folder with a `.git` directory that records repository metadata and committed history. `git init` starts a local repository; it does not publish files to a hosting service.

## Working tree

The working tree is the set of project files you can currently edit. Git compares it with its recorded state to show what changed. You stage and commit changes to save snapshots in history.

## `.gitignore`

A `.gitignore` file lists generated files and folders Git should leave untracked. This project ignores Python bytecode caches, local Python virtual environments, and installed Node dependencies so generated files don't clutter source changes.

## Monorepo

A monorepo stores related parts of a system in one repository. Here, the simulator, API, frontend, infrastructure, tests, and documentation will live together. This is simpler to learn and run than coordinating several repositories.

## Empty directories in Git

Git records files, not folders by themselves. An empty folder disappears from a clone, so a small placeholder such as `.gitkeep` is used until that folder contains real project files.

## WSL 2

Windows Subsystem for Linux (WSL) runs a Linux environment alongside Windows. WSL 2 uses a lightweight virtual machine; Docker Desktop can use it to run Linux containers on a Windows computer.

## Docker Desktop

Docker Desktop is a Windows application that provides Docker tools and a place to run containers. It can connect to WSL 2 for Linux containers. Docker recommends a per-user installation for most users; it installs under the user's profile and does not require administrator rights. Docker Desktop is installed here and its Linux engine has been verified running.

## Docker CLI and engine

The Docker CLI (`docker`) is a client program: it sends requests to a Docker engine (daemon), which builds and runs containers. Installing the CLI is not the same as starting the engine. `docker info` is a useful connection check. Here, the client can reach the Linux engine; the client and server report version 29.9.3.

## Authenticode signature

On Windows, Authenticode is a way to verify that an executable has a valid digital signature from its publisher. We verified Docker's downloaded installer was signed by Docker Inc. before launching it. A valid signature does not itself mean the software has been installed.

## Data contract

A data contract is an agreed shape and meaning for information exchanged between parts of a system. `MeterReading` is the simulator's first contract: it says a reading has a meter ID, timestamp, demand, and solar generation.

## Dataclass and immutability

A Python `dataclass` generates standard methods for a class whose main job is to hold named data. `frozen=True` makes its fields read-only after construction, which helps keep one reading a stable snapshot as it moves through the system.

## Net grid power

Net grid power is the household or site's demand minus its solar generation. A positive value means the site needs to import power from the grid; a negative value means solar is producing more than the site uses and the surplus can flow back to the grid; zero means they balance.

## Deterministic profile

A deterministic profile gives the same output for the same inputs. Our daily demand and solar functions depend only on the timestamp (and solar capacity), so we can reproduce and test their values before adding randomness.

## Gaussian curve

A Gaussian curve is a smooth bell shape. We use two scaled Gaussian curves to model broad morning and evening demand peaks over a steady baseline. The peak times and widths are simple illustrative assumptions, not measurements from real meters.

## Solar daylight curve

The simulator uses a sine curve between fixed 6:00 and 18:00 daylight bounds. It starts and ends at zero and reaches the configured peak capacity at noon. This is intentionally a simple baseline: weather, season, location, panel tilt, and daylight-saving transitions are not represented yet.

## Pseudorandom generator and seed

A pseudorandom generator produces a sequence that looks random but is determined by its starting state. Supplying the same seed reproduces the same sequence, which makes bugs and tests repeatable. The simulator receives a `random.Random` instance so the caller controls that state.

## Gaussian noise

Gaussian (normal) noise adds small values centered around zero, with most values close to zero and larger deviations less common. The simulator adds this to baseline demand and solar power, then clamps at zero because these measurements cannot be negative.

## Injected fault versus anomaly

An injected fault is a known unusual value deliberately added by the simulator; `is_fault` records that ground truth for testing. An anomaly is a value an analysis algorithm later flags as unusual without relying on that marker. Keeping those separate lets us evaluate anomaly detection honestly.

## Virtual time and lazy iteration

Virtual time advances reading timestamps without waiting in real time. A lazy iterator creates each reading only when the caller asks for it, so a large simulation can be streamed or processed without holding all readings in memory.

## Real-time pacing

Real-time pacing makes the CLI wait between virtual time steps so output arrives at approximately the requested interval. It is optional; without it, the simulator runs as quickly as it can. Sleep timing depends on the operating system and processing overhead, so it is approximate rather than a precise real-time guarantee.

## JSON Lines (JSONL)

JSON Lines stores one independent JSON object per line. It is convenient for logs and command-line pipelines because each reading can be parsed or handled separately.

## JSON serialization and wire format

Serialization turns an in-memory object into a transferable representation. The simulator serializes each `MeterReading` to JSON with ISO 8601 timestamps before printing or publishing it. This JSON shape is the current wire format that future consumers must understand.

## Redis Pub/Sub

Redis publish/subscribe sends a message to a named channel. Publishers do not need to know who is listening; subscribers receive messages only while subscribed. Pub/Sub is simple and low-latency, but it is ephemeral: Redis does not keep messages for offline subscribers and there is no replay. A durable stream or broker such as Redis Streams or Kafka would be alternatives if replay and consumer tracking became requirements.

## Async/await and event loop

`async def` defines a coroutine, and `await` lets it pause while waiting for I/O without blocking the thread. Python's event loop can use that waiting time to handle other work, such as HTTP requests. This is useful for the API's long-lived Redis subscription. CPU-heavy work is not automatically made faster by `async`.

## FastAPI lifespan and background task

FastAPI lifespan code runs when the application starts and stops. We use startup to connect to Redis and subscribe, then run message consumption as an asyncio task while HTTP endpoints stay responsive. Shutdown cancels that task and closes the Redis connections.

## Pydantic validation at a service boundary

Pydantic checks incoming data against a declared model and produces useful validation errors. The API validates Redis JSON for required fields, finite nonnegative measurements, a timezone-aware timestamp, and the net-power calculation before accepting it into its in-memory cache.

## In-memory cache versus durable storage

The API previously kept each meter's latest reading in process memory, which disappeared on restart and was not shared across workers. It now stores readings in TimescaleDB and reads latest values from the database, so history survives an API restart.

## PostgreSQL and asyncpg

PostgreSQL is the relational database that stores the reading rows. `asyncpg` is an asynchronous PostgreSQL driver for Python; it lets FastAPI wait for database network I/O without blocking the event loop.

## TimescaleDB hypertable

TimescaleDB extends PostgreSQL for time-series data. A hypertable looks like a PostgreSQL table to queries, but TimescaleDB partitions its rows into time-based chunks for efficient time-series storage. The project creates `meter_readings` as a hypertable partitioned by `timestamp`.

## Composite time-series key and upsert

The database primary key `(timestamp, meter_id)` identifies one meter's reading at one time. TimescaleDB requires unique indexes on a hypertable to include its time partitioning column, which is why the timestamp is part of the key. `INSERT ... ON CONFLICT ... DO UPDATE` is an upsert: a repeated message with the same key updates the row instead of creating a duplicate.

## Database connection pool

A connection pool keeps a reusable group of database connections rather than opening a new network connection for every reading. The FastAPI lifespan creates the asyncpg pool at startup and closes it at shutdown.

## WebSocket

A WebSocket upgrades an HTTP connection into a persistent, bidirectional connection. The server can push live reading batches without the browser repeatedly polling an HTTP endpoint. This project exposes the stream at `/ws/readings`.

## Batching and backpressure

Batching groups several readings into one WebSocket event, either after a short flush interval or when the maximum batch size is reached. A bounded queue limits memory when messages arrive faster than they can be broadcast. This project's policy drops and counts the oldest queued *live update* on overflow; the reading was already persisted to TimescaleDB, so historical data remains intact.

## Slow WebSocket clients

A slow client can delay or accumulate outgoing work. The server sends to clients concurrently with a timeout and removes clients whose sends fail or time out. This prevents one slow connection from holding up all clients.

## React state and effects

React state stores changing UI data; setting state schedules the component to render with the new values. An effect (`useEffect`) is for synchronizing with something outside React, such as opening a WebSocket. The effect cleanup closes the socket and clears reconnect timers when the page unmounts.

## Exponential reconnect backoff

When a WebSocket connection fails, the frontend waits before trying again. Exponential backoff increases the delay after repeated failures, up to a cap, so a disconnected service does not cause a tight retry loop. Once connected, the retry counter resets.

## Runtime validation at the browser boundary

TypeScript checks code at build time but does not check JSON arriving over a network at runtime. The WebSocket hook therefore validates message shape, finite numeric values, timestamps, and net-power consistency before adding readings to React state.

## Bounded chart history

The browser keeps only its most recent 60 aggregated time points. This bounds memory and avoids continually growing chart data and rendering work during a long-running stream.

## Port binding

A server listens on an address and port, such as `127.0.0.1:8000`. Only one process can listen on the same address and port at a time, like one shop per street address. A second server fails to start: Windows reports error 10048. `netstat -ano` shows which process ID owns a listening port.

## Time buckets and aggregation order

`time_bucket('5 minutes', timestamp)` rounds each timestamp down to the start of its 5-minute bucket, like grouping receipts by hour. `GROUP BY` then summarizes each bucket. The order of aggregation changes the answer: to get average *system* load, first sum meters at each timestamp, then average those totals per bucket. The history query does that with a CTE (`WITH system_totals AS (...)`), a named intermediate result.

## Half-open time windows

The history query uses `timestamp >= start AND timestamp < end`. Including the start but excluding the end means back-to-back windows (00:00–00:05, 00:05–00:10) never count the boundary reading twice.

## CORS (cross-origin resource sharing)

An origin is scheme + host + port. The dashboard at `http://localhost:3000` and API at `http://localhost:8000` are different origins. By default a browser will not let a page read a cross-origin `fetch` response; the server opts in by returning `Access-Control-Allow-Origin` for trusted origins, like a guest list at a door. The server still responds either way, so CORS protects users' browsers rather than securing the API. WebSockets are not governed by CORS.

## Cancelling stale requests

When inputs change quickly (1h → 24h), an older request may finish last and overwrite newer data. `AbortController` cancels the in-flight `fetch` when the effect re-runs or unmounts, so only the latest request can update state.

## Power vs energy

Power (kW) is a rate, like a car's speed; energy (kWh) is an amount, like distance. Energy = power × hours. A 2 kW load running for 30 minutes uses 1 kWh.

## SQL window functions

A window function computes a value from related rows while keeping every row, unlike `GROUP BY`, which collapses them. `LEAD(timestamp) OVER (PARTITION BY meter_id ORDER BY timestamp)` returns the same meter's next timestamp on each row, so the KPI query can work out how long each reading lasted. `PARTITION BY` restarts the window per meter.

## Left Riemann sum and data gaps

Assuming each reading's power holds until the next sample and adding power × duration approximates the area under the power curve. If the next sample is far away (over 15 minutes here), the meter was probably offline, so that stretch is skipped rather than filled with a stale value.

## Rolling z-score

z = (value − mean) / standard deviation, computed over a meter's recent readings. It says how unusual a value is relative to that meter's own recent behaviour, like noticing a student who usually scores 70 ± 5 suddenly scoring 30. A rolling window lets "normal" follow the daily demand curve. A warm-up avoids judging with too few samples, and a std floor stops a flat window from turning tiny noise into huge z-scores.

## Precision, recall, and false-positive rate

Precision: of the readings flagged, how many were real faults. Recall: of the real faults, how many were flagged. False-positive rate: of normal readings, how many were wrongly flagged. Raising the threshold usually trades recall for precision. Scoring needs ground truth; here the simulator's `is_fault` flag provides it, and the detector itself never reads it.

## Baseline feedback and winsorizing

What a detector does with flagged values changes its future baseline. Including them lets surges inflate the mean and std, hiding later surges. Excluding them can trap the baseline during a real trend: in a decline, lows are flagged and dropped, so the mean stays too high. Winsorizing (clipping) adds a flagged value capped at mean ± threshold·std, so the baseline still moves with genuine trends but a single surge has limited influence.

## Stateful stream processing

Some stream logic needs memory of earlier events. The anomaly detector keeps each meter's last 30 readings, like a guard who knows each regular's face. That memory lives in the API process, so it is lost on restart and the detector must warm up again. Durable state (rebuilding from the database or a state store) avoids that blind spot at the cost of complexity.

## Backward-compatible messages

Adding an optional field to an existing message lets older clients keep working because they ignore fields they do not read. Here alerts are an extra `alerts` array on the WebSocket `readings` message rather than a new message type the current dashboard would reject as invalid.

## Held-out validation

Picking a setting (such as a threshold) and reporting its performance on the same data overstates how good it is. Choose on one dataset, then confirm on data not used for the choice — here, different simulator seeds.

## Merging and de-duplicating event sources

When the same event can arrive by two paths (live push and a history fetch), give it a stable identity and keep one copy per identity. Alerts use meter + parsed timestamp, the same as the database primary key. Parsing the timestamp matters: `2026-11-12T17:45:00Z` and `2026-11-12T17:45:00+00:00` are the same instant written differently.

## Graceful degradation

A feature should fail partially rather than completely when one dependency is missing. The alerts panel still shows live alerts if the history endpoint fails, and explains why history is missing.

## Test runners and regression tests

A test runner (Vitest for the frontend, `unittest` for Python) finds test files, runs them, and reports results. Unlike a one-off check, a test reruns after every change and catches regressions. A test that can never fail proves nothing; breaking the code on purpose and watching the test fail confirms it checks something real.

## ES modules vs CommonJS

Node supports two module systems: CommonJS (`require`) and ES modules (`import`). A `.ts`/`.js` file follows the package's `"type"` setting; `.mts`/`.mjs` always means ES module. Renaming one config file is a narrower fix than changing the whole package's module type.

## Throughput, latency, and percentiles

Throughput is how many readings per second get through (cars passing per minute); latency is how long one reading takes (one car's trip). p95 latency means 95% of readings were at least this fast. Percentiles expose the slow tail that averages hide. Nearest-rank method: sort the values and take the one at position ceil(p/100 × n).

## Capacity and backlogs

If messages arrive faster than the slowest stage can process them, the excess queues up. Latency then grows the longer the overload lasts, rather than settling at a constant. The highest arrival rate that does not build a backlog is the measured capacity.

## Commits and fsync

When a transaction commits, PostgreSQL by default waits for its write-ahead log to be flushed to disk (fsync) so the change survives a crash. On a slow or busy disk that wait dominates small inserts. Committing many rows together (batching) shares one flush across them; `synchronous_commit=off` skips the wait but risks losing the last moments of committed data on a crash.

## Natural batching and backpressure

Natural batching processes whatever has already arrived instead of waiting a fixed time for more, like a lift that leaves as soon as someone is inside but takes everyone already waiting. At low load batches are small, so no latency is added; under load they grow, and many rows share one commit. A bounded buffer between the Redis reader and the database writer applies backpressure: when it is full, the reader pauses and the backlog stays upstream instead of consuming memory.

## Fair A/B comparisons

To measure a change on a noisy machine, run the old and new versions back to back, alternating (A, B, A, B), with the same test. A switch that restores the old behavior (`CONSUMER_MAX_BATCH=1`) makes that possible without separate code versions.

## Docker images, layers, and multi-stage builds

An image is a packaged filesystem plus a start command; a container is a running instance of it. Each Dockerfile instruction makes a cached layer, so copying `requirements.txt` and installing dependencies before copying source code means code edits do not reinstall packages. A multi-stage build compiles in one stage and copies only the output into a smaller runtime stage, leaving build tools behind.

## Docker Compose networking

Compose puts services on a private network where each is reachable by its service name (`redis`, `timescale`, `api`). Inside a container, `localhost` means that container, not your computer. Only ports listed under `ports:` are reachable from the host; here Redis and TimescaleDB are not published at all.

## Health checks and startup order

`depends_on` with `condition: service_healthy` starts a service only after its dependency's health check passes, not merely after its process starts. A health check must test what dependents use: checking Postgres over its Unix socket passed during first-time initialization while TCP was still closed, so the check now uses TCP.

## Build-time vs runtime configuration

Next.js replaces `NEXT_PUBLIC_*` variables with their values when the app is built, because the code runs in the browser. Changing them requires rebuilding the image. Server-side settings such as `DATABASE_URL` are read when the process starts and can change without rebuilding.

## Concept coverage

All concepts planned at Phase 0 are now covered above: async/await and event loops, Redis Pub/Sub, WebSockets, time-series databases, SQL window functions, anomaly detection, React state and effects, and Docker networking.
