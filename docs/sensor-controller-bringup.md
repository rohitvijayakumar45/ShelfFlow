# Shelf sensor controller: tripod box (3 load cells), RC522, 16x2 LCD and service buttons

Status: **firmware compiles; payloads validated against the backend schema; calibration and position maths
checked numerically. Not yet wired or run on the physical shelf.** The controller is the existing ordinary ESP32 (WROOM-32,
30-pin DEVKIT V1); a **new, separate 38-pin ESP32 now programs the camera**. Remove all old camera programming wires
and the EN jumper from this sensor controller. See [current board roles and connections](dual-rc522-and-38pin-programmer.md). If your module is
WROVER, S2, S3 or C3, stop: GPIO16/17 and other pins below differ.

The box (tray) rests on **three load cells in a tripod**: A bottom-left, B bottom-right, C top-middle. Each cell has its
own HX711. The total weight is the sum of the three. How a change splits across the cells gives the **position**
on the box where an item was taken or placed. A 16x2 LCD shows live readings, and two **staff-only** buttons inside the
enclosure page the display and run on-shelf calibration. They are never customer controls.

## Parts

| Qty | Part | Required specification |
|---:|---|---|
| 1 | ESP32-WROOM-32 dev board | 30-pin DEVKIT V1 labels used below |
| 3 | HX711 modules | Powered from **3.3 V** so DT never exceeds 3.3 V |
| 3 | Four-wire full-bridge bar load cells | **Each** rated for the full loaded box plus margin (an item near one corner can load one cell heavily) |
| 2 | RC522 modules | 3.3 V only; separate R1/R2 chip selects |
| 1 | 16x2 LCD with PCF8574 I2C backpack | Address 0x27 or 0x3F (auto-detected) |
| 1 | **Bi-directional I2C level shifter** (BSS138, 4-channel) | Needed when the LCD backpack runs at 5 V (most do). Skip only for a true 3.3 V LCD |
| 2 | Momentary push buttons | Normally open; mounted **inside** the enclosure |
| 1 | 100–470 µF electrolytic capacitor | Across ESP 3V3–GND; stops Wi-Fi brownouts |
| 1 | Stiff box base | 6 mm or thicker plywood or acrylic, so weight transfers predictably to the three cells |

## Master pin table

| Device | Pin | ESP32 (30-pin label) | GPIO | Note |
|---|---|---|---:|---|
| HX711 **A** (bottom-left) | VCC / GND | 3V3 / GND | – | |
| | DT | **D32** | 32 | |
| | SCK | **D33** | 33 | |
| HX711 **B** (bottom-right) | VCC / GND | 3V3 / GND | – | |
| | DT | **D25** | 25 | |
| | SCK | **D26** | 26 | |
| HX711 **C** (top-middle) | VCC / GND | 3V3 / GND | – | |
| | DT | **D34** | 34 | input-only pin: fine for DT, never SCK |
| | SCK | **D27** | 27 | |
| RC522 R1 and R2 | 3.3V / GND | 3V3 / GND | – | Shared power/ground |
| R1 | SDA (chip select) | **D21** | 21 | R1 only |
| R2 | SDA (chip select) | **D4** | 4 | R2 only |
| | SCK / MOSI / MISO | **D18 / D23 / D19** | 18 / 23 / 19 | |
| | RST | **D22** | 22 | IRQ not connected |
| LCD (through level shifter) | SDA | **RX2** | 16 | **not** 21/22 (RC522 uses them) |
| | SCL | **TX2** | 17 | |
| Button **PAGE** | one leg | **D13** | 13 | other leg to GND (internal pull-up) |
| Button **ACTION** | one leg | **D14** | 14 | other leg to GND |

None of these is a boot-strap pin (0, 2, 5, 12, 15) or a flash pin (6–11). GPIO14 briefly toggles at boot, which is
harmless for a button input.

## LCD wiring through the level shifter

```text
ESP32 3V3 ── LV        level shifter        HV ── ESP32 VIN (USB 5 V)
ESP32 GND ── GND  ──────────────────────── GND ── LCD GND
ESP32 D16 ── LV1                           HV1 ── LCD SDA
ESP32 D17 ── LV2                           HV2 ── LCD SCL
                                    LCD VCC ── ESP32 VIN (5 V)
```
- The LCD backpack runs at 5 V and pulls SDA/SCL up to 5 V. **5 V must never reach an ESP32 pin**, so the shifter
  sits in between.
