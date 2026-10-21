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
    # Per-frame vision output (boxes, hands, markers) kept with the evidence for the review player.
    annotations: dict | None = None

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

    def preserve(self, root, event_id, start_monotonic, end_monotonic, redact=None):
        """Write the frames of one collection window. redact(jpeg) -> (jpeg, faces) blurs faces;
        frames without a face stay the original bytes (no recompression)."""
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        with self.lock:
            selected = [f for f in self.frames if start_monotonic <= f.received_monotonic <= end_monotonic]
        refs = []
        for f in selected:
            name = f"{event_id}-{f.boot_id}-{f.sequence}.jpg"
            jpeg, faces = redact(f.jpeg) if redact else (f.jpeg, 0)
            (root / name).write_bytes(jpeg)
            metadata = {key: value for key, value in vars(f).items() if key != "jpeg"}
            metadata["faces_redacted"] = faces
            metadata["original_bytes"] = faces == 0
            (root / name.replace('.jpg', '.json')).write_text(json.dumps(metadata), encoding="utf-8")
            refs.append(name)
        return refs

class Capture:
    def __init__(self, url, token, mode="A", buffer=None, transport="poll", stream_url=None):
        if transport not in ("poll", "stream"):
            raise ValueError("transport must be poll or stream")
        self.url, self.token, self.mode, self.transport = url.rstrip('/'), token, mode, transport
        parts = httpx.URL(self.url)
        self.stream_url = stream_url or str(parts.copy_with(port=81, path="/stream"))
        self.buffer = buffer or RollingBuffer()
        self.client = httpx.Client(timeout=3)
        self.stream = None
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

    def _fetch_poll(self):
        r = self.client.get(self.url + "/jpeg", headers={"X-Camera-Key": self.token})
        r.raise_for_status()
        if not r.headers.get('content-type','').startswith('image/jpeg'):
            raise ValueError('Camera response is not JPEG transport')
        return r.headers, r.content

    def _fetch_stream(self):
        if self.stream is None:
            self.stream = MjpegStream(self.client, self.stream_url, self.token)
        try:
            return self.stream.next_part()
        except Exception:
            self.close_stream()  # reconnect on the next poll; the frame is reported as a failure
            raise

    def close_stream(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None

    def close(self):
        self.close_stream()
        self.client.close()

    def poll(self):
        start = time.monotonic()
        headers, content = self._fetch_stream() if self.transport == "stream" else self._fetch_poll()
        if not content.startswith(b'\xff\xd8'):
            raise ValueError('Camera response is not JPEG transport')
        end, wall = time.monotonic(), time.time()
        seq, boot = int(headers["X-Frame-Sequence"]), headers["X-Boot-Id"]
        capture_us = int(headers["X-Capture-Us"])
        if self.last_capture and self.last_capture[0]==boot and capture_us<=self.last_capture[1]:
            raise ValueError('Repeated or out-of-order capture timestamp despite new transport sequence')
        if self.last == (boot, seq):
            raise ValueError("Stale repeated frame")
        if self.offset_us is None or getattr(self, 'clock_boot', None) != boot:
            self.sync()
        if self.clock_boot!=boot:
            raise ValueError('Camera rebooted between frame and clock sample')
        self.last = (boot, seq)
        # For the stream transport, upload_ms is the wait for the next part, not a full request.
        frame = Frame(content, seq, boot, capture_us, wall, end, (end - start) * 1000, int(headers["X-Width"]), int(headers["X-Height"]), self.uncertainty_ms, (capture_us + self.offset_us) / 1e6)
        if wall - frame.capture_wall > 2:
            raise ValueError("Frame too old; check camera queue and timing")
        self.last_capture=(boot,capture_us)
        self.buffer.append(frame)
        return frame

class MjpegStream:
    """Reads multipart/x-mixed-replace JPEG parts with per-part ShelfFlow headers."""
    def __init__(self, client, url, token, max_part=4 * 1024 * 1024, timeout=3):
        self.max_part = max_part
        self.context = client.stream("GET", url, headers={"X-Camera-Key": token}, timeout=httpx.Timeout(timeout))
        self.response = self.context.__enter__()
        self.response.raise_for_status()
        if not self.response.headers.get("content-type", "").startswith("multipart/x-mixed-replace"):
            self.close()
            raise ValueError("Camera stream is not multipart JPEG")
        self.chunks = self.response.iter_raw()
        self.data = bytearray()

    def _fill(self):
        chunk = next(self.chunks, None)
        if chunk is None:
            raise ConnectionError("Camera stream ended")
        self.data += chunk
        if len(self.data) > self.max_part * 2:
            raise ValueError("Camera stream part exceeds buffer limit")

    BOUNDARY = b"--sfframe\r\n"

    def next_part(self):
        while (boundary := self.data.find(self.BOUNDARY)) < 0:
            self._fill()
        del self.data[:boundary + len(self.BOUNDARY)]
        while (split := self.data.find(b"\r\n\r\n")) < 0:
            self._fill()
        headers = {}
        for line in bytes(self.data[:split]).decode("latin-1").split("\r\n"):
            name, _, value = line.partition(":")
            headers[name.strip()] = value.strip()
        del self.data[:split + 4]
        length = int(headers["Content-Length"])
        if length <= 0 or length > self.max_part:
            raise ValueError("Invalid camera stream part length")
        while len(self.data) < length:
            self._fill()
        body = bytes(self.data[:length])
        del self.data[:length]
        return httpx.Headers(headers), body

    def close(self):
        try:
            self.context.__exit__(None, None, None)
        except Exception:
            pass
