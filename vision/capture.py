"""JPEG polling transport. Capture clock and arrival clock remain distinct."""
import time
import json
import threading
from dataclasses import dataclass
from collections import deque
from pathlib import Path
import httpx

@dataclass
class Frame:
    jpeg: bytes
    sequence: int
    boot_id: str
    capture_us: int
    received_wall: float
    received_monotonic: float
    upload_ms: float
    width: int
    height: int
    clock_uncertainty_ms: float
    capture_wall: float | None

class RollingBuffer:
    def __init__(self, max_frames=120, max_bytes=64 * 1024 * 1024):
        self.frames = deque()
        self.max_frames, self.max_bytes = max_frames, max_bytes
        self.size = 0
        self.lock = threading.Lock()
        self.dropped = 0

    def append(self, frame):
        with self.lock:
            self.frames.append(frame)
            self.size += len(frame.jpeg)
            while len(self.frames) > self.max_frames or self.size > self.max_bytes:
                self.size -= len(self.frames.popleft().jpeg)
                self.dropped += 1

    def preserve(self, root, event_id, start_monotonic, end_monotonic):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        with self.lock:
            selected = [f for f in self.frames if start_monotonic <= f.received_monotonic <= end_monotonic]
        refs = []
        for f in selected:
            name = f"{event_id}-{f.boot_id}-{f.sequence}.jpg"
            (root / name).write_bytes(f.jpeg)  # Original JPEG: no recompression.
            metadata = {key: value for key, value in vars(f).items() if key != "jpeg"}
            (root / name.replace('.jpg', '.json')).write_text(json.dumps(metadata), encoding="utf-8")
            refs.append(name)
        return refs

class Capture:
    def __init__(self, url, token, mode="A", buffer=None):
        self.url, self.token, self.mode = url.rstrip('/'), token, mode
        self.buffer = buffer or RollingBuffer()
        self.client = httpx.Client(timeout=3)
        self.last = None
        self.last_capture = None
        self.offset_us = None
        self.uncertainty_ms = float('inf')
        self.failures = 0

    def sync(self):
        a = time.time()
        r = self.client.get(self.url + "/clock", headers={"X-Camera-Key": self.token})
        r.raise_for_status()
        b = time.time()
        clock = r.json()
        self.offset_us = (a + b) * 500000 - clock["monotonic_us"]
        self.uncertainty_ms = (b - a) * 500 + 10
        self.clock_boot = clock["boot_id"]

    def high_resolution(self, enabled):
        if self.mode == "B":
            r = self.client.post(self.url + "/mode", headers={"X-Camera-Key": self.token}, params={"high": int(enabled)})
            r.raise_for_status()

    def poll(self):
        start = time.monotonic()
        r = self.client.get(self.url + "/jpeg", headers={"X-Camera-Key": self.token})
        r.raise_for_status()
        if not r.headers.get('content-type','').startswith('image/jpeg') or not r.content.startswith(b'\xff\xd8'):
            raise ValueError('Camera response is not JPEG transport')
        end, wall = time.monotonic(), time.time()
        seq, boot = int(r.headers["X-Frame-Sequence"]), r.headers["X-Boot-Id"]
        capture_us = int(r.headers["X-Capture-Us"])
        if self.last_capture and self.last_capture[0]==boot and capture_us<=self.last_capture[1]:
            raise ValueError('Repeated or out-of-order capture timestamp despite new transport sequence')
        if self.last == (boot, seq):
            raise ValueError("Stale repeated frame")
        if self.offset_us is None or getattr(self, 'clock_boot', None) != boot:
            self.sync()
        if self.clock_boot!=boot:
            raise ValueError('Camera rebooted between frame and clock sample')
        self.last = (boot, seq)
        frame = Frame(r.content, seq, boot, capture_us, wall, end, (end - start) * 1000, int(r.headers["X-Width"]), int(r.headers["X-Height"]), self.uncertainty_ms, (capture_us + self.offset_us) / 1e6)
        if wall - frame.capture_wall > 2:
            raise ValueError("Frame too old; check camera queue and timing")
        self.last_capture=(boot,capture_us)
        self.buffer.append(frame)
        return frame
