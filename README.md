# Barcode Monitor

A local, real-time production monitoring dashboard for synthetic **barcode
activation** events. It is a self-contained sibling application to the original
FactoryPulse demo and does **not** touch or depend on it.

Everything in this app is **simulated**:

* barcodes look like `TEST-000001`
* scanners look like `SCANNER-01` … `SCANNER-04`
* every row is stored with `is_simulated = true`
* there is **no** connection to real PDP servers, hardware scanners, MQTT
  brokers, OPC UA devices, or production databases.

---

## Stack

| Layer      | Technology                                                    |
| ---------- | ------------------------------------------------------------- |
| Frontend   | React 19 + TypeScript + Vite 6 + Tailwind CSS 3 + Plotly      |
| Backend    | Python 3.12 + FastAPI + Uvicorn                               |
| Database   | PostgreSQL 16 (single source of truth — **no InfluxDB**)      |
| Live push  | Authenticated WebSocket at `/ws/events`                       |
| Auth       | HMAC-signed bearer tokens (same format as FactoryPulse)       |

### Data flow

```
asyncio event generator ──► PostgreSQL (barcode_events)
        │                         │
        │ live push               │ REST aggregates / history / pagination
        ▼                         ▼
      WebSocket /ws/events ──► React dashboard (KPIs, charts, table, scanners)
```

The frontend never fabricates metrics — every KPI, chart and table row is
computed by the backend from the same persisted event dataset.

---

## Prerequisites

| Tool                          | Notes                                                    |
| ----------------------------- | -------------------------------------------------------- |
| macOS (Apple Silicon tested)  |                                                          |
| Python 3.12+                  | `python3 --version`                                      |
| Node.js 20+ and npm           | `node --version`                                         |
| PostgreSQL 16                 | Homebrew `postgresql@16`, or any local server on 5432    |
| Git                           |                                                          |

Docker is **not** required.

---

## Quick start

### 1. Get the code and configure environment

```bash
cd barcode-monitor
cp ../.env.example .env
# Edit .env if your PostgreSQL port/user differs. Generate a dev secret:
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # paste into AUTH_SECRET
```

### 2. Prepare the database

Preferred: a dedicated PostgreSQL cluster on port **5433**.

```bash
bash scripts/init_db.sh
```

This script is idempotent and **never drops or truncates** anything. It will:

1. reuse a server already listening on 5433 or 5432, or create a dedicated
   5433 cluster at `~/.local/share/barcode-pg` if none exists;
2. create the `production_monitoring` database if missing;
3. leave existing databases untouched.

It prints the matching `DATABASE_URL` to put in `.env`. The default is:

```
DATABASE_URL=postgresql://barcode@localhost:5433/production_monitoring
```

Fallback to the existing Homebrew server on 5432:

```
DATABASE_URL=postgresql://<your-os-user>@localhost:5432/production_monitoring
```

> macOS note: PostgreSQL refuses to start without a valid locale. The init
> script exports `LANG`/`LC_ALL=en_US.UTF-8` for you.

### 3. Terminal 1 — backend

