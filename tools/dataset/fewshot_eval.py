"""How many reference photos does a new product need? (few-shot gallery evaluation)

Builds galleries from k crops per product (session-grouped train folder), with and without
augmented views, and scores them on a separate session's crops. One product is also held out
of the gallery to measure how often an unknown item is wrongly accepted.

  python -m tools.dataset.fewshot_eval --train data/shelf-pilot/objects/train --test data/shelf-pilot/objects/val

Frame-level, single-crop numbers. Not shopping accuracy; track voting and weight are separate.
"""
import argparse
import json
from pathlib import Path
import numpy as np
from vision.identify import Gallery, UNKNOWN
from vision.fewshot import augment

def load(folder):
    import cv2
    data = {}
    for product in sorted(p for p in Path(folder).iterdir() if p.is_dir()):
        data[product.name] = [cv2.imread(str(f)) for f in sorted(product.glob("*.png")) + sorted(product.glob("*.jpg"))]
    return data

def evaluate(train_vectors, test_vectors, k, use_aug, threshold, margin, holdout, seed):
    rng = np.random.default_rng(seed)
    gallery = Gallery("eval")
    for product, refs in train_vectors.items():
        if product == holdout:
            continue
        picks = rng.choice(len(refs), size=min(k, len(refs)), replace=False)
        for i in picks:
            views = refs[i] if use_aug else refs[i][:1]
            gallery.add(product, views, [f"live:{product}{i}:{n}" for n in range(len(views))])
    counts = {"known": 0, "correct": 0, "rejected": 0, "wrong": 0, "top1": 0, "unknown": 0, "unknown_accepted": 0}
    for product, vectors in test_vectors.items():
        for v in vectors:
            match = gallery.identify(v, threshold, margin)
            if product == holdout:
                counts["unknown"] += 1
                counts["unknown_accepted"] += match.known
                continue
            counts["known"] += 1
            counts["top1"] += match.top == product
            counts["correct"] += match.product_id == product
            counts["rejected"] += match.product_id == UNKNOWN
            counts["wrong"] += match.known and match.product_id != product
    return counts

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--train", required=True); p.add_argument("--test", required=True)
    p.add_argument("--device", default="cuda:0"); p.add_argument("--threshold", type=float, default=0.68)
    p.add_argument("--margin", type=float, default=0.06); p.add_argument("--repeats", type=int, default=10)
    p.add_argument("--shots", default="1,3,5,10"); p.add_argument("--output", default="reports/fewshot-eval.json")
    a = p.parse_args()
    from vision.identify import Dinov2Embedder
    embedder = Dinov2Embedder(a.device)
    train, test = load(a.train), load(a.test)
    train_vectors = {prod: [embedder.embed([img for _, img in augment(crop)]) for crop in crops] for prod, crops in train.items()}
    test_vectors = {prod: embedder.embed(crops) for prod, crops in test.items()}
    rows = []
    for k in [int(x) for x in a.shots.split(",")] + [10_000]:
        for use_aug in (False, True):
            for holdout in [None] + sorted(train):
                total = {}
                for seed in range(a.repeats if k < 10_000 else 1):
                    c = evaluate(train_vectors, test_vectors, k, use_aug, a.threshold, a.margin, holdout, seed)
                    for key, value in c.items():
                        total[key] = total.get(key, 0) + value
                rows.append({"shots": "all" if k == 10_000 else k, "augmented": use_aug, "held_out": holdout,
                             "known_correct": round(total["correct"] / total["known"], 3) if total["known"] else None,
                             "known_top1": round(total["top1"] / total["known"], 3) if total["known"] else None,
                             "known_rejected": round(total["rejected"] / total["known"], 3) if total["known"] else None,
                             "known_wrong": round(total["wrong"] / total["known"], 3) if total["known"] else None,
                             "unknown_accepted": round(total["unknown_accepted"] / total["unknown"], 3) if total["unknown"] else None})
    report = {"train": a.train, "test": a.test, "threshold": a.threshold, "margin": a.margin, "repeats": a.repeats,
              "products": {k: len(v) for k, v in train.items()}, "test_crops": {k: len(v) for k, v in test.items()},
              "rows": rows, "note": "Frame-level single crops from a separate session. Not shopping accuracy."}
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    Path(a.output).write_text(json.dumps(report, indent=1))
    for r in rows:
        if r["held_out"] is None:
            print(f"shots={r['shots']!s:>3} aug={r['augmented']!s:5}  correct={r['known_correct']}  top1={r['known_top1']}  rejected={r['known_rejected']}  wrong={r['known_wrong']}")
    for r in rows:
        if r["held_out"]:
            print(f"shots={r['shots']!s:>3} aug={r['augmented']!s:5}  held-out {r['held_out']:9}: unknown accepted as another product = {r['unknown_accepted']}")

if __name__ == "__main__":
    main()
