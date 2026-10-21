"""Compare camera clock/quality settings through the shared local preview."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import statistics
import time

import httpx
from vision.capture import MjpegStream


PROFILES = [(20, 12, 10), (16, 12, 10), (16, 20, 10),
            (20, 20, 10), (20, 20, 20), (16, 20, 20), (20, 12, 10)]


def sync_clock(client, base, key):
    samples = []
    for _ in range(3):
        try:
            before = time.monotonic()
            response = client.get(base + '/clock', headers={'X-Camera-Key': key})
            response.raise_for_status()
            after = time.monotonic()
            clock = response.json()
            samples.append({'offset_s': (before + after) / 2 - clock['monotonic_us'] / 1e6,
                            'uncertainty_s': (after - before) / 2,
                            'boot_id': clock['boot_id']})
        except (httpx.HTTPError, ValueError, KeyError):
            continue
    return min(samples, key=lambda s: s['uncertainty_s']) if samples else None


def frame_age_ms(clock, headers, received):
    if not clock or headers.get('x-boot-id') != clock['boot_id']:
        return None
    estimate = 1000 * (received - clock['offset_s'] - int(headers['x-capture-us']) / 1e6)
    # Preserve uncertainty; a negative estimate is not evidence of zero latency.
    return round(estimate, 2)


def summarize(records, elapsed, max_gap):
    ages = sorted(r['age_ms'] for r in records if r['age_ms'] is not None)
    return {'received_frames': len(records), 'elapsed_s': round(elapsed, 2),
            'fps': round(len(records) / elapsed, 2), 'max_gap_s': round(max_gap, 2),
            'mean_jpeg_bytes': round(statistics.mean(r['bytes'] for r in records)) if records else None,
            'median_age_ms': round(statistics.median(ages), 2) if ages else None,
            'p95_age_ms': ages[max(0, (95 * len(ages) + 99) // 100 - 1)] if ages else None}


def measure(client, preview, clock, duration, expected_size=None, snapshot=None):
    started = time.monotonic()
    until = started + duration
    records, errors = [], []
    stream = None
    previous = started
    max_gap = 0
    seen = set()
    last_jpeg = None
    try:
        while time.monotonic() < until:
            try:
                if stream is None:
                    stream = MjpegStream(client, preview + '/stream', '', timeout=3)
                headers, jpeg = stream.next_part()
                now = time.monotonic()
                if now > until:
                    break
                identity = (headers['x-boot-id'], headers['x-frame-sequence'])
                if identity in seen:
                    continue
                if not jpeg.startswith(b'\xff\xd8'):
                    raise ValueError('Invalid JPEG markers')
                size = (int(headers['x-width']), int(headers['x-height']))
                if expected_size and size != expected_size:
                    continue  # Old in-flight frames do not count toward the new resolution.
                seen.add(identity)
                last_jpeg = jpeg
                max_gap = max(max_gap, now - previous)
                previous = now
                records.append({'sequence': identity[1], 'boot_id': identity[0],
                                'received_s': round(now - started, 3), 'bytes': len(jpeg),
                                'width': int(headers['x-width']), 'height': int(headers['x-height']),
                                'age_ms': frame_age_ms(clock, headers, now)})
            except (httpx.HTTPError, ConnectionError, OSError, ValueError, KeyError) as error:
                errors.append(type(error).__name__)
                if stream:
                    stream.close()
                    stream = None
                time.sleep(min(.25, max(0, until - time.monotonic())))
    finally:
        if stream:
            stream.close()
    max_gap = max(max_gap, until - previous)
    result = summarize(records, duration, max_gap)
    result.update(records=records, errors=errors, clock=clock)
    if snapshot and last_jpeg:
        snapshot.write_bytes(last_jpeg)
        result['snapshot'] = str(snapshot)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--camera', required=True)
    parser.add_argument('--preview', default='http://127.0.0.1:8765')
    parser.add_argument('--duration', type=float, default=30)
    parser.add_argument('--clocks', type=int, nargs='+', choices=[8, 16, 20],
                        help='Compare these clocks in order, holding quality and FPS limit fixed')
    parser.add_argument('--quality', type=int, choices=range(10, 31), default=12)
    parser.add_argument('--fps-limit', type=int, choices=range(5, 26), default=10)
    parser.add_argument('--resolutions', nargs='+', choices=['320x240', '480x320', '640x480', '800x600'],
                        help='Compare sizes at a fixed quality/FPS limit; defaults to the 8 MHz clock')
    parser.add_argument('--secrets', type=Path, default=Path('firmware/camera/include/secrets.h'))
    parser.add_argument('--output', type=Path, default=Path('data/hardware-backups/camera-benchmark.json'))
    args = parser.parse_args()
    if args.duration < 5:
        parser.error('Use a measurement window of at least five seconds')
    key = re.search(r'^\s*#define\s+CAMERA_KEY\s+"([^"\r\n]+)"',
                    args.secrets.read_text(), re.MULTILINE).group(1)
    report = {'started_utc': datetime.now(timezone.utc).isoformat(), 'runs': [],
              'notes': 'Age estimates cover capture to local receiver, not screen rendering. '
                       'Clock uncertainty is half the best sampled RTT; asymmetric paths add error. '
                       'FPS windows include reconnects. All runs use a 500ms socket enqueue deadline.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=5, trust_env=False) as client:
        profiles = [(clock, args.quality, args.fps_limit) for clock in args.clocks] if args.clocks else PROFILES
        runs = [(clock, args.quality, args.fps_limit, resolution)
                for clock in (args.clocks or [8]) for resolution in args.resolutions] if args.resolutions else [
                    (clock, quality, fps, None) for clock, quality, fps in profiles]
        for index, (clock, quality, fps, resolution) in enumerate(runs):
            profile = {'clock_mhz': clock, 'quality': quality, 'fps': fps}
            if resolution:
                profile['resolution'] = resolution
            run = {'profile': profile}
            try:
                response = client.post(args.camera.rstrip('/') + '/tune', params=profile,
                                       headers={'X-Camera-Key': key})
                response.raise_for_status()
                time.sleep(2)
                clock_sample = sync_clock(client, args.camera.rstrip('/'), key)
                expected = tuple(map(int, resolution.split('x'))) if resolution else None
                snapshot = args.output.with_name(args.output.stem + f'-{index}-{resolution}.jpg') if resolution else None
                run.update(measure(client, args.preview.rstrip('/'), clock_sample, args.duration, expected, snapshot))
            except (httpx.HTTPError, ValueError) as error:
                run['control_error'] = type(error).__name__
            report['runs'].append(run)
            args.output.write_text(json.dumps(report, indent=2))
            print(json.dumps({k: v for k, v in run.items() if k not in ('records', 'errors')}), flush=True)
    print('Results saved to ' + str(args.output), flush=True)


if __name__ == '__main__':
    main()
