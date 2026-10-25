# ShelfFlow handoff (state as of 25 October 2026, ~02:00)

**Newest:** the second-laptop session (25 Oct evening) is summarised in [HANDOFF-2026-10-25-laptop2.md](HANDOFF-2026-10-25-laptop2.md). Read it first.

**Later update — new laptop and video training (25 Oct evening):** The project now also runs on a second laptop
(RTX 4050, CUDA PyTorch 2.10). There the shelf controller is **COM7** and the programmer **COM9** (pass
`--port COM7` to `tools.usb_bridge`); git-ignored files were restored from the old machine and the absolute
paths in `runtime.env` re-pointed to the new folder. PlatformIO lives in the project's `.platformio-core/`; from
that long path ESP32 compiles exceed the Windows 32,767-character command limit, so build firmware with a short
`PLATFORMIO_CORE_DIR` (e.g. the default `%USERPROFILE%\.platformio`). No firmware was changed or flashed. New:
**Train products** page (record a clip → SAM 2.1 boxes → blur/odd-frame review → YOLO26n) served by
`tools.local_app trainer`; see [training.md](training.md). Camera start-up brownouts (section 8) are still
unsolved. Full stack verified live: controller events delivered, camera 11 fps, 225 tests passed.

**Later update — manual capture:** The manual button now saves the latest raw
camera snapshot immediately into a durable queue, then uses a user-drawn box
and background SAM segmentation. Staff preview and approve the tight original
crop plus SAM cutout before either enters the gallery. Migration 0013 and
`MANUAL_CAPTURE_WORKER` are required (the local backend launcher enables it).
Automatic Teach is unchanged. See [manual-capture.md](manual-capture.md).
This supersedes the manual one-view teach description in section 9.2 below.
Validation: 220 Python tests and both app production builds passed. A live
Sanitizer snapshot saved in 250 ms; SAM inference took 1,078 ms after model
loading. Its crop was left READY and unapproved for staff inspection.

Everything needed to pick the project up where it stopped: what works, what is broken, what changed in the
24–25 October sessions, how to run it, and what to do next. Read sections 1–3 first.

---

## 1. What ShelfFlow is

A prototype "pick it up, it's in your cart" smart shelf.

