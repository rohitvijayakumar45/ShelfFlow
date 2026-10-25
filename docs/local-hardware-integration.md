# Local hardware integration

The current prototype uses one mixed-product tray, three load cells, two RC522 readers and an OV3660 camera. Keep the main ESP32 on laptop USB during normal operation. The camera uses Wi-Fi and a separate power source. Its programmer is needed on laptop USB only for flashing.

## Start the application

Run each long-running command in its own terminal from the repository root:

```powershell
.\.venv\Scripts\python.exe -m tools.local_app setup
.\.venv\Scripts\python.exe -m tools.local_app backend
.\.venv\Scripts\python.exe -m tools.usb_bridge
.\.venv\Scripts\python.exe -m tools.camera_preview --camera http://CAMERA_IP
.\.venv\Scripts\python.exe -m tools.detector_preview
.\.venv\Scripts\python.exe -m tools.local_app vision
.\.venv\Scripts\python.exe -m tools.local_app trainer   # Train products page (docs/training.md)
```

Replace CAMERA_IP with the IP printed by the camera after joining the hotspot. Set `PHYSICAL_CAMERA_URL` in ignored `data/integration/runtime.env` to the same camera URL before starting vision. Keep only one camera connection: the detector and vision collector consume the shared preview on port 8765.

Start the existing Vite customer and staff workspaces from `apps` using `npm run dev --workspace @shelfflow/customer` and `npm run dev --workspace @shelfflow/staff`.

| Page | Address | Local login |
|---|---|---|
| Staff | http://127.0.0.1:5174 | Password `ShelfFlow-Staff-2026!` |
| Customer | http://127.0.0.1:5173 | Username `shopper`, password `ShelfFlow-Shopper-2026!` |
| Detector | http://127.0.0.1:8766 | Local preview |
| API | http://127.0.0.1:8000/docs | API documentation |

These credentials are for this local prototype. Device keys, hotspot credentials and the integration database remain in ignored local files.

## Prepare real stock and a shopping session

1. Verify live controller health, saved calibration and camera preview in the staff app. Initial stock is zero; setup does not invent item weights or inventory.
2. Use the tray loading workflow to measure and register each physical unit. Select Sanitizer (₹25), Vicks (₹199) or Sunscreen (₹99). Finish loading before testing purchases.
3. Sign in as the customer. Staff must confirm cleaning and physical handover before assigning a bracelet. B01 has RFID `43D51829`; an RFID tap alone does not prove handover or ownership.
4. Pick up or return an item. The controller records weight changes; vision supplies product candidates. Staff review identifies the customer and the actual unit before committing a cart change.
5. Reconcile outstanding evidence before checkout. Payment is simulated. Hardware mode does not automatically attribute products to shoppers.

## Event delivery and recovery

### A/C-only firmware (B disabled, 24 October)

Use environment `devkitc-v4-usb-ac` for the temporary two-cell setup. It holds B's HX711 SCK (GPIO26) high for power-down and does not sample B's DT (GPIO25). Only A and C appear in weight/health/calibration events; B cannot contribute to the total, noise, fault checks or calibration readiness. The LCD's cell pages show `B DISABLED`. The board may still supply power to B's breakout while its HX711 is in power-down.

The A/C calibration has separate NVS keys from the previous three-cell calibration. The first A/C boot shows `UNCALIB` until service calibration is saved. The previous calibration and queued events are retained. The existing known mass remains 44 g. A backup of the previous NVS partition and SHA-256 manifest is stored locally in `data/hardware-backups/sensor-nvs-before-disable-b-20261024.*`.

To calibrate manually:

1. Hold PAGE and ACTION together for three seconds, release both, then short-press ACTION to enter `EMPTY BOX ACT`.
2. Empty the tray; short-press/release ACTION, then keep hands off while it captures zero.
3. At `MASS ON A ACT`, put the 44 g weight near A; short-press/release ACTION and wait hands-off.
4. At `MASS ON C ACT`, move the same weight near C; short-press/release ACTION and wait. There is no B step.
5. At `VERIFY HOLD ACT`, move the weight to the centre and check the reading. Hold ACTION for at least 1.5 seconds, release, and wait hands-off to save. Saving requires a stable reading within 2 g or 3% of the known mass, whichever is larger.
6. Confirm `SAVED ACT:back`. Hold both buttons for three seconds and release to leave service mode.

Two-cell calibration cannot validate arbitrary load placement on a tray still mechanically supported at three points. The previous 2D load-position calculation is disabled in A/C mode. Check the same known mass at several shelf positions before relying on whole-tray item weights.

### Filtered A/C pipeline and zero tracking

The updated controller separates raw acquisition, fixed grams-per-count calibration, raw-domain EMA, tare and bounded AZT. See [complete weighing implementation and configuration](weighing-ac.md). An arbitrary loaded tare never arms zero tracking; `tareempty` explicitly confirms an empty tray.

