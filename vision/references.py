"""Vision side of few-shot product learning (runs inside vision.run).

ReferenceSync     mirrors the backend's ACTIVE references into the live gallery (augmented views,
                  embeddings cached on disk), drops REJECTED ones, refits the look-alike guard and
                  reports gallery status back to staff.
EnrollmentWorker  turns each restocked unit's before/after stills into a reference crop.
TeachCollector    during staff teach mode, keeps diverse views of the product being shown.
All three talk to the backend through an injected HTTP client (httpx.Client in production).
"""
import base64
import time
from pathlib import Path
import numpy as np
from vision.fewshot import change_box, crop_box, augment, sharpness, DiversityFilter, LookalikeGuard
from vision.association import roi_pixels

def _decode(data):
    import cv2
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)

def _encode(image):
    import cv2
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise ValueError("Could not encode reference crop")
    return base64.b64encode(buf.tobytes()).decode()

class ReferenceSync:
    def __init__(self, api, gallery, embedder, cache_dir, guard=None, report=True):
        self.api, self.gallery, self.embedder = api, gallery, embedder
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.guard = guard or LookalikeGuard()
        self.loaded = set()
        self.report = report

    def _vectors(self, ref):
        cached = self.cache / f"{ref['id']}.npz"
        if cached.is_file():
            data = np.load(cached, allow_pickle=False)
            return data["vectors"], data["views"].tolist()
        image = _decode(self.api.get(f"/devices/vision-1/catalogue-file/{ref['file']}").content)
        if image is None:
            raise ValueError("Reference image could not be decoded")
        views = augment(image)
        vectors = self.embedder.embed([v for _, v in views])
        np.savez_compressed(cached, vectors=vectors, views=np.array([n for n, _ in views]))
        return vectors, [n for n, _ in views]

    def sync(self):
        response = self.api.get("/devices/vision-1/references")
        response.raise_for_status()
        changed = False
        for ref in response.json():
            prefix = f"live:{ref['id']}:"
            if ref["status"] == "ACTIVE" and ref["id"] not in self.loaded:
                try:
                    vectors, views = self._vectors(ref)
                except Exception:
                    continue  # retried next sync; never blocks capture
                self.gallery.add(ref["product_id"], vectors, [prefix + v for v in views])
                self.loaded.add(ref["id"])
                changed = True
            elif ref["status"] != "ACTIVE" and ref["id"] in self.loaded:
                self.gallery.remove_sources(prefix)
                self.loaded.discard(ref["id"])
                changed = True
        if changed:
            self.guard.fit(self.gallery)
            if self.report:
                status = {"version": self.gallery.version, "products": self.gallery.products,
                          "live_references": len(self.loaded), **self.guard.report()}
                try:
                    self.api.post("/devices/vision-1/gallery-status", json=status)
                except Exception:
                    pass
        return changed

class EnrollmentWorker:
    def __init__(self, api, min_side=24, min_sharpness=15.0):
        self.api, self.min_side, self.min_sharpness = api, min_side, min_sharpness

    def _fail(self, job, reason):
        self.api.post(f"/devices/vision-1/enrollment-jobs/{job['unit_id']}/failed", json={"reason": reason})
        return reason

    def process(self, job):
        before = _decode(self.api.get(f"/devices/vision-1/catalogue-file/{job['before']}").content)
        after = _decode(self.api.get(f"/devices/vision-1/catalogue-file/{job['after']}").content)
        if before is None or after is None:
            return self._fail(job, "UNREADABLE_STILL")
        roi = roi_pixels(job["roi"], after.shape[1], after.shape[0])
        found = change_box(before, after, roi)
        if found["box"] is None:
            return self._fail(job, found["reason"])
        crop = crop_box(after, found["box"])
        if min(crop.shape[:2]) < self.min_side:
            return self._fail(job, "TOO_SMALL")
        quality = {"sharpness": round(sharpness(crop), 1), "area_frac": round(found["area_frac"], 4), "contours": found.get("contours")}
        if quality["sharpness"] < self.min_sharpness:
            return self._fail(job, "BLURRED")
        self.api.post("/devices/vision-1/references", json={"product_id": job["product_id"], "source": "RESTOCK_AUTO",
                                                             "image_b64": _encode(crop), "box": list(found["box"]),
                                                             "quality": quality, "unit_id": job["unit_id"]}).raise_for_status()
        return "OK"

    def run_once(self):
        response = self.api.get("/devices/vision-1/enrollment-jobs")
        response.raise_for_status()
        results = {}
        for job in response.json():
            try:
                results[job["unit_id"]] = self.process(job)
            except Exception as exc:
                results[job["unit_id"]] = "ERROR:" + type(exc).__name__  # retried next round
        return results

class TeachCollector:
    """Staff turn the product in front of the camera; keep the largest item crop when it adds a new view."""

    def __init__(self, api, embedder, interval=0.4, max_similarity=0.95):
        self.api, self.embedder, self.interval = api, embedder, interval
        self.filters, self.last = {}, 0.0
        self.max_similarity = max_similarity

    def observe(self, teach, image, detections, now=None):
        now = now or time.time()
        if not teach or not detections or now - self.last < self.interval or now > teach["until"]:
            return None
        self.last = now
        box = max((d.box for d in detections), key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
        crop = crop_box(image, box)
        if min(crop.shape[:2]) < 24 or sharpness(crop) < 15:
            return None
        vector = self.embedder.embed([crop])[0]
        keep = self.filters.setdefault(teach["teach_id"], DiversityFilter(max_refs=teach.get("remaining", 20), max_similarity=self.max_similarity))
        if not keep.accept(teach["product_id"], vector):
            return None
        response = self.api.post("/devices/vision-1/references", json={"product_id": teach["product_id"], "source": "TEACH",
                                                                       "image_b64": _encode(crop), "box": list(box),
                                                                       "teach_id": teach["teach_id"],
                                                                       "quality": {"sharpness": round(sharpness(crop), 1)}})
        return response.status_code
