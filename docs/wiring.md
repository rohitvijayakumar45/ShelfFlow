# Hardware and wiring — proposed, not physically validated

**Current update, 15 October 2026:** two RC522 shelf readers share the sensor ESP32
SPI bus, with chip selects GPIO21 (R1) and GPIO4 (R2). A **new separate 38-pin ESP32
programs the ESP32-S CAM**. See [the current connection tables](dual-rc522-and-38pin-programmer.md),
which supersede the older one-reader board-role descriptions below. The user has
reported a successful OV3660 initialization and 640x480 JPEG, with 4 MB flash and
4 MB PSRAM; the new programmer and complete shelf wiring remain to be tested.

## Confirmed and missing facts

Confirmed: **exactly one OV3660 camera sensor and an RC522 reader**. User reported the camera module marking “ESP32S”, ID “28clpesp32s”, and clarified that a normal ESP32 is used as a programmer; its module/PCB revision remains unconfirmed. The marking has not been matched to a carrier schematic. Unknown: camera carrier model/revision, exact controller PCB revision, schematic, pin map, PSRAM and flash capacities, lens and regulator capability. Do not connect a guessed camera pin map. The camera diagnostic firmware deliberately drives no camera pins.

The sensor and gate targets select a **separate ESP32-DevKitC V4 with ESP32-WROOM-32** as the proposed controller. “Normal ESP32” does not confirm that PCB revision; generic `esp32dev` compile target is compatible with the module family, but exposed pins and power routing still need inspection. These are added controllers, not additional cameras. RC522 model is confirmed; inspect its actual module voltage/pin labels before connection. Different reader protocols would need a new adapter.

## Bill of materials

Reported as owned (12 Oct 2026): camera board + OV3660, one ordinary ESP32 (programmer, then sensor controller), one RC522. **Still to obtain:** gate ESP32, second RC522 for the gate, 2× HX711, 2× load cells, servo, release beam, position switch, supplies. Step-by-step connections: [programming-camera.md](programming-camera.md) (camera flashing through the spare ESP32) and [sensor-controller-bringup.md](sensor-controller-bringup.md) (RC522 + trays).


| Qty | Component | Required specification / assumption |
|---:|---|---|
| 1 existing | ESP32 camera + OV3660 | Exact carrier, PSRAM, lens and pins to be confirmed |
| 1 additional | Sensor ESP32 DevKitC V4 WROOM-32 | Separate SPI RFID + two HX711 controller |
| 1 additional | Gate ESP32 DevKitC V4 WROOM-32 | Demonstration mechanism and feedback |
| 3 per box | Four-wire full-bridge load cells (tripod) | Each rated for the full loaded box; see sensor-controller-bringup.md |
| 3 per box | HX711 modules | One per cell; 3.3 V supply so DT stays at 3.3 V |
| 1 | 16x2 LCD + PCF8574 I2C backpack | On GPIO16/17 (not 21/22) |
| 1 | Bi-directional I2C level shifter (BSS138) | Required for 5 V LCD backpacks |
| 2 | Push buttons (staff service, inside enclosure) | GPIO13/14 to GND; never customer controls |
| 1 shelf + 1 gate | RC522 modules | Provisional 3.3V SPI; one shelf reader covers only its measured wrist zone |
| 4 demo | Passive RFID bracelets | Unique measured UIDs plus rigid printable marker faces |
| 1 demo | SG90-class servo | Selected demo class, actual current/torque/travel must be measured |
| 1 | Optical release beam | 3.3V-compatible output, one clean pulse per physical bracelet |
| 1 | Closed-position switch | Normally open to ground, 3.3V input pullup; verify mechanism polarity |
| 1 optional | Status LED + resistor | GPIO27; calculate resistor for measured LED forward voltage/current |
| As sized | Regulated low-voltage supplies | Board input ratings; isolated actuator supply and USB power plan |
| 2 | Rigid isolated trays | One SKU each; independent force paths |
| 1 | Return bin | Staff confirms return and cleaning in app |
| 1 | 5 V ≥ 2 A regulated supply for the camera board | USB 5 V through jumper wires is enough for flashing only, not Wi-Fi capture |
| 1 laptop + phone optional | Laptop and gate browser camera | Shelf camera is never repurposed for gate scanning |

Passive bracelet identifiers are not secure credentials. Multiple marker faces use different IDs mapped to the same bracelet. Keep black/white contrast, quiet border, rigid face and wrist orientation visible.

## Proposed separate sensor wiring

**Superseded for the tripod box:** the current pin map (3 cells on 32/33, 25/26, 34/27; LCD on 16/17; buttons on 13/14) is in [sensor-controller-bringup.md](sensor-controller-bringup.md). The table below is the original two-tray proposal.

