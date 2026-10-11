# Evidence, transactions and recovery

## Flow

Continuous camera capture and tray sensing → bounded pre/post buffer → hypotheses → explicit vetoes → atomic cart/inventory update or pending review. Simulator, HTTP devices and MQTT ingest call `service.ingest` and `fusion.evaluate`; simulation never edits cart rows directly.

Customer entry token is a random 32-byte opaque value. Only SHA-256 digest is stored, with 120-second expiry and single-use flag. Under the same SQL lock, the gate consumes it, reserves a clean bracelet, creates an idempotent command and writes an outbox record. Cart activates only after actual UID matches and release plus position sensors confirm. Wrong UID, timeout, uncertain release and jams quarantine the bracelet. Manual recovery requires actual UID, physical confirmation and reason.

## Version 1 device contract

Authoritative Pydantic schema: `backend/schemas.py`; OpenAPI `/docs`. Event carries event ID/type, schema version, device/boot/sequence, shelf/tray, capture start/end, monotonic time, clock synchronization and uncertainty, calibration version, raw/filtered/baseline weight, evidence references, health, model status and optional candidates. Extra fields and non-finite floats are rejected.

`delta = post_g - pre_g`: negative removal, positive placement. Unit mass/tolerance are catalogue measurements; seed numbers are explicitly simulated. Firmware calibration converts raw counts from explicit zero and known mass. Median window and stable dwell establish baselines; noise, hysteresis, overload, stuck readings and disconnects defer acceptance. Python processor also flags slow drift; firmware's drift coverage needs physical tuning and should not substitute for the processor's health checks. No automatic tare.

Application ingest is serialized across processes with a PostgreSQL advisory transaction lock. SQLite tests use BEGIN IMMEDIATE. This prototype sacrifices throughput for straightforward atomicity; a larger store needs finer locks and measured throughput. Unique event IDs and device/boot/sequence tuples prevent replay. Exact redelivery is ACKed without another update. Event-ID content conflict and sequence reuse reject. Old boot, out-of-order or old capture events are durably retained for review. Receive time is stored separately and never replaces capture time.

Firmware has second-resolution NTP wall time and therefore reports `clock_synced=false`, uncertainty 1000ms. It cannot meet precise attribution timing yet. Camera clock offset is estimated by midpoint HTTP roundtrip with an uncertainty allowance. Before enabling physical automatic fusion, implement hardware clock-offset calibration, drift tracking, sync epoch recording and uncertainty-aware interval matching across source events. Hardware mode is review-first regardless of score.

## Ownership and quantity

Decoded marker → same bracelet/session mapping → visible bracelet/wrist/hand link → grasp/contact → tracked withdrawal/placement → matching tray weight. Detector confidence is displayed separately and never called a charge probability. Identity, hand-product, SKU, quantity, weight, temporal consistency and health stay separate. RFID mismatch vetoes; last read UID and nearest marker never establish ownership.

Joint same-tray hypotheses validate all candidates before applying any update. Duplicate product tracks veto. Tracker IDs are scoped to interaction clip; a confirmed trajectory cannot be reused. Concurrent different trays/customers have no single-shopper gate. Crossed hands, hidden markers, opposite hand, handoff and occlusion defer. Same-tray pickup/return cancellation is always reviewed, even with zero net mass. Pending quantities are not billed.

Integer net quantity must fit measured weight/tolerance uniquely and visual quantity. Excess tolerance, mass mismatch, unstable readings or missing evidence veto. Multi-candidate net weight cannot independently prove each person's quantity; simulator candidates are supplied as explicit mock ground truth. Real extraction is not validated and never auto-confirms. Restock is a separate audited inventory movement; it never removes a cart item. Same-tray returns require matching owned quantity. Handoff ownership stays with original picker; no automatic ownership transfer.

## Durable update and exit

Confirmed cart event, inventory movement, session version and outbox record commit together. WebSockets use authenticated full snapshots with monotonic cart versions and reconnect recovery. Pending state can change without a cart version increase; clients accept equal-version snapshots. Outbox notifications are hints; SQL is canonical.

Payment uses integer minor INR units and line price snapshots. Same payment key returns original SUCCESS/FAILURE/TIMEOUT; retry with a new key. Pending disputes/unknown owners block finalization, not egress. An unpaid order snapshot cannot silently change after a retry. Hardware checkout requires audited staff reconciliation because source timing and flush completeness are not yet physically proven. Late events stay pending and require corrections; the original receipt must remain audit-visible. See status for implemented correction coverage.

## Queues and privacy

ESP32 stores at most 64 stable-delta/RFID events as atomic LittleFS files; it writes per event, not per ADC sample. ACK removes files. Queue overflow is counted and reported, never treated as complete evidence. High-rate diagnostics are best effort. File selection is not guaranteed chronological; out-of-order events become pending. Test actual flash endurance and boot recovery. Laptop disk spool holds at most 1000 events; evidence loss is explicit. Frame buffer is limited by frames and bytes. Original JPEG evidence is preserved separately from model input.

Retention deletes JPEG/JSON evidence by age and expires evidence references; correction audit metadata remains. Evidence endpoints require staff authorization. No facial recognition.


## Historical binding and evidence allocation

Bracelet binding intervals retain the original RFID/marker mapping after return and reuse. Delayed footage is evaluated against capture-time intervals, not the current wearer; overlapping intervals remain ambiguous. Sensor evidence is exclusively allocated to one interaction group, while that group may contain multiple jointly validated shoppers. Another camera event cannot reuse the same tray delta to charge again, including through ordinary staff confirmation. Positive corrections also respect source allocation.

Uncertain gate reservations can be cancelled only after an unactivated session and physically recovered matching UID are verified with an audit reason. Cancellation clears the reservation and commands an inspected reset; it never silently repeats dispensing.
