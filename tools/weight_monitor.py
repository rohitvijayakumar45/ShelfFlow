"""Live ADC dashboard and guarded calibration. Never opens COM3 directly.

Run: python -m tools.weight_monitor, then open http://127.0.0.1:8767
"""
import argparse
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
QC_RUNNING = {'EMPTY1', 'ON_A', 'ON_C', 'CENTRE', 'EMPTY2'}


class Samples:
    def __init__(self, path):
        self.path = path
        self.offset = 0
        self.signature = None
        self.rows = deque(maxlen=1800)
        self.weight = {}
        self.weight_time = 0
        self.last_raw_time = 0
        self.last_raw_ms = None

    def read(self):
        try:
            stat = self.path.stat()
            signature = (stat.st_ino, stat.st_ctime_ns)
            if signature != self.signature or stat.st_size < self.offset:
                self.offset = 0
                self.signature = signature
            with self.path.open('rb') as source:
                source.seek(self.offset)
                while True:
                    start = source.tell()
                    line = source.readline()
                    if not line or not line.endswith(b'\n'):
                        self.offset = start
                        break
                    self.offset = source.tell()
                    try:
                        item = json.loads(line)
                        is_raw = item['line'].startswith('RAW ')
                        if not is_raw and not item['line'].startswith('WEIGH '):
                            continue
                        fields = dict(re.findall(r'(\w+)=([^ ]+)', item['line']))
                        values = {key: float(value) for key, value in fields.items()}
                        timestamp = float(item['received_s'])
                        if is_raw:
                            # Do not graph a reboot or an earlier cell profile
                            # as a physical ADC jump within one measurement session.
                            if self.last_raw_ms is None or values['sample_ms'] < self.last_raw_ms:
                                self.rows.clear()
                                self.weight = {}
                                self.weight_time = 0
                            self.last_raw_ms = values['sample_ms']
                            inherited = self.weight if timestamp-self.weight_time < 2 else {}
                            self.rows.append({**inherited, **values, 'time': timestamp})
                            self.last_raw_time = timestamp
                        else:
                            self.weight = {k: v for k, v in values.items() if not k.startswith('raw_')}
                            self.weight_time = timestamp
                            if self.rows and timestamp-self.last_raw_time < 1:
                                self.rows[-1].update(self.weight)
                            else:  # Compatibility with firmware that prints no RAW lines.
                                self.rows.append({'time': timestamp, **values})
                    except (ValueError, KeyError, TypeError):
                        continue
        except FileNotFoundError:
            pass
        return list(self.rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8767)
    args = parser.parse_args()
    samples = Samples(ROOT / 'data/integration/usb/rfid-diagnostics.jsonl')
    page = Path(__file__).with_name('weight_monitor.html').read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path not in ('/api/zero', '/api/calibrate', '/api/qc'):
                self.send_error(404)
                return
            # Same-origin browser requests only; this local endpoint changes the reference.
            if (self.headers.get('Origin') != f'http://127.0.0.1:{args.port}'
                    or self.headers.get('Content-Type') != 'application/json'):
                self.send_error(403)
                return
            root = ROOT / 'data/integration/usb'
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length < 0 or length > 1024:
                    self.send_error(400)
                    return
                body = json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(body, dict):
                    self.send_error(400, 'Expected a JSON object')
                    return
                action = 'tareempty' if self.path == '/api/zero' else body.get('action')
                allowed = {'cal empty': {'OFF', 'HOME', 'SAVED'}, 'cal a': {'A'},
                           'cal c': {'C'}, 'cal save': {'VERIFY'},
                           'cal cancel': {'EMPTY', 'A', 'C', 'VERIFY', 'SAVED', 'HOME'}}
                if self.path == '/api/qc':
                    allowed = {'qc start': set(), 'qc next': set(), 'qc cancel': set()}
                if not isinstance(action, str) or (self.path != '/api/zero' and action not in allowed):
                    self.send_error(400, 'Unknown calibration action')
                    return
                status = json.loads((root / 'status.json').read_text())
                if not status.get('connected') or time.time()-status.get('last_seen', 0)>10:
                    self.send_error(503, 'Controller is not connected')
                    return
                state = status['controller']
                calibration = state.get('calibration_ui', {})
                step = calibration.get('step', 'OFF')
                if action != 'tareempty' and state.get('active_cells') != ['A', 'C']:
                    self.send_error(409, 'Two-cell A/C firmware must be uploaded first')
                    return
                quick = state.get('quick_check', {}).get('step', 'OFF')
                phase = state.get('quick_check', {}).get('phase')
                running = quick in QC_RUNNING
                if self.path == '/api/qc':
                    if ((action == 'qc start' and (running or step != 'OFF'))
                            or (action == 'qc next' and not (running and phase == 'WAIT_MOVE'))
                            or (action == 'qc cancel' and not running)):
                        self.send_error(409, 'Wrong quick-check step')
                        return
                elif running:
                    self.send_error(409, 'A quick check is running')
                    return
                if (state.get('tare_pending') or
                        (calibration.get('pending') and action != 'cal cancel')):
                    self.send_error(409, 'A zero request is already pending')
                    return
                if ((self.path == '/api/calibrate' and step not in allowed[action])
                        or (action == 'tareempty' and step != 'OFF')):
                    self.send_error(409, 'Wrong calibration step')
                    return
                request = {'action': action, 'created_s': time.time(),
                           'boot_id': state['boot_id']}
                with (root / 'monitor-zero-request.json').open('x') as output:
                    json.dump(request, output)
            except FileExistsError:
                self.send_error(409, 'A zero request is already queued')
                return
            except (OSError, ValueError, KeyError):
                self.send_error(503, 'Cannot reach the controller bridge')
                return
            body = json.dumps({'tare_count': state.get('tare_count', 0),
                               'boot_id': state['boot_id']}).encode()
            self.send_response(202)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = urlsplit(self.path).path
            if route == '/api/readings':
                rows = samples.read()
                try:
                    status = json.loads((ROOT / 'data/integration/usb/status.json').read_text())
                except (OSError, ValueError):
                    status = {}
                body = json.dumps({'now': time.time(), 'samples': rows,
                                   'connected': status.get('connected', False),
                                   'controller': status.get('controller', {})}).encode()
                content_type = 'application/json'
            elif route == '/':
                body, content_type = page, 'text/html; charset=utf-8'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    print(f'Weight monitor: http://127.0.0.1:{args.port} (COM3 stays with bridge)', flush=True)
    HTTPServer(('127.0.0.1', args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