- **True 3.3 V LCD:** wire SDA→D16, SCL→D17 and VCC→3V3 directly, with no shifter.
- Contrast: turn the small blue trimmer on the backpack until the characters appear.

## Wiring diagram (one box)

```text
                       ESP32 DEVKIT V1 (sensor; USB to laptop)
   3V3  GND  D32 D33  D25 D26  D34 D27  D21 D18 D23 D19 D22  D16 D17  D13 D14  VIN
    │    │    │   │    │   │    │   │    └── RC522 ──────┘   │   │    │   │    │
    │    │    HX711 A  HX711 B  HX711 C                   level shifter  │   │   LCD 5V
    │    │    E± A± ── cell A   cell B   cell C           ── LCD SDA/SCL │   │
    └────┴── 3V3 / GND to every HX711 + RC522 + shifter LV          PAGE  ACTION (to GND)
```

## Mechanical (tripod)
- Spread A, B and C as wide as the box allows. Items must sit **inside the triangle**.
- Each cell is a cantilever: the fixed end is bolted to a block on the base with nothing under the middle, and the
  free end carries the box through a small spacer. All three flex **downward**.
- Shim the spacers so the box sits level and **all three cells read a load when the box is empty**.
- Use an overload stop screw about 1 mm under each corner.
- Measure where each cell's free-end support point is, in mm from one corner of the box. These numbers become the
  tray **geometry** (see Step 6).

## Step 0: prepare and flash (no modules attached)
1. Remove the programmer wires and the EN jumper.
2. Create `firmware\shelf-controller\include\secrets.h` from `firmware\common\Secrets.example.h`. Wi-Fi is optional for
   bench bring-up.
3. Flash the firmware and initialise the event-queue filesystem once:
   ```powershell
   .\.venv\Scripts\pio.exe run -d firmware\shelf-controller -e devkitc-v4-proposed -t upload --upload-port COM3
   .\.venv\Scripts\pio.exe run -d firmware\shelf-controller -e devkitc-v4-proposed -t uploadfs --upload-port COM3
   ```
   If the board reboots with `Brownout detector was triggered`, fit the capacitor first. For bench checks only, use
   the `devkitc-v4-offline` build, which runs with Wi-Fi off.
4. Monitor at 115200. Each second it prints:
   `DIAG raw A=… B=… C=… total=UNCAL noise=… flags=DISCONNECTED_A,… rc522=0x.. lcd=none svc=0 wifi=… mqtt=… fs=ok`

## Step 1: RC522
Wire both readers as in the table: SCK/MOSI/MISO/RST are shared, SS is GPIO21 for R1 and GPIO4 for R2.
Test R1 first, then add R2 with power off. Valid version values vary; `0x00` or `0xFF` means check wiring.
A tag prints `DIAG RFID reader=R1 uid=…` or `reader=R2`. Firmware alternates the antenna fields.
Measure each reading zone along the natural wrist path.

## Step 2: identify each load cell's wires
Measure resistance across all six wire pairs. The excitation pair (E+/E−) and the signal pair (A+/A−) are the two
pairs that are *not* among the four equal "mixed" readings. Label the wires. Colours are not universal.

## Step 3: HX711 A, then B, then C (one at a time, power off while wiring)
1. Wire VCC→3V3, GND→GND, DT, SCK as in the table. Connect the cell: E+→E+, E−→E−, A+→A+, A−→A−. Leave B± empty.
2. Meter with power off: HX711 VCC–GND must not beep.
3. Power on: VCC reads 3.2–3.4 V. In `DIAG`, that cell's `raw` value moves when you press above it, and its
   `DISCONNECTED_x` flag disappears.
4. Place the known mass near A, then B, then C: the load split should change with position. A rigid tripod tray
   normally loads several cells together. If a raw reading goes **down** under load, calibration can take the
   polarity into account. Ensure the tray does not touch any support outside its three intended load paths.

## Step 4: LCD and buttons
1. Wire the level shifter and LCD as above, then the two buttons to D13/D14 and GND.
2. On power-up the LCD shows `ShelfFlow sensor`, and `DIAG` shows `lcd=ok`. `lcd=none` means: check the shifter, the
   5 V supply, the contrast trimmer, and the address (0x27 or 0x3F).
3. **PAGE** (short press) cycles six pages:

