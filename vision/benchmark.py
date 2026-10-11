import argparse
import time
import json
import statistics
from pathlib import Path
import cv2
import numpy as np
from vision.capture import Capture

def percentiles(values):
    return {f"p{p}": float(np.percentile(values, p)) if values else None for p in (50, 95, 99)}

def benchmark(url, key, mode, frames, output):
    capture = Capture(url, key, mode)
    records, errors = [], []
    previous = None
    for i in range(frames):
        switch_ms = 0
        try:
            if mode == "B" and i % 20 in (0, 5):
                start = time.monotonic()
                capture.high_resolution(i % 20 == 0)
                switch_ms = (time.monotonic() - start) * 1000
            f = capture.poll()
            img = cv2.imdecode(np.frombuffer(f.jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
            gap = 0 if previous is None else (f.received_monotonic - previous) * 1000
            previous = f.received_monotonic
            records.append({"upload_ms": f.upload_ms, "age_ms": (f.received_wall - f.capture_wall) * 1000, "gap_ms": gap, "switch_ms": switch_ms, "blur_laplacian": float(cv2.Laplacian(img, cv2.CV_64F).var()), "width": f.width, "height": f.height, "sequence": f.sequence, "clock_uncertainty_ms": f.clock_uncertainty_ms})
        except Exception as exc:
            errors.append(type(exc).__name__)
    result = {"mode": mode, "hardware_verified": False, "measurement_source":url, "frames": records, "errors": errors, "latency": {k: percentiles([r[k] for r in records]) for k in ("upload_ms", "age_ms", "gap_ms", "switch_ms")}, "recommendation": "Transport benchmark only; inspect actual hardware and compare ownership-visible frames and gaps across A/B. No automatic winner."}
    Path(output).write_text(json.dumps(result, indent=2))
    capture.client.close()

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("url");p.add_argument("--key", required=True);p.add_argument("--mode", choices=['A','B'], default='A');p.add_argument("--frames",type=int,default=200);p.add_argument("--output",default='reports/capture.json')
    a=p.parse_args();benchmark(a.url,a.key,a.mode,a.frames,a.output)
