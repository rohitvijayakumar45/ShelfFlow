# Shelf sensor controller: step-by-step connections (RC522 + two HX711 trays)

Status: **pin map and firmware compile-verified; not yet wired or powered.** The controller is the same
ordinary ESP32 board that programmed the camera ([programming-camera.md](programming-camera.md)),
after all programming wires and J1 are removed. Proposed for an ESP32-WROOM-32 dev board (DevKitC V4 or
30-pin DEVKIT V1). If your module is WROVER, S2, S3 or C3, stop and re-check every pin below.

Bring modules up **one at a time**: RC522 first, then tray A, then tray B. After each module, the serial
`DIAG` line proves it works before you add the next one.

## Parts

| Qty | Part | Required specification |
|---:|---|---|
| 1 | ESP32-WROOM-32 dev board | The former programmer |
| 1 | RC522 module (8 pins: SDA SCK MOSI MISO IRQ GND RST 3.3V) | **3.3 V only** |
| 2 | HX711 modules (MCU side: GND DT SCK VCC; cell side: E+ E- A- A+ B- B+) | Powered from 3.3 V so DT never exceeds 3.3 V |
| 2 | Four-wire full-bridge load cells (bar type) | Rated above tray + full stock mass, with margin |
| ~14 | Female-female Dupont wires | Plus screw terminals or solder for load-cell leads |
| 1 | Multimeter | Resistance, continuity, DC volts |
| 2 | Known reference masses | For example 200 g and 500 g, ideally a calibrated weight or a kitchen-scale-checked object |

## Master pin table

| Controller label (DevKitC / DEVKIT V1) | GPIO | Goes to | Wire colour (suggested) |
|---|---:|---|---|
| IO18 / D18 | 18 | RC522 **SCK** | orange |
| IO23 / D23 | 23 | RC522 **MOSI** | yellow |
| IO19 / D19 | 19 | RC522 **MISO** | green |
| IO21 / D21 | 21 | RC522 **SDA** (= SPI chip select, not I²C) | blue |
| IO22 / D22 | 22 | RC522 **RST** | purple |
| 3V3 | – | RC522 **3.3V** | red |
| GND | – | RC522 **GND** | black |
| – | – | RC522 **IRQ**: leave unconnected | – |
| IO32 / D32 | 32 | HX711 **A** DT | white |
| IO33 / D33 | 33 | HX711 **A** SCK | grey |
| IO25 / D25 | 25 | HX711 **B** DT | white (tag "B") |
| IO26 / D26 | 26 | HX711 **B** SCK | grey (tag "B") |
| 3V3 | – | HX711 A **VCC** and HX711 B **VCC** | red |
| GND | – | HX711 A **GND** and HX711 B **GND** | black |
| IO27 / D27 (optional) | 27 | LED anode through a resistor, cathode to GND | – (status LED not driven by current firmware) |

Pin safety check: none of 18, 19, 21, 22, 23, 25, 26, 27, 32, 33 is a boot-strap pin (0, 2, 5, 12, 15),
a flash pin (6–11) or an input-only pin (34–39). The 3V3 pin feeds three small modules, roughly
30 mA in total, well within the dev board regulator. **Never** power the RC522 or HX711 from 5V/VIN.

```text
                     ┌───────────── ESP32-WROOM-32 dev board (USB to laptop) ─────────────┐
                     │ 3V3   GND   18   23   19   21   22        32   33        25   26     │
                     └──┬─────┬────┬────┬────┬────┬────┬──────────┬────┬─────────┬────┬────┘
   3V3 rail ────────────┤     │    │    │    │    │    │          │    │         │    │
   GND rail ──────────────────┤    │    │    │    │    │          │    │         │    │
                        │     │    │    │    │    │    │          │    │         │    │
   RC522   3.3V ◄───────┤     │    │    │    │    │    │          │    │         │    │
           GND  ◄─────────────┤    │    │    │    │    │          │    │         │    │
           SCK  ◄──────────────────┘    │    │    │    │          │    │         │    │
           MOSI ◄───────────────────────┘    │    │    │          │    │         │    │
           MISO ────────────────────────────►┘    │    │          │    │         │    │
           SDA  ◄─────────────────────────────────┘    │          │    │         │    │
           RST  ◄──────────────────────────────────────┘          │    │         │    │
           IRQ   (not connected)                                  │    │         │    │
                                                                  │    │         │    │
   HX711 A VCC ◄── 3V3   GND ◄── GND   DT ───────────────────────►┘    │         │    │
                                       SCK ◄───────────────────────────┘         │    │
           E+ E- A- A+ ── load cell A (tray A)                                   │    │
                                                                                 │    │
   HX711 B VCC ◄── 3V3   GND ◄── GND   DT ──────────────────────────────────────►┘    │
                                       SCK ◄──────────────────────────────────────────┘
           E+ E- A- A+ ── load cell B (tray B)        (B- / B+ channel unused on both)
```