| Page | Line 1 | Line 2 |
|---|---|---|
| 1 | `tray-a 1234.5g` (or `UNCALIB`) | `STABLE +-0.6` / `SETTLING` / fault flags |
| 2 | grams on A and B | grams on C |
| 3 | raw A and B (thousands) | raw C |
| 4 | IP address or `WiFi down` | MQTT state, `NOFS` if the queue filesystem is missing |
| 5 | last RFID tag | UID and seconds ago |
| 6 | calibration version | geometry version or `geo NOT SET` |

**ACTION** (short press) in normal mode only toggles the backlight. It changes nothing in the store.

## Step 5: on-shelf calibration (staff service mode)
Three cells need three scales. The firmware solves them exactly from three placements of one known mass.

1. Set the **actual measured known mass**. The default is 500 g; it can be changed from the backend with a `CONFIG`
   command. In `devkitc-v4-rfid-diagnostic` (USB bench build, Wi-Fi off), send `mass 70` with Newline for a 70 g mass,
   then `calstatus`. Wait for `CAL known mass saved: 70.000 g` and status `mass_g=70.000` before capturing readings.
   This persists the reference mass, not the calibration itself. `mass` is refused after a calibration capture
   sequence has begun; press PAGE to cancel back to service home before changing it.
2. **Enter service mode:** hold **PAGE + ACTION together for 3 s**. The LCD shows `SERVICE ACT:cal`. While in service
   mode no weight events are produced, and HEALTH reports `SERVICE_MODE`.
3. Press **ACTION**: `EMPTY BOX ACT`. Empty the box, then short-press **ACTION** once and release it. The LCD shows
   `WAIT HANDS OFF`; capture happens only after both buttons are released and the sensors stay healthy and still
   for one second. This avoids treating the button press itself as tray motion. Wait for `MASS ON A ACT` before
   placing the mass. A request times out after 20 seconds without a stable window; press ACTION to retry.
4. `MASS ON A ACT`: put the mass **near cell A**, short-press ACTION once, release it and remove your hand.
   Leave the mass there until the display advances to B. Repeat for **B** and **C**, moving the same mass each time.
   Exact positions don't matter, but the three must be clearly different. If they're too similar the LCD shows
   `FAIL SPREAD MASSES` and starts again at zero.
5. `VERIFY HOLD ACT`: the LCD shows the live total with the new calibration. Put the mass **anywhere**, for example the
   centre. It should read close to the known mass at every position. Leave the mass on the tray, wait for still
   readings, and **hold ACTION for 1.5 s**, then release it to request saving. Keep the mass still while the
   controller waits for one second of stable hands-off readings. Save is refused if readings are unhealthy/unstable or the
   verification total differs by more than max(2 g, 3%) from the known mass. Check the empty reading and other
   masses separately afterward; this save guard is not a measured accuracy specification.
6. The LCD shows `SAVED` and the new version (`dev-<boot>-<n>`). The firmware stores it and sends a **CALIBRATION**
   event. The backend records it in the audit trail and switches the tray to that version, so events on the old
   version are refused.
7. **Exit:** hold PAGE + ACTION for 3 s again, or wait 2 minutes without touching a button. If the box contents differ
   from before service mode, that change is still reported, flagged `AFTER_SERVICE`, and goes to staff review. It is
   never zeroed away.

PAGE (short press) during service mode cancels back to `SERVICE ACT:cal`. Alternatively, staff can send per-cell
calibration from the backend: `POST /staff/trays/tray-a/calibration` with `cells: [{name, zero_raw, counts_per_g}]`.

## Step 6: geometry (enables the position signal)
Measure each cell's support point in mm, in one frame for the whole box (origin at a box corner), then send:
```text
POST /staff/trays/tray-a/geometry
{"version": "g1", "reason": "measured supports",
 "cells": [{"name":"A","x_mm":20,"y_mm":20}, {"name":"B","x_mm":230,"y_mm":20}, {"name":"C","x_mm":125,"y_mm":180}]}
```
The backend refuses support points that lie nearly in a line. From then on every settled weight change carries
`position_mm`, the place on the box where the change happened.
- **Loading:** each item's position is stored with it.
- **Pickup:** the system checks that the matched item was at the place the weight left from. A mismatch raises
  `POSITION_CONFLICT`. Two items of almost equal weight are told apart by position.

Until geometry is set, no position is reported. The LCD's last page shows `geo NOT SET`.

**Check:** with the mass at a known spot, the reported position should land within about 1–2 cm. The backend's
starting tolerance is 30 mm; tighten it once measured.

