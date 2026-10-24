"""Observe RFID tests through the live USB bridge; direct serial is opt-in for bench use."""
import argparse
import json
from pathlib import Path
import re
import time

import serial

EVENT = re.compile(r'DIAG RFID reader=(R[12]) uid=([0-9A-F]+) captured_ms=(\d+) dispatch_ms=(\d+)')
QUEUE = re.compile(r'RFID_QUEUE durable=(\d+)/(\d+) ram=(\d+) scan_gap_max_ms=(\d+)/(\d+) historical_overflow=(\d+)')
ERRORS = ('RFID_BUFFER_OVERFLOW', 'RFID_PERSIST_FAILED', 'QUEUE_OVERFLOW', 'QUEUE_WRITE_FAILED', 'RFID_TASK_FAILED')


def summarize(rows, expected):
    events, queues, errors = [], [], []
    for row in rows:
        line = row['line']
        match = EVENT.search(line)
        if match:
            events.append({'received_s': row['received_s'], 'reader': match[1],
                           'uid': match[2], 'captured_ms': int(match[3]), 'dispatch_ms': int(match[4])})
        match = QUEUE.search(line)
        if match:
            queues.append(dict(zip(('durable', 'capacity', 'ram', 'r1_scan_gap_max_ms',
                                    'r2_scan_gap_max_ms', 'historical_overflow'), map(int, match.groups()))))
        if any(error in line for error in ERRORS):
            errors.append(row)
    counts = {r: sum(e['reader'] == r for e in events) for r in ('R1', 'R2')}
    matching = counts == expected
    baseline = queues[0]['historical_overflow'] if queues else None
    healthy = bool(queues) and (baseline == 0 or len(queues) >= 2) and all(
        q['historical_overflow'] == baseline for q in queues)
    return {'expected_taps': expected, 'captured_taps': counts, 'count_matches': matching,
            'passed': matching and healthy and not errors, 'events': events, 'errors': errors,
            'queue_samples': queues,
            'historical_loss_baseline': baseline,
            'new_loss_count': queues[-1]['historical_overflow'] - baseline if queues else None,
            'max_dispatch_ms': max((e['dispatch_ms'] for e in events), default=None),
            'scope': 'Counts physical taps performed by the operator; dispatch time starts after RF decoding. '
                     'Not a guarantee against all missed taps, power loss or untested RF conditions.'}


def follow_bridge(path, seconds):
    """Read only new diagnostics; the bridge retains exclusive ownership of COM3."""
    offset = path.stat().st_size if path.exists() else 0
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        if path.exists():
            if path.stat().st_size < offset:
                offset = 0  # Bridge log rotated.
            with path.open('rb') as source:
                source.seek(offset)
                while True:
                    before = source.tell()
                    line = source.readline()
                    if not line.endswith(b'\n'):
                        source.seek(before)
                        break
                    offset = source.tell()
                    try:
                        row = json.loads(line)
                        yield {'received_s': round(time.monotonic() - started, 3), 'line': row['line']}
                    except (ValueError, KeyError):
                        continue
        time.sleep(.05)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='COM3')
    parser.add_argument('--source', choices=['bridge', 'serial'], default='bridge')
    parser.add_argument('--bridge-root', type=Path, default=Path('data/integration/usb'))
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--expect-r1', type=int, default=7)
    parser.add_argument('--expect-r2', type=int, default=7)
    parser.add_argument('--output', type=Path, default=Path('data/hardware-backups/rfid-seven-item-test.json'))
    args = parser.parse_args()
    connection = None
    rows = []
    started = time.monotonic()
    try:
        if args.source == 'bridge':
            status_path = args.bridge_root / 'status.json'
            status = json.loads(status_path.read_text()) if status_path.exists() else {}
            if not status.get('connected') or time.time() - status.get('last_seen', 0) > 15:
                raise RuntimeError('Start tools.usb_bridge first; no fresh controller connection was found.')
            print('Recording through live USB bridge; COM3 ownership and reader mode unchanged.', flush=True)
            for row in follow_bridge(args.bridge_root / 'rfid-diagnostics.jsonl', args.seconds):
                rows.append(row)
                if EVENT.search(row['line']) or any(error in row['line'] for error in ERRORS):
                    print(row['line'], flush=True)
        else:
            connection = serial.Serial(port=None, baudrate=115200, timeout=.2)
            connection.dtr = connection.rts = False
            connection.port = args.port
            connection.open()
            connection.reset_input_buffer()
            # Explicit bench mode only. Normal app tests never stop forwarding or change antenna mode.
            connection.write(b'\nboth\ncalstatus\n')
        pending = b''
        while connection is not None and time.monotonic() - started < args.seconds:
            pending += connection.read(min(max(connection.in_waiting, 1), 4096))
            while b'\n' in pending:
                raw, pending = pending.split(b'\n', 1)
                line = raw.decode(errors='replace').strip()
                if line and len(rows) < 10000:
                    rows.append({'received_s': round(time.monotonic() - started, 3), 'line': line})
                    if EVENT.search(line) or any(error in line for error in ERRORS):
                        print(line, flush=True)
            pending = pending[-4096:]
    finally:
        if connection is not None:
            connection.close()
        report = summarize(rows, {'R1': args.expect_r1, 'R2': args.expect_r2})
        report['serial_rows'] = rows
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('captured_taps', 'count_matches', 'passed', 'max_dispatch_ms')}, indent=2))


if __name__ == '__main__':
    main()
