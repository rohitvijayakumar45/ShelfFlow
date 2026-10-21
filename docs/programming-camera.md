# Programming the OV3660 camera board with a second ESP32 (no USB-TTL adapter)

**15 October hardware update:** the user now uses a **new 38-pin ESP32 as the
camera programmer**, separate from the shelf sensor ESP32. Use its GPIO1/TX,
GPIO3/RX, EN and GND labels for Method A; pin count is not a board model.
The user has reported OV3660 PID 0x3660, successful initialization and a
640x480 JPEG, with 4 MB flash and 4 MB PSRAM on the camera. The new programmer
wiring itself has not yet been tested. See the [current connections](dual-rc522-and-38pin-programmer.md).
The staged procedure below was written before that successful camera report.

Status: **procedure and firmware builds verified on the development PC; no step below has been
performed on physical hardware yet.** Every stage ends with a check you can see on screen or measure.
If a check does not match, stop at that stage and use the troubleshooting table at the end.

What the user has reported: one camera board marked "ESP32-S" / "28clpesp32s" carrying the **OV3660**
sensor, one ordinary ESP32 development board (used here as the programmer), and one RC522 reader. The
camera board's schematic is not confirmed, so this guide first proves which board it is, then proves
the camera pin map by reading the sensor's ID back, before anything relies on it.

---

## 0. Parts and tools

| Qty | Item | Note |
|---:|---|---|
| 1 | Camera board (ESP32-S module + OV3660 on ribbon cable) | Remove the microSD card if one is inserted |
| 1 | New 38-pin programmer ESP32 with a USB-UART port | Separate from the existing shelf sensor controller |
| 1 | USB **data** cable for the programmer | Charge-only cables give no COM port |
| 6 | Female-to-female Dupont jumper wires, 10–20 cm | Suggested colours below |
| 1 | Digital multimeter with continuity beeper | Required for the checks |
| 1 | 5 V regulated supply, at least 2 A, with GND lead | Needed only from stage 8 (Wi-Fi running) |

Suggested wire colours (use whatever you have, but write down your mapping):
W1 yellow, W2 green, W3 black, W4 red, J1 blue (programmer jumper), J2 white (camera jumper).

