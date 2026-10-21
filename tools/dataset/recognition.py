"""Training/enrolment tools for open-set recognition (single-class item detector + gallery).

Pipeline (see docs/recognition.md):
  label full frames -> crop (tray crops, labels transformed) -> make-yaml -> model train --single-class
  label full frames -> objects (per-product item crops) -> enroll (gallery) -> gallery-eval (held-out products)
"""
import json
import hashlib
from pathlib import Path
import numpy as np

IMAGE_TYPES = (".jpg", ".jpeg", ".png")

def _images(folder):
    return sorted(p for p in Path(folder).rglob("*") if p.suffix.lower() in IMAGE_TYPES)

def read_labels(path):
    rows = []
    if Path(path).is_file():
        for line in Path(path).read_text().splitlines():
            if line.strip():
                c, x, y, w, h = line.split()
                rows.append((int(c), float(x), float(y), float(w), float(h)))
    return rows

def to_pixels(row, width, height):
    c, x, y, w, h = row
    return c, (x - w / 2) * width, (y - h / 2) * height, (x + w / 2) * width, (y + h / 2) * height

def transform_box(box_px, roi_px, min_visible):
    """Map a full-frame pixel box into a crop. Returns (normalised row | None, visible fraction)."""
    c, x1, y1, x2, y2 = box_px
    rx1, ry1, rx2, ry2 = roi_px
    ix1, iy1, ix2, iy2 = max(x1, rx1), max(y1, ry1), min(x2, rx2), min(y2, ry2)
    area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    visible = inter / area if area else 0.0
    if visible < min_visible or inter == 0:
        return None, visible
    cw, ch = rx2 - rx1, ry2 - ry1
    return (c, ((ix1 + ix2) / 2 - rx1) / cw, ((iy1 + iy2) / 2 - ry1) / ch, (ix2 - ix1) / cw, (iy2 - iy1) / ch), visible

def crop_dataset(root, rois, output, min_visible=0.4, single_class=False):
    """Cut each labelled full frame into tray crops (the detector runs on crops at runtime).

    Crops where any box is only slightly inside (0 < visible < min_visible) are skipped:
    keeping their pixels without a label would teach the model that a partial item is background.
    Crops are written as PNG to avoid a second JPEG compression.
    """
    import cv2
    root, output = Path(root), Path(output)
    manifest = {"source": str(root.resolve()), "min_visible": min_visible, "single_class": single_class, "rois": rois, "crops": [], "skipped_partial": 0}
    for image_path in _images(root / "images"):
        split = image_path.relative_to(root / "images").parts[0]
        image = cv2.imread(str(image_path))
        if image is None:
            continue
        height, width = image.shape[:2]
        boxes = [to_pixels(r, width, height) for r in read_labels(root / "labels" / split / (image_path.stem + ".txt"))]
        for tray, (x1, y1, x2, y2) in rois.items():
            roi = (int(x1 * width), int(y1 * height), int(x2 * width), int(y2 * height))
            rows, partial = [], False
            for box in boxes:
                row, visible = transform_box(box, roi, min_visible)
                if row:
                    rows.append((0 if single_class else row[0],) + row[1:])
                elif 0 < visible:
                    partial = True
            if partial:
                manifest["skipped_partial"] += 1
                continue
            name = f"{image_path.stem}__{tray}"
            (output / "images" / split).mkdir(parents=True, exist_ok=True)
            (output / "labels" / split).mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(output / "images" / split / (name + ".png")), image[roi[1]:roi[3], roi[0]:roi[2]])
            (output / "labels" / split / (name + ".txt")).write_text("".join(f"{r[0]} {r[1]:.6f} {r[2]:.6f} {r[3]:.6f} {r[4]:.6f}\n" for r in rows))
            manifest["crops"].append({"file": f"{split}/{name}.png", "source": str(image_path.relative_to(root)), "tray": tray,
                                      "origin_px": roi[:2], "size_px": [roi[2] - roi[0], roi[3] - roi[1]], "boxes": len(rows)})
    output.mkdir(parents=True, exist_ok=True)
    (output / "crop-manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest

def object_crops(root, names, output, pad=0.1, min_side=16):
    """Cut every labelled item into output/<split>/<product_id>/ for gallery enrolment and evaluation."""
    import cv2
    root, output, counts = Path(root), Path(output), {}
    for image_path in _images(root / "images"):
        split = image_path.relative_to(root / "images").parts[0]
        image = cv2.imread(str(image_path))
        if image is None:
            continue
        height, width = image.shape[:2]
        for i, row in enumerate(read_labels(root / "labels" / split / (image_path.stem + ".txt"))):
            c, x1, y1, x2, y2 = to_pixels(row, width, height)
            px, py = (x2 - x1) * pad, (y2 - y1) * pad
            a, b, cc, d = max(0, int(x1 - px)), max(0, int(y1 - py)), min(width, int(x2 + px)), min(height, int(y2 + py))
            if cc - a < min_side or d - b < min_side:
                continue
            product = names[c]
            target = output / split / product
            target.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(target / f"{image_path.stem}_{i}.png"), image[b:d, a:cc])
            counts.setdefault(split, {}).setdefault(product, 0)
            counts[split][product] += 1
    return counts

