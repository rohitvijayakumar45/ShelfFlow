"""Cheap second opinions next to the embedding gallery.

reference_quality  rejects reference crops that would poison the gallery: blurred, over/under
                   exposed, cut off by the tray edge, mostly hand, or too small.
ColourCue          per-product HSV colour histograms. Embeddings are trained to ignore colour
                   shifts; flavour variants of one brand often differ mainly in colour. Used only
                   to break a near-tie between two products the gallery already short-listed.
"""
from collections import defaultdict
import numpy as np
from vision.identify import Match

# ---------------------------------------------------------------- reference quality
LIMITS = {
    "RESTOCK_AUTO": {"min_sharpness": 15.0, "max_clipped": 0.35, "max_skin": 0.30, "allow_edge": False, "min_side": 24},
    "TEACH": {"min_sharpness": 15.0, "max_clipped": 0.35, "max_skin": 0.55, "allow_edge": True, "min_side": 24},
    "STAFF_LABEL": {"min_sharpness": 8.0, "max_clipped": 0.5, "max_skin": 0.7, "allow_edge": True, "min_side": 16},
}

def skin_fraction(image):
    """Share of pixels in a broad skin-tone range (YCrCb). A hand covering the product scores high."""
    import cv2
    ycrcb = cv2.cvtColor(image, cv2.COLOR_BGR2YCrCb)
    mask = cv2.inRange(ycrcb, (40, 135, 85), (250, 175, 130))
    return float(np.count_nonzero(mask)) / mask.size if mask.size else 0.0

def clipped_fraction(image):
    """Share of blown-out highlights (glare). Dark pixels are not counted: black trays are normal."""
    import cv2
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return float(np.count_nonzero(gray > 247)) / gray.size if gray.size else 1.0

def mean_brightness(image):
    import cv2
    return float(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).mean()) if image.size else 0.0

def touches_edge(box, roi, margin=3):
    if box is None or roi is None:
        return False
    x1, y1, x2, y2 = box
    rx1, ry1, rx2, ry2 = roi
    return x1 <= rx1 + margin or y1 <= ry1 + margin or x2 >= rx2 - margin or y2 >= ry2 - margin

def reference_quality(crop, source="RESTOCK_AUTO", box=None, roi=None):
    """Returns {"ok", "score" 0..1, "reasons", measurements}. Score ranks references for staff."""
    from vision.fewshot import sharpness
    limits = LIMITS.get(source, LIMITS["RESTOCK_AUTO"])
    h, w = crop.shape[:2]
    m = {"sharpness": round(sharpness(crop), 1) if min(h, w) >= 3 else 0.0, "clipped": round(clipped_fraction(crop), 3),
         "skin": round(skin_fraction(crop), 3), "edge": touches_edge(box, roi), "min_side": int(min(h, w)),
         "brightness": round(mean_brightness(crop), 1)}
    reasons = []
    if m["min_side"] < limits["min_side"]:
        reasons.append("TOO_SMALL")
    if m["sharpness"] < limits["min_sharpness"]:
        reasons.append("BLURRED")
    if m["clipped"] > limits["max_clipped"] or m["brightness"] < 18:
        reasons.append("BAD_EXPOSURE")
    if m["skin"] > limits["max_skin"]:
        reasons.append("HAND_COVERED")
    if m["edge"] and not limits["allow_edge"]:
        reasons.append("CUT_OFF_AT_TRAY_EDGE")
    score = (min(1.0, m["sharpness"] / 150) * (1 - min(1.0, m["clipped"] * 2)) * (1 - min(1.0, m["skin"]))
             * (0.6 if m["edge"] else 1.0) * min(1.0, m["min_side"] / 96))
    return {"ok": not reasons, "score": round(float(score), 3), "reasons": reasons, **m}

# ---------------------------------------------------------------- colour
def colour_histogram(image, bins=(12, 6, 4)):
    """Normalised HSV histogram, ignoring near-grey pixels' hue (masked backgrounds are grey)."""
    import cv2
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    keep = ~((hsv[:, :, 1] < 25) & (np.abs(hsv[:, :, 2].astype(int) - 124) < 6))  # drop the neutral mask fill
    hist = cv2.calcHist([hsv], [0, 1, 2], keep.astype(np.uint8) * 255, list(bins), [0, 180, 0, 256, 0, 256]).flatten()
    total = hist.sum()
    return (hist / total).astype(np.float32) if total else hist.astype(np.float32)

def histogram_similarity(a, b):
    """1 - Bhattacharyya distance (1 = identical colour distribution)."""
    bc = float(np.sum(np.sqrt(np.clip(a, 0, None) * np.clip(b, 0, None))))
    return max(0.0, min(1.0, bc))

class ColourCue:
    """Per-product colour references; resolves a gallery near-tie only when colour clearly prefers one."""

    def __init__(self, min_gap=0.12, min_similarity=0.55):
        self.min_gap, self.min_similarity = min_gap, min_similarity
        self.refs = defaultdict(dict)  # product -> {source: histogram}

    def add(self, product_id, source, image):
        self.refs[product_id][source] = colour_histogram(image)

    def remove_sources(self, prefix):
        for refs in self.refs.values():
            for source in [s for s in refs if str(s).startswith(prefix)]:
                del refs[source]

    def similarity(self, product_id, hist):
        refs = self.refs.get(product_id) or {}
        return max((histogram_similarity(hist, r) for r in refs.values()), default=None)

    def refine(self, image, match, threshold):
        """Acts only on a gallery rejection caused by two products being too close (not on low similarity)."""
        if match.known or not match.top or not match.second or match.similarity < threshold:
            return match
        hist = colour_histogram(image)
        a, b = self.similarity(match.top, hist), self.similarity(match.second, hist)
        if a is None or b is None:
            return match
        best, other, sa, sb = (match.top, match.second, a, b) if a >= b else (match.second, match.top, b, a)
        if sa < self.min_similarity or sa - sb < self.min_gap:
            return match
        return Match(best, match.similarity, round(sa - sb, 3), other, match.top, match.second, via="colour")
