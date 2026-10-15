# Standalone OV3660 camera test using another ESP32

This sketch identifies an OV3660, captures a JPEG, and creates a local Wi-Fi
access point with a browser preview. The preview refreshes individual JPEGs;
it is not an MJPEG stream. No router or ShelfFlow services are needed.

The original ESP32 camera driver supports OV3660, but **ESP32-S identifies the
module, not the carrier wiring**. The supplied camera map is specifically the
AI-Thinker ESP32-CAM map. A matching outer header does not prove the ribbon map.
Check the carrier schematic or follow the pin-map probe in
[the detailed programming guide](../../docs/programming-camera.md) before
enabling this map. Otherwise leave `CAMERA_PINMAP_VERIFIED` at 0: the sketch
prints board information and does not drive camera pins.

## Recommended programming connections: onboard USB-UART hardware

Use a normal original ESP32 DevKit with a CP210x/CH340 onboard USB-UART chip.
Native-USB S2/S3/C3 boards are not covered by this connection table.
Disconnect its HX711 and other peripherals first. Unplug power before wiring.

| Normal programmer ESP32 | ESP32-CAM | Purpose |
| --- | --- | --- |
| EN -> its own GND | — | Holds the programmer CPU in reset |
| TX0 / GPIO1 | U0T | Camera transmit -> onboard USB-UART receive |
| RX0 / GPIO3 | U0R | Onboard USB-UART transmit -> camera receive |
| GND | GND | Shared reference |
| — | IO0 -> camera GND | Fit only when entering upload mode |

**TX0 to U0T and RX0 to U0R is intentional.** The header names describe the
programmer's CPU signals. Holding EN low releases those pins, letting its
USB-UART chip talk directly to the camera. No bridge sketch is needed.
Do not use GPIO16/17 for this hardware method. Verify the dev board's schematic
if its header UART pins are isolated from the onboard USB-UART.

Recommended power: laptop USB powers the programmer; a separate regulated
5 V supply rated at least 1 A (2 A recommended) powers the camera's **5V** pin.
Join supply ground, camera ground and programmer ground. Leave the programmer's
5V/VIN disconnected from the camera when using the separate supply. Never
apply 5 V to the camera's 3V3 pin or UART pins. Do not use the programmer's
3V3 regulator to power the camera. Short leads matter during Wi-Fi operation.
Keep the camera ribbon installed; remove any microSD card for this test.

## Arduino IDE upload

1. Install **esp32 by Espressif Systems** through Boards Manager. The repository
   compile check uses Arduino-ESP32 2.0.17. WiFi, WebServer and esp_camera are
   bundled; no separate camera library is needed.
2. Open `ov3660-test.ino`. Select **AI Thinker ESP32-CAM** only for the matching
   carrier; choose the programmer USB COM port and upload speed **115200**.
   If shown, enable PSRAM. Leave other options at that board's defaults.
3. Leave the programmer **EN -> GND** connected throughout upload and monitoring.
4. Connect camera **IO0 -> GND**, then press the **camera's RST** button.
5. Close Serial Monitor and click Upload. If it remains at `Connecting...`,
   briefly press camera RST again while IO0 remains grounded. The programmer's
   automatic reset circuit cannot reset the separate camera.
6. Wait for successful write/verification. Remove camera **IO0 -> GND**.
7. Open Serial Monitor at **115200** and press **camera RST**. You should see
   chip/flash/PSRAM details and `IDENTIFY ONLY` with the default sketch.
8. Verify the camera map with its schematic or the repository's pin-map probe.
   Then set `CAMERA_PINMAP_VERIFIED` to **1** in the sketch and repeat steps 4–7.
   IO0 must be free when running: on this carrier it is the camera clock pin.
9. Expect `Sensor PID: 0x3660`, `CAMERA PASS`, then the AP name and IP address.
10. On your laptop connect to **ESP32CAM-Test**, password **camera123**. Stay
    connected if Windows warns there is no internet. Open **http://192.168.4.1**
    (or the IP actually printed). The page refreshes captured images.

After testing, disconnect power before removing wires. Remove the programmer's
EN jumper to use it normally again. The camera runs independently with only
its 5 V supply; UART wires are not needed for the browser preview.

## PlatformIO alternative

From the ShelfFlow root, replace COM5 with the programmer's COM port:

```powershell
.venv\Scripts\python.exe -m platformio run -d firmware/ov3660-test -e identify-only -t upload --upload-port COM5
.venv\Scripts\python.exe -m platformio device monitor -d firmware/ov3660-test --port COM5
```

Close the monitor before upload; manually fit/remove camera IO0 and press RST
as described above. After verifying the camera map, upload the enabled target:

```powershell
.venv\Scripts\python.exe -m platformio run -d firmware/ov3660-test -e ai-thinker-camera-test -t upload --upload-port COM5
```

These targets use 115200 baud and no automatic reset before/after upload.

## If you already flashed the software bridge onto the programmer

Use either the hardware method above with EN grounded (the bridge code does
not run), or the existing `firmware/programmer` bridge with **EN left free**:
GPIO17/TX2 -> camera U0R; GPIO16/RX2 <- camera U0T; shared GND. Do not mix the
two UART tables. The software bridge is for a WROOM board with GPIO16/17 free,
not WROVER PSRAM pins. It runs at a fixed 115200; use the PlatformIO no-reset
upload targets above so flashing does not reset and overwrite the programmer.
Manually set camera IO0/RST. The detailed guide includes MAC-address checks
to confirm that esptool is communicating with the camera, not the programmer.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Connecting forever | EN grounded for hardware method, camera IO0 grounded, camera RST, correct UART table/COM port |
| Garbled output | 115200 baud, short wires, common ground |
| Brownout / resets | Camera 5 V supply, current capacity, wiring resistance; do not disable brownout detection |
| Camera init error | Verified ribbon map, seating with power off, camera IO0 jumper removed |
| PID differs from 0x3660 | Sensor identification differs; record it rather than assuming OV3660 |
| Page unavailable | Laptop connected to camera AP, use HTTP and printed IP |
| Rotated/mirrored image | After a valid image, adjust sensor vflip/hmirror for the physical mounting |

Compile checked on the PC for both targets. Not flashed or tested on physical
hardware; supply, board wiring, sensor identity and image quality remain to verify.

Primary references:
- [Espressif camera driver](https://github.com/espressif/esp32-camera)
- [Espressif camera pin tables](https://github.com/espressif/arduino-esp32/blob/master/libraries/ESP32/examples/Camera/CameraWebServer/camera_pins.h)
- [DevKitC guide and schematic](https://documentation.espressif.com/esp-dev-kits/en/latest/esp32/esp32-devkitc/user_guide.html)
- [esptool v4 reset modes](https://docs.espressif.com/projects/esptool/en/release-v4/esp32/esptool/advanced-options.html)