```bash
cd barcode-monitor/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

set -a; source ../.env; set +a
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

On first start the backend:

1. creates the schema if needed,
2. seeds ~24 hours of synthetic history **only if the table is empty**
   (~1,700 events with the defaults),
3. starts the asynchronous event generator.

### 4. Terminal 2 — frontend

```bash
cd barcode-monitor/frontend
npm install
npm run dev
```

> If `npm install` warns that `esbuild`'s install script was skipped, run
> `node node_modules/esbuild/install.js` once, then `npm run dev`.

### 5. Open the dashboard

| URL                              | What                       |
| -------------------------------- | -------------------------- |
| http://localhost:5173            | Dashboard                  |
| http://localhost:8000/docs       | Interactive API docs       |
| http://localhost:8000/health     | Health check               |

Sign in with a development account:

| Username   | Password      | Can view | Can control generator / reset |
| ---------- | ------------- | -------- | ----------------------------- |
| `viewer`   | `viewer123`   | ✅       | ❌                            |
| `operator` | `operator123` | ✅       | ✅                            |
| `admin`    | `admin123`    | ✅       | ✅                            |

### Dashboard preferences

Both preferences are available in the header (and on the login screen) and are
persisted in `localStorage`:

* **Theme** — light / dark toggle (defaults to the OS `prefers-color-scheme`).
  Implemented with Tailwind's class strategy (`darkMode: "class"` on `<html>`).
* **Language** — English / Bahasa Indonesia toggle. All UI labels are
  translated via a typed dictionary in `frontend/src/i18n.ts`; timestamps and
  numbers follow the browser locale.

> Note: after changing `tailwind.config.js` you must restart the Vite dev server
> so Tailwind regenerates the `dark:` classes.


---

## Stopping safely

* Frontend / backend: press `Ctrl-C` in each terminal (Uvicorn shuts the
  generator task down cleanly).
* Dedicated PostgreSQL cluster on 5433:

  ```bash
  /opt/homebrew/opt/postgresql@16/bin/pg_ctl -D ~/.local/share/barcode-pg stop
  ```

---

## Tests

Backend unit tests (mocked persistence — they never touch your dev dataset):

```bash
cd barcode-monitor/backend
source .venv/bin/activate
python -m unittest discover -s tests
```

Frontend type-check + production build:

```bash
cd barcode-monitor/frontend
npm run build
```

Smoke test against a running stack:

```bash
cd barcode-monitor
./backend/.venv/bin/python scripts/smoke_test.py
```

The smoke test logs in, checks auth enforcement, summary/hourly/events/scanners,
pauses then resumes the generator, verifies the reset confirmation guard, and
asserts the WebSocket sends a `snapshot` first.

---

## API reference

All endpoints except `/health` and `/api/auth/login` require
`Authorization: Bearer <token>`.

| Method | Path                             | Purpose                                            |
| ------ | -------------------------------- | -------------------------------------------------- |
| GET    | `/health`                        | Liveness + DB status (no auth)                     |
| POST   | `/api/auth/login`                | Exchange dev credentials for a bearer token        |
| GET    | `/api/auth/me`                   | Current user                                       |
| GET    | `/api/monitoring/status`         | DB + generator state                               |
| GET    | `/api/monitoring/summary`        | Totals, success rate, active scanners, latest time |
| GET    | `/api/monitoring/hourly`         | Hourly aggregates (bucketed in the display timezone) |
| GET    | `/api/monitoring/scanner-activity` | Per-scanner totals over the range                |
| GET    | `/api/events`                    | Paginated/filterable/sortable event history        |
| GET    | `/api/scanners`                  | Simulated scanner status                           |
| POST   | `/api/generator/pause`           | Pause generation (operator/admin)                  |
| POST   | `/api/generator/resume`          | Resume generation (operator/admin)                 |
| POST   | `/api/generator/config`          | Set interval / success rate (operator/admin)       |
| POST   | `/api/test/reset`                | Reset + reseed simulated data (requires `confirm`) |
| WS     | `/ws/events?token=…`             | Live updates                                       |

### Query parameters for `/api/events`

`page`, `page_size` (1–200), `scanner_id`, `status` (`success|failed`),
`event_type`, `range_hours`, `sort` (`timestamp|event_id|scanner_id|status`),
`order` (`asc|desc`).

### WebSocket message schema

| `type`              | Payload                                           |
| ------------------- | ------------------------------------------------- |
| `snapshot`          | summary + scanners + generator state (on connect) |
| `event.created`     | a newly generated barcode event                   |
| `metrics.updated`   | updated summary KPI fields                        |
| `scanner.updated`   | `{ scanner_id, state }`                           |
| `generator.status`  | generator state (pause/resume/config/reset)       |
| `system.error`      | error detail                                      |

---

## Authentication notes

* Tokens use the **HMAC-SHA256 bearer** format: `header.payload.signature`
  (base64url, unpadded). The `AUTH_SECRET` never reaches the browser.
* The frontend calls `POST /api/auth/login` with a development username/password
  and reuses the returned token for REST and WebSocket calls.
* This is a local-development scheme, **not** production identity management.
  Do not treat the demo passwords as secure.
* CORS is restricted to `http://localhost:5173` / `http://127.0.0.1:5173`
  (configurable via `CORS_ALLOW_ORIGINS`).

