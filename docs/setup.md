# Setup and operation

## Local simulator

Follow the README. The local `.env` is loaded by all Python entry points. This mode intentionally permits alias-only customer enrollment, mocked ownership evidence, fixture weights and development secrets. Bind to loopback. Do not use it for real customer charging.

The database must be migrated before starting. `backend.seed` runs once and does not overwrite later edits. Delete/recreate only a disposable simulator database to reset; never delete a real event ledger. PostgreSQL migrations run in Compose's `migrate` service. The initial schema is frozen independently of the ORM, with subsequent revisions for corrections, reconciliation and measured weight distributions.

Apps use `/api` and `/ws` through Vite proxies locally and Nginx in Compose. Production build: `cd apps; npm ci; node build.mjs`. A phone scanner needs HTTPS or a loopback secure context. For phone access, use a trusted HTTPS reverse proxy and add its exact origins to `CORS_ORIGINS`.

Customer login lasts one hour. Reconnect opens an authenticated socket with a token in its first message and recovers a full snapshot. JWTs are in sessionStorage, not URLs. Staff can provision password-authenticated local customer accounts in Sessions & receipts; hardware mode requires them. Passwords are salted scrypt hashes, and an active account login resumes its existing visit. Sign-out ends that browser login; it does not silently clear an active shopping ledger.

For hardware mode use a separate database and `SHELFFLOW_MODE=hardware`, configured non-default secrets, actual SKU samples/calibration, verified stock restocks and registered physical bracelet UIDs/marker IDs. Hardware seed creates empty trays, unmeasured catalogue entries and no fixture bracelets. Never migrate a populated simulator ledger into a physical shopping trial without explicit reconciliation.

## PostgreSQL, jobs and broker

Compose requires Docker Desktop (Linux containers); it is not installed on the implementation machine. Broker init creates a password file in a named volume. `.env` is not built into images. PostgreSQL and Redis have no host ports. Mosquitto and the HTTP apps bind localhost. `worker` runs RQ jobs; `scheduler` enqueues retention and uncertain-release timeout checks every 30 seconds.

On Linux, native RQ worker: `rq worker --url redis://localhost:6379/0`. On Windows use Docker for the normal fork worker (or RQ SimpleWorker for development). Python API/simulation does not require Redis.

Outbox delivery is at least once. Device commands and events are idempotent at the application layer. Broker ACK delivery is only attempted after durable SQL commit. The laptop queue retains an event until HTTP returns `durable: true`. Firmware removes its file only after matching application ACK.

## Native vision

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[vision]'
.\.venv\Scripts\python.exe -m vision.markers 1 data\marker-1.png
.\.venv\Scripts\python.exe -m vision.benchmark http://CAMERA-IP --key YOUR_CAMERA_KEY --mode A --output reports\capture-a.json
.\.venv\Scripts\python.exe -m vision.benchmark http://CAMERA-IP --key YOUR_CAMERA_KEY --mode B --output reports\capture-b.json
```

Configure `vision/config.example.json` from the actual image dimensions and tray openings. Copy it to an ignored data directory. Export MQTT credentials through `VISION_MQTT_USERNAME=vision-1`, `VISION_MQTT_PASSWORD`, and `MQTT_CA` for a TLS broker.

```powershell
.\.venv\Scripts\python.exe -m vision.run --camera http://CAMERA-IP --camera-key YOUR_CAMERA_KEY --device-key YOUR_VISION_DEVICE_KEY --config data\vision-config.json --weights models\custom-best.pt --device cpu
```

Omit `--weights` to run capture, motion collection and markers with `MODEL_UNAVAILABLE`; no exact SKU recognition is claimed. CPU is the default. Use `--device 0` only after installing a supported PyTorch CUDA build and measuring memory/latency on the actual GPU. One detector/tracker instance per tray avoids crossing crop coordinate systems. Marker decode uses original full-resolution images. Product boxes are crop coordinates; `full_frame_box` adds crop origin before full-image overlays. Any further scaling must be explicitly inverted.

Mode A defaults to SVGA continuous JPEG. Mode B switches SVGA ↔ QXGA for evidence and refuses high resolution without PSRAM. No pre-event laptop buffers are flushed. Switching delay and queued-frame capture timestamps must be measured. This transport implements authenticated JPEG polling, not MJPEG; the dataset MJPEG tool supports an external camera endpoint if its actual carrier provides one.

Real pipeline collects motion without waiting for RFID or a weight delta, so zero-net exchanges are still observable. The baseline does not prove grasp or assign identities from proximity. Its proposals go to staff review. Motion and trigger thresholds are unvalidated starting configuration.

## Firmware

Install `.[firmware]`. Each project: copy `firmware/common/Secrets.example.h` to `include/secrets.h`, replace all values, then run `python -m platformio run -d firmware/shelf-controller` (or gate-controller/camera). Provision LittleFS explicitly using an empty filesystem image (`pio run -t buildfs`, then `uploadfs`) before event capture; the runtime deliberately never auto-formats. Set upload port after identifying the USB device. Use `pio device monitor -b 115200` for status.

Camera diagnostic target compiles without any pin driving. `pio run -d firmware/camera -e camera-api-check` additionally type-checks the real capture handlers against the SDK, with disabled sentinel pins and a startup that never initializes hardware. Identify the carrier, select its exact PlatformIO board, PSRAM settings and partition capacity, then fill `board_config.h`. Neither build verifies the actual carrier or its pin map. Do not flash the API-check target.

Shelf setup shows best-effort raw ADC readings even before calibration. Record the stable empty raw value and a known-mass loaded raw value, then apply explicit calibration with a new version. Repeated calibration versions are refused by firmware. Initial NTP timing remains uncertain; firmware reports that honestly and hardware associations remain staff-reviewed.
