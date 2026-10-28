"""Forward the controller's durable USB queue; ACK only after matching backend commit."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import time

import httpx
import serial
from dotenv import load_dotenv


def record_diagnostic(path, line):
    # Never put credentials or complete device payloads in the tap-test log.
    prefixes = ('LCD GUIDE', 'WEIGH_SELFTEST ', 'WEIGH ', 'RAW ', 'CAL ', 'CALC ', 'AZT_', 'TARE_', 'DIAG RFID', 'RFID MODE ', 'RF TUNE ', 'RFID_BUFFER_OVERFLOW',
                'RFID_PERSIST_FAILED', 'QUEUE_OVERFLOW', 'QUEUE_WRITE_FAILED', 'RFID_TASK_FAILED', 'RFID_REINIT', 'RFID_WATCHDOG')
    if not line.startswith(prefixes):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 10_000_000:
        path.replace(path.with_suffix('.previous.jsonl'))
    with path.open('a', encoding='utf-8') as output:
        output.write(json.dumps({'received_s': time.time(), 'line': line}) + '\n')


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


def consume_monitor_command(root, connection, latest_state):
    """One-shot, short-lived local tare request; never accept arbitrary UART text."""
    path = root / 'monitor-zero-request.json'
    try:
        request = json.loads(path.read_text(encoding='utf-8'))
        path.unlink()  # Consume before dispatch: reconnects must never replay a tare.
    except (FileNotFoundError, PermissionError, ValueError):
        return
    action = request.get('action')
    allowed = {'tareempty', 'cal empty', 'cal a', 'cal c', 'cal save', 'cal cancel', 'qc start', 'qc next', 'qc cancel'}
    if (action in allowed
            and request.get('boot_id') == latest_state.get('boot_id')
            and 0 <= time.time() - request.get('created_s', 0) <= 10):
        connection.write(('\n' + action + '\nusbstatus\n').encode('ascii'))


def lcd_command(request):
    """Serial command for a staff guidance request, or None. Text is printable ASCII only, 16 characters
    a line, and never contains the line separator, so it cannot turn into another controller command."""
    seconds = request.get('seconds')
    if seconds == 0:
        return 'lcd off'
    if not isinstance(seconds, int) or not 1 <= seconds <= 600:
        return None
    lines = list(request.get('lines') or [])[:2] + ['', '']
    clean = [''.join(ch for ch in str(line) if ' ' <= ch <= '~' and ch != '|')[:16] for line in lines[:2]]
    return f'lcd {seconds} {clean[0]}|{clean[1]}'


def consume_lcd_request(root, connection):
    """One-shot LCD guidance from the backend (tray loading). Stale requests are dropped."""
    path = root / 'lcd-request.json'
    try:
        request = json.loads(path.read_text(encoding='utf-8'))
        path.unlink()
    except (FileNotFoundError, PermissionError, ValueError):
        return
    command = lcd_command(request)
    if command and 0 <= time.time() - request.get('created_s', 0) <= 10:
        connection.write(('\n' + command + '\n').encode('ascii'))


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
    # A cancelled bench experiment may have left one antenna selected. Normal
    # application operation always starts with both readers, without resetting.
    connection.write(b'\nboth\n')
    pending = b''
    ready = False
    next_pull = next_state = next_retry = next_status = 0.0
    inflight = None
    latest_state = {}
    reported_boots = set()
    status = {'connected': False, 'delivered': 0, 'last_error': None}
    event_future = health_future = live_future = None
    latest_health = latest_live = None
    live_tray = 'tray-a'  # replaced by the tray named in the controller's HEALTH reports
    diagnostics_file = root / 'rfid-diagnostics.jsonl'
    print('USB controller bridge starting', flush=True)
    # Only this loop owns COM3. Separate clients/workers keep slow HTTP requests
    # from pausing the serial receiver or delaying tap diagnostics.
    with httpx.Client(base_url='http://127.0.0.1:8000', timeout=3, trust_env=False) as client, \
         httpx.Client(base_url='http://127.0.0.1:8000', timeout=3, trust_env=False) as health_client, \
         ThreadPoolExecutor(max_workers=3) as workers:
        try:
            while True:
                now = time.monotonic()
                if ready:
                    consume_monitor_command(root, connection, latest_state)
                    consume_lcd_request(root, connection)
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
                        record_diagnostic(diagnostics_file, text)
                    except OSError:
                        pass  # Optional test logging must not interrupt durable delivery.
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
                        elif 'SF_IDLE' in text and latest_state.get('boot_id') and latest_state['boot_id'] not in reported_boots:
                            # Nothing deliverable is left (the count can include incomplete .tmp files after a
                            # reset): report the saved calibration now so the backend adopts it.
                            connection.write(b'calreport\n')
                            reported_boots.add(latest_state['boot_id'])
                        elif text.startswith('WEIGH ') and ready:
                            # Once a second: the live and held tray weight, pushed to the staff screens.
                            values = dict(part.split('=', 1) for part in text.split()[1:] if '=' in part)
                            latest_live = {'tray_id': live_tray, 'live_g': float(values['shown_g']), 'held_g': float(values['held_g'])}
                        elif 'SF_HEALTH ' in text:
                            raw = json.loads(text.split('SF_HEALTH ', 1)[1])
                            live_tray = raw.get('tray_id') or live_tray
                            if ready and raw['boot_id'] in store.offsets:
                                event = dict(raw)
                                event['capture_start'] = event['capture_end'] = store.offsets[raw['boot_id']] + raw['monotonic_ms'] / 1000
                                event['clock_synced'] = False
                                event['clock_uncertainty_ms'] = max(1000, event['clock_uncertainty_ms'])
                                latest_health = event  # Coalesce health; never build an unbounded HTTP queue.
                        elif 'SF_ERROR' in text:
                            status['last_error'] = text[-120:]
                    except (ValueError, KeyError):
                        status['last_error'] = 'Invalid USB protocol response'
                pending = pending[-65536:]
                if event_future is not None and event_future.done():
                    try:
                        if event_future.result():
                            connection.write(('ack ' + inflight['event_id'] + '\n').encode())
                            status['delivered'] += 1
                            status['last_error'] = None
                            inflight = None
                        else:
                            status['last_error'] = 'Backend ACK mismatch; retained on controller'
                    except (httpx.HTTPError, ValueError, OSError):
                        status['last_error'] = 'Backend unavailable or event rejected; retained on controller'
                    event_future = None
                    next_retry = time.monotonic() + 1
                if inflight is not None and event_future is None and now >= next_retry:
                    event_future = workers.submit(store.deliver, inflight, client, key)
                if health_future is not None and health_future.done():
                    try:
                        health_future.result().raise_for_status()
                    except httpx.HTTPError:
                        pass  # HEALTH is best effort, never a controller ACK.
                    health_future = None
                if live_future is not None and live_future.done():
                    try:
                        live_future.result()
                    except httpx.HTTPError:
                        pass  # live weight is best effort; HEALTH and WEIGHT events stay authoritative
                    live_future = None
                if latest_live is not None and live_future is None:
                    live_future = workers.submit(health_client.post, '/devices/sensor-1/live',
                                                 json=latest_live, headers={'X-Device-Key': key})
                    latest_live = None
                if latest_health is not None and health_future is None:
                    health_future = workers.submit(health_client.post, '/devices/sensor-1/events',
                                                  json=latest_health, headers={'X-Device-Key': key})
                    latest_health = None
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
