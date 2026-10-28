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
        self.together_frames = 0  # frames with two different bracelets at hands at once

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
        if len(set().union(*near.values())) > 1:
            self.together_frames += 1
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
                "hand_markers": {int(k): v for k, v in self.hand_markers.items()},
                "bracelets_together_frames": self.together_frames, "tracks": tracks}

BRACELET_DOMINANCE = 0.75  # share of a hand's band sightings one pattern needs to name the bracelet

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
            # The model can flip between the two band patterns on ONE bracelet. With only one bracelet in view
            # all window, every sighting is that bracelet; with two at once, only this item's hand counts.
            # The bracelet is named only when one pattern clearly dominates; a split goes to staff.
            pool = track["markers"] if summary.get("bracelets_together_frames") else (summary.get("hand_markers") or track["markers"])
            top, n = max(pool.items(), key=lambda kv: kv[1])
            if n / sum(pool.values()) >= BRACELET_DOMINANCE and top in seen:
                c.marker_ids, c.identity = [top], True
            else:
                c.marker_ids, c.identity = sorted(set(seen) | {top})[:16], False
        c.chain_refs = track["refs"][:30]
    return candidates

def movement_candidates(summary, matches, clip_id, min_contact=1):
    """Candidates for a collection window WITHOUT a scale: one per item track that left the tray
    (PICKUP) or arrived on it (RETURN) under a hand. Items that only wobbled, or that moved with no
    hand contact, produce nothing, so motion alone never floods staff review.

    matches: {track_id: vision.identify.Match} last gallery verdict per track (may be missing).
    """
    from backend.schemas import Candidate
    from vision.association import scores
    out = []
    for track_id, t in sorted(summary["tracks"].items()):
        if not (t["exited"] or t["entered"]) or t["contact_frames"] < min_contact:
            continue
        match = matches.get(track_id)
        known = match is not None and match.known
        out.append(Candidate(product_id=match.product_id if known else "UNKNOWN", product_track=f"{clip_id}:{track_id}",
                             hand_track="UNPROVEN", action="PICKUP" if t["exited"] else "RETURN", quantity=1,
                             sku=known, detector_confidence=0.0, **scores(match)))
    return annotate(out, summary, out[0].action) if out else out

# ---------------------------------------------------------------------------
# Bracelet bands as identity. A Train-products model may also know the two bracelet
# patterns (classes "band-stripe", "band-circle"). A band says WHO, never WHAT: it leaves
# the product list and becomes a bracelet marker (the same IDs staff give the bracelet in
# Shelf setup, so the existing marker -> bracelet -> session mapping applies), and the
# area around it stands in for that wrist's hand when the model has no hand class.
# ---------------------------------------------------------------------------
BAND_MARKERS = {"band-stripe": 200, "band-circle": 201}


def split_bands(detections, band_markers=None):
    """(products, bands) from one frame's detections. A bracelet the model names twice (stripes AND circles
    on the same spot) keeps only its more confident pattern; otherwise one wrist looks like two bracelets."""
    band_markers = BAND_MARKERS if band_markers is None else band_markers
    products, bands = [], []
    for d in detections:
        (bands if d.sku in band_markers else products).append(d)
    def iou(a, b):
        iw, ih = max(0.0, min(a[2], b[2]) - max(a[0], b[0])), max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
        union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - iw * ih
        return iw * ih / union if union > 0 else 0.0
    kept = []
    for d in sorted(bands, key=lambda b: -b.confidence):
        # Near-identical boxes are one bracelet named twice; two real bracelets side by side overlap far less.
        if all(iou(d.box, k.box) < 0.8 for k in kept):
            kept.append(d)
    return products, kept


class BandConfirmer:
    """Two shoppers at the shelf: a bracelet counts as seen once its pattern is in `frames` consecutive frames
    (one stray frame of the other pattern breaks the run). Reported once per appearance; it can be reported
    again only after it has been out of view for `gap` frames."""

    def __init__(self, frames=5, gap=10):
        self.need, self.gap = frames, gap
        self.streak, self.missing, self.reported = {}, {}, set()

    def update(self, markers):
        """markers: the bracelet marker IDs in this frame. Returns the IDs confirmed by this frame."""
        seen, confirmed = set(markers), []
        for m in list(self.streak):
            if m not in seen:
                self.streak.pop(m)
        for m in list(self.reported):
            self.missing[m] = 0 if m in seen else self.missing.get(m, 0) + 1
            if self.missing[m] >= self.gap:
                self.reported.discard(m)
                self.missing.pop(m)
        for m in seen:
            self.streak[m] = self.streak.get(m, 0) + 1
            if self.streak[m] >= self.need and m not in self.reported:
                self.reported.add(m)
                self.missing[m] = 0
                confirmed.append(m)
        return confirmed


def band_evidence(bands, origin, band_markers=None, reach=3.0):
    """Bracelet markers (full-frame corners) and hand regions for the bands seen in a tray crop."""
    band_markers = BAND_MARKERS if band_markers is None else band_markers
    markers, hands = [], []
    for d in bands:
        x1, y1, x2, y2 = offset(d.box, origin)
        markers.append({"id": band_markers[d.sku], "corners": [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]})
        hands.append(expand((x1, y1, x2, y2), reach))
    return markers, hands


def track_candidates(summary, skus, matches, clip_id, action):
    """Several items moved in one weight window (e.g. two shoppers reaching in together): one candidate
    per moved item track, so each carries only the bracelet seen at the hand that moved it. The shared
    scale then checks the total; fusion holds the event for review when any hand stays unproven.
    None unless at least two items moved and every one of them was named as a known product.

    skus: {track_id: product id last named for that track}; matches: {track_id: Match} for scores.
    """
    from backend.schemas import Candidate
    from vision.association import scores
    moved = [tid for tid, t in sorted(summary["tracks"].items()) if (t["exited"] if action == "PICKUP" else t["entered"])]
    if len(moved) < 2 or not all(skus.get(tid) not in (None, "UNKNOWN") for tid in moved):
        return None
    out = [Candidate(product_id=skus[tid], product_track=f"{clip_id}:{tid}", hand_track="UNPROVEN", action=action,
                     quantity=1, sku=True, detector_confidence=0.0, **scores(matches.get(tid))) for tid in moved]
    return annotate(out, summary, action)
