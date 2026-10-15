# Standalone HX711 + 3 kg load cell test

Use your normal ESP32 DevKit / ESP32-WROOM board with USB. This test needs no
Wi-Fi, RFID, camera, backend, or ShelfFlow credentials. It prints averaged
weight in grams and kilograms every half second after calibration.

## Wiring (power off while connecting)

| HX711 | Normal ESP32 DevKit |
| --- | --- |
| VCC | 3V3 |
| GND | GND |
| DT / DOUT | GPIO32 |
| SCK / CLK | GPIO33 |

These pins are for the separate normal ESP32, not the ESP32-CAM. Keep HX711
digital signals at 3.3 V; do not power a single-supply module at 5 V directly
with this wiring. If your module separates analog and digital supplies,
check its schematic before wiring it.

Connect a four-wire full-bridge load cell to HX711 E+, E-, A+, A- according
to its manufacturer labels. A common convention is red = E+, black = E-,
green = A+, white = A-, but colors vary: verify your cell's diagram.
Mount a bar load cell with its fixed end secured and its loading end supporting
the platform; leave the bending section free. The 3 kg capacity includes the
platform and anything placed on it. Taring does not increase the capacity.

## Arduino IDE

1. Install ESP32 board support and **HX711 by Bogdan Necula** (tested with 0.7.5)
   through Library Manager. Preferences is included with ESP32 board support.
2. Open `hx711-test.ino` in this directory.
3. Select **ESP32 Dev Module** and the normal ESP32's USB port; upload.
4. Open Serial Monitor at **115200 baud**, select **Newline**, and press Reset
   if you missed the startup instructions.
5. Empty the mounted platform, send **`t`**, and wait for tare completion.
6. Put a known mass on it (for example 500 g), send **`c 500`**, and keep it
   still until calibration completes. Substitute the actual measured mass.
7. Remove the mass to check approximately zero, then test another known mass.

Example calibrated output (illustrative, not a hardware measurement):

```text
Weight: 500.0 g | 0.500 kg
```

The calibration factor is saved to ESP32 flash. On every restart, empty the
platform and send `t` again. Send `h` for help. Before calibration the sketch
prints raw ADC counts rather than pretending they are grams. Negative
calibration factors are supported. Small negative readings near zero are
shown honestly, not clamped away.

## PlatformIO

From the repository root:

```powershell
python -m platformio run -d firmware/hx711-test
python -m platformio run -d firmware/hx711-test -t upload
python -m platformio device monitor -d firmware/hx711-test
```

If using the project environment, replace `python` with `.venv\Scripts\python.exe`.
Specify `--upload-port COMx` for upload and `--port COMx` for the monitor when
multiple boards are connected. Close Serial Monitor before uploading.

No readings: check the USB port, 115200 baud, common ground, power, and DT/SCK.
Drift or noisy readings: check mounting, keep wires short, let the sensor
settle, then tare and recalibrate. Sampling times out after five seconds if
the HX711 stops responding. A software range warning cannot protect a cell
against physical overload, especially after tare.

Library reference: https://github.com/bogde/HX711

Validation: compile checked for the original ESP32 target; physical accuracy
must be checked with your mounted cell and known masses.
