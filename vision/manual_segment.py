"""Box-prompted SAM crop; no YOLO detector, identity guesses or automatic approval."""
from pathlib import Path
import time

import cv2
import numpy as np

from vision.cues import reference_quality


def make_crops(image, mask, box):
    """Constrain the mask to the operator's box; retain original and cutout variants."""
    h, w = image.shape[:2]
    if mask.shape != (h, w):
        mask = cv2.resize(mask.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST)
    x1, y1, x2, y2 = box
    foreground = np.zeros((h, w), dtype=np.uint8)
    foreground[y1:y2, x1:x2] = mask[y1:y2, x1:x2] > 0
    ys, xs = np.nonzero(foreground)
    if len(xs) < 16:
        raise ValueError('SAM found no usable foreground. Draw a tighter box and try again.')
    pad = max(2, round(max(xs.max()-xs.min(), ys.max()-ys.min())*.04))
    bounds = [max(x1, int(xs.min())-pad), max(y1, int(ys.min())-pad),
              min(x2, int(xs.max())+1+pad), min(y2, int(ys.max())+1+pad)]
    bx1, by1, bx2, by2 = bounds
    plain = image[by1:by2, bx1:bx2].copy()
    cutout = plain.copy()
    cutout[foreground[by1:by2, bx1:bx2] == 0] = (124, 124, 124)
    quality = reference_quality(plain, 'MANUAL_SAM', bounds, (0, 0, w, h))
    # These are visible warnings, not capture rejection or approval vetoes.
    warnings = list(quality.get('reasons', []))
    fraction = len(xs) / max(1, (x2-x1)*(y2-y1))
    if fraction > .97:
        warnings.append('Mask covers nearly the whole box; check its boundary.')
    if min(plain.shape[:2]) < 24:
        warnings.append('Small crop: there may be too little detail for recognition.')
    return plain, cutout, {'bounds': bounds, 'foreground_fraction': round(fraction, 4),
                           'quality': quality, 'warnings': warnings, 'segmenter': 'sam2.1_t'}


class ManualSegmenter:
    def __init__(self, weights, device='cuda:0'):
        if not Path(weights).is_file():
            raise RuntimeError('SAM weights are missing. Install models/sam2.1_t.pt.')
        from ultralytics import SAM
        self.model, self.device = SAM(str(weights)), device

    def __call__(self, image, box):
        started = time.monotonic()
        results = self.model(image, bboxes=[box], device=self.device, verbose=False)
        masks = results[0].masks
        if masks is None or not len(masks.data):
            raise ValueError('SAM found no mask. Draw a different box and try again.')
        plain, cutout, details = make_crops(image, masks.data[0].cpu().numpy() > .5, box)
        details['processing_ms'] = round((time.monotonic()-started)*1000)
        return plain, cutout, details
