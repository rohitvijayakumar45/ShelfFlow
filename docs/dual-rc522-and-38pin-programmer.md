# Two RC522 readers and the new 38-pin camera programmer

Hardware update, 15 October 2026: the **new 38-pin ESP32 is the programmer for
the ESP32-S CAM**. The existing sensor ESP32 runs both shelf RC522 readers,
the three HX711 channels and the display/buttons. The camera still runs on
the ESP32-S CAM itself. These are separate board roles.

The 38-pin count alone does not establish a model: the programmer connections
below apply to an original ESP32 development board with an onboard USB-UART
chip (CP210x/CH340 or equivalent). Verify EN, TX/GPIO1, RX/GPIO3 and GND labels
against the actual board. Use GPIO labels, not physical header-pin numbers.

## Both RC522 readers on the sensor ESP32

Power off before wiring. These match `firmware/shelf-controller/src/main.cpp`.
Both readers share the SPI bus and reset, but have separate chip-select wires.

| RC522 pin | Reader R1 -> sensor ESP32 | Reader R2 -> sensor ESP32 |
| --- | --- | --- |
| 3.3V | 3V3 | 3V3 |
| GND | GND | GND |
| SCK | GPIO18 | GPIO18 |
| MOSI | GPIO23 | GPIO23 |
| MISO | GPIO19 | GPIO19 |
| SDA / SS | **GPIO21** | **GPIO4** |
| RST | GPIO22 | GPIO22 |
| IRQ | Not connected | Not connected |

RC522 **SDA means SPI chip select in this setup**, not I2C SDA. Do not join
the two SDA/SS wires. Power both modules at 3.3 V, not 5 V. Keep SPI wiring
short and all grounds common. The LCD stays on GPIO16/17 through the required
level shifter; do not move it onto GPIO21/22, which the readers use.

1. Remove any camera-programming wires and EN-to-GND jumper from the **sensor**
   ESP32: its processor must run normally.
2. Wire R1 and test it first, then power off and add R2.
3. Run the shelf-controller firmware, or its offline environment if Wi-Fi/MQTT
   are not ready. Open Serial Monitor at 115200.
4. Expect separate startup diagnostics, for example `DIAG RC522 R1 ss=21
   version=0x92` and `DIAG RC522 R2 ss=4 version=0x92`. Version bytes vary by
   module; 0x00 or 0xFF warrants checking power, chip select and SPI wiring.
5. Present a tag to each reader separately. Expect `DIAG RFID reader=R1 uid=...`
   and `DIAG RFID reader=R2 uid=...`. These are examples, not measured results.

The firmware selects one RF field at a time to reduce interference. Measure
each actual reading zone; two RC522s do not make an entire shelf readable.
The two modules here are **both shelf readers**; any gate reader is separate.

Repeated scans use WUPA (wake-up) rather than only REQA: REQA ignores a tag
put into HALT after its previous read, and a neighbouring RF field may keep
the tag powered. Reader state is cleaned up on unsuccessful selections too.
Each reader has its own UID/repeat timer and reports a held tag at most once
per second; the same UID on R1 never suppresses R2. `scans=A/B` in the serial
diagnostic counts successful UID selections per reader, including repetitions
suppressed from event output. Repeated observations do not represent separate
customer actions or purchases. Verify both readers with repeated removal and
presentation tests on the physical shelf.

Live check after the WUPA change: in a 50-second capture, R1 reported UID
`43D51829` twice and R2 reported no UID (`scans=2/0`). R1's version stayed
at `0xB2`; R2 alternated between `0xB2`, `0x00` and once `0x0F`.
Repeated R2 operation is therefore **not verified**. The next isolation test
is R2 alone, with R1 fully disconnected and R2 still using SS GPIO4.

### RFID bench diagnosis, 15 October

The `devkitc-v4-rfid-diagnostic` build accepts USB serial commands at 115200
with a newline: `r1`, `r2`, or `both`. Single-reader mode leaves both modules
wired and powered but polls only the selected reader, with its field kept on
between polls and the other field off. `both` restores alternating fields.
It does not physically isolate a faulty module's shared MISO or power rail.
This console is compiled only into the diagnostic build, which disables Wi-Fi.
Switching modes does not reset the board or erase calibration.

Physical R2-only check (both modules still wired): the firmware acknowledged
`RFID MODE R2`, disabled R1's antenna, and kept R2's field on. During 55 seconds
the user reported no R2 detection and the serial log recorded no UID, with
54 diagnostic samples showing `0xB2/0xB2` and final `scans=0/0`. Reader
switching alone therefore does not explain this failure; shared electrical
connections and the R2 RF path still need isolation. Both-reader polling was
restored after the capture.

