"""Cut the product out of its crop so the gallery compares the product, not the tray behind it.

A reference crop of a bottle on the tray also contains tray, shelf edge and neighbouring items.
Two different products photographed on the same tray then look more alike than they are, and an
unknown item on that tray looks like a known one. Masking the background to neutral grey removes
that shared context.

  grabcut  OpenCV GrabCut seeded with the crop rectangle. No download, CPU, ~20-60 ms per crop.
  sam      Segment Anything 2.1 (tiny) through ultralytics, box-prompted with the crop. Better
           edges on transparent/shiny packaging; downloads weights on first use; GPU recommended.
  none     unchanged crop.

A mask that is implausible (almost nothing, or almost everything) is ignored and the plain crop
is used: masking must never make a reference worse than no masking.
"""
import numpy as np

NEUTRAL = (124, 124, 124)
METHODS = ("none", "grabcut", "sam")

def _plausible(mask, low=0.08, high=0.97):
    frac = float(np.count_nonzero(mask)) / mask.size if mask.size else 0.0
    return low <= frac <= high, frac

def grabcut_mask(crop, inset=0.04, iterations=3, work_side=112):
    """GrabCut on a downscaled copy (cost grows with pixels; the mask only needs to be coarse)."""
    import cv2
    h, w = crop.shape[:2]
    if h < 16 or w < 16:
        return None
    scale = min(1.0, work_side / max(h, w))
    small = cv2.resize(crop, (max(16, int(w * scale)), max(16, int(h * scale))), interpolation=cv2.INTER_AREA) if scale < 1 else crop
    sh, sw = small.shape[:2]
    mask = np.zeros((sh, sw), np.uint8)
    rect = (max(1, int(sw * inset)), max(1, int(sh * inset)), max(2, int(sw * (1 - 2 * inset))), max(2, int(sh * (1 - 2 * inset))))
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(small, mask, rect, bgd, fgd, iterations, cv2.GC_INIT_WITH_RECT)
    except cv2.error:
        return None
    mask = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    return cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST) if scale < 1 else mask

class SamMasker:
    """Box-prompted SAM 2.1 tiny. Loaded once; prompts are the whole crop (it already frames one item)."""

    def __init__(self, weights="models/sam2.1_t.pt", device="cpu"):
        from ultralytics import SAM
        self.model, self.device = SAM(weights), device

    def __call__(self, crop):
        h, w = crop.shape[:2]
        if h < 16 or w < 16:
            return None
        result = self.model(crop, bboxes=[[2, 2, w - 3, h - 3]], device=self.device, verbose=False)[0]
        if result.masks is None or not len(result.masks.data):
            return None
        mask = result.masks.data[0].cpu().numpy()
        if mask.shape != (h, w):
            import cv2
            mask = cv2.resize(mask.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST)
        return (mask > 0).astype(np.uint8) * 255

def apply_mask(crop, mask, pad=0.06):
    """Neutral background outside the mask, cropped to the mask's bounding box (plus padding)."""
    import cv2
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    out = crop.copy()
    out[mask == 0] = NEUTRAL
    ys, xs = np.nonzero(mask)
    h, w = crop.shape[:2]
    py, px = int(h * pad), int(w * pad)
    return out[max(0, ys.min() - py):min(h, ys.max() + 1 + py), max(0, xs.min() - px):min(w, xs.max() + 1 + px)]

class Masker:
    """Callable crop -> (crop', info). Never raises: falls back to the plain crop."""

    def __init__(self, method="grabcut", device="cpu"):
        if method not in METHODS:
            raise ValueError(f"mask method must be one of {METHODS}")
        self.method = method
        self.sam = SamMasker(device=device) if method == "sam" else None

    @property
    def name(self):
        return self.method

    def __call__(self, crop):
        if self.method == "none":
            return crop, {"mask": "none"}
        try:
            mask = self.sam(crop) if self.sam else grabcut_mask(crop)
        except Exception as exc:
            return crop, {"mask": "failed", "error": type(exc).__name__}
        if mask is None:
            return crop, {"mask": "failed"}
        ok, frac = _plausible(mask)
        if not ok:
            return crop, {"mask": "rejected", "foreground": round(frac, 3)}
        return apply_mask(crop, mask), {"mask": self.method, "foreground": round(frac, 3)}