def load_folder(folder, embedder, products=None, batch=32):
    """Embed folder/<product_id>/* images. Returns (vectors, labels, sources)."""
    import cv2
    vectors, labels, sources = [], [], []
    for product_dir in sorted(p for p in Path(folder).iterdir() if p.is_dir()):
        if products is not None and product_dir.name not in products:
            continue
        files = _images(product_dir)
        for start in range(0, len(files), batch):
            chunk = files[start:start + batch]
            images = [cv2.imread(str(f)) for f in chunk]
            ok = [(f, im) for f, im in zip(chunk, images) if im is not None]
            if ok:
                vectors.append(embedder.embed([im for _, im in ok]))
                labels += [product_dir.name] * len(ok)
                sources += [str(f) for f, _ in ok]
    return (np.vstack(vectors) if vectors else np.zeros((0, 1), np.float32)), labels, sources

def enroll(folder, output, embedder, min_refs=8):
    from vision.identify import Gallery
    vectors, labels, sources = load_folder(folder, embedder)
    if not labels:
        raise ValueError("No reference images found (expected folder/<product_id>/*.png)")
    gallery = Gallery(embedder.name)
    for product in sorted(set(labels)):
        idx = [i for i, l in enumerate(labels) if l == product]
        gallery.add(product, vectors[idx], [sources[i] for i in idx])
    version = gallery.save(output)
    thin = {p: n for p, n in gallery.products.items() if n < min_refs}
    return {"version": version, "products": gallery.products, "few_references": thin}

def evaluate_open_set(gallery, query_vectors, query_labels, thresholds, margins, max_error=0.01):
    """Sweep (threshold, margin). Known = product in gallery; unknown = held-out product.

    error = wrong product accepted (known mislabelled + unknown accepted) / all queries.
    The chosen point maximises known accuracy with error <= max_error. Frame-level only:
    track voting in deployment usually improves on these numbers but must be measured.
    """
    from vision.identify import UNKNOWN
    known_set = set(gallery.labels)
    scores = [sorted(gallery.scores(v).items(), key=lambda kv: -kv[1]) for v in query_vectors]
    table = []
    for t in thresholds:
        for m in margins:
            k_total = k_correct = k_wrong = u_total = u_accept = 0
            for ranked, truth in zip(scores, query_labels):
                top, sim = ranked[0]
                second = ranked[1][1] if len(ranked) > 1 else -1.0
                guess = top if sim >= t and sim - second >= m else UNKNOWN
                if truth in known_set:
                    k_total += 1
                    k_correct += guess == truth
                    k_wrong += guess not in (truth, UNKNOWN)
                else:
                    u_total += 1
                    u_accept += guess != UNKNOWN
            n = k_total + u_total
            table.append({"threshold": round(t, 3), "margin": round(m, 3), "known_accuracy": k_correct / k_total if k_total else None,
                          "known_rejected": (k_total - k_correct - k_wrong) / k_total if k_total else None,
                          "known_wrong": k_wrong / k_total if k_total else None, "unknown_accepted": u_accept / u_total if u_total else None,
                          "error": (k_wrong + u_accept) / n if n else None})
    ok = [r for r in table if r["error"] is not None and r["error"] <= max_error and r["known_accuracy"] is not None]
    chosen = max(ok, key=lambda r: (r["known_accuracy"], -r["error"])) if ok else None
    return {"queries": len(query_labels), "known_products": sorted(known_set), "unknown_products": sorted(set(query_labels) - known_set),
            "max_error": max_error, "chosen": chosen, "table": table,
            "note": "Frame-level, single crop. Not shopping accuracy. Choose thresholds from held-out sessions only."}

def gallery_eval(gallery_dir, query_dir, holdout, embedder, max_error=0.01):
    from vision.identify import Gallery
    holdout = set(holdout)
    all_products = {p.name for p in Path(gallery_dir).iterdir() if p.is_dir()}
    vectors, labels, sources = load_folder(gallery_dir, embedder, products=all_products - holdout)
    gallery = Gallery(embedder.name)
    for product in sorted(set(labels)):
        idx = [i for i, l in enumerate(labels) if l == product]
        gallery.add(product, vectors[idx], [sources[i] for i in idx])
    q_vectors, q_labels, _ = load_folder(query_dir, embedder)
    if not q_labels:
        raise ValueError("No query images")
    report = evaluate_open_set(gallery, q_vectors, q_labels, np.arange(0.30, 0.951, 0.01), [0.0, 0.02, 0.04, 0.06, 0.08, 0.10], max_error)
    report["held_out_from_gallery"] = sorted(holdout)
    return report

def make_yaml(dataset, names, output, single_class):
    dataset = Path(dataset).resolve()
    lines = [f"path: {dataset.as_posix()}", "train: images/train", "val: images/val", "test: images/test", "names:"]
    lines += ["  0: item"] if single_class else [f"  {i}: {n}" for i, n in enumerate(names)]
    Path(output).write_text("\n".join(lines) + "\n")
    return output