| Controller GPIO | Module signal | Notes |
|---|---|---|
| 18 | RC522 SCK | SPI clock |
| 23 | RC522 MOSI | SPI data out |
| 19 | RC522 MISO | SPI data in |
| 21 | RC522 SDA/SS | SS label differs from I²C SDA |
| 22 | RC522 RST | Reset |
| 32 | HX711 A DT | Tray A |
| 33 | HX711 A SCK | Tray A |
| 25 | HX711 B DT | Tray B |
| 26 | HX711 B SCK | Tray B |
| 27 optional | LED anode via resistor | Cathode to ground |
| 3V3 | RC522 VCC / HX711 digital VCC | Only after checking actual module schematic |
| GND | All logic grounds | Common reference |

Each load cell: E+ → HX711 excitation positive, E− → excitation negative, A+ → bridge signal positive, A− → signal negative. **Wire colours are not universal.** Use load-cell datasheet or measured bridge resistance/polarity. Four-wire full bridge only; three-wire bathroom half bridges are not interchangeable. Shielding/strain relief and short twisted signal wires reduce noise.

## Proposed gate wiring

RC522 uses the same SPI pins on its own controller. Servo signal GPIO13, release beam GPIO32, closed-position switch GPIO33. Firmware assumes sensor LOW means detection/closed; verify actual polarity and debounce. Servo power comes from its separate rated 5V-class supply, not ESP32 rail. Gate servo pulses start disabled; unknown reboot state faults and requires physical inspection. Software does not create a reliable bracelet separator. Build a chute, one-item stop, accessible jam release and a tested one-bracelet feed geometry.

```text
LOGIC SIGNALS (3.3V)               ACTUATOR POWER (separate)
sensor ESP32 ─SPI── RC522          rated actuator PSU + ───── servo V+
    ├─32/33──── HX711 A            rated actuator PSU − ──┬─ servo GND
    └─25/26──── HX711 B                                    └─ common GND
HX711 A ─E+/E−/A+/A−─ cell A       gate GPIO13 ──────────── servo signal
HX711 B ─E+/E−/A+/A−─ cell B       gate 32/33 ← release/position sensors

BOARD POWER
regulated input → actual board-approved input (inspect USB/5V/3V3 topology)
all logic GND ─────────────────── common reference ── PSU−
camera power/pins: NO ASSIGNMENT UNTIL CARRIER VERIFIED
```

Never power motors from GPIO or the ESP32 3.3V rail. Never feed 5V logic directly to ESP32. An open-collector sensor can pull up to 3.3V if its actual schematic permits; otherwise use a verified level shifter. Common grounds are required for signal reference; separate actuator supply prevents current transients through board regulator. Add suitable local decoupling after measuring current/ripple. Do not connect USB and an external input simultaneously unless the actual board's power-routing design explicitly permits it.

## Supply sizing and boot restrictions

Measure camera Wi-Fi peaks, sensor/RFID demand and servo stall current separately. Select continuous supply current above measured worst-case sum with a documented margin, cable voltage-drop budget, fusing/current limiting appropriate to low-voltage components, and thermal check. Servo stall may greatly exceed running current; no universal current number is assumed. No mains wiring is needed.

ESP32 WROOM boot strap pins include GPIO0, 2, 5, 12 and 15; avoid externally imposed startup levels without checking its datasheet. GPIO6–11 are normally flash-connected. GPIO34–39 are input-only and lack internal pullups. The proposed sensor pins avoid these restrictions. Camera carriers may repurpose other pins; never transpose controller assignments.

## Mechanical isolation and placement

Rigid shelf base; each load cell fixed according to its datasheet, with load applied through the intended direction and free end. Each tray has one independent force path. Do not let trays touch shelf walls or each other; prevent packaging or cables bridging trays. Add overload stops without carrying normal stock force. Tare empty verified trays, apply known masses at several tray positions and test cross-talk by loading only one tray.

Use [placement drawing](placement.svg). Mount diagonally above the narrow shelf, not behind front lips. Verify product faces, entry hands, wrist marker and full withdrawal/return trajectory in one image. Actual lens decides position; no distance is guaranteed. Different customers may stand together. Preserve marker visibility on the picking hand and document this constraint to customers.

## Before power-up

1. Photograph PCB labels front/back; obtain schematics; record carrier/module/revision/PSRAM/flash.
2. With power off, check ground continuity and absence of supply shorts.
3. Power each regulator alone; measure voltage/polarity before plugging boards in.
4. Verify RFID/HX711 logic levels and release sensor output high voltage with a multimeter.
5. Verify USB/external supply exclusivity and actuator ground path.
6. Start with actuators disconnected; check board boot and serial diagnostics.
7. Flash sensor diagnostic, verify each HX711 raw reading separately, then RFID UID/path.
8. Initialize filesystem deliberately; confirm reboot preserves queued test events.
9. Explicit tare and known-mass calibration; measure drift, noise and tray cross-talk.
10. Configure camera only after carrier identification; confirm OV3660 detection and PSRAM.
11. Add actuator supply with current limiting; inspect sensor signals and emergency manual release.
12. Record evidence visibility before running any accuracy trials.

Flash through the actual board's approved USB/serial method. If boot mode needs GPIO0-to-GND, follow that carrier's guide; do not guess connectors or UART voltage. Confirm firmware port and chip target. Never flash an arbitrary S3 carrier with the generic WROOM diagnostic configuration.
