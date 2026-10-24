"""Log the camera's serial output through the programmer, with timestamps, without resetting it.

The programmer (ESP #2, EN held to GND) is only a USB-serial adapter here: DTR/RTS stay
released so opening the port never resets anything. Every line is printed and appended to
the log, so a reboot shows its reason (brownout, crash/panic, watchdog) and Wi-Fi drops show
WIFI_LOST / WIFI_TRY.

Run: python -m tools.camera_serial_log --port COM4 [--log reports/camera-serial.log]
"""
import argparse
import time
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--port', required=True)
    parser.add_argument('--log', default=str(ROOT / 'reports/camera-serial.log'))
    args = parser.parse_args()
    port = serial.Serial()
    port.port, port.baudrate, port.timeout = args.port, 115200, 0.5
    port.dtr = port.rts = False
    port.open()
    pending = b''
    with open(args.log, 'a', encoding='utf-8') as log:
        log.write(f'--- {time.strftime("%Y-%m-%d %H:%M:%S")} logging {args.port}\n'); log.flush()
        while True:
            pending += port.read(4096)
            *lines, pending = pending.split(b'\n')
            for raw in lines:
                text = raw.decode('utf-8', 'replace').rstrip('\r')
                if not text:
                    continue
                line = f'{time.strftime("%H:%M:%S")} {text}'
                print(line, flush=True); log.write(line + '\n'); log.flush()


if __name__ == '__main__':
    main()
