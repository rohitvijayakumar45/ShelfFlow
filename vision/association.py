"""Replaceable ownership extractor. Co-visibility alone never becomes ownership.

Tracks are temporary geometric hints. Without a validated wrist/grasp/trajectory
extractor, observations are retained as pending. No nearest-bracelet fallback.
"""
from backend.schemas import Candidate
from collections import Counter


def review_weight_candidates(before, after, clip_id, action):
    """Describe changed product counts; never infer ownership from shelf visibility."""
    first=Counter(p for p in before if p!='UNKNOWN')
    last=Counter(p for p in after if p!='UNKNOWN')
    changed=(first-last) if action=='PICKUP' else (last-first)
    if not changed:
        return [Candidate(product_id='UNKNOWN',product_track=clip_id+':unknown',hand_track='UNPROVEN',action=action,quantity=1)]
    return [Candidate(product_id=product,product_track=clip_id+':'+product,hand_track='UNPROVEN',
                      action=action,quantity=min(count,10),sku=True)
            for product,count in sorted(changed.items())]

def roi_pixels(roi, width, height):
    x1, y1, x2, y2 = roi
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        raise ValueError("Invalid normalized ROI")
    return (int(x1 * width), int(y1 * height), int(x2 * width), int(y2 * height))

def full_frame_box(box, crop_origin):
    x, y = crop_origin
    a, b, c, d = box
    return (a + x, b + y, c + x, d + y)

class ReviewFirstAssociator:
    def observe(self, detections, markers, clip_id, action="PICKUP"):
        return [Candidate(marker_ids=[m["id"] for m in markers][:16], product_id=d.sku, product_track=f"{clip_id}:{d.track_id}", hand_track="UNPROVEN", action=action, quantity=1, sku=d.sku != "UNKNOWN", identity=False, hand_product=False, trajectory=False, bracelet_hand=False, detector_confidence=d.confidence, **scores(d.match)) for d in detections]

def scores(match):
    """Recognition scores carried on a candidate so staff decisions can re-tune thresholds."""
    if match is None:
        return {}
    clip = lambda v, lo, hi: None if v is None else max(lo, min(hi, round(float(v), 4)))
    return {"similarity": clip(match.similarity, -1, 1), "margin": clip(match.margin, -2, 2),
            "top_product": match.top, "second_product": match.second, "via": match.via}