With both readers attached, the live sensor board on COM3 (MAC
`00:4b:12:ee:81:f4`) reported version `0xEE` from both readers. A bench build
`devkitc-v4-rfid-diagnostic` disables Wi-Fi and lowers SPI to 1 MHz. Firmware
now resets the shared GPIO22 once and keeps it driven high; each library
instance uses software reset without reconfiguring that common wire.
Startup writes distinct ModeReg values to R1/R2, reads them back, then restores
the original values. This checks communication and chip-select separation;
it is not a tag-read or RF performance test.

The first physical diagnostic still reported `0xEE/0xEE`, reset HIGH, and both
ModeReg readbacks `0x00` rather than `0x3D/0x3F` (`bus=FAIL`). The reset change
and slower SPI therefore have **not resolved the physical communication fault**.
Next isolate each module on the bus with the other fully disconnected, power
off before wiring, and check the 3.3 V rail at the module itself. A shared-bus
failure does not establish that both modules are faulty.

Boot, NVS and app0 were backed up locally before upload under the git-ignored
`data/hardware-backups/` directory. Filesystem storage was not flashed or formatted.

## New 38-pin ESP32 -> ESP32-S CAM (hardware USB-UART method)

No programmer sketch is needed. Grounding EN holds the programmer CPU in
reset so its onboard USB-UART chip communicates directly with the camera.
Disconnect power before changing the wires.

| New 38-pin programmer | ESP32-S CAM / connection |
| --- | --- |
| EN | Programmer's **own GND** |
| TX / TX0 / GPIO1 | Camera **U0T** |
| RX / RX0 / GPIO3 | Camera **U0R** |
| GND | Camera GND |
| USB | Laptop USB data connection |
| — | Camera IO0 -> camera GND **only to enter upload mode** |

The matching TX/TX and RX/RX labels are deliberate for this hardware method:
the programmer header labels name the disabled ESP32 CPU's signals, while
the USB-UART chip is connected to their opposite ends. Do not substitute
GPIO16/17: those are for the different software-bridge method.

Power the camera 5V pin from one regulated 5 V supply, preferably rated 2 A,
and join its ground with both boards. Leave programmer 5V/VIN disconnected
from the camera when using this external supply. Do not feed 5 V into 3V3
or UART pins. Laptop USB powers the programmer. The camera power supply and
the programmer's USB supply must not have their positive rails tied together.

### Flash and run

1. Plug in only the new programmer to identify its COM port. It may differ
   from the previous COM3; select the port shown for the new board.
2. With the wiring above, keep **programmer EN grounded**.
3. Connect **camera IO0 to GND**, power the camera and press **camera RST**.
4. In Arduino IDE select the **camera target**, not the programmer target.
   For the tested AI-Thinker camera map, use **AI Thinker ESP32-CAM**;
   PSRAM support is enabled by that board profile. Select 115200 upload speed.
5. Close Serial Monitor, upload, and press camera RST during `Connecting...`
   if needed. The programmer's reset circuitry does not reset the camera.
6. After successful verification, **remove camera IO0-to-GND**, open Serial
   Monitor at 115200, then press **camera RST** to run.
7. Keep programmer EN grounded while using its USB-UART for camera logs.
   The camera can run with only its 5 V supply once UART monitoring is done.

Changing the programmer does **not** require changing the camera GPIO map,
Wi-Fi settings, firmware image or model. No upload is needed merely to swap
programmers if the camera already has the desired firmware.

User-reported camera test, before this programmer change: ESP32-D0WD-V3,
4 MB flash, 4 MB PSRAM, MAC `68:09:47:F8:93:B8`, PID `0x3660`, camera init
`0x0`, and a 640x480 JPEG captured successfully. This establishes the reported
bench result, not physical validation of the new programmer's wiring.

See [camera programming](programming-camera.md) for identification/probe and
software-bridge alternatives, and [sensor bring-up](sensor-controller-bringup.md)
for the complete tray wiring.

References: [Espressif DevKitC headers and schematic](https://documentation.espressif.com/esp-dev-kits/en/latest/esp32/esp32-devkitc/user_guide.html),
[MFRC522 multi-reader example](https://github.com/miguelbalboa/rfid/blob/master/examples/ReadUidMultiReader/ReadUidMultiReader.ino).
