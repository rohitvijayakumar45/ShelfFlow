"""Record original JPEGs and capture headers from the shared local preview."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import httpx
from vision.capture import MjpegStream


def record(source, output, session, scenario, seconds=60, fps=3):
    if seconds <= 0 or not 0 < fps <= 20:
        raise ValueError('Recording duration and sampling rate must be positive (FPS <= 20)')
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use an empty recording folder to avoid mixing clips')
    output.mkdir(parents=True, exist_ok=True)
    metadata = {'session_id': session, 'scenario': scenario, 'camera': 'OV3660',
                'started_utc': datetime.now(timezone.utc).isoformat(), 'original_jpeg': True,
                'source': source, 'sample_fps_limit': fps, 'requested_seconds': seconds,
                'timing': 'Source capture timestamps and receiver times; no cross-device clock calibration',
                'annotation_status': 'UNLABELLED', 'frames': [], 'errors': []}
    started = time.monotonic()
    last_saved = started - 1 / fps
    seen = set()
    stream = None
    try:
        with httpx.Client(trust_env=False) as client:
            while time.monotonic() - started < seconds:
                try:
                    if stream is None:
                        stream = MjpegStream(client, source, '', timeout=3)
                    headers, jpeg = stream.next_part()
                    now = time.monotonic()
                    if now - started >= seconds:
                        break
                    identity = (headers['x-boot-id'], int(headers['x-frame-sequence']))
                    if identity in seen or now - last_saved < 1 / fps:
                        continue
                    if not jpeg.startswith(b'\xff\xd8'):
                        raise ValueError('Invalid JPEG transport')
                    # Do not turn upstream metadata into a filesystem path.
                    import hashlib
                    boot_filename = hashlib.sha256(identity[0].encode()).hexdigest()[:12]
                    filename = f'{boot_filename}-{identity[1]:08}.jpg'
                    (output / filename).write_bytes(jpeg)
                    metadata['frames'].append({'file': filename, 'boot_id': identity[0],
                        'sequence': identity[1], 'capture_us': int(headers['x-capture-us']),
                        'width': int(headers['x-width']), 'height': int(headers['x-height']),
                        'receive_elapsed_s': round(now - started, 4), 'bytes': len(jpeg)})
                    seen.add(identity)
                    last_saved = now
                except (httpx.HTTPError, OSError, ConnectionError, ValueError, KeyError) as error:
                    metadata['errors'].append({'elapsed_s': round(time.monotonic() - started, 3),
                                               'type': type(error).__name__})
                    if stream:
                        stream.close()
                        stream = None
                    time.sleep(.2)
    finally:
        if stream:
            stream.close()
        metadata['elapsed_s'] = round(time.monotonic() - started, 3)
        (output / 'session.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='http://127.0.0.1:8765/stream')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--scenario', required=True)
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--fps', type=float, default=3)
    args = parser.parse_args()
    result = record(args.source, args.output, args.session, args.scenario, args.seconds, args.fps)
    print(json.dumps({'saved_frames': len(result['frames']), 'errors': len(result['errors']),
                      'output': str(args.output.resolve()), 'annotation_status': 'UNLABELLED'}), flush=True)


if __name__ == '__main__':
    main()
