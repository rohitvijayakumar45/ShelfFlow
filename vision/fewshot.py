"""Few-shot product learning from the fixed shelf camera.

Nothing here retrains the detector. New products are learned from a handful of crops:
  * change_box      - where a newly placed item is, from before/after stills of the tray
                      (the restock wizard already takes both), so nobody crops photos by hand.
  * augment         - extra views of each crop (flip, tighter crop, light, small rotation).
  * DiversityFilter - teach mode keeps only crops that add a new view, not near-duplicates.
  * LookalikeGuard  - for products the gallery confuses, a small linear probe on the frozen
                      DINOv2 features of exactly those products. It is used only if it proves
                      itself on held-out reference views; otherwise the products stay UNKNOWN to
                      vision and weight/position (or staff review) decide.
Scores are similarities, not probabilities.
"""
from collections import defaultdict
import numpy as np
from vision.identify import Match, UNKNOWN

# ---------------------------------------------------------------- placement crop
def change_box(before, after, roi=None, diff_threshold=28, min_area_frac=0.003, max_area_frac=0.6):
    """Bounding box (full-image pixels) of the largest new object between two stills.

    roi: (x1, y1, x2, y2) pixel rectangle to search (the tray). Brightness is matched first
    because the camera's auto-exposure shifts between stills. Returns a dict with
    box=None and a reason when nothing reliable changed or the whole view changed.
    """
    import cv2
    if before.shape != after.shape:
        after = cv2.resize(after, (before.shape[1], before.shape[0]))
    h, w = before.shape[:2]
    x1, y1, x2, y2 = roi if roi is not None else (0, 0, w, h)
    b = cv2.cvtColor(before[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY).astype(np.float32)
    a = cv2.cvtColor(after[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY).astype(np.float32)
    if b.size == 0:
        return {"box": None, "reason": "EMPTY_ROI", "area_frac": 0.0}
    a *= (np.median(b) + 1) / (np.median(a) + 1)  # undo global exposure change
    diff = cv2.absdiff(cv2.GaussianBlur(b, (5, 5), 0), cv2.GaussianBlur(a, (5, 5), 0))
    mask = (diff > diff_threshold).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    changed = float(np.count_nonzero(mask)) / mask.size
    if changed > max_area_frac:
        return {"box": None, "reason": "GLOBAL_CHANGE", "area_frac": changed}  # camera moved or light changed
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = [c for c in contours if cv2.contourArea(c) >= min_area_frac * mask.size]
    if not contours:
        return {"box": None, "reason": "NO_CHANGE", "area_frac": changed}
    biggest = max(contours, key=cv2.contourArea)
    bx, by, bw, bh = cv2.boundingRect(biggest)
    # Merge pieces of the same item (label text and edges often split into several blobs).
    for c in contours:
        cx, cy, cw, ch = cv2.boundingRect(c)
        if cx < bx + bw + 15 and bx < cx + cw + 15 and cy < by + bh + 15 and by < cy + ch + 15:
            nx, ny = min(bx, cx), min(by, cy)
            bw, bh = max(bx + bw, cx + cw) - nx, max(by + bh, cy + ch) - ny
            bx, by = nx, ny
    return {"box": (bx + x1, by + y1, bx + bw + x1, by + bh + y1), "reason": "OK", "area_frac": changed,
            "contours": len(contours)}

def crop_box(image, box, pad=0.12):
    h, w = image.shape[:2]
    x1, y1, x2, y2 = box
    px, py = (x2 - x1) * pad, (y2 - y1) * pad
    a, b, c, d = max(0, int(x1 - px)), max(0, int(y1 - py)), min(w, int(x2 + px)), min(h, int(y2 + py))
    return image[b:d, a:c]

def sharpness(image):
    import cv2
    return float(cv2.Laplacian(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())

# ---------------------------------------------------------------- few-shot views
def augment(crop):
    """A few physically plausible variants of one reference crop (all small changes)."""
    import cv2
    h, w = crop.shape[:2]
    views = [("orig", crop), ("flip", cv2.flip(crop, 1))]
    my, mx = int(h * 0.075), int(w * 0.075)
    if h > 20 and w > 20:
        views.append(("tight", crop[my:h - my, mx:w - mx]))
    views.append(("bright", np.clip(crop.astype(np.float32) * 1.15, 0, 255).astype(np.uint8)))
    views.append(("dark", (crop.astype(np.float32) * 0.85).astype(np.uint8)))
    for angle in (-8, 8):
        m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        views.append((f"rot{angle}", cv2.warpAffine(crop, m, (w, h), borderMode=cv2.BORDER_REPLICATE)))
    return views

class DiversityFilter:
    """Accept a new reference view only if it differs from the product's existing views."""

    def __init__(self, max_refs=25, max_similarity=0.96):
        self.max_refs, self.max_similarity = max_refs, max_similarity
        self.kept = defaultdict(list)

    def accept(self, product_id, vector):
        kept = self.kept[product_id]
        if len(kept) >= self.max_refs:
            return False
        if kept and float(np.max(np.stack(kept) @ vector)) >= self.max_similarity:
            return False
        kept.append(np.asarray(vector, np.float32))
        return True

# ---------------------------------------------------------------- look-alike products
def _group_key(source):
    """References of one physical photo (and its augmented views) share a group for validation."""
    source = str(source)
    return source.rsplit(":", 1)[0] if source.startswith("live:") else source

def _ridge(x, y, lam):
    xb = np.hstack([x, np.ones((len(x), 1), np.float32)])
    return np.linalg.solve(xb.T @ xb + lam * np.eye(xb.shape[1], dtype=np.float32), xb.T @ y)

def _scores(w, vector):
    return np.append(np.asarray(vector, np.float32), 1.0) @ w

class LookalikeGuard:
    """Finds products the gallery confuses and, per confusable group, fits a validated linear probe."""

    def __init__(self, pair_threshold=0.85, probe_margin=0.25, min_cv_accuracy=0.9, lam=1.0, folds=5):
        self.pair_threshold, self.probe_margin = pair_threshold, probe_margin
        self.min_cv_accuracy, self.lam, self.folds = min_cv_accuracy, lam, folds
        self.pairs, self.groups = [], []

    def fit(self, gallery):
        labels = np.array(gallery.labels)
        products = sorted(set(gallery.labels))
        protos = {}
        for p in products:
            v = gallery.vectors[labels == p].mean(0)
            protos[p] = v / (np.linalg.norm(v) + 1e-9)
        self.pairs = []
        parent = {p: p for p in products}

        def find(p):
            while parent[p] != p:
                p = parent[p]
            return p
        for i, a in enumerate(products):
            for b in products[i + 1:]:
                sim = float(protos[a] @ protos[b])
                if sim >= self.pair_threshold:
                    self.pairs.append({"a": a, "b": b, "similarity": round(sim, 3)})
                    parent[find(a)] = find(b)
        members = defaultdict(list)
        for p in products:
            members[find(p)].append(p)
        self.groups = []
        for group in (sorted(m) for m in members.values() if len(m) > 1):
            self.groups.append(self._fit_group(gallery, labels, group))
        return self

    def _fit_group(self, gallery, labels, group):
        idx = np.where(np.isin(labels, group))[0]
        x = gallery.vectors[idx]
        y = np.zeros((len(idx), len(group)), np.float32)
        for row, i in enumerate(idx):
            y[row, group.index(labels[i])] = 1
        keys = [_group_key(gallery.sources[i]) for i in idx]
        unique = sorted(set(keys))
        correct = total = 0
        if len(unique) >= self.folds and all(np.sum(labels[idx] == g) >= 2 for g in group):
            fold_of = {k: n % self.folds for n, k in enumerate(unique)}
            folds = np.array([fold_of[k] for k in keys])
            for f in range(self.folds):
                train, test = folds != f, folds == f
                if not test.any() or len(set(np.argmax(y[train], 1))) < len(group):
                    continue
                w = _ridge(x[train], y[train], self.lam)
                pred = np.argmax(np.hstack([x[test], np.ones((test.sum(), 1), np.float32)]) @ w, 1)
                correct += int(np.sum(pred == np.argmax(y[test], 1)))
                total += int(test.sum())
        accuracy = correct / total if total else None
        usable = accuracy is not None and accuracy >= self.min_cv_accuracy
        return {"products": group, "weights": _ridge(x, y, self.lam) if usable else None,
                "cv_accuracy": accuracy, "usable": usable,
                "advice": None if usable else "Camera cannot reliably tell these apart: keep them on different trays or rely on weight/position."}

    def group_of(self, product):
        return next((g for g in self.groups if product in g["products"]), None)

    def refine(self, vector, match, threshold):
        """Only acts when the gallery rejected a crop for being too close between two look-alikes."""
        if match.known or not match.top or not match.second or match.similarity < threshold:
            return match
        group = self.group_of(match.top)
        if not group or not group["usable"] or match.second not in group["products"]:
            return match
        scores = _scores(group["weights"], vector)
        order = np.argsort(-scores)
        best, gap = group["products"][order[0]], float(scores[order[0]] - scores[order[1]])
        if gap < self.probe_margin or best not in (match.top, match.second):
            return match
        other = match.second if best == match.top else match.top
        return Match(best, match.similarity, gap, other, match.top, match.second, via="lookalike-probe")

    def report(self):
        return {"pairs": self.pairs,
                "groups": [{k: v for k, v in g.items() if k != "weights"} for g in self.groups]}