### Manual tare for demonstrations

In normal mode, hold **ACTION for 1.5 seconds**, release it, then keep hands off. The LCD shows `TARE WAIT...` until the calibrated, healthy readings settle for one second, then `ZERO SET`. The current load becomes zero; the normal weight page has a `T` prefix while tare is active. There is a 20-second timeout if the readings never settle. Holding both buttons cancels a pending tare and retains the existing service-mode gesture.

Hold **PAGE for 1.5 seconds** to clear the tare and restore the calibrated reading. Short PAGE presses still change pages, and short ACTION presses still toggle the backlight. Service-mode ACTION continues to capture/save calibration rather than tare.

Tare is a temporary offset, not a new calibration: it does not write the calibration zero/scales to NVS. Restarting the controller or saving a calibration clears it. Repeating tare replaces the previous reference. Tare/clear actions are queued as durable HEALTH audit observations before applying the offset, and subsequent weight/health evidence includes the tare reference. Raw readings and the raw event baseline stay intact, preserving genuine weight deltas. If the audit cannot be saved, the reference stays unchanged and the LCD asks for a retry.

If a 44 g weight is present when taring, that weight is now part of the zero reference. Removing it should show approximately **−44 g**; adding another item shows the change relative to the reference. Tare the empty tray when the desired reading is the item's standalone weight. Tare does not stop further sensor drift.

The controller has a 256-event durable queue. The bridge freezes each payload locally before posting it and sends a controller ACK only after the backend confirms the matching event ID and durable commit. Network failures retain queued events for retry. Old reboot backlog retains its uncertainty and may require review.

Saved calibration is reported without writing new calibration to NVS. Do not erase flash, re-tare with items present, or run calibration while shopping. Stop the USB bridge before using COM3 for firmware uploads, and verify the camera MAC `68:09:47:F8:93:B8` before flashing camera firmware. The main controller MAC is `00:4B:12:EE:81:F4`.

The Yeti hotspot name observed on this laptop includes a trailing space (`Yeti `). Firmware SSIDs must match it exactly. A change of hotspot usually changes the camera IP; update the preview command and `PHYSICAL_CAMERA_URL`, then restart the preview and vision services.

## Validation boundary

### Connected RFID test — 24 October

The bridge now keeps receiving serial diagnostics while separate workers perform backend requests. RFID tests observe the bridge log by default, so testing does not stop event forwarding, reopen COM3, or change antenna selection. Bridge startup restores both-reader mode after any interrupted single-reader bench test.

With the bridge running, start a test using:

```powershell
.\.venv\Scripts\python.exe -m tools.rfid_tap_test --seconds 60 --expect-r1 3 --expect-r2 3
```

Perform the requested physical taps only after recording starts. Use `--source serial` only for an isolated bench test with the bridge stopped. Test reports preserve historical loss counters and fail on new losses; a passing new test does not erase earlier losses.

The connected retest captured three R1 taps and four R2 taps, matching the operator-confirmed physical counts. All seven events were found in the backend database, with no new persistence errors or loss-counter increases. Decoded-event dispatch measured 0–69 ms; this does not measure RF presentation-to-decode latency. The operator-confirmed report is in ignored `data/hardware-backups/rfid-connected-fix-20261024-operator-confirmed.json`. The software suite passed 181 tests. These results apply to this trial rather than guaranteeing all possible short taps.

The initial 16 October integration passed 130 tests; both apps passed TypeScript and production builds. Controller event delivery, saved calibration reporting and prices were checked against the running hardware backend. A complete physical stock-loading, customer-assignment, pickup/return and checkout trial remains necessary. The three-product model is a pilot; unknown objects require staff review and later dataset collection.

## Camera and RFID suggestions without weights (assisted mode)

With `WEIGHTS_ENABLED=false` in `data/integration/runtime.env`, the backend reports `weights_enabled: false` to the
vision service, which then:

- starts a collection on motion or an RFID tap instead of a weight change;
- turns only items that **left the tray (pickup) or arrived on it (return) while touched by a hand** into candidates
  (product from the gallery, bracelet markers next to that hand); windows without such a movement are dropped;
- sends these as INTERACTION events, which the backend keeps pending (no automatic cart change).

**Assisted workflow → Camera suggestions** lists each pending camera interaction pre-filled with product, direction,
quantity and customer. The customer comes from the bracelet marker at the hand, else a bracelet read by R1/R2 within
15 s, else the only active shopper. Staff play the clip, correct anything, tick the check and press **Record movement**:
this records the same audited assisted MOVE as a manual entry and resolves the camera event in one transaction
(`POST /staff/preintegration/from-evidence/{hypothesis_id}`). **Dismiss** closes events where nothing moved.
Unresolved camera events block checkout, like any pending evidence. Counted stock must exist for the product on that
tray before a pickup can be recorded.
