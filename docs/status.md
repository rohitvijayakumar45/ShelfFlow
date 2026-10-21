# Current hardware integration — 16 October 2026

The current setup uses one tray with three load cells (saved calibration `dev-ddc308-1`, 273 g), two RC522 readers and an OV3660 camera with 4 MB PSRAM. A three-product pilot detector/gallery exists for Sanitizer, Vicks and Sunscreen. The main controller now forwards durable USB events to the hardware-mode backend, which acknowledges only after commit. The customer and staff apps use a separate local integration database. See [runbook](local-hardware-integration.md).

Latest automated suite: **130 passed**, with one test-client deprecation warning. Customer/staff TypeScript checks and production builds passed. The USB firmware compiled and was uploaded; live controller health, queue drain, saved calibration ingestion and catalogue prices were checked. The camera network is being changed to Yeti; live video must be verified after reconnecting.

These checks do not establish automatic shopper ownership, general unknown-object recognition, production payment, or full physical shopping accuracy. Stock must be loaded from real item measurements before reviewing pickups/returns. Physical attribution remains staff-reviewed.

---

The following is the historical 9–12 October audit; statements about absent hardware/models describe that earlier state.

# Implementation and validation status — 9–10 October 2026 (audited 12 October 2026)

Audit of 12 October: see [audit-2026-10-12.md](audit-2026-10-12.md) for the requirement matrix, fixed defects (flush-barrier ordering, queue order, Mode B switching) and open items. Suite now **68 passed**. New camera environments `esp32cam-identify` and `esp32cam-pinmap-probe` compile; the camera pin map is a candidate until the probe reports `PROBE_PASS pid=0x3660` on the physical board.

## Verified software

| Deliverable | Result |
|---|---|
| Shopping flow | HTTP and browser QR → simulated UID/release binding → pickup → live cart → same-tray return → simulated payment → receipt |
| Automated suite | **68 passed** (12 Oct audit; was 66), one test-client deprecation warning; SQLite test database, no hardware |
| Apps | TypeScript checks and customer/staff Vite production builds passed; npm audit reported zero vulnerabilities |
| Migrations | Fresh upgrade/seed, downgrade, re-upgrade and ORM/schema comparison passed on SQLite |
| Sensor controller | Compiled `devkitc-v4-proposed`; RAM 45,728 bytes, flash 855,077 bytes |
| Gate controller | Compiled `devkitc-v4-demo`; RAM 45,676 bytes, flash 857,541 bytes |
| Camera diagnostic | Compiled; deliberately drives no camera pins |
| Camera API check | Configured capture handlers compile against SDK with disabled sentinel pins and no hardware startup |
| UART programmer | Compiled WROOM-32 USB/UART2 pass-through at fixed 115200 baud; hardware pass-through (EN held low) documented as the recommended method; not physically tested |
| Camera identify / pin-map probe | `esp32cam-identify` and `esp32cam-pinmap-probe` compiled; upload command verified as 115200 baud, `no_reset`/`no_reset` |
| Marker/dataset tools | ArUco generation/decode, split leakage checks, annotation validation, missing-model guards and metric null denominators tested |

Tests cover QR expiry/replay, wrong UID/jam/uncertain release, duplicate dispensing, manual recovery/cancellation, cleaning/reuse, pickups/returns, restock isolation, quantity ambiguity, same/different-tray concurrency, unproven co-visibility, crossed/hidden/opposite-hand/handoff vetoes, equal-weight exchange, missing camera/blur/stale/gaps, weight drift/disconnect/stuck/overload, duplicate/out-of-order/reboot/offline events, cart reconnect/atomic versions, payment timeout/retry, late corrections, historical bracelet bindings, exclusive sensor allocation, capture timestamp/boot checks, authorization, retention and flush checkpoint scope.

Firmware toolchain: Espressif32 6.10.0, Arduino 3.20017.241212+sha.dcc1105b, Xtensa 8.4.0+2021r2-patch5. The official framework archive was verified against registry SHA-256 `7dbcfb86f9dfd5ecf6c881ed8226239d9ef63f27e1e1a2ddd87a87762e2ffbc9`. **No firmware was flashed and no hardware was touched.** Compiler memory sizes are for the generic esp32dev target, not measured capacities of the supplied carrier.

## Implemented, still unverified or limited

- Compose PostgreSQL/Redis/RQ/Mosquitto/Nginx stack and device-topic ACLs: **Docker was unavailable**, so container operation, PostgreSQL transactions, broker delivery and RQ workers were not exercised. SQLite tests are not full-stack verification.
- Native JPEG capture, A/B benchmark, full-detail markers, per-tray YOLO26/ByteTrack adapter, ROI transforms, rolling buffer, original-JPEG evidence, disk spool and continuous motion/RFID/weight collection: no live OV3660 endpoint was supplied; capture timing and blur are unmeasured.
- Custom SKU train/evaluate/export tools: **no actual SKU dataset or trained weights were supplied or generated**. YOLO and optional pose inference/training were not run. Missing weights report MODEL_UNAVAILABLE; mock mode is explicit.
- Real ownership extraction is a **review-first baseline**, not a validated grasp/hand ownership model. It collects candidate observations and frames but does not prove ownership from co-visibility/proximity. Physical mode always defers automatic cart changes to staff review. Opposite-hand and handoff attribution remain unsupported automatically.
- Controller mappings are proposed for separate DevKitC V4 WROOM-32 boards. User confirmed RC522 and an ESP32-S CAM marking “28clpesp32s”, with a normal ESP32 used as programmer. Exact camera carrier/schematic/PSRAM and programmer/controller board revisions remain unconfirmed. Camera wiring is intentionally unfinished.
- SG90-class demo dispenser, UID read, release/position feedback and persistent command ledger compile. Reliable one-bracelet separation, electrical levels, jam/reboot behavior and feedback timing require physical trials. State-machine timers do not make bounded synchronous SPI/HTTP/MQTT operations hard real time; test release pulse capture under network stalls.
- Flush checkpoints can drain queues, but physical completeness and clock ordering are not proven; hardware checkout still needs audited assistance, especially offline.
- Sensor NTP timing reports uncertainty. Cross-device offset/drift calibration remains needed. Python weight processing flags slow drift; firmware drift tuning is incomplete.
- TLS firmware/device credential provisioning, individual staff identities and production security hardening remain deployment work; see security guide.

## Accuracy and latency

Physical SKU precision/recall, quantity/ownership accuracy, false-charge rate, missed/pending rates, identity failures, data loss and p50/p95/p99 interaction latency are **not measured**. They are separately unavailable in `reports/physical-metrics.json`; unavailable is not zero. Automated test runtime is not shopping latency. Use the dataset evaluator and capture benchmark after recorded trials, retaining denominators and scenario breakdowns.

The next physical step is carrier identification, isolated sensor bring-up and 20–30 human-reviewable interactions before training or accuracy claims. See [validation](validation.md), [wiring](wiring.md), [programmer](programming-camera.md), [dataset](dataset.md) and [security](security.md).
