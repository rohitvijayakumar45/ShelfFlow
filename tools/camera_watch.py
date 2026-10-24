"""Log the camera's availability without a serial cable, to tell WHY it disappears.

Every few seconds: the laptop's Wi-Fi (SSID + access-point BSSID: a phone hotspot that
restarts usually comes back with a new BSSID) and the camera's /health. When the camera
comes back after a gap, its uptime says what happened while it was gone:
  uptime shorter than the gap  -> it rebooted (power/brownout or crash);
  uptime longer than the gap   -> it kept running but was off the network (Wi-Fi/hotspot).

Run: python -m tools.camera_watch [--camera http://10.12.94.251] [--log reports/camera-watch.jsonl]
"""
import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def wifi():
    out = subprocess.run(['netsh', 'wlan', 'show', 'interfaces'], capture_output=True, text=True).stdout
    def field(name):
        m = re.search(rf'^\s*{name}\s*:\s?(.*)$', out, re.M)
        return m.group(1).strip() if m else None
    return {'ssid': field('SSID'), 'bssid': field('AP BSSID'), 'state': field('State')}


def main():
    load_dotenv(ROOT / 'data/integration/runtime.env')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--camera', default=os.getenv('PHYSICAL_CAMERA_URL', 'http://10.12.94.251'))
    parser.add_argument('--log', default=str(ROOT / 'reports/camera-watch.jsonl'))
    parser.add_argument('--interval', type=float, default=3)
    args = parser.parse_args()
    headers = {'X-Camera-Key': os.environ['CAMERA_KEY']}
    last_ok, down_since, last_wifi, last_uptime, seen = None, None, None, None, False
    with open(args.log, 'a', encoding='utf-8') as log, httpx.Client(timeout=2.5, trust_env=False) as client:
        def note(kind, **fields):
            row = {'t': round(time.time(), 1), 'time': time.strftime('%H:%M:%S'), 'kind': kind, **fields}
            log.write(json.dumps(row) + '\n'); log.flush()
            print(row['time'], kind, json.dumps(fields), flush=True)
        note('START', camera=args.camera, **wifi())
        while True:
            now = time.time()
            w = wifi()
            if w != last_wifi:
                note('WIFI', **w); last_wifi = w
            try:
                h = client.get(args.camera.rstrip('/') + '/health', headers=headers).json()
                uptime = h.get('uptime_ms', 0) / 1000
                if down_since is not None:
                    gap = now - down_since
                    note('CAMERA_BACK', gap_s=round(gap), uptime_s=round(uptime), rssi=h.get('rssi_dbm'),
                         verdict='REBOOTED while away' if uptime < gap + args.interval else 'STAYED RUNNING, was off the network')
                    down_since = None
                elif last_uptime is not None and uptime + 1 < last_uptime:
                    note('CAMERA_REBOOTED', uptime_s=round(uptime), previous_uptime_s=round(last_uptime))
                elif not seen:
                    note('CAMERA_UP', uptime_s=round(uptime), rssi=h.get('rssi_dbm'), size=f"{h.get('width')}x{h.get('height')}")
                last_ok, last_uptime, seen = now, uptime, True
            except Exception as exc:
                if down_since is None:
                    down_since = last_ok or now
                    note('CAMERA_GONE', last_uptime_s=round(last_uptime) if last_uptime else None, error=type(exc).__name__)
            time.sleep(args.interval)


if __name__ == '__main__':
    main()