---

## Simulated data model

Event shape (stored in `barcode_events`):

```json
{
  "event_id": "EVT-000001",
  "barcode": "TEST-000001",
  "scanner_id": "SCANNER-01",
  "event_type": "barcode_activation",
  "status": "success",
  "timestamp": "2026-10-12T08:00:10+07:00",
  "error_message": null,
  "is_simulated": true
}
```

* **Timezone**: stored as `TIMESTAMPTZ` (UTC on disk); returned and bucketed in
  `DISPLAY_TZ` (default `Asia/Jakarta`).
* **Seeding**: 24 hours, deterministic, uneven hour-of-day volume, per-scanner
  volume differences, a couple of offline windows, plausible error messages.
  Runs only when the table is empty.
* **Generator**: one backend asyncio task, interval + success-rate configurable
  at runtime, unique `EVT-######` ids, occasional offline/reconnect windows.
* **Duplicates**: prevented with `UNIQUE(event_id)` plus `ON CONFLICT DO NOTHING`.
* **Reset**: `POST /api/test/reset` truncates only `barcode_events` and
  `scanner_status` (this app's simulated data) and reseeds.

---

## Project layout

```
barcode-monitor/
├── backend/
│   ├── app/
│   │   ├── config.py      env-driven settings
│   │   ├── models.py      Pydantic contracts
│   │   ├── auth.py        HMAC bearer tokens
│   │   ├── db.py          schema + queries + aggregation
│   │   ├── seed.py        24h deterministic seeding
│   │   ├── generator.py   asyncio event generator
│   │   └── main.py        app, REST API, WebSocket
│   ├── tests/test_core.py
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── src/{main.tsx, api.ts, types.ts, i18n.ts, styles.css}
│   └── package.json
├── scripts/
│   ├── init_db.sh         idempotent DB setup
│   └── smoke_test.py
└── README.md
```

---

## Configuration (environment variables)

See `../.env.example`. Key values: `DATABASE_URL`, `AUTH_SECRET`,
`GENERATOR_ENABLED`, `GENERATOR_INTERVAL_MS`, `GENERATOR_SUCCESS_RATE`,
`SCANNER_COUNT`, `SEED_HOURS`, `SEED_EVENTS_PER_HOUR`, `DISPLAY_TZ`,
`CORS_ALLOW_ORIGINS`, `VITE_API_URL`, `VITE_WS_URL`.

---

## Common errors and recovery

| Symptom | Cause | Fix |
| --- | --- | --- |
| `could not connect to server` / `DatabaseUnavailable` | PostgreSQL not running or wrong port | `bash scripts/init_db.sh`; confirm `DATABASE_URL` |
| `invalid locale settings` when starting PG | missing `LANG`/`LC_ALL` | export `LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8` |
| Dashboard shows "Live connection unavailable" | WebSocket blocked/dropped | REST keeps refreshing every 10s; check backend is up |
| `esbuild` install script skipped | npm allow-scripts policy | `node node_modules/esbuild/install.js` |
| 401 everywhere | expired/missing token | sign out and sign in again |
| No events appear | generator paused | click **Resume** in Simulation controls |

---

## Known limitations

* Demo auth only — no production identity provider.
* Scanner status is simulated; it does not reflect real hardware.
* The Plotly bundle is large (as in the original FactoryPulse project).
* No frontend unit tests yet (build + type-check + smoke test cover integration).