Software (already in this repo's `.venv`): PlatformIO and esptool 4.5.1. All commands are PowerShell,
run from the repository root `C:\Users\rohit\Projects\ShelfFlow`.

---

## 1. Identify both boards (power off, no wires)

### 1a. Camera board

Look at the silkscreen labels next to the two 8-pin headers (flip the board if they are printed on the
other side). Tick each one:

| Header row 1 | Header row 2 |
|---|---|
| ☐ 5V | ☐ 3V3 |
| ☐ GND | ☐ IO16 |
| ☐ IO12 | ☐ IO0 |
| ☐ IO13 | ☐ GND |
| ☐ IO15 | ☐ VCC |
| ☐ IO14 | ☐ U0R |
| ☐ IO2 | ☐ U0T |
| ☐ IO4 | ☐ GND |

Also confirm: ☐ a metal-can module marked ESP32-S, ☐ a small RST push-button, ☐ a camera ribbon
connector with the OV3660 ribbon latched in, ☐ a microSD slot, ☐ a white flash LED.

* **All 16 labels match** → the board has the AI-Thinker ESP32-CAM header layout. Continue.
* **Any label differs** (for example the board has its own USB port, or labels like `TX`/`RX`/`G0`) →
  stop. Photograph both sides and add the photos to the hardware checklist; this guide's wiring does
  not apply to that board.

Header layout matching is **not** proof of the camera pin map. Stage 7 proves it electrically.

Write down which pin labelled GND sits next to IO0. On some clones that pin is not really ground, so
stage 3 checks it with the meter.

### 1b. Programmer ESP32

Find these labels on the programmer. The two common boards print them differently:

| Signal | ESP32-DevKitC (38-pin) label | "ESP32 DEVKIT V1" (30-pin) label |
|---|---|---|
| Reset input | EN | EN |
| UART0 TX = GPIO1 | TX (or TXD0 / IO1) | TX0 |
| UART0 RX = GPIO3 | RX (or RXD0 / IO3) | RX0 |
| UART2 RX = GPIO16 | IO16 | RX2 |
| UART2 TX = GPIO17 | IO17 | TX2 |
| USB 5 V out | 5V | VIN |
| Ground | GND | GND |

☐ The module on the programmer says **ESP32-WROOM-32** (or WROOM-32D/E/U). If it says **WROVER**,
do not use Method B, because GPIO16/17 belong to its PSRAM. Method A is still fine.
☐ If the module says ESP32-S3 or ESP32-C3, stop. Those boards need a different procedure.

### 1c. USB driver and COM port

1. Plug only the programmer into the laptop (nothing else wired).
2. Run:
   ```powershell
   .\.venv\Scripts\pio.exe device list
   ```
3. Note the new `COMx` entry. Its description usually contains `CP210x` or `CH340`. If no new port
   appears, try another cable (it may be charge-only), then install the CP210x or CH340 driver.
4. **Record the programmer's own MAC address.** Hold the programmer's BOOT button, tap EN, release BOOT,
   then run (replace `COM5`):
   ```powershell
   .\.venv\Scripts\python.exe "$env:USERPROFILE\.platformio\packages\tool-esptoolpy\esptool.py" --chip esp32 --port COM5 --baud 115200 flash_id
   ```
   Copy the `MAC:` line to your notes as **PROGRAMMER MAC**. Later, any esptool output showing this
   MAC means you are talking to the programmer, not the camera. Never write camera firmware in that case.
5. Unplug the USB cable.

---

## 2. Choose a method

| | **Method A: hardware pass-through (recommended)** | Method B: software UART2 bridge |
|---|---|---|
| Idea | Hold the programmer's ESP32 in reset. Its onboard USB-serial chip then talks straight to the camera | Programmer runs `firmware/programmer`, which copies bytes between USB and UART2 |
| Firmware on programmer | None needed | Flash `firmware/programmer` first |
| TX/RX wiring | **Same names:** TX0→U0T, RX0→U0R | **Crossed:** TX2(GPIO17)→U0R, RX2(GPIO16)→U0T |
| Baud | Any (115200 is used, faster is possible) | Fixed 115200 only |
| Main risk | Forgetting the EN jumper, so two TX outputs fight | Bridge resets itself and esptool flashes the programmer (prevented by the MAC check) |

Method A works on almost every ESP32 dev board because the header pins TX0/RX0 are the same copper
traces as the USB-serial chip. Stage 4A proves that on your board with a loopback test.

Why the TX/RX names line up in Method A: with EN held low, the programmer's ESP32 stops driving its pins.
The pin labelled TX0 is then just the USB chip's *receive* input, and RX0 is the USB chip's *transmit*
output. So the camera's U0T (its transmit) must go to TX0, and U0R to RX0.

---

## 3. Wiring (USB unplugged during all wiring)

### Method A diagram

```text
 LAPTOP USB
     │
┌────┴─────────────── PROGRAMMER ESP32 ──────┐          ┌──────── CAMERA BOARD (ESP32-S + OV3660) ───────┐
│                                            │          │                                                │
│  EN ──────┐                                │          │                                                │
│           J1 (blue) holds ESP32 in reset   │          │                                                │
│  GND ─────┘                                │          │                                                │
│                                            │          │                                                │
│  TX0 / TX  (GPIO1) ────────── W1 yellow ───┼──────────┼──► U0T  (camera transmits → USB chip)          │
│  RX0 / RX  (GPIO3) ────────── W2 green ────┼──────────┼──► U0R  (USB chip transmits → camera)          │
│  GND ──────────────────────── W3 black ────┼──────────┼──► GND  (the GND beside 5V)                    │
│  5V / VIN  (USB 5 V) ──────── W4 red ──────┼──────────┼──► 5V                                          │
│                                            │          │                                                │
└────────────────────────────────────────────┘          │  IO0 ─────┐                                     │
                                                        │           J2 (white): fit to FLASH, remove to RUN│
                                                        │  GND ─────┘ (any verified GND pin)              │
                                                        │                                                │
                                                        │  NOT CONNECTED: 3V3, VCC, IO16, IO12, IO2,      │
                                                        │  IO4, IO13, IO14, IO15                          │
                                                        └────────────────────────────────────────────────┘
```

### Method B diagram (only if you choose the software bridge)

```text
PROGRAMMER (running firmware/programmer)                  CAMERA BOARD
  TX2 / IO17 (GPIO17) ─────── W1 yellow ───────────────►  U0R
  RX2 / IO16 (GPIO16) ─────── W2 green  ───────────────►  U0T
  GND ─────────────────────── W3 black  ───────────────►  GND (beside 5V)
  5V / VIN ────────────────── W4 red    ───────────────►  5V
  EN: NOTHING (no J1, the programmer must run)             IO0 ── J2 ── GND (flash only)
  TX0 / RX0: NOTHING (they are the USB link)
```

### Wire-by-wire table

| # | From (programmer) | To (camera) | Method A | Method B |
|---|---|---|---|---|
| J1 | EN | GND (programmer) | **fit** | **do not fit** |
| W1 | TX0 (A) / GPIO17 TX2 (B) | U0T (A) / U0R (B) | ✔ | ✔ |
| W2 | RX0 (A) / GPIO16 RX2 (B) | U0R (A) / U0T (B) | ✔ | ✔ |
| W3 | GND | GND next to 5V | ✔ | ✔ |
| W4 | 5V / VIN | 5V | ✔ (flashing only) | ✔ (flashing only) |
| J2 | camera IO0 | camera GND | fit to flash, remove to run | same |

Never connect: any programmer pin to camera **3V3** or **VCC** (VCC is an *output* on this layout);
two different 5 V sources to the camera at once; anything to camera IO12 (a boot-strap pin that sets
flash voltage).

### Micro-steps

1. Unplug the programmer's USB. Remove the microSD card from the camera board.
2. Check the camera ribbon: the latch is closed and the ribbon is fully inserted and straight. Remove
   the lens protective film if it is still there.
3. **Method A only:** fit J1 between programmer **EN** and programmer **GND**. Do this before W1/W2 so the
   programmer can never drive TX0 against the camera.
4. **Method B only:** first do stage 4B (flash the bridge, loopback test) with nothing else attached,
   then come back here.
5. Fit W3 (GND): programmer GND → camera GND beside 5V.
6. Fit W1 per the table for your method.
7. Fit W2 per the table for your method.
8. Fit W4 (5 V): programmer 5V/VIN → camera 5V.
9. Fit J2: camera IO0 → camera GND (the one you will verify in the next step).
10. Hold each Dupont housing and tug gently. Loose female sockets on header pins are the most common
    cause of failed syncs.

### Multimeter checks, power still OFF (continuity / beep mode)

| Probe A | Probe B | Expected |
|---|---|---|
| Camera GND (beside 5V) | Camera GND (beside IO0) | **Beep.** If silent, use the GND beside 5V for J2 |
| Camera 5V | Camera GND | **No beep** (a beep means a short, so stop) |
| Camera 5V | Camera 3V3 | No beep |
| Programmer GND | Camera GND | Beep (W3 good) |
| Programmer 5V/VIN | Camera 5V | Beep (W4 good) |
| Camera IO0 | Camera GND | Beep while J2 is fitted |
| Programmer EN | Programmer GND | Beep (Method A only, J1 fitted) |
| Programmer TX0 / GPIO17 | Camera U0T / U0R as wired | Beep on the intended pair only |

---

## 4. Prove the serial link before connecting the camera's TX/RX

Do this once. It catches wrong pins, a dead cable and a wrong driver before the camera is involved.

### 4A. Method A loopback

1. Disconnect W1, W2 and W4 from the camera (leave them hanging). Keep J1 fitted.
2. Join the free ends of W1 and W2 together, using a short male-male pin or by touching them (so TX0 is
   connected to RX0 on the programmer).
3. Plug in USB. The programmer's power LED lights. Nothing else should happen.
4. Measure programmer **EN to GND: 0.0 V** (the ESP32 is held in reset). Measure **5V/VIN to GND: 4.6–5.2 V**
   and write it down.
5. Open a monitor:
   ```powershell
   .\.venv\Scripts\pio.exe device monitor -p COM5 -b 115200 --dtr 0 --rts 0
   ```
6. Type `hello` (no local echo is enabled). **Expected: `hello` appears.** That proves USB chip → RX0 →
   your wires → TX0 → USB chip.
7. Remove the W1–W2 link and type again. **Expected: nothing appears.**
8. Optional: briefly unplug J1. The programmer boots and prints `rst:0x1 ... boot:0x13`. Seeing this
   proves the monitor works and that J1 really was holding reset. **Refit J1.**
9. `Ctrl+C` to close the monitor. Unplug USB. Reconnect W1→U0T, W2→U0R, W4→5V on the camera.

### 4B. Method B: flash the bridge, then loopback

1. With **nothing** connected to the programmer except USB, flash the bridge:
   ```powershell
   .\.venv\Scripts\pio.exe run -d firmware\programmer -t upload --upload-port COM5
   ```
   Expected ending: `Hash of data verified.` and `[SUCCESS]`.
2. Put a jumper between programmer GPIO16 (RX2) and GPIO17 (TX2).
3. Open the monitor (`--dtr 0 --rts 0` keeps the bridge from resetting), then type `hello`:
   ```powershell
   .\.venv\Scripts\pio.exe device monitor -p COM5 -b 115200 --dtr 0 --rts 0
   ```
   **Expected: `hello` appears**, and nothing appears once the jumper is removed.
4. Close the monitor and unplug USB. Wire the camera per the Method B table.

---

## 5. Enter the camera's download mode and confirm it

1. J2 fitted (camera IO0 to GND). USB unplugged.
2. Open the monitor first, then plug in USB (power-on is a reset). If you plug in first, press the
   camera's **RST** button afterwards.
   ```powershell
   .\.venv\Scripts\pio.exe device monitor -p COM5 -b 115200 --dtr 0 --rts 0
   ```
3. **Expected text from the camera:**
   ```text
   rst:0x1 (POWERON_RESET),boot:0x3 (DOWNLOAD_BOOT(UART0/UART1/SDIO_REI_REO_V2))
   waiting for download
   ```
   * `boot:0x13 (SPI_FAST_FLASH_BOOT)` → IO0 was not low. Check J2 and its GND pin.
   * Garbage characters → wrong baud or a loose W1/W3.
   * Nothing at all → the camera has no power (measure 5V pin) or W1 is on the wrong pin
     (Method A: TX0→U0T; Method B: GPIO16→U0T).
4. Measure at the camera: **5V to GND ≥ 4.6 V** and **3V3 to GND 3.2–3.4 V** (the on-board regulator).
5. Close the monitor with `Ctrl+C`. The COM port must be free for esptool.

---

## 6. Read-only probe of the camera chip (writes nothing)

1. Press camera **RST** (J2 still fitted). Do this before **every** esptool or upload command.
2. Run:
   ```powershell
   .\.venv\Scripts\python.exe "$env:USERPROFILE\.platformio\packages\tool-esptoolpy\esptool.py" --chip esp32 --port COM5 --baud 115200 --before no_reset --after no_reset flash_id
   ```
3. Check and record:

| Output line | Expected for an ESP32-CAM-layout board | If different |
|---|---|---|
| `Chip is ESP32-D0WD…` or `ESP32-D0WDQ6` | ESP32 (original family) | S2/S3/C3 → stop; wrong guide |
| `MAC: …` | **Must differ from PROGRAMMER MAC** | Same MAC → you are talking to the programmer. Stop and check J1 (A) or the bridge (B) |
| `Detected flash size: 4MB` | 4MB | Record the real size; `board_upload.flash_size` must match it |
| `Features:` | WiFi, BT, Dual Core | Record |

Record the camera MAC as **CAMERA MAC**.

---

## 7. Flash and run the identification firmware (drives no camera pins)

1. Press camera RST (J2 fitted).
2. Upload:
   ```powershell
   .\.venv\Scripts\pio.exe run -d firmware\camera -e esp32cam-identify -t upload --upload-port COM5
   ```
   Expected: `Chip is ESP32…`, a `MAC:` equal to CAMERA MAC, `Writing at 0x00010000…`,
   `Hash of data verified.` (four times), `[SUCCESS]`. The environment uses 115200 baud with
   `--before no_reset --after no_reset`. This was verified from the generated command.
3. **Remove J2**, open the monitor, and press camera RST.
4. Expected, every 2 seconds:
   ```text
   IDENTIFY chip=ESP32-D0WDQ6 rev=1 cores=2 cpu_mhz=240 flash_bytes=4194304 psram_bytes=41xxxxx heap_free=… mac=…
   ```
   (Illustrative: your chip revision and exact byte counts will differ.)
   * `psram_bytes` around 4 000 000 → PSRAM present (needed for high-resolution OV3660 frames).
   * `psram_bytes=0` → no working PSRAM. Mode B and QXGA evidence will be refused; record this.
   * Repeating `rst:` / `Brownout detector was triggered` → power sag. Shorten W4/W3 or go to the
     stage 8 power arrangement.
5. Record chip, revision, flash and PSRAM in `docs/validation.md` → "Identify hardware".

---

## 8. Prove the camera pin map electrically (OV3660 ID + one JPEG)

The `esp32cam-pinmap-probe` environment tries the published AI-Thinker layout
(`firmware/camera/include/candidates/ai_thinker_layout.h`) once. It accepts the map only if the sensor
answers with product ID `0x3660` **and** a real JPEG frame arrives. It never starts Wi-Fi.

1. Fit J2, press RST, upload:
   ```powershell
   .\.venv\Scripts\pio.exe run -d firmware\camera -e esp32cam-pinmap-probe -t upload --upload-port COM5
   ```
2. **Remove J2.** This matters because GPIO0 is the camera clock (XCLK) on this layout. Open the monitor
   and press RST.
3. Expected (repeats every 2 s):
   ```text
   PROBE_PASS pid=0x3660 frame=320x240 bytes=… jpeg_soi=ok psram_bytes=…
   ```

| Result | Meaning | Action |
|---|---|---|
| `PROBE_PASS pid=0x3660` | SCCB, clock, data, sync pins and PSRAM all proven | Go to stage 9 |
| `PROBE_FAIL esp_camera_init error …` | No sensor answered | Re-seat ribbon (latch open, insert, close), check J2 removed, retry. Still failing → layout differs, so stop |
| `PROBE_FAIL sensor PID 0x2642` (or other) | A different sensor (0x2642 = OV2640) | Your module is not OV3660. Report it; the project requirement assumes OV3660 |
| `PROBE_FAIL … no frame arrived` | Sensor talks but data pins differ | Layout differs. Stop; do not edit pins by guessing |
| `PROBE_FAIL no PSRAM detected` | Not the expected module or PSRAM fault | Record; stop |

Only after `PROBE_PASS`, create the confirmed board file (it is ignored by git):

```powershell
@'
#pragma once
// Confirmed on <date> by esp32cam-pinmap-probe: PROBE_PASS pid=0x3660, CAMERA MAC <xx:xx:...>
#define BOARD_CONFIRMED 1
#include "candidates/ai_thinker_layout.h"
'@ | Set-Content -Encoding utf8 firmware\camera\include\board_config.h
```

---

## 9. Flash the real camera server

1. Create `firmware\camera\include\secrets.h` from `firmware\common\Secrets.example.h`. Set
   `WIFI_SSID`, `WIFI_PASSWORD` (2.4 GHz network; the ESP32 has no 5 GHz) and a long random
   `CAMERA_KEY`. The same key goes into `.env` as `CAMERA_KEY`, and the camera URL into `CAMERA_URL`.
2. **Change power before Wi-Fi runs.** USB 5 V through jumper wires often browns out on Wi-Fi peaks.
   1. Unplug USB.
   2. Remove W4 (programmer 5V → camera 5V).
   3. Connect the 5 V ≥ 2 A supply: **+ → camera 5V**, **– → camera GND**. Keep W3 (common ground for
      the serial link), W1 and W2.
   4. Never connect the external supply and W4 at the same time.
3. Fit J2, power the camera supply, plug USB into the programmer, press camera RST, then upload:
   ```powershell
   .\.venv\Scripts\pio.exe run -d firmware\camera -e esp32cam -t upload --upload-port COM5
   ```
4. Remove J2, open the monitor, press RST. Expected:
   ```text
   WIFI_CONNECTED ip=192.168.x.y rssi=-5x ov3660=ok
   ```
   `ov3660=FAILED` means `board_config.h` is missing or wrong; go back to stage 8.
5. From the laptop (same LAN):
   ```powershell
   curl.exe -s -H "X-Camera-Key: YOUR_KEY" http://192.168.x.y/health
   curl.exe -s -H "X-Camera-Key: YOUR_KEY" -o reports\first-frame.jpg -D - http://192.168.x.y/jpeg
   ```
   Expected `/health`: `{"sensor":"OV3660","healthy":true,"psram_bytes":…,"high":false}`.
   `/jpeg` returns headers `X-Frame-Sequence`, `X-Boot-Id`, `X-Capture-Us`, `X-Width: 800`,
   `X-Height: 600`. Open `first-frame.jpg` and confirm it shows the scene. A request without the key
   must return `401`.
6. Measure capture behaviour (this is the first real data for the requirements):
   ```powershell
   .\.venv\Scripts\python.exe -m vision.benchmark http://192.168.x.y --key YOUR_KEY --mode A --output reports\capture-a.json
   .\.venv\Scripts\python.exe -m vision.benchmark http://192.168.x.y --key YOUR_KEY --mode B --output reports\capture-b.json
   ```

---

## 10. Free the programmer for its next job (shelf sensor controller)

1. Unplug USB and the camera supply.
2. Remove W1, W2, W3, W4, J1 and J2. The camera now runs alone from its 5 V supply. Re-flashing it later
   repeats stages 3–9 (rewire, J2, RST, upload).
3. Continue with [sensor-controller-bringup.md](sensor-controller-bringup.md). The programmer is flashed
   through its own USB from now on. It cannot be the bridge and the shelf controller at the same time.

---

## Troubleshooting

| Symptom | Likely cause | Check / fix |
|---|---|---|
| `A fatal error occurred: Failed to connect to ESP32: No serial data received` | Camera not in download mode, or TX/RX wrong | Stage 5 text must say `DOWNLOAD_BOOT`. Swap W1/W2 only if the stage 4 loopback passed and stage 5 shows nothing |
| `Wrong boot mode detected (0x13)` | IO0 not low at reset | J2 fitted? Use the GND beside 5V. Press RST after fitting J2 |
| esptool shows PROGRAMMER MAC | Talking to the programmer | Method A: J1 missing or on the wrong pin (EN must read 0 V). Method B: bridge not running, so reflash it |
| `Invalid head of packet` / timeouts mid-write | Loose Dupont, long wires, power sag | Shorter wires, re-seat, stay at 115200 |
| Upload OK, monitor shows nothing after RST | J2 still fitted, or wrong monitor baud | Remove J2, 115200, press RST |
| `Brownout detector was triggered` loops | Supply cannot cover Wi-Fi/camera peaks | Stage 9 step 2 external 5 V ≥ 2 A; thick, short leads |
| `PROBE_FAIL esp_camera_init error` | Ribbon not seated or J2 still on GPIO0 | Re-seat ribbon, remove J2 |
| Camera board gets hot | Short or reversed supply | Unplug immediately; repeat the stage 3 meter checks |
| No COM port | Charge-only cable or missing driver | Different cable; CP210x/CH340 driver |
| COM port "access denied" | Monitor still open elsewhere | Close every serial monitor, Arduino IDE and other tools using the port |

## Engineering notes

* The programmer's auto-reset transistors act only on the **programmer's** EN/IO0, never the camera's.
  That is why the camera environments use `--before no_reset --after no_reset` and you press RST by hand.
* Method B's bridge (`firmware/programmer`) forwards bytes at a fixed 115200 baud. It does not forward
  DTR/RTS and does not follow esptool baud changes. `monitor_dtr = 0` / `monitor_rts = 0` in the
  PlatformIO files stop the monitor from resetting the bridge.
* Logic levels: the programmer and the camera are both 3.3 V ESP32 parts, so no level shifter is needed
  on W1/W2. The 5 V on W4 goes only to the camera's 5V input pin (into its on-board 3.3 V regulator).
* GPIO16 on the camera is used by its PSRAM; GPIO0 is XCLK; GPIO4 is the flash LED; GPIO2/12/13/14/15
  are the microSD bus and GPIO12 is a boot-strap pin. None of them may be used for external wiring.
* The camera pin map comes from the AI-Thinker reference layout and counts as confirmed **only** after
  `PROBE_PASS` on this physical board. Until then, `board_config.h` must not exist.

Primary references: [Espressif DevKitC guide](https://documentation.espressif.com/esp-dev-kits/en/latest/esp32/esp32-devkitc/user_guide.html),
[esptool v4 reset modes](https://docs.espressif.com/projects/esptool/en/release-v4/esp32/esptool/advanced-options.html),
[ESP32 boot-mode selection](https://docs.espressif.com/projects/esptool/en/latest/esp32/advanced-topics/boot-mode-selection.html),
[esp32-camera driver and board pin tables](https://github.com/espressif/esp32-camera).


## Cached camera preview

The normal camera mode is now QVGA (320x240); the optional high-resolution
mode remains QXGA. `frame_cache.cpp` owns all runtime framebuffer reads and
copies the latest JPEG into PSRAM, at most ten times per second. `/jpeg` and
MJPEG consumers share immutable snapshots. A slow network send no longer
holds a driver framebuffer, and consumers skip superseded frames rather than
building an application-level queue. Captures older than two seconds are not
served as fresh images. TCP buffers can still add latency on a slow connection.

`/health` reports `cached_frame_ready` and, when fresh, its sequence, age and
size. After flashing, check that the sequence advances, dimensions are 320x240,
and the local preview receives new frames over a sustained interval. The
capture limit is not a measured delivered frame rate. This change needs a
physical upload before performance can be evaluated.


### Runtime comparison build

Authenticated `POST /tune?clock_mhz=16&quality=20&fps=10` can compare 8/16/20 MHz
OV3660 clock settings, JPEG quality 10..30, and capture limits 5..25 FPS without
reflashing each profile. Settings are RAM-only and reset to 8 MHz / quality 12 /
20 FPS and 320x240 at reboot. Changing the clock reapplies the current sensor frame size
so the driver recalculates its PLL; capture and tuning share a mutex. `/health`
reports the selected settings. Invalid or unauthenticated tuning is rejected.

The MJPEG writer uses nonblocking socket sends with one 500 ms deadline across
the part header, JPEG and separator. A partial/expired write closes the stream
so the local preview reconnects. This bounds time spent enqueueing each frame;
it does not bound radio retries, TCP delivery, or browser display time.

With the local preview running, execute:

```powershell
.venv\Scripts\python.exe -m tools.camera_benchmark --camera http://CAMERA_IP
```

The tool compares six clock/quality/rate profiles and repeats the baseline last,
saving records under ignored `data/hardware-backups/`. Each profile has a 30 s
receive window including outages. Capture-to-receiver age estimates include a
clock-offset estimate and reported uncertainty; they exclude browser rendering.
Keep the camera view, lighting and hardware setup fixed during comparisons.


For the experimental 8 MHz comparison, hold JPEG quality and capture limit
constant and repeat the 20 MHz baseline afterward:

```powershell
.venv\Scripts\python.exe -m tools.camera_benchmark --camera http://CAMERA_IP --clocks 20 8 20 --quality 12 --fps-limit 10 --duration 30 --output data/hardware-backups/camera-benchmark-8mhz.json
```

The 8 MHz camera-clock workaround comes from reports on other ESP32 camera
setups, primarily OV2640. On OV3660 this is an experiment, not a validated FPS
improvement; the driver reapplies its own sensor PLL profile after changing
XCLK. A lower clock may reduce capture FPS even if network delay improves.


### Resolution comparison

The resolution-test build boots at the measured 8 MHz clock and a 20 FPS capture
limit. On a PSRAM board it initializes the driver with QXGA-sized JPEG buffers,
then lowers the sensor to 320x240 before starting capture. This reserves room
for larger runtime frames; changing a sensor size cannot resize existing driver
buffers. Boards without PSRAM stay at QVGA.

`POST /tune` also accepts optional `resolution=320x240`, `480x320`, `640x480`, or
`800x600`; the clock, quality and FPS arguments are still required. Larger modes
require detected PSRAM. The resolution is changed under the same capture mutex.

Compare sizes while keeping clock and quality fixed:

```powershell
.venv\Scripts\python.exe -m tools.camera_benchmark --camera http://CAMERA_IP --clocks 8 --resolutions 320x240 480x320 640x480 320x240 --quality 12 --fps-limit 20 --duration 30 --output data/hardware-backups/camera-resolution-comparison.json
```

The tool excludes in-flight frames with the previous dimensions and saves a
JPEG for each profile next to the report. Check actual received dimensions,
image detail, FPS, frame-age uncertainty and long gaps before choosing a size.


## Multiple camera Wi-Fi networks

In the ignored `firmware/camera/include/secrets.h`, keep `WIFI_SSID` and
`WIFI_PASSWORD` for the primary network. Add `WIFI_SSID_2` / `WIFI_PASSWORD_2`
and optionally `WIFI_SSID_3` / `WIFI_PASSWORD_3` for fallback networks.
Empty SSIDs are skipped. Use 2.4 GHz networks.

The camera tries each configured entry for 15 seconds without blocking the video
loop. It stays on a connected network and cycles entries after disconnection.
It does not automatically switch back while the current connection works.
Rebuild and upload camera firmware after changing credentials. Serial output
shows the selected SSID and new IP address, never the password. Switching
networks interrupts the feed; connect the laptop to the same network and point
the local preview at the camera's new address.
