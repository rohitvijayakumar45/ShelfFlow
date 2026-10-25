# ShelfFlow

A standalone, continuous-sensing smart-store prototype for **one OV3660 shelf camera, two independently weighed trays, and concurrent shoppers**. React customer PWA and staff app, FastAPI/SQLAlchemy, PostgreSQL/Alembic, Redis/RQ, MQTT, native laptop vision, and PlatformIO controllers.

**Current prototype (16 October):** one mixed-product tray with three calibrated load cells, two RC522 readers, a verified OV3660 camera and a trained three-product pilot. The USB controller integration and customer/staff apps run locally in hardware mode. Physical ownership remains review-first; payments are simulated. Follow [local hardware integration](docs/local-hardware-integration.md), or use the simulation instructions below. Historical validation is in [implementation status](docs/status.md).

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
- [Two RC522 readers and the new 38-pin camera programmer](docs/dual-rc522-and-38pin-programmer.md)
- [Shelf sensor controller step-by-step bring-up](docs/sensor-controller-bringup.md)
- [Standalone HX711 + 3 kg load cell weight test](firmware/hx711-test/README.md)
- [Requirements audit, 12 Oct 2026](docs/audit-2026-10-12.md)
- [Mixed-product trays: per-unit weights, weight-first matching](docs/mixed-trays.md)
- [New products without retraining: restock auto-crops, teach mode, look-alikes, returns](docs/fewshot-learning.md)
- [Immediate manual capture, SAM crop preview and gallery approval](docs/manual-capture.md)
- [Dataset capture, annotation and training](docs/dataset.md)
- [Event contracts, fusion and recovery](docs/architecture.md)
- [Security and retention](docs/security.md)
- [Physical validation checklist](docs/validation.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Test results and remaining limitations](docs/status.md)

There are no customer shelf taps or shelf activation windows. Evidence buffers and settling periods are internal collection mechanisms, not permission gates. RFID and ArUco are copyable prototype identifiers. Egress is never controlled by payment state.
