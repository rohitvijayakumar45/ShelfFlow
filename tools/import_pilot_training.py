"""Bring the earlier system's hand-checked shelf-pilot frames into the Train products page.

data/shelf-pilot/labelled holds full camera frames with one box per product (sanitizer, vicks,
sunscreen; mixed clips have all three); data/shelf-pilot/objects/<product>/<frame>_<k>.png says
which product box k is. They become one "imported" recording (several products per frame, the
original train/val split kept), and the 25 Oct empty-tray frames become an empty-tray recording.
After this, every model trained on the page knows these products as well.

Frames are scaled to the camera's current 320x240 so training stays at frame size 320.

Run: python -m tools.import_pilot_training [--force]
"""
import argparse
import shutil
import time
from pathlib import Path

import cv2
import numpy as np

from vision import training as T

PILOT = T.ROOT / "data" / "shelf-pilot"
SESSIONS = T.DATA / "sessions"
SIZE = (320, 240)
CLASS_IDS = {0: "sanitizer", 1: "vicks", 2: "sunscreen"}  # the pilot labels' own class order (checked against the crops)
PRODUCTS = "20261016-000000-pilot"
EMPTY = "20261025-013439-pilot-empty"


def crop_products():
    """{(frame stem, box index): product} from the per-product crop folders."""
    out = {}
    for crop in PILOT.glob("objects/*/*/*.png"):
        stem, k = crop.stem.rsplit("_", 1)
        out[(stem, int(k))] = crop.parent.name
    return out


def fresh(sid, force):
    path = SESSIONS / sid
    if path.exists():
        if not force:
            print(f"{sid}: already imported (use --force to redo)")
            return None
        shutil.rmtree(path)
    (path / "frames").mkdir(parents=True)
    return T.Session(path)


def import_products(force):
    s = fresh(PRODUCTS, force)
    if s is None:
        return
    crops, w, h = crop_products(), *SIZE
    mismatch = 0
    for split in ("train", "val"):
        for label in sorted((PILOT / "labelled/labels" / split).glob("*.txt")):
            image = cv2.imread(str(PILOT / "labelled/images" / split / f"{label.stem}.jpg"))
            if image is None:
                continue
            jpeg = cv2.imencode(".jpg", cv2.resize(image, SIZE, interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
            objects = []
            for k, row in enumerate(label.read_text(encoding="ascii").split("\n")):
                if not row.strip():
                    continue
                c, cx, cy, bw, bh = row.split()
                pid = crops.get((label.stem, k), CLASS_IDS[int(c)])
                mismatch += pid != CLASS_IDS[int(c)]
                cx, cy, bw, bh = float(cx) * w, float(cy) * h, float(bw) * w, float(bh) * h
                objects.append([pid, round(cx - bw / 2, 1), round(cy - bh / 2, 1), round(cx + bw / 2, 1), round(cy + bh / 2, 1)])
            i = len(s.frames)
            s.frame_path(i).write_bytes(jpeg)
            s.frames.append({"i": i, "sharp": T.sharpness(jpeg), "split": split, "objects": objects, "flags": [], "source": label.stem})
    products = sorted({o[0] for f in s.frames for o in f["objects"]})
    s.meta = {"kind": "imported", "product_ids": products, "product_name": "Earlier system (16 Oct clips)", "created": time.time(),
              "width": w, "height": h, "status": "LABELLED", "blur_threshold": 0, "source": "data/shelf-pilot/labelled"}
    s.save()
    print(f"{PRODUCTS}: {len(s.frames)} frames, products {products}, per product {s.summary()['per_product']}"
          + (f", {mismatch} boxes where the crop folder disagreed with the class id (crop folder used)" if mismatch else ""))


def import_empty(force):
    images = sorted((PILOT / "negatives/empty-run1/images").glob("*.jpg"))
    if not images:
        return
    s = fresh(EMPTY, force)
    if s is None:
        return
    for i, image in enumerate(images):
        data = image.read_bytes()
        frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if frame.shape[1::-1] != SIZE:
            data = cv2.imencode(".jpg", cv2.resize(frame, SIZE, interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
        s.frame_path(i).write_bytes(data)
        s.frames.append({"i": i, "sharp": T.sharpness(data), "box": None, "flags": [], "source": image.stem})
    s.meta = {"kind": "background", "product_name": "Empty tray (earlier system, 25 Oct)", "created": time.time(),
              "width": SIZE[0], "height": SIZE[1], "status": "RECORDED", "blur_threshold": 0, "source": "data/shelf-pilot/negatives/empty-run1"}
    s.save()
    print(f"{EMPTY}: {len(s.frames)} empty-tray frames")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="re-import, replacing the earlier import")
    a = parser.parse_args()
    import_products(a.force)
    import_empty(a.force)


if __name__ == "__main__":
    main()
