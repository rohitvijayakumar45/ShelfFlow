"""Forward the controller's durable USB queue; ACK only after matching backend commit."""
import argparse
import json
import os
from pathlib import Path
import re
import time

import httpx
import serial
from dotenv import load_dotenv


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    for attempt in range(10):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt==9:raise
            time.sleep(.02*(attempt+1))  # Windows readers/indexers briefly hold rename locks.


class EventStore:
    def __init__(self, root):
        self.root = Path(root)
        self.offsets = {}

    def prepare(self, raw):
        event_id = raw['event_id']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', event_id):
            raise ValueError('Invalid event ID')
        path = self.root / 'prepared' / (event_id + '.json')
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
        event = json.loads(json.dumps(raw))
        event.pop('device_key', None)
        offset = self.offsets.get(event['boot_id'])
        if offset is not None:
            event['capture_start'] = event['capture_end'] = offset + event['monotonic_ms'] / 1000
        # USB wall alignment is useful for evidence display, not validated fusion timing.
        event['clock_synced'] = False
        event['clock_uncertainty_ms'] = max(1000, event['clock_uncertainty_ms'])
        atomic_json(self.root / 'raw' / (event_id + '.json'), raw)
        atomic_json(path, event)  # Freeze once: retries must never change an event's payload.
        return event

    def deliver(self, event, client, key):
        response = client.post('/devices/sensor-1/events', json=event, headers={'X-Device-Key': key})
        response.raise_for_status()
        ack = response.json()
        if ack.get('durable') is not True or ack.get('ack') != event['event_id']:
            return False
        atomic_json(self.root / 'delivered' / (event['event_id'] + '.json'), event)
        return True


def run(args):
    load_dotenv(args.env, override=True)
    key = os.environ['SENSOR_DEVICE_KEY']
    root = Path(os.getenv('USB_EVENT_ROOT', 'data/integration/usb'))
    store = EventStore(root)
    offsets_file = root / 'clock-offsets.json'
    if offsets_file.exists():
        store.offsets = json.loads(offsets_file.read_text())
    connection = serial.Serial(port=None, baudrate=115200, timeout=.1)
    connection.dtr = connection.rts = False
    connection.port = args.port
    connection.open()
    pending = b''
    ready = False
    next_pull = next_state = next_retry = next_status = 0.0
    inflight = None
    latest_state = {}
    reported_boots = set()
    status = {'connected': False, 'delivered': 0, 'last_error': None}
    print('USB controller bridge starting', flush=True)
    with httpx.Client(base_url='http://127.0.0.1:8000', timeout=3, trust_env=False) as client:
        try:
            while True:
                now = time.monotonic()
                if now >= next_state:
                    connection.write(b'\nusbstatus\n')
                    next_state = now + 5
                if ready and inflight is None and now >= next_pull:
                    connection.write(b'next\n')
                    next_pull = now + 1
                pending += connection.read(min(max(connection.in_waiting, 1), 8192))
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    text = line.decode(errors='replace').strip()
                    try:
                        if 'SF_STATE ' in text:
                            latest_state = json.loads(text.split('SF_STATE ', 1)[1])
                            boot = latest_state['boot_id']
                            if boot not in store.offsets:
                                store.offsets[boot] = time.time() - latest_state['monotonic_ms'] / 1000
                                atomic_json(offsets_file, store.offsets)
                            ready = True
                            status.update(connected=True, last_seen=time.time(), controller=latest_state)
                            if boot not in reported_boots and latest_state['queued']==0:
                                connection.write(b'calreport\n')  # Report saved scales; no calibration writes.
                                reported_boots.add(boot)
                        elif 'SF_EVENT ' in text:
                            raw = json.loads(text.split('SF_EVENT ', 1)[1])
                            if raw['device_id'] != 'sensor-1':
                                raise ValueError('Unexpected device scope')
                            inflight = store.prepare(raw)
                        elif 'SF_HEALTH ' in text:
                            raw = json.loads(text.split('SF_HEALTH ', 1)[1])
                            if ready and raw['boot_id'] in store.offsets:
                                event = dict(raw)
                                event['capture_start'] = event['capture_end'] = store.offsets[raw['boot_id']] + raw['monotonic_ms'] / 1000
                                event['clock_synced'] = False
                                event['clock_uncertainty_ms'] = max(1000, event['clock_uncertainty_ms'])
                                try:
                                    client.post('/devices/sensor-1/events', json=event, headers={'X-Device-Key': key}).raise_for_status()
                                except httpx.HTTPError:
                                    pass  # HEALTH is best effort, never a controller ACK.
                        elif 'SF_ERROR' in text:
                            status['last_error'] = text[-120:]
                    except (ValueError, KeyError):
                        status['last_error'] = 'Invalid USB protocol response'
                pending = pending[-65536:]
                if inflight is not None and now >= next_retry:
                    try:
                        if store.deliver(inflight, client, key):
                            connection.write(('ack ' + inflight['event_id'] + '\n').encode())
                            status['delivered'] += 1
                            status['last_error'] = None
                            inflight = None
                    except (httpx.HTTPError, ValueError):
                        status['last_error'] = 'Backend unavailable or event rejected; retained on controller'
                    next_retry = time.monotonic() + 1
                if now>=next_status:
                    try:atomic_json(root / 'status.json', status)
                    except PermissionError:pass # A transient status-file lock must not stop capture.
                    next_status=now+1
        finally:
            connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', default='data/integration/runtime.env')
    parser.add_argument('--port', default='COM3')
    args = parser.parse_args()
    while True:
        try:
            run(args)
        except (serial.SerialException, OSError):
            print('USB connection interrupted; retrying with controller events retained', flush=True)
            time.sleep(1)


if __name__ == '__main__':
    main()
