"""Hands and motion proof: did a hand touch an item, and did the item leave (or arrive) with it?

Weight says THAT something left the tray and roughly what; the gallery says which product. Neither
says WHO. This module links the three per collection window:

  hand seen in the tray area  ->  hand box overlaps an item track  ->  that track leaves the tray
  (pickup) or appears under a hand (return)  ->  bracelet markers decoded next to that hand.

Hands come from the item detector itself when it was trained with a second class "hand"
(tools.dataset.cli make-yaml --classes item,hand), or from MediaPipe when installed.
The result is evidence for review and for fusion's chain checks; real-mode ownership stays
review-first (backend vetoes PHYSICAL_OWNERSHIP_NOT_VALIDATED).
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field

def centre(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)

def expand(box, factor):
    cx, cy = centre(box)
    w, h = (box[2] - box[0]) * factor / 2, (box[3] - box[1]) * factor / 2
    return (cx - w, cy - h, cx + w, cy + h)

def inside(point, box):
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]

def overlap(a, b):
    """Intersection over the smaller box: 1.0 when a small item sits fully inside a hand box."""
    iw = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return iw * ih / small if small > 0 else 0.0

def offset(box, origin):
    return (box[0] + origin[0], box[1] + origin[1], box[2] + origin[0], box[3] + origin[1])

class MediaPipeHands:
    """Optional fallback hand finder (pip install mediapipe). Boxes in input-image pixels."""

    def __init__(self, max_hands=4, confidence=0.5):
        import mediapipe as mp
        self.hands = mp.solutions.hands.Hands(static_image_mode=False, max_num_hands=max_hands, min_detection_confidence=confidence)

    def detect(self, image_bgr):
        h, w = image_bgr.shape[:2]
        result = self.hands.process(image_bgr[:, :, ::-1])
        boxes = []
        for landmarks in result.multi_hand_landmarks or []:
            xs = [p.x * w for p in landmarks.landmark]
            ys = [p.y * h for p in landmarks.landmark]
            boxes.append((min(xs), min(ys), max(xs), max(ys)))
        return boxes

@dataclass
class TrackStory:
    first: float
    last: float
    first_box: tuple
    last_box: tuple
    frames: int = 0
    contact_frames: int = 0
    contact_at_first: bool = False
    contact_at_last: bool = False
    markers: Counter = field(default_factory=Counter)
    refs: list = field(default_factory=list)

class MotionProof:
    """Accumulates one collection window. All coordinates are full-frame pixels."""

    def __init__(self, roi_px, start, contact=0.2, marker_reach=1.8, edge_px=6):
        self.roi, self.start = roi_px, start
        self.contact, self.marker_reach, self.edge_px = contact, marker_reach, edge_px
        self.tracks = {}
        self.hand_frames = 0
        self.frames = 0
        self.last_time = start
        self.hand_markers = Counter()

    def observe(self, t, items, hands, markers, ref=None):
        """items: [(track_id, box)], hands: [box], markers: [{"id", "corners"}]."""
        self.frames += 1
        self.last_time = t
        hands = [h for h in hands if inside(centre(h), expand(self.roi, 1.3))]
        if hands:
            self.hand_frames += 1
        near = defaultdict(set)  # hand index -> marker ids next to it (bracelet on that wrist)
        for i, hand in enumerate(hands):
            reach = expand(hand, self.marker_reach)
            for m in markers:
                xs = [p[0] for p in m["corners"]]
                ys = [p[1] for p in m["corners"]]
                if inside((sum(xs) / 4, sum(ys) / 4), reach):
                    near[i].add(m["id"])
                    self.hand_markers[m["id"]] += 1
        for track_id, box in items:
            if track_id is None:
                continue
            touching = [i for i, hand in enumerate(hands) if overlap(box, hand) >= self.contact]
            story = self.tracks.get(track_id)
            if story is None:
                story = self.tracks[track_id] = TrackStory(t, t, box, box, contact_at_first=bool(touching))
            story.last, story.last_box = t, box
            story.frames += 1
            story.contact_at_last = bool(touching)
            if touching:
                story.contact_frames += 1
                for i in touching:
                    story.markers.update(near[i])
                if ref and len(story.refs) < 30:
                    story.refs.append(ref)

    def _at_edge(self, box):
        x1, y1, x2, y2 = self.roi
        e = self.edge_px
        return box[0] <= x1 + e or box[1] <= y1 + e or box[2] >= x2 - e or box[3] >= y2 - e

    def summary(self, settle=0.4):
        """exited: seen, then gone before the window ended, last seen in a hand or at the tray edge.
        entered: first seen after the window started, arriving in a hand or from the edge, still there."""
        tracks = {}
        for track_id, s in self.tracks.items():
            gone = self.last_time - s.last > settle
            exited = gone and s.frames >= 2 and (s.contact_at_last or self._at_edge(s.last_box))
            entered = (s.first - self.start > settle and not gone and s.frames >= 2
                       and (s.contact_at_first or self._at_edge(s.first_box)))
            tracks[track_id] = {"frames": s.frames, "contact_frames": s.contact_frames, "exited": exited, "entered": entered,
                                "markers": {int(k): v for k, v in s.markers.items()}, "refs": list(s.refs)}
        return {"frames": self.frames, "hand_frames": self.hand_frames, "hand_seen": self.hand_frames > 0,
                "hand_markers": {int(k): v for k, v in self.hand_markers.items()}, "tracks": tracks}

def annotate(candidates, summary, action, min_marker_frames=2):
    """Set the evidence flags of review candidates from a MotionProof summary (in place)."""
    moved = {tid: t for tid, t in summary["tracks"].items() if (t["exited"] if action == "PICKUP" else t["entered"])}
    for c in candidates:
        tail = c.product_track.rsplit(":", 1)[-1]
        track = summary["tracks"].get(int(tail)) if tail.lstrip("-").isdigit() else None
        if track is None and len(moved) == 1:
            track = next(iter(moved.values()))  # weight-driven candidates name a product, not a track
        c.temporal = bool(summary["hand_seen"])
        if track is None:
            continue
        c.hand_product = track["contact_frames"] > 0
        c.trajectory = track["exited"] if action == "PICKUP" else track["entered"]
        seen = {m: n for m, n in track["markers"].items() if n >= min_marker_frames}
        if seen:
            c.marker_ids = sorted(seen)[:16]  # markers at the hand that moved the item, not every marker in view
            c.bracelet_hand = True
            c.identity = len(seen) == 1
        c.chain_refs = track["refs"][:30]
    return candidates