## Step 0: prepare the board (no modules attached)

1. Confirm the programmer wires and J1 (EN to GND) from camera programming are **removed**.
2. Create `firmware\shelf-controller\include\secrets.h` from `firmware\common\Secrets.example.h`.
   You can leave Wi-Fi/MQTT unset for bench bring-up: the `DIAG` output works offline.
3. Flash the firmware:
   ```powershell
   .\.venv\Scripts\pio.exe run -d firmware\shelf-controller -t upload --upload-port COM5
   ```
4. Initialize the event-queue filesystem **once** (the firmware never auto-formats queued evidence):
   ```powershell
   .\.venv\Scripts\pio.exe run -d firmware\shelf-controller -t uploadfs --upload-port COM5
   ```
5. Open the monitor:
   ```powershell
   .\.venv\Scripts\pio.exe device monitor -d firmware\shelf-controller -p COM5
   ```
   Expected once per second:
   `DIAG raw tray-a=0(NO_DATA) tray-b=0(NO_DATA) rc522_version=0x00 wifi=down mqtt=down fs=ok`
   * `fs=MISSING` → step 4 did not run.
   * `NO_DATA` and `0x00` are correct while nothing is attached.
6. Unplug USB.

## Step 1: RC522 (power off while wiring)

1. Read the RC522 silkscreen. Confirm the labels `SDA SCK MOSI MISO IRQ GND RST 3.3V` and that the
   power pin is **3.3V**, not 5V.
2. Wire, in this order: GND (black) → 3.3V (red) → SCK 18 → MOSI 23 → MISO 19 → SDA 21 → RST 22.
3. Meter, power off: RC522 3.3V to RC522 GND **must not beep**. Controller GND to RC522 GND **beeps**.
4. Plug in USB and measure RC522 3.3V to GND: **3.2–3.4 V**.
5. Monitor: `rc522_version=0x91` or `0x92` (genuine NXP) or `0x88` / `0x12` (common clones).
   * `0x00` or `0xFF` → no SPI communication. Recheck SDA=21, MISO/MOSI not swapped, 3.3V present.
6. Hold a bracelet tag flat against the antenna. Expected: `DIAG RFID uid=04A1B2C3…`. Write down each
   bracelet's UID; these become `Bracelet.rfid` values in the staff app.
7. **Measure the reading zone (required by the project).** Mark the antenna centre. With the bracelet on
   a wrist, record the maximum distance at which a UID still prints, both face-on and with the wrist
   rotated 45° and 90°. Typical RC522 range is only a few centimetres. Mount the antenna where the
   natural wrist path into the tray passes through that measured zone. One RC522 marks one local
   zone; it never covers the whole shelf.

## Step 2: identify each load cell's wires (cell disconnected)

Wire colours are **not universal**. Measure resistance between every pair of the four wires (six
measurements) and write them in a table.

| Pair type | Typical relation |
|---|---|
| Excitation pair (E+/E−) | The **input** resistance, usually the highest or equal-highest reading (for example about 1000–1100 Ω on a 1 kΩ cell) |
| Signal pair (A+/A−) | The **output** resistance, similar to or slightly below the input reading |
| Mixed pairs (one E, one A) | About ¾ of the bridge value; four equal readings |

1. The two pairs that are *not* the 4 equal mixed readings are excitation and signal. If the datasheet
   gives input and output resistance, match them. The common colour code (red E+, black E−, white A−,
   green A+) is only a starting guess to confirm with the meter and datasheet.
2. Polarity of E+/E−: either way works electrically. Polarity of A+/A−: if loading the cell gives a
   *decreasing* raw count, swap A+ and A− (or accept a negative calibration scale; both work with
   explicit calibration, but be consistent).
