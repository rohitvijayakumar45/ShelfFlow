"""Open-set product identification without per-product training.

A single-class detector finds "an item"; this module decides WHICH product it is by
comparing a frozen DINOv2 embedding of the item crop with a gallery of reference crops
captured from the shelf camera. Adding a product = enrolling reference crops; no retraining.

Similarity scores are cosine similarities, not calibrated probabilities. Thresholds must be
chosen from a held-out evaluation (tools.dataset.cli gallery-eval), never assumed.
"""
import hashlib
import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np

UNKNOWN = "UNKNOWN"

class Dinov2Embedder:
    """Frozen DINOv2 ViT-S/14 (Apache-2.0). CLS + mean patch token, L2-normalised."""
    default_model = "facebook/dinov2-small"

    def __init__(self, device="cpu", model=None):
        import torch
        from transformers import AutoImageProcessor, AutoModel
        self.torch, self.device = torch, device
        self.name = model or self.default_model
        self.processor = AutoImageProcessor.from_pretrained(self.name)
        self.model = AutoModel.from_pretrained(self.name).to(device).eval()

    def embed(self, images_bgr):
        if not len(images_bgr):
            return np.zeros((0, self.dim), np.float32)
        torch = self.torch
        rgb = [np.ascontiguousarray(img[:, :, ::-1]) for img in images_bgr]
        with torch.inference_mode():
            inputs = self.processor(images=rgb, return_tensors="pt").to(self.device)
            hidden = self.model(**inputs).last_hidden_state
            vectors = torch.cat([hidden[:, 0], hidden[:, 1:].mean(1)], dim=1)
            vectors = torch.nn.functional.normalize(vectors, dim=1)
        return vectors.cpu().numpy().astype(np.float32)

    @property
    def dim(self):
        return 2 * self.model.config.hidden_size

@dataclass
class Match:
    product_id: str
    similarity: float
    margin: float
    runner_up: str | None
    top: str | None = None      # best-scoring product even when rejected (for look-alike resolution)
    second: str | None = None
    via: str = "gallery"        # "gallery" or "lookalike-probe"

    @property
    def known(self):
        return self.product_id != UNKNOWN

class Gallery:
    """Reference embeddings per product, versioned by content hash."""

    def __init__(self, embedder_name, vectors=None, labels=None, sources=None, created=None, mask="none"):
        self.embedder_name = embedder_name
        self.mask = mask  # background masking used for the references (vision.segment); queries must match
        self.vectors = np.zeros((0, 0), np.float32) if vectors is None else np.asarray(vectors, np.float32)
        self.labels = list(labels or [])
        self.sources = list(sources or [])
        self.created = created or time.time()

    def add(self, product_id, vectors, sources):
        vectors = np.asarray(vectors, np.float32)
        if product_id == UNKNOWN or not product_id:
            raise ValueError("Reserved or empty product id")
        if len(vectors) != len(sources):
            raise ValueError("One source per reference vector")
        self.vectors = vectors if not self.labels else np.vstack([self.vectors, vectors])
        self.labels += [product_id] * len(vectors)
        self.sources += list(sources)

    def remove(self, product_id):
        keep = [i for i, label in enumerate(self.labels) if label != product_id]
        self.vectors = self.vectors[keep] if keep else np.zeros((0, self.vectors.shape[1] if self.vectors.ndim == 2 else 0), np.float32)
        self.labels = [self.labels[i] for i in keep]
        self.sources = [self.sources[i] for i in keep]

    def remove_sources(self, prefix):
        """Drop references whose source starts with prefix (e.g. one staff-rejected live reference)."""
        keep = [i for i, source in enumerate(self.sources) if not str(source).startswith(prefix)]
        if len(keep) == len(self.sources):
            return 0
        dim = self.vectors.shape[1] if self.vectors.ndim == 2 else 0
        self.vectors = self.vectors[keep] if keep else np.zeros((0, dim), np.float32)
        removed = len(self.labels) - len(keep)
        self.labels = [self.labels[i] for i in keep]
        self.sources = [self.sources[i] for i in keep]
        return removed

    @property
    def products(self):
        return dict(Counter(self.labels))

    @property
    def version(self):
        digest = hashlib.sha256(self.vectors.tobytes() + json.dumps(self.labels).encode()).hexdigest()
        return f"gallery-{digest[:12]}"

    def scores(self, vector):
        """Best similarity per product (nearest reference: robust to multi-view galleries)."""
        if not self.labels:
            return {}
        sims = self.vectors @ np.asarray(vector, np.float32)
        best = defaultdict(lambda: -1.0)
        for label, sim in zip(self.labels, sims):
            best[label] = max(best[label], float(sim))
        return dict(best)

    def identify(self, vector, threshold, margin):
        ranked = sorted(self.scores(vector).items(), key=lambda kv: -kv[1])
        if not ranked:
            return Match(UNKNOWN, 0.0, 0.0, None)
        top, top_sim = ranked[0]
        runner_up, second = (ranked[1] if len(ranked) > 1 else (None, -1.0))
        gap = top_sim - second
        if top_sim < threshold or gap < margin:
            return Match(UNKNOWN, top_sim, gap, top, top, runner_up)  # never guess: below threshold or too close to another product
        return Match(top, top_sim, gap, runner_up, top, runner_up)

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, vectors=self.vectors, labels=np.array(self.labels), sources=np.array(self.sources))
        path.with_suffix(".json").write_text(json.dumps({"version": self.version, "embedder": self.embedder_name, "created": self.created,
                                                         "products": self.products, "dim": int(self.vectors.shape[1]) if self.labels else 0,
                                                         "mask": self.mask}, indent=2))
        return self.version

    @classmethod
    def load(cls, path):
        path = Path(path)
        meta = json.loads(path.with_suffix(".json").read_text())
        data = np.load(path, allow_pickle=False)
        gallery = cls(meta["embedder"], data["vectors"], data["labels"].tolist(), data["sources"].tolist(), meta["created"], meta.get("mask", "none"))
        if gallery.version != meta["version"]:
            raise ValueError("Gallery file does not match its recorded version")
        return gallery

