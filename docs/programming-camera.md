# Using the spare ESP32 to program the camera

User clarified: ESP32-S CAM camera board, RC522 reader, and a normal ESP32 available as a programmer. The sole camera sensor remains **OV3660**, per the original confirmed requirement. Pasted OV2640 examples do not establish the actual carrier's wiring. An “ESP32S” module marking is not sufficient to choose `CAMERA_MODEL_AI_THINKER` or camera pin assignments.

## Option: fixed-baud software pass-through

`firmware/programmer` implements USB UART0 ↔ UART2 byte forwarding at **115200 baud**, with bounded receive buffers and write-space checks. It is configured for a **proposed ESP32-DevKitC V4 with ESP32-WROOM-32 and an onboard USB-to-UART bridge**. Confirm that this is the normal board you have. GPIO16/17 can be tied to PSRAM on WROVER variants; do not use those pins there. Native USB ESP32-S3 boards require a different bridge configuration.

| Verified WROOM-32 programmer | Camera UART signal | Purpose |
|---|---|---|
| GPIO17 / UART2 TX | Confirmed target U0R / RX | Programmer sends to camera |
| GPIO16 / UART2 RX | Confirmed target U0T / TX | Camera sends to programmer |
| GND | GND | Common signal reference |
| No default power link | Board-approved regulated input | Verify actual carrier and supply first |

These UART2 pins belong to the programmer, not the shelf camera. Logic must be 3.3V. Do not tie two powered 5V rails together or assume the programmer's USB/5V path can supply camera Wi-Fi peaks. Power the camera from one suitable, verified source and share ground. Confirm camera U0R/U0T, boot/reset and power labels against its actual carrier; the carrier's camera-sensor pin map remains separate and unconfigured.

1. With the camera disconnected, flash the programmer through its normal onboard USB using `python -m platformio run -d firmware/programmer -t upload --upload-port COMx` after confirming that board/chip target.
2. Close Serial Monitor and other software using that COM port.
3. Connect confirmed UART signals and ground. Apply the camera's approved power source.
4. Put the target camera into its documented ROM bootloader mode, normally target GPIO0 low during reset on an original ESP32 carrier. Do not guess labels or hold an unrelated pin low.
5. Use esptool at **115200 baud with `--before no_reset --after no_reset`**, and manually reset the target. Default automatic DTR/RTS reset acts on the programmer's onboard USB circuitry, not the remote camera. Faster baud switching is not supported by this fixed-baud bridge.
6. The command template is `esptool.py --chip esp32 --port COMx --baud 115200 --before no_reset --after no_reset write_flash ...`. Supply addresses/images from the build for the **confirmed actual camera carrier**, not guessed flash offsets. No camera upload command is provided until that target is identified.
7. After uploading, remove the target's temporary boot strap and reset it into normal run mode. Monitor at the same 115200 baud through the bridge if needed. Do not power two sources simultaneously without verified board power routing.
8. After camera flashing, disconnect the UART programming wires before reusing this ESP32 as the shelf sensor controller. It cannot run bridge and shelf firmware simultaneously. The separate gate controller remains part of the BOM.

The software bridge does not forward reset signals or dynamically follow baud changes. Build success does not verify electrical levels, power capacity or serial signal integrity. Verify chip identity with a read-only esptool probe before writing flash. A DevKit whose onboard USB-UART pins are directly accessible can sometimes be used with its MCU held in reset; that wiring depends on the exact board schematic and differs from the active UART2 method. Do not mix the two methods.

Primary references: [Espressif DevKitC guide](https://documentation.espressif.com/esp-dev-kits/en/latest/esp32/esp32-devkitc/user_guide.html), [esptool v4 reset modes](https://docs.espressif.com/projects/esptool/en/release-v4/esp32/esptool/advanced-options.html). Underscore option names above match the pinned esptool v4.5.1; v5 documentation uses hyphens.
