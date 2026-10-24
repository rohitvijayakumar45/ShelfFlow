"""Capture empty-tray frames as hard negatives for the item detector.

Frames come from the local camera proxy (tools.camera_preview, port 8765) every --interval
seconds. Each frame gets an empty YOLO label file: "nothing to detect here". Wires, tray edges,
shadows and reflections the detector mistakes for items are exactly what these teach it to ignore.

Run with the tray EMPTY: python -m tools.dataset.capture_negatives --count 60
"""
import argparse
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--proxy', default='http://127.0.0.1:8765')
    parser.add_argument('--count', type=int, default=60)
    parser.add_argument('--interval', type=float, default=2.0)
    parser.add_argument('--output', default=str(ROOT / 'data/shelf-pilot/negatives' / time.strftime('empty-%Y%m%d-%H%M%S')))
    args = parser.parse_args()
    images, labels = Path(args.output) / 'images', Path(args.output) / 'labels'
    images.mkdir(parents=True, exist_ok=True); labels.mkdir(parents=True, exist_ok=True)
    saved, last = 0, b''
    with httpx.Client(timeout=5, trust_env=False) as client:
        while saved < args.count:
            try:
                jpeg = client.get(args.proxy.rstrip('/') + '/jpeg').content
            except httpx.HTTPError as exc:
                print('waiting for camera:', type(exc).__name__, flush=True); time.sleep(args.interval); continue
            if jpeg.startswith(b'\xff\xd8') and jpeg != last:
                name = f'empty-{time.strftime("%Y%m%d-%H%M%S")}-{saved:03d}'
                (images / f'{name}.jpg').write_bytes(jpeg)
                (labels / f'{name}.txt').write_text('')
                saved, last = saved + 1, jpeg
                print(f'{saved}/{args.count} {name}.jpg', flush=True)
            time.sleep(args.interval)
    print('saved', saved, 'frames to', args.output)


if __name__ == '__main__':
    main()
