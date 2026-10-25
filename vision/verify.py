"""Unknown check for models from the Train products page.

A YOLO model only chooses among the products it was trained on, so an object it never saw is
called the closest product, often with a high score. Here every detection is compared (frozen
DINOv2 embedding, cosine similarity) with reference crops of the product YOLO named, cut from
that model's own training frames. Too dissimilar -> UNKNOWN.

References sit next to the weights as <weights stem>-refs.npz (run/best-refs.npz,
models/trained/active-refs.npz). They are built automatically after training. The threshold per
product is the 5th percentile of its validation crops' similarity to its training crops, kept
within [MIN_THRESHOLD, MAX_THRESHOLD]. Without a references file nothing is checked, so a model
behaves exactly as before.
"""
import json
from pathlib import Path

import numpy as np

UNKNOWN = "UNKNOWN"
EMBEDDER = "facebook/dinov2-small"
PAD = 0.1
MAX_REFS = 200            # per product, spread evenly over its frames
MIN_THRESHOLD, MAX_THRESHOLD = 0.55, 0.75


def refs_path(weights):
    weights = Path(weights)
    return weights.with_name(f"{weights.stem}-refs.npz")


def crop(image, box, pad=PAD):
    h, w = image.shape[:2]
    x1, y1, x2, y2 = box
    px, py = (x2 - x1) * pad, (y2 - y1) * pad
    a, b, c, d = max(0, int(x1 - px)), max(0, int(y1 - py)), min(w, int(x2 + px)), min(h, int(y2 + py))
    return image[b:d, a:c] if c > a and d > b else image[0:1, 0:1]


def _dataset_crops(dataset, split, names):
    import cv2
    out = {}
    for label in sorted((Path(dataset) / "labels" / split).glob("*.txt")):
        rows = [r.split() for r in label.read_text(encoding="ascii").splitlines() if r.strip()]
        if not rows:
            continue
        image = cv2.imread(str(Path(dataset) / "images" / split / f"{label.stem}.jpg"))
        if image is None:
            continue
        h, w = image.shape[:2]
        for c, cx, cy, bw, bh in rows:
            cx, cy, bw, bh = float(cx) * w, float(cy) * h, float(bw) * w, float(bh) * h
            out.setdefault(names[int(c)], []).append(crop(image, (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)))
    return out


def _thin(items, limit):
    if len(items) <= limit:
        return items
    step = len(items) / limit
    return [items[int(k * step)] for k in range(limit)]


def build(dataset, out, device="cpu", embedder=None):
    """References + per-product thresholds from a Train-products dataset folder."""
    from vision.identify import Dinov2Embedder
    names = json.loads((Path(dataset) / "summary.json").read_text(encoding="utf-8"))["classes"]
    embedder = embedder or Dinov2Embedder(device, EMBEDDER)
    train, val = _dataset_crops(dataset, "train", names), _dataset_crops(dataset, "val", names)
    vectors, labels, thresholds = [], [], {}
    for pid in names:
        refs = embedder.embed(_thin(train.get(pid, []), MAX_REFS))
        checks = embedder.embed(_thin(val.get(pid, []), MAX_REFS))
        if len(refs) == 0:
            continue
        own = (checks @ refs.T).max(1) if len(checks) else np.array([MIN_THRESHOLD])
        thresholds[pid] = round(float(np.clip(np.percentile(own, 5), MIN_THRESHOLD, MAX_THRESHOLD)), 3)
        vectors.append(np.concatenate([refs, checks]) if len(checks) else refs)
        labels += [pid] * (len(refs) + len(checks))
    out = Path(out)
    tmp = out.with_name(out.stem + ".tmp.npz")
    np.savez_compressed(tmp, vectors=np.concatenate(vectors).astype(np.float32), labels=np.array(labels),
                        thresholds=np.array(json.dumps(thresholds)), embedder=np.array(embedder.name))
    tmp.replace(out)
    return thresholds


class Verifier:
    def __init__(self, path, device="cpu", embedder=None):
        from vision.identify import Dinov2Embedder
        data = np.load(path, allow_pickle=False)
        self.vectors, self.labels = data["vectors"], data["labels"].tolist()
        self.thresholds = json.loads(str(data["thresholds"]))
        self.embedder = embedder or Dinov2Embedder(device, str(data["embedder"]))
        self.index = {pid: np.array([k for k, x in enumerate(self.labels) if x == pid]) for pid in self.thresholds}

    @classmethod
    def for_weights(cls, weights, device="cpu", embedder=None):
        """The verifier for a weights file, or None (no references, or they fail to load): then nothing is checked."""
        path = refs_path(weights)
        if not path.is_file():
            return None
        try:
            return cls(path, device, embedder)
        except Exception as exc:
            print("Unknown check off (references unreadable):", type(exc).__name__, flush=True)
            return None

    def similarities(self, image, boxes, products):
        """Best similarity of each detection to the references of the product YOLO named (None: product has no references)."""
        if not boxes:
            return []
        vectors = self.embedder.embed([crop(image, b) for b in boxes])
        out = []
        for vector, pid in zip(vectors, products):
            rows = self.index.get(pid)
            out.append(float((self.vectors[rows] @ vector).max()) if rows is not None and len(rows) else None)
        return out

    def known(self, product, similarity):
        return similarity is None or similarity >= self.thresholds.get(product, MIN_THRESHOLD)


class TrackCheck:
    """Average the similarity over a track's frames, so one blurred or turned frame never flips a product to UNKNOWN."""

    def __init__(self, verifier, keep=30):
        self.verifier, self.keep, self.seen = verifier, keep, {}

    def apply(self, image, detections, key=""):
        """Detections (vision.detector.Detection) with sku set to UNKNOWN when they do not look like their product."""
        if not detections:
            return detections
        sims = self.verifier.similarities(image, [d.box for d in detections], [d.sku for d in detections])
        for d, sim in zip(detections, sims):
            if sim is None:
                continue
            if d.track_id is not None:
                history = self.seen.setdefault((key, d.track_id, d.sku), [])
                history.append(sim)
                del history[:-self.keep]
                sim = sum(history) / len(history)
            if not self.verifier.known(d.sku, sim):
                d.sku = UNKNOWN
        if len(self.seen) > 500:
            self.seen.clear()
        return detections