| Part | Role |
|---|---|
| **Shelf controller** (ESP32 #1, DevKit V1 30-pin) | **Two 10 kg load cells** (HX711 A left, C right, one under each side of the tray; cell B was removed), two RC522 RFID readers (R1 left, R2 right) for customer bracelets, 16×2 I2C LCD, two staff buttons (PAGE, ACTION). Talks to the laptop over **USB** (Wi-Fi off) |
| **Camera** (ESP32-CAM, AI-Thinker, OV3660) | Watches the tray over **Wi-Fi** (phone hotspot **X200T** only), MJPEG stream |
| **Programmer** (ESP32 #2, DevKit V1) | USB-to-serial adapter: flashing the camera, and reading the camera's serial log |
| **Laptop** | Backend (FastAPI + SQLite), USB bridge, camera proxy, detector preview, vision (YOLO item detector + DINOv2 gallery), weight monitor, staff app (port 5174), customer app (port 5173) |

Design rules that hold everywhere:

- **Review-first in hardware mode.** The backend never charges a customer from sensors alone. Every real interaction
  is a hypothesis staff confirm or dismiss.
- The backend is still in **assisted no-weights mode** (`WEIGHTS_ENABLED=false`, section 11.3): weight events are
  recorded and linked to camera events, but staff confirm every cart movement.
- Customers wear a bracelet with an **RFID tag** plus **ArUco markers**.

---

## 2. Current status

| Area | Status | Notes |
|---|---|---|
| Load cells A and C | ✅ | New 10 kg cells. Both respond to load and return to zero. Noise ~0.2 g. A had no signal and was rewired; C had loose DT/SCK and was reseated |
| Weighing method | ✅ new | **Step-hold auto-tare** (section 7). Held 43.67 g within ±0.3 g for 2 min while the raw reading drifted ~2 g |
| Quick calibration | ✅ | 44 g, under a minute, from the weight monitor (section 7.3). Last saved: `ac2-167a4d-8q` (gains A 247.3, C 244.7 counts/g) |
| HX711 read glitches | ⚠️ rare | Occasional corrupt reads (all-0/all-1). Filtered in firmware and counted (`glitches` in controller state). A few per 3 min on C at worst |
| RFID R1 / R2 | ✅ | R1 fixed by the timing change: 12–13/13 reads, R2 9–11/11, no failures. Test tag `43D51829` |
| Camera Wi-Fi | ✅ | Firmware now joins **X200T only**. After a hotspot restart it reconnects by itself in ~12 s |
| Camera start-up | ❌ intermittent | Sometimes fails to start for a long time (once 23 min), then boots and connects in seconds. One boot log showed `Brownout detector was triggered`. Cause not proven (section 8) |
| Camera resolution | 320×240 | Reverted from 640×480 because the stream was laggy (`CAMERA_TUNE` in `runtime.env`) |
| Recognition (gallery) | ✅ | 4 products: sanitizer, sunscreen, vicks, **hair-care** (new, taught on 25 Oct) |
| Item detector | ⚠️ | Original model in use. Misses items at the tray **edges** (camera framing). Occasionally boxes wires. A retrain with empty-tray images was **rejected** (section 9.4) |
| Hand link | ❌ | Detector has no `hand` class, so camera events never link a hand (`hand_product: false`) |
| Teach / manual capture | ✅ new | Products & recognition page has a live camera view and a **Capture view** button (section 9.2) |
| Pending reviews | ⚠️ | **72** `PENDING_REVIEW` hypotheses, mostly from testing. Clear before a demo |
| Automated tests | ✅ | `python -m pytest -q`: **210 passed**. Native firmware tests pass (section 12) |
| Git | ⚠️ | Nothing from 24–25 Oct is committed (section 14) |

---

## 3. Next steps, in order

1. **Camera framing.** Re-aim or raise the camera so the whole basket fits with a margin on every side. Today the
   basket's left side runs to the image edge, so items there are cut off and not detected (confidence ~0.07 vs 0.6–0.75
   in the middle). Then set the tray ROI in `data/integration/vision-config.json` (currently the full frame `[0,0,1,1]`)
   to the basket, so wires outside it are ignored.
2. **Camera start-up failures.** Keep the programmer wired to the camera **without** the IO0 jumper (EN→GND on the
   programmer) and run `tools.camera_serial_log` (section 8.2). Power-cycle the camera until it fails to come up; the
   log shows brownout, crash or hang. Fit the **100 µF capacitor across the camera's 5V/GND pins** regardless.
3. **Clear the 72 pending reviews** (Evidence review in the staff app) so they don't block checkout.
4. **Teach the gallery evenly.** hair-care has 343 views vs ~70 for the others. Reject the blurry/hand-covered hair-care
   references (Products & recognition → References) and add 10–20 views each for sanitizer, sunscreen, vicks with
   **Capture view**.
5. **Retrain the item detector properly** (after step 1): frames with products at all positions including edges and
   the hair wax, varied empty-tray frames (wires moved, different light), and the actual frames where a wire was boxed.
   Evaluate on a held-out set that includes hair-care before switching (section 9.4).
6. **Hand class** for the detector (labelling + training, `docs/model-pipeline.md` "Training the item + hand
   detector") so camera events can link the hand and bracelet.
7. **Weights on in the backend** once happy with step-hold weighing: remove `WEIGHTS_ENABLED=false` from
   `runtime.env`, restart the backend, and run a full pickup/return trial.
8. **Commit** (section 14).

---

## 4. What changed on 24–25 October

**Shelf controller firmware** (`firmware/shelf-controller`)
- Cell B removed everywhere: `include/cell_layout.h` is A (D32/D33) and C (D34/D27) only, 2-position calibration,
  new NVS keys `ac2*` so calibrations of the old cells are never loaded. Overload limit 9 000 g (10 kg cells).
  The `-usb-ac` and `-usb-abc` builds are gone; `devkitc-v4-usb-integration` is the only shelf build and runs the
  weighing self-test at boot.
- HX711 glitch filter in `sampleCells()`: a read > 60 000 counts from the last good one is held until the next read
  confirms it; isolated outliers are dropped and counted.
- **Quick calibration** (`include/drift_check.h`, `qc*` in `src/main.cpp`).
- **Step-hold weighing** (`include/hold_tracker.h`), replacing the earlier drift-projection and empty-tray-only
  auto-zero experiments. Pickup/return threshold 3 g (was 8 g).
- Weighing pipeline gained an optional drift model (`setDrift`) and separate AZT capture/freeze windows; both are
  tested but not used by the current configuration.
- Backup of the controller code before the step-hold change:
  `data/hardware-backups/shelf-controller-20261025-005705/` (source, tests, `platformio.ini`, flashed `.bin`).

**Camera firmware**: `firmware/camera/include/secrets.h` (git-ignored) now holds **X200T only**. The previous file
(Pixel, X200T, Yeti, Shaun) is backed up in `data/hardware-backups/camera-secrets-20261025-001525.h`.

**Tools**
- `tools/weight_monitor.*`: two cells, quick-calibration panel, shows the held (auto-tared) weight.
- `tools/usb_bridge.py`: allows `qc start|next|cancel`, no B commands.
- New `tools/camera_watch.py`, `tools/camera_serial_log.py`, `tools/dataset/capture_negatives.py`.
- `tools/detector_preview.py`: loads the **live** reference gallery from the backend (it used to show newly taught
  products as "Unknown").

**Backend / vision / apps**
- `POST /staff/products/{id}/capture`: manual capture = one-view teach session, 12 s.
- `vision/run.py`: polls `/devices/vision-1/config` every 2 s (was 10 s) so teach/capture requests are seen quickly.
- Staff app, Products & recognition: live camera view, product picker, **Capture view**, last 6 taught images.

---

## 5. Hardware: identities, power, network

| Board | MAC | Port / address |
|---|---|---|
| Shelf controller (ESP32 #1) | `00:4B:12:EE:81:F4` | **COM3** (CP210x) |
| ESP32-CAM | `68:09:47:F8:93:B8` | Wi-Fi, X200T. Has been at `10.12.94.251` every time |
| Programmer (ESP32 #2) | not recorded | **COM4** when the shelf is also plugged in, COM3 when alone |

**Always check the MAC before flashing** (`esptool read_mac`).

**Power**
- External **5 V 2 A supply** → 5V rail: ESP #1 VIN and the ESP-CAM 5V pin. ESP #1 also keeps its USB for data.
- **3V3** only from ESP #1's 3V3 pin: HX711 A and C, both RC522s, the LCD.
- The programmer is powered only by its own USB. **Its VIN is never connected.**
- Multimeter readings at the camera are a steady 5 V / 3.3 V. That does not rule out microsecond brownout dips.

**Network**
- Laptop and camera on the phone hotspot **X200T**, 2.4 GHz channel 11. Laptop `10.12.94.78`, camera
  `10.12.94.251` (`PHYSICAL_CAMERA_URL` in `runtime.env`).
- The hotspot restarts often (its BSSID changed at least 5 times on 25 Oct). The camera survives this: it reconnects
  ~12 s after the hotspot returns. Turn off hotspot auto-off / battery saver on the phone anyway.

**Camera tuning** (`/tune`, lost on camera reboot, re-applied by `tools.local_app vision` from `CAMERA_TUNE`):
**8 MHz XCLK, 320×240, JPEG quality 12, ≤20 fps**. The camera CPU runs at 240 MHz (board default).

---

## 6. Wiring (matches the firmware)

### 6.1 Shared lines

| Line | Everything on it |
|---|---|
| **5V** | supply +, ESP #1 VIN, ESP-CAM 5V, C2 + |
| **GND** | supply −, ESP #1 GND, HX711 A/C GND, R1 GND, R2 GND, LCD GND, both buttons' leg 2, ESP-CAM GND, C1 −, C2 −, C3 − |
| **3V3** | ESP #1 3V3 (the only source), HX711 A/C VCC, R1 3.3V, R2 3.3V, LCD VCC, C1 +, C3 + |

### 6.2 Per module

**HX711 A, C** (each drives one 10 kg cell; one cell under each side of the tray)

| Module | VCC | GND | DT | SCK | E+ | E− | A− | A+ |
|---|---|---|---|---|---|---|---|---|
| A (left) | 3V3 | GND | ESP #1 D32 | ESP #1 D33 | cell A red | cell A black | cell A white | cell A green |
| C (right) | 3V3 | GND | ESP #1 D34 | ESP #1 D27 | cell C red | cell C black | cell C white | cell C green |

**RC522 R1, R2**

| Reader | SDA | SCK | MOSI | MISO | IRQ | GND | RST | 3.3V |
|---|---|---|---|---|---|---|---|---|
| R1 (left) | ESP #1 D21 | ESP #1 D18 | ESP #1 D23 | ESP #1 D19 | NC | GND | ESP #1 D22 | 3V3 |
| R2 (right) | ESP #1 D4 | ESP #1 D18 | ESP #1 D23 | ESP #1 D19 | NC | GND | ESP #1 D22 | 3V3 |

**LCD** (I2C backpack, 0x27 or 0x3F): GND, VCC 3V3, SDA ESP #1 RX2 (16), SCL ESP #1 TX2 (17).

**Buttons** (internal pull-ups): PAGE ESP #1 D13 → GND, ACTION ESP #1 D14 → GND.

**ESP #1 (shelf controller)**

| VIN | GND | 3V3 | D32 | D33 | D34 | D27 | D18 | D23 | D19 | D22 | D21 | D4 | RX2 16 | TX2 17 | D13 | D14 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5V | GND | 3V3 | A DT | A SCK | C DT | C SCK | RC522 SCK | MOSI | MISO | RST | R1 SDA | R2 SDA | LCD SDA | LCD SCL | PAGE | ACTION |

Not connected: D25, D26 (were cell B), EN, VP, VN, D35, D12 (boot pin), D2, D15, D5, TX0, RX0.

**ESP32-CAM**: 5V and GND to the supply. For flashing **or** serial logging, the programmer: ESP #2 TX0 → camera
U0T, ESP #2 RX0 → camera U0R, ESP #2 GND → camera GND, ESP #2 EN → ESP #2 GND. **IO0 → GND jumper only while
flashing**; it must be removed for normal running and logging.

**Capacitors** (stripe = −): C1 100 µF at ESP #1 3V3/GND; C2 100 µF at the ESP-CAM 5V/GND pins (**still not
fitted**); C3 10 µF at the RC522s 3V3/GND.

The perfboard layout is in [perfboard/ShelfFlow-perfboard-guide.pdf](perfboard/ShelfFlow-perfboard-guide.pdf)
(generated by `tools/perfboard_guide.py`, drawn for the old three-cell build).

---

## 7. Weighing: how it works now

### 7.1 What was measured

- The zero of the new cells **wanders with temperature**: ±1 g around a trend that reversed from minute to minute
  (−2.2, +3.6, −1.4 g/min), netting about −0.6 g/min over 5 minutes. It cannot be predicted.
- Projecting a drift rate measured over 50 s made things **worse** (it guessed the wrong sign: empty tray went
  −6.9 → −15.4 g in 5 min). That approach was removed.

### 7.2 Step-hold auto-tare (`include/hold_tracker.h`)

1. At boot, the first settled reading is the **empty tray = 0 g**. Boot with the tray empty.
2. Every **2 s** while the reading is settled, each cell is re-tared to the weight it currently holds (0 g empty,
   10 g with a 10 g item, …). Drift never gets more than ~2 s to build up.
3. A settled change of **≥ 3 g** is an item: the held weight moves to the new value (that is the pickup/return
   `WEIGHT` event, with pre/post per cell) and re-taring continues from there.
4. A step that lands within **1.5 g of 0** snaps to exactly 0.
5. Sliding an item across the tray (same total, different split between A and C) is not an event.
6. Tare, "Zero empty tray" and any calibration reset the hold to 0.

Limits: changes under 3 g are absorbed as drift (items should be ≥ ~5 g); drift while a hand is on the tray rides
along with the step (a few tenths of a gram); if the board boots with items on the tray they read as 0.

The pipeline's own auto-zero is disabled (`autoZeroEnabled = false`) because the hold tracker does it for any load.

### 7.3 Quick calibration (before demos, 44 g, under a minute)

Weight monitor (http://127.0.0.1:8767) → **Quick calibration** → **Start**, then follow the prompt; each move is
detected automatically: tray empty → 44 g over **A (left)** → over **C (right)** → at the **centre** → off. Each
window is 5 s after 1 s of stillness; touching the tray restarts that window. All samples are fitted to
`zero + rate·t + load[position]` per cell, so drift during the run is removed before the A/C loads give the gains;
the centre must read 44 ± 2 g. Saved as a normal calibration `ac2-<boot>-<n>q`. Refuses (changes nothing) on
movement (fit scatter > 1.5 g), A/C readings too alike, centre check off, or drift > 6 g/min. Serial: `qc start`,
`qc next` (manual "I moved it"), `qc cancel`; on the shelf, ACTION = "I moved it", long PAGE = cancel.

The 3-step service-mode calibration (`cal empty`, `cal a`, `cal c`, `cal save`) still exists as a fallback.

---

## 8. Camera reliability

### 8.1 What is known

- When it starts, it works: joins X200T within ~3–5 s of boot, healthy OV3660, RSSI −53 to −62 dBm.
- **Hotspot restart test (01:14):** camera stayed running (uptime 101 s), was off the network 20 s, back 12 s after
  the hotspot. Wi-Fi recovery works.
- **Long outages are boot failures:** after a 23-minute outage it came back with uptime 3 s, i.e. it had not been
  running and one boot finally succeeded.
- One serial boot log (24 Oct, programmer attached) showed `Brownout detector was triggered` right after `entry`,
  ~5 times per second. A steady multimeter reading does not exclude these dips.
- Both recoveries on 24/25 Oct coincided with the programmer being connected. Unproven; worth testing.

### 8.2 Tools

- `python -m tools.camera_watch` → `reports/camera-watch.jsonl`. Every 3 s: laptop SSID/BSSID and camera
  `/health`. On return it prints **`REBOOTED while away`** (power/crash) or **`STAYED RUNNING, was off the
  network`** (Wi-Fi).
- `python -m tools.camera_serial_log --port COM4` → `reports/camera-serial.log`. Camera serial via the programmer
  without resetting anything. Needs camera U0T → programmer TX0, GND, programmer EN → GND, **no IO0 jumper**. On 25 Oct
  the log stayed empty (wire not connected, then the port was unplugged), so no failure has been captured yet.

### 8.3 Flashing the camera

Programmer on its own COM port (COM4 with the shelf plugged in), IO0 → GND jumper fitted, then:

```text
.\.venv\Scripts\python.exe "$env:USERPROFILE\.platformio\packages\tool-esptoolpy\esptool.py" --chip esp32 --port COM4 --baud 115200 --before no_reset --after no_reset --connect-attempts 0 read_mac
.\.venv\Scripts\python.exe -m platformio run -d firmware\camera -e esp32cam -t upload --upload-port COM4
```

Press the camera **RST** while esptool shows `Connecting...` (a second press is sometimes needed). Confirm the MAC
is `68:09:47:F8:93:B8` before uploading. Afterwards remove the IO0 jumper and power-cycle. Details:
[programming-camera.md](programming-camera.md).

---

## 9. Recognition

### 9.1 Gallery

Recognition = frozen DINOv2-small embeddings compared with a gallery of reference crops (threshold 0.68, margin
0.06). No retraining: new references reach the live gallery within ~10 s, in both vision and the detector preview.
Views (each reference counts 7 augmented views): sanitizer 69, sunscreen 78, vicks 76, **hair-care 343** (49
references, ~6 of them blurred or hand-covered). No look-alike pairs reported.

### 9.2 Teach and manual capture (staff app → Products & recognition → Catalogue)

- **Teach** (per product row): 20 s automatic, keeps up to 20 views that add a new look.
- **Show camera and capture manually**: live view (the detector preview's annotated frame), product picker,
  **Capture view**. A capture is a one-view, 12 s teach session: the next detected item crop that passes the quality
  gate (sharp, not mostly skin, ≥ 24 px) becomes a reference. It only works when the live view shows a **box** around
  the product, so keep it away from the tray edges and keep hands out of it.

### 9.3 Detector

`models/shelf-pilot-item-detector.pt` (sha1 `288ddc6e…`, YOLO26n, single class "item", 99 training images from
16 Oct, 640 px). Confidence threshold 0.25. Weak at the tray edges (framing) and sometimes boxes wires. No hand class.

### 9.4 Rejected retrain (25 Oct)

Fine-tuned for 30 epochs with 45 empty-tray frames (`data/shelf-pilot/negatives/empty-run1`, 60 captured, 15 held
out). On the old test images it looked better (recall 0.94 → 0.98, mAP50 0.935 → 0.968), but live it **stopped
detecting the hair wax** (0.06–0.25 vs 0.59–0.75): the near-identical empty frames taught "this tray = nothing" and
no training image contained the new product. Rolled back. Kept for reference only:
`models/shelf-pilot-item-detector.rejected-negatives-20261025.pt`. The run and report are in
`data/shelf-pilot/runs/item-negatives/` and `data/shelf-pilot/negatives/train-run1/report.json`.

---

## 10. Firmware builds and flashing (shelf controller)

`pio.exe` is blocked by Windows Application Control; run PlatformIO through Python. **Stop the USB bridge first**
(it owns COM3).

```text
.\.venv\Scripts\python.exe -m platformio run -d firmware\shelf-controller -e devkitc-v4-usb-integration
.\.venv\Scripts\python.exe "$env:USERPROFILE\.platformio\packages\tool-esptoolpy\esptool.py" --chip esp32 --port COM3 read_mac
.\.venv\Scripts\python.exe -m platformio run -d firmware\shelf-controller -e devkitc-v4-usb-integration -t upload --upload-port COM3
```

The flash keeps the LittleFS event queue and NVS (calibration). Serial commands (115200, newline): `usbstatus`,
`calstatus`, `mass <g>`, `tare`, `tareempty`, `untare`, `cal empty|a|c|save|cancel`, `qc start|next|cancel`,
`r1`, `r2`, `both`, `rfpower 0|1|2`, `rfthr <hex>`, `rfsettle <3-20>`, `rftries <1-4>`, `rfstatus`, `scale debug on|off`.

---

## 11. Running it

### 11.1 Services (each in its own terminal, from the repo root)

```text
.\.venv\Scripts\python.exe -m tools.local_app backend
.\.venv\Scripts\python.exe -m tools.usb_bridge
.\.venv\Scripts\python.exe -m tools.camera_preview --camera http://10.12.94.251
.\.venv\Scripts\python.exe -m tools.detector_preview
.\.venv\Scripts\python.exe -m tools.local_app vision
.\.venv\Scripts\python.exe -m tools.weight_monitor
.\.venv\Scripts\python.exe -m tools.camera_watch          (optional, diagnostics)
cd apps; npm run dev --workspace @shelfflow/staff     (and @shelfflow/customer)
```

| Page | Address |
|---|---|
| Staff | http://127.0.0.1:5174 (password in [local-hardware-integration.md](local-hardware-integration.md)) |
| Customer | http://127.0.0.1:5173 |
| Weight monitor + quick calibration | http://127.0.0.1:8767 |
| Detector preview | http://127.0.0.1:8766 |
| Camera proxy | http://127.0.0.1:8765 |
| API docs | http://127.0.0.1:8000/docs |

After restarting a service, **Ctrl+F5** open pages; a tab opened while a service was down can stay on "Waiting for
live A and C readings".

### 11.2 Gotchas

- **Stale processes** keep ports 8000/8765/8766/8767 and the new one silently fails. List them with
  `Get-CimInstance Win32_Process -Filter "Name='python.exe'"`. Each venv process shows twice (launcher + child).
- Only **one** process may open COM3 (bridge or flasher). The weight monitor does not open it.
- After the camera reboots it falls back to 320×240 defaults; restarting vision re-applies the tune.
- Detector preview and vision each load the detector at start: restart both after changing the model file.

### 11.3 Configuration (`data/integration/runtime.env`, git-ignored)

`SHELFFLOW_MODE=hardware`, `WEIGHTS_ENABLED=false` (assisted no-weights mode), `PHYSICAL_CAMERA_URL=http://10.12.94.251`,
`CAMERA_TUNE="8,12,20,320x240"`, plus secrets and keys. Database `data/integration/store.db`, migration 0012.

---

## 12. Tests

- Python: `.\.venv\Scripts\python.exe -m pytest -q` → **210 passed**.
- Apps: `cd apps; npx tsc --noEmit -p staff` and `-p customer`.
- Firmware (host, MSYS2 g++ in `C:\msys64\ucrt64\bin`):
  `g++ -std=c++17 -Wall -Wextra -Werror firmware/shelf-controller/tests/<name>_test.cpp -o <out>.exe` for
  `cell_calibration`, `manual_tare`, `weighing_pipeline`, `drift_check`, `hold_tracker`, `rfid_tap`. All pass.
  Windows Smart App Control sometimes refuses to run a freshly built test `.exe` ("Permission denied"); rebuilding
  with a different flag or output name usually gets one through.

---

## 13. Backups and rollback

| What | Where | Restore |
|---|---|---|
| Shelf controller before step-hold | `data/hardware-backups/shelf-controller-20261025-005705/` | copy sources back, or flash `firmware-flashed.bin` |
| Camera Wi-Fi list (4 networks) | `data/hardware-backups/camera-secrets-20261025-001525.h` | copy to `firmware/camera/include/secrets.h`, rebuild, flash |
| Item detector (in use) | `models/shelf-pilot-item-detector.before-negatives-20261025-015217.pt` (= current file) | — |
| Rejected detector | `models/shelf-pilot-item-detector.rejected-negatives-20261025.pt` | do not use |

---

## 14. Uncommitted work

The last commit is `8a4fee3`. Everything since is uncommitted (64 paths): the 24 Oct work (A/C pipeline, assisted
mode, migration 0012, queue index, RFID timing, perfboard guide) and the 25 Oct work in section 4.

Before committing: run the tests (section 12). **Never commit:** `firmware/**/include/secrets.h`, `data/`
(`runtime.env`, DB, backups, datasets), `models/`, `evidence/`. Consider leaving `reports/camera-watch.jsonl` and
`reports/camera-serial.log` out as well.

---

## 15. Diagnostics cheat-sheet

| Symptom | Meaning / action |
|---|---|
| Empty tray not 0 after boot | Booted with something on the tray, or not settled: empty it and press "Zero empty tray" |
| Weight jumps then stays wrong by a few g | An item < 3 g, or drift during handling. Re-zero when empty |
| `glitches` count rising fast | HX711 DT/SCK/VCC/GND joint loose on that channel |
| `DISCONNECTED_x` / raw stuck | That HX711's wiring |
| Quick calibration `SPREAD MASSES` | Mass not right over A/C, or one cell not responding (press test it) |
| Quick calibration `CENTRE CHECK` | Mass not at the centre, or gains off: repeat |
| Camera `REBOOTED while away` | Power/brownout or crash: read the serial log |
| Camera `STAYED RUNNING, was off the network` | Hotspot/Wi-Fi |
| Camera `Brownout detector was triggered` | Fit C2, short thick 5 V wires, or a separate 5 V supply |
| Capture: "Nothing usable was seen" | No box around the product in the live view (edge, hand, blur) |
| New product shows "Unknown" in the preview | Teach it; the preview picks up references within ~10 s |
| Staff app looks old / endpoints 404 | A stale backend is on port 8000 |
| `rc522=0x00` | Version is read at boot only; restart ESP #1 |