## Power
- Laptop USB powers the ESP. Its 3V3 pin feeds the three HX711s, the RC522 and the shifter's LV side (roughly 40 mA).
  VIN (USB 5 V) feeds the LCD and the shifter's HV side.
- One shared GND for everything. Use a short strip next to the ESP rather than the long breadboard rails.
- The camera has its own 5 V supply and shares no wires with this controller.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `DISCONNECTED_x` | That HX711's DT/SCK swapped or on the wrong GPIO, or it has no power |
| `SATURATED_x` / raw pinned near ±8388607 | Cell miswired (E/A pairs mixed up) or overloaded |
| One cell changes when pressing above another | Box touching something, cell mount binding, base not stiff |
| Calibration `FAIL SPREAD MASSES` | The three placements were too close together; move the mass right next to each cell |
| Verify total varies with position | Base flexing, cells not all loaded at rest (re-shim), or an item outside the triangle |
| `lcd=none` | Level shifter LV/HV swapped, LCD not on 5 V, contrast trimmer, I2C address |
| Brownout reboots | Capacitor on 3V3, direct laptop USB port, short cable |


## Brief RFID taps and queue capacity (2026-10-16)

The shelf controller uses a dedicated RF polling task. That task alone owns both
RC522s after initialization, alternates their antennas, and pushes observations
into a 64-entry FreeRTOS queue without waiting for filesystem or MQTT operations.
The main loop persists up to four observations per pass, using the original
capture time. `captured_ms` and `dispatch_ms` distinguish capture from serial
notification. A long network reconnect can still delay persistence; RAM is not
power-loss durable. Flash operations may also briefly pause task execution.

Each reader reports a tag immediately on first observation. A held tag produces
one presentation event; the same tag rearms after 600 ms without successful
observation. A different UID and the same UID at the other reader are independent.
There is no one-second reporting cooldown. This release debounce is not a promise
that arbitrarily brief fly-by movements will be captured: verify realistic taps
on the physical antennas. An RF dropout longer than 600 ms can also rearm a held tag.

The durable event queue holds up to 256 files, with a free-space guard; it does
not overwrite unacknowledged events. Capacity is event count, not shopping-item
count. A seven-item session with 14 RFID presentations and weight-change events
fits comfortably when space is available. Queue depth is counted at boot and
maintained on successful writes/ACKs instead of scanning every file on each tap.
A separate memory buffer keeps up to 64 pending RFID presentation events.

Diagnostics show `durable`, `ram`, `scan_gap_max_ms`, and historical overflow.
Cached reader version bytes in DIAG are startup values, not continuous bus tests.
Overflow/write failure remains explicit; old overflow history is not silently
reset. A full-flash backup before this update preserves calibration, queue and
previous firmware locally in `data/hardware-backups/`. Ordinary application
uploads do not format LittleFS or erase NVS. No filesystem upload is needed.

Native policy regression test (MSYS2 g++):

```powershell
g++ -std=c++11 -Wall -Wextra -Werror firmware/shelf-controller/tests/rfid_tap_test.cpp -o data/hardware-backups/rfid-tap-test.exe
.\data\hardware-backups\rfid-tap-test.exe
```

Hardware acceptance: tap R1 seven times, then R2 seven times, moving the tag
fully away for at least one second between taps. Compare all 14 physical
presentations with captured events and inspect scan gaps, dispatch delay, queue
growth and overflow. Test a held tag separately: it should not repeatedly fill
the queue. Retest during sensor changes and, before deployment, network outages.


The offline RFID diagnostic build accepts USB command `bencharchive` after a
verified full-flash host backup. It moves retired bench-test files into
`/bench-archive/` on flash and records the previous overflow count before starting
a fresh active queue. It never formats storage or changes the `box` calibration
namespace. Partial failures leave remaining active files and failure history
visible. This command is absent from online shopping builds and must not be used
to conceal lost customer transactions.


Queue filenames now omit the device prefix: `<boot>-<sequence>.json` is at most
32 characters, including a 16-character boot ID and a 10-digit sequence. The
complete globally unique event ID remains in JSON. Durable ACKs accept both the
old full filenames and the new compact filenames. This avoids rename failures
when a filesystem formatted with a 32-character name limit reaches longer
sequence numbers. Writes serialize to a single buffer and check byte count.

Offline USB command `queuetest` writes and reads back 14 explicitly marked bench
HEALTH probes, including sequences across 99/100. It is not a physical RFID test
or a customer transaction. Archive probes after verifying reboot persistence.
