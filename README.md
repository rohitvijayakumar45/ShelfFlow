# ShelfFlow

A standalone, continuous-sensing smart-store prototype for **one OV3660 shelf camera, two independently weighed trays, and concurrent shoppers**. React customer PWA and staff app, FastAPI/SQLAlchemy, PostgreSQL/Alembic, Redis/RQ, MQTT, native laptop vision, and PlatformIO controllers.

**Current boundary:** the simulator-backed shopping flow runs through the real ingestion/fusion interface. Physical ownership extraction is deliberately review-first. No custom SKU model is supplied or trained; no hardware has been physically verified. The camera carrier is unknown and its pins are not invented. See [implementation status](docs/status.md).

## Fast local start (Windows, no Docker required)

Python 3.12 and Node 22+ are recommended. From this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[test]'
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m backend.seed
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

In another terminal:

```powershell
cd apps
npm ci
npm run dev --workspace @shelfflow/customer
```

In a third terminal, from `apps`:

```powershell
npm run dev --workspace @shelfflow/staff
```

Customer: http://localhost:5173 · Staff: http://localhost:5174 · API docs: http://localhost:8000/docs. Local staff password: `local-demo-staff`. Each customer tab has a separate session-storage login; use two browser tabs for two shoppers. All payments are simulated. Local SQLite is an explicit developer fallback; Compose uses PostgreSQL.

```powershell
.\.venv\Scripts\python.exe -m simulator.demo
.\.venv\Scripts\python.exe -m pytest -q
cd apps
npm run build
```

If an old parent-directory npm installation intercepts nested npm commands, invoke the current Node installation's npm CLI directly. The included `apps/build.mjs` also builds both apps without nested npm commands.

## Full stack

Install Docker Desktop with Linux containers first. Copy `.env.example` to `.env`, then:

```powershell
docker compose --env-file .env -f infra/compose.yaml up --build
```

Includes migrations, seeding, apps, PostgreSQL, Redis, Mosquitto, durable MQTT ingestion, outbox publishing, RQ maintenance and scheduler. Services bind loopback. Local broker credentials are provisional and insecure; deployment requires separate device secrets/passwords and TLS. Native laptop vision is intentionally outside Docker. See [setup](docs/setup.md).

## Read next

- [Demo walkthrough](docs/demo.md)
- [Hardware, BOM and wiring](docs/wiring.md) and [placement drawing](docs/placement.svg)
- [Use the spare ESP32 as a programmer](docs/programming-camera.md)
- [Dataset capture, annotation and training](docs/dataset.md)
- [Event contracts, fusion and recovery](docs/architecture.md)
- [Security and retention](docs/security.md)
- [Physical validation checklist](docs/validation.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Test results and remaining limitations](docs/status.md)

There are no customer shelf taps or shelf activation windows. Evidence buffers and settling periods are internal collection mechanisms, not permission gates. RFID and ArUco are copyable prototype identifiers. Egress is never controlled by payment state.
