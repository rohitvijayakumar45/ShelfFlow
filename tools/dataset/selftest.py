"""End-to-end install check of the recognition pipeline on SYNTHETIC images.

Generates labelled frames with drawn "products", then runs crop -> make-yaml -> single-class
YOLO26 training (a few epochs) -> validation -> object crops -> DINOv2 enrol -> held-out
gallery evaluation. It proves the tools, GPU and model downloads work together.
Its numbers say nothing about accuracy on real camera images.
"""
import argparse
import json
import random
from pathlib import Path
import cv2
import numpy as np
from tools.dataset.recognition import crop_dataset, make_yaml, object_crops, enroll, gallery_eval

PRODUCTS = {  # name: (BGR colour, pattern)
    "coffee": ((30, 60, 160), "stripes"), "chips": ((40, 170, 220), "dots"),
    "bar": ((90, 40, 120), "plain"), "soap": ((200, 180, 60), "checks"),
}

def draw_item(img, name, x, y, w, h, rng):
    color, pattern = PRODUCTS[name]
    cv2.rectangle(img, (x, y), (x + w, y + h), color, -1)
    if pattern == "stripes":
        for yy in range(y, y + h, 8):
            cv2.line(img, (x, yy), (x + w, yy), (255, 255, 255), 2)
    elif pattern == "dots":
        for _ in range(12):
            cv2.circle(img, (int(rng.integers(x, x + w)), int(rng.integers(y, y + h))), 4, (20, 20, 20), -1)
    elif pattern == "checks":
        for yy in range(y, y + h, 10):
            for xx in range(x + (yy // 10 % 2) * 10, x + w, 20):
                cv2.rectangle(img, (xx, yy), (min(xx + 10, x + w), min(yy + 10, y + h)), (255, 255, 255), -1)
    cv2.putText(img, name[:3].upper(), (x + 4, y + h // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 2)

def generate(root, frames, seed=0):
    rng = np.random.default_rng(seed)
    names = list(PRODUCTS)
    for split, count in (("train", int(frames * 0.7)), ("val", int(frames * 0.15)), ("test", int(frames * 0.15))):
        (root / "images" / split).mkdir(parents=True, exist_ok=True)
        (root / "labels" / split).mkdir(parents=True, exist_ok=True)
        for i in range(count):
            img = rng.integers(60, 110, (480, 640, 3)).astype(np.uint8)
            cv2.GaussianBlur(img, (7, 7), 0, dst=img)
            rows = []
            for half in (0, 1):  # each tray (left/right half) holds 0-3 items, kept inside its half
                for _ in range(int(rng.integers(0, 4))):
                    w, h = int(rng.integers(50, 110)), int(rng.integers(60, 140))
                    x = int(rng.integers(half * 320 + 5, half * 320 + 315 - w)); y = int(rng.integers(5, 475 - h))
                    name = names[int(rng.integers(0, len(names)))]
                    draw_item(img, name, x, y, w, h, rng)
                    rows.append(f"{names.index(name)} {(x + w / 2) / 640:.6f} {(y + h / 2) / 480:.6f} {w / 640:.6f} {h / 480:.6f}")
            cv2.imwrite(str(root / "images" / split / f"s{i:04}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
            (root / "labels" / split / f"s{i:04}.txt").write_text("\n".join(rows) + ("\n" if rows else ""))
    return names

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--device", default="cpu"); p.add_argument("--frames", type=int, default=120)
    p.add_argument("--epochs", type=int, default=5); p.add_argument("--output", default="data/selftest")
    a = p.parse_args()
    out = Path(a.output); full = out / "full"
    names = generate(full, a.frames)
    manifest = crop_dataset(full, {"tray-a": [0, 0, 0.5, 1], "tray-b": [0.5, 0, 1, 1]}, out / "crops", single_class=True)
    data_yaml = make_yaml(out / "crops", [], out / "items.yaml", single_class=True)
    from ultralytics import YOLO
    model = YOLO("yolo26n.pt")
    model.train(data=str(data_yaml), epochs=a.epochs, imgsz=320, batch=8, device=a.device, single_cls=True, seed=42,
                project=str((out / "runs").resolve()), name="items", exist_ok=True, verbose=False, plots=False)
    metrics = YOLO(str(out / "runs" / "items" / "weights" / "best.pt")).val(data=str(data_yaml), split="test", imgsz=320, device=a.device, verbose=False, plots=False)
    counts = object_crops(full, names, out / "objects")
    from vision.identify import Dinov2Embedder
    embedder = Dinov2Embedder("cuda" if str(a.device) not in ("cpu",) else "cpu")
    gallery = enroll(out / "objects" / "train", out / "gallery.npz", embedder)
    report = gallery_eval(out / "objects" / "train", out / "objects" / "test", ["soap"], embedder, max_error=0.01)
    summary = {"SYNTHETIC_DATA_NOT_ACCURACY": True, "crops": len(manifest["crops"]), "skipped_partial": manifest["skipped_partial"],
               "detector_test_map50": float(metrics.box.map50), "detector_test_recall": float(metrics.box.mr),
               "object_crops": counts, "gallery": gallery, "gallery_eval_chosen": report["chosen"],
               "held_out": report["held_out_from_gallery"]}
    (out / "selftest-summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()