3. Label each wire with tape: `E+`, `E−`, `A+`, `A−`.

## Step 3: tray A (HX711 A + cell A)

1. Mechanical: bolt the load cell to the rigid base at its fixed end and the tray to its free end,
   following the arrow on the cell (force direction). Use spacers so the cell can flex. Leave a gap so
   the tray touches nothing else: not the shelf walls, not tray B, not cables.
2. Cell to HX711 A: `E+`→E+, `E−`→E−, `A+`→A+, `A−`→A−. Leave B+/B− empty. Twist the four wires
   together and keep them away from the Wi-Fi antenna and servo wiring.
3. HX711 A to controller (power off): GND (black) → VCC to 3V3 (red) → DT to 32 → SCK to 33.
4. Meter, power off: HX711 VCC to GND **no beep**. Controller GND to HX711 GND **beeps**.
5. Power on. Measure HX711 VCC to GND: **3.2–3.4 V**. Measure E+ to E−: a steady DC voltage (on
   3.3 V-supplied boards this is near the supply; record it). 0 V → check the module's power path.
6. Monitor: `tray-a=<number>` with no `(NO_DATA)`. Empty-tray value should vary by only a few hundred
   counts at most.
7. Press the tray gently with a finger. The number moves a lot and returns when released. Note the
   direction: if it decreases under load, swap A+/A− now (step 2.2).
8. Place the reference mass at the tray centre, then near each corner. Raw changes should be similar at
   all positions. A large difference means the tray is binding or touching something.

## Step 4: tray B (HX711 B + cell B)

Repeat step 3 with DT to **25** and SCK to **26**. Then check for **cross-talk**: put a mass on tray A
only. `tray-b` must not change beyond its normal noise, and the reverse must hold too.

## Step 5: calibration (explicit, versioned)

The firmware never tares automatically. For each tray:

1. Empty tray. Wait 10 s. Record the stable raw value as `zero_raw`.
2. Place the known mass (for example 500 g). Wait 10 s. Record `mass_raw`.
3. In the staff app go to **Calibration**, enter `zero_raw`, `mass_raw`, `known_mass_g` and a **new**
   version string (for example `tray-a-2026-10-12-1`). The backend sends the `CALIBRATE` command over
   MQTT; the firmware refuses a repeated version string.
4. Verify with a *different* mass. The reported filtered grams should match within the noise you
   measured. Record noise, drift over 30 minutes and the results in `docs/validation.md`.
5. Weigh 5 or more real units of each demo SKU separately and enter them in the product's sample
   weights. The catalogue stores the measured mean, spread and tolerance. Do not invent unit weights.

## Step 6: join the network

Fill `secrets.h` with Wi-Fi, `MQTT_HOST` (the laptop's LAN IP), `MQTT_PASSWORD` and `DEVICE_KEY`
(matching the backend), re-flash, and confirm `wifi=up mqtt=up`. HEALTH, WEIGHT and RFID events then
appear in Staff → Device health.

## Power arrangement for the finished shelf

```text
 5 V ≥ 2 A supply A ── camera board 5V / GND             (camera only)
 laptop USB or 5 V ≥ 1 A supply B ── sensor ESP32 USB  (powers RC522 + both HX711 from its 3V3 pin)
 All GNDs that share a signal must be common. The camera shares no signal wire with the sensor
 controller (both talk over Wi-Fi), so the two supplies need not be joined.
 Never feed USB and the 5V/VIN pin of the same board from two sources at once.
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `rc522_version=0x00` / `0xFF` | SPI wiring (SDA=21, SCK=18, MOSI=23, MISO=19), 3.3 V present, module not 5 V-only |
| RC522 version OK, no UID | Tag type must be 13.56 MHz MIFARE/NTAG; hold flat and close; metal behind the antenna detunes it |
| `tray-x … (NO_DATA)` | DT/SCK swapped or wrong GPIO, HX711 not powered |
| Raw value pinned at 8388607 or −8388608 | Signal saturated: A+/A− or E+/E− miswired, or the cell is overloaded |
| Raw noise thousands of counts | Loose terminals, untwisted leads, tray touching neighbours, noisy USB supply |
| Weight depends on where the item sits | Mechanical binding; fix the mounting before calibrating |
| Load on A changes B | Trays or cables touching; packaging bridging trays |
