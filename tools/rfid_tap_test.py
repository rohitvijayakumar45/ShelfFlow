"""Record physical RFID tap tests without resetting or recalibrating the controller."""
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
    healthy = bool(queues) and all(q['historical_overflow'] == 0 for q in queues)
    return {'expected_taps': expected, 'captured_taps': counts, 'count_matches': matching,
            'passed': matching and healthy and not errors, 'events': events, 'errors': errors,
            'queue_samples': queues,
            'max_dispatch_ms': max((e['dispatch_ms'] for e in events), default=None),
            'scope': 'Counts physical taps performed by the operator; dispatch time starts after RF decoding. '
                     'Not a guarantee against all missed taps, power loss or untested RF conditions.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='COM3')
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--expect-r1', type=int, default=7)
    parser.add_argument('--expect-r2', type=int, default=7)
    parser.add_argument('--output', type=Path, default=Path('data/hardware-backups/rfid-seven-item-test.json'))
    args = parser.parse_args()
    connection = serial.Serial(port=None, baudrate=115200, timeout=.2)
    connection.dtr = False
    connection.rts = False
    connection.port = args.port
    rows = []
    started = time.monotonic()
    try:
        connection.open()
        connection.reset_input_buffer()
        connection.write(b'\nboth\ncalstatus\n')
        pending = b''
        while time.monotonic() - started < args.seconds:
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
        connection.close()
        report = summarize(rows, {'R1': args.expect_r1, 'R2': args.expect_r2})
        report['serial_rows'] = rows
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('captured_taps', 'count_matches', 'passed', 'max_dispatch_ms')}, indent=2))


if __name__ == '__main__':
    main()
