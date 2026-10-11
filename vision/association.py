"""Replaceable ownership extractor. Co-visibility alone never becomes ownership.

Tracks are temporary geometric hints. Without a validated wrist/grasp/trajectory
extractor, observations are retained as pending. No nearest-bracelet fallback.
"""
from backend.schemas import Candidate

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
        return [Candidate(marker_ids=[m["id"] for m in markers], product_id=d.sku, product_track=f"{clip_id}:{d.track_id}", hand_track="UNPROVEN", action=action, quantity=1, sku=True, identity=False, hand_product=False, trajectory=False, bracelet_hand=False, detector_confidence=d.confidence) for d in detections]