@dataclass
class TrackVoter:
    """Decide a track's product from many frames, not one: in-hand frames are often partial or blurred."""
    min_votes: int = 3
    min_share: float = 0.6
    votes: dict = field(default_factory=lambda: defaultdict(list))

    def add(self, track, match):
        self.votes[track].append(match.product_id)

    def decision(self, track):
        votes = self.votes.get(track, [])
        known = [v for v in votes if v != UNKNOWN]
        if len(known) < self.min_votes:
            return UNKNOWN
        product, count = Counter(known).most_common(1)[0]
        return product if count / len(votes) >= self.min_share else UNKNOWN

    def forget(self, track):
        self.votes.pop(track, None)

class GalleryIdentifier:
    """Detections in, product ids out (or UNKNOWN), with per-track voting."""

    def __init__(self, gallery, embedder, threshold, margin, voter=None, pad=0.1, guard=None, masker=None, colour=None):
        if gallery.embedder_name != getattr(embedder, "name", None):
            raise ValueError("Gallery was built with a different embedder")
        if gallery.mask != getattr(masker, "name", "none"):
            raise ValueError(f"Gallery references use mask '{gallery.mask}'; queries must use the same")
        self.gallery, self.embedder = gallery, embedder
        self.threshold, self.margin, self.pad = threshold, margin, pad
        self.voter = voter or TrackVoter()
        self.guard = guard  # optional fewshot.LookalikeGuard for products the gallery confuses
        self.masker = masker  # optional vision.segment.Masker (background to neutral grey)
        self.colour = colour  # optional vision.cues.ColourCue for flavour-variant near-ties
        self.observer = None  # optional callback(track, crop, match) e.g. the uncertain-crop queue

    def crops(self, image, boxes):
        h, w = image.shape[:2]
        out = []
        for x1, y1, x2, y2 in boxes:
            px, py = (x2 - x1) * self.pad, (y2 - y1) * self.pad
            a, b = max(0, int(x1 - px)), max(0, int(y1 - py))
            c, d = min(w, int(x2 + px)), min(h, int(y2 + py))
            out.append(image[b:d, a:c] if c > a and d > b else image[0:1, 0:1])
        return out

    def label(self, image, detections, clip_id):
        """Return detections with sku set to the track's voted product (UNKNOWN until settled)."""
        if not detections:
            return []
        crops = self.crops(image, [d.box for d in detections])
        if self.masker is not None:
            crops = [self.masker(c)[0] for c in crops]
        vectors = self.embedder.embed(crops)
        labelled = []
        for detection, vector, crop in zip(detections, vectors, crops):
            match = self.gallery.identify(vector, self.threshold, self.margin)
            if self.guard is not None:
                match = self.guard.refine(vector, match, self.threshold)
            if self.colour is not None:
                match = self.colour.refine(crop, match, self.threshold)
            detection.match = match
            track = f"{clip_id}:{detection.track_id}"
            if self.observer is not None:
                self.observer(track, crop, match)
            self.voter.add(track, match)
            detection.sku = self.voter.decision(track) if detection.track_id is not None else match.product_id
            labelled.append(detection)
        return labelled
