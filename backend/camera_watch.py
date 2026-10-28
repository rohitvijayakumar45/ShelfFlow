"""Recent camera history for the RFID-attributed shelf cart.

A background thread asks the detector (CAMERA_URL /status) what it sees twice a second and keeps
the last minute of per-product counts. When the shelf weight steps, `change` compares what the
camera saw before the hand came in with what it sees now: the product whose count dropped was
picked up, the one whose count rose was put back. Bracelet bands are never products.
"""
import os
import threading
import time
from collections import Counter, deque

BEFORE_S = (20.0, 3.0)  # "before": frames 20 s to 3 s before the weight step (the hand is often in view later)
AFTER_S = 1.5           # "after": frames from the last 1.5 s (the weight has settled, the hand has left)
_frames: deque = deque(maxlen=200)  # (time, Counter of products) for fresh frames only
_lock = threading.Lock()
_stop = threading.Event()


def record(products, at=None):
    """Store one fresh frame's detections (product ids; None/'' count as UNKNOWN)."""
    counts = Counter(p or "UNKNOWN" for p in products if not str(p or "").startswith("band-"))
    with _lock:
        _frames.append((time.time() if at is None else at, counts))


def clear():
    with _lock:
        _frames.clear()


def change(now=None, since=None, before_end=None):
    """{product: count after - count before} for products whose count changed, or None when the camera
    has no frames from both before the step and now (camera offline or just started). `since` (the
    previous weight step) bounds "before", so an earlier pickup is not mistaken for this one; `before_end`
    (just before the shopper's bracelet appeared) ends it, so the hand never hides the item in "before"."""
    now = time.time() if now is None else now
    start = max(now - BEFORE_S[0], since or 0)
    end = now - BEFORE_S[1] if before_end is None else min(now - BEFORE_S[1], before_end)
    with _lock:
        frames = list(_frames)
    before = [c for t, c in frames if start <= t <= end]
    after = [c for t, c in frames if now - AFTER_S <= t <= now + 1]
    if not before or not after:
        return None
    # Per product the most seen in any one frame: a single missed detection does not count as a pickup.
    most = lambda counts: {p: max(c[p] for c in counts) for p in set().union(*counts)}
    b, a = most(before), most(after)
    return {p: a.get(p, 0) - b.get(p, 0) for p in set(a) | set(b) if a.get(p, 0) != b.get(p, 0)}


def _poll():
    import httpx
    url = os.getenv("CAMERA_URL", "http://127.0.0.1:8766").rstrip("/") + "/status"
    with httpx.Client(timeout=0.4, trust_env=False) as client:
        while not _stop.wait(0.5):
            try:
                status = client.get(url).json()
            except (httpx.HTTPError, ValueError):
                continue
            age = status.get("frame_age_s")
            if status.get("connected") is True and age is not None and age <= 2:
                record([p.get("product") for p in status.get("predictions", [])])


def start():
    _stop.clear()
    threading.Thread(target=_poll, name="camera-watch", daemon=True).start()


def stop():
    _stop.set()
