"""Which embedding model and which crop cleaning work best on THIS shelf's crops?

Compares embedders (vision.embedders) x background masking (vision.segment) with the few-shot
protocol used in production: references come from one recording session (--train), queries from
a different session (--test), one product at a time is held out of the gallery as "unknown".

Metrics are threshold-free or use a per-model threshold, because cosine scales differ per model:
  top1@k        nearest reference is the right product (k reference photos per product, no rejection)
  auroc         how well the best similarity separates known-and-correct queries from unknown items
  known@5%      share of known queries accepted correctly when the threshold is set so that at most
                5% of unknown-item queries are accepted (threshold chosen on these same queries:
                optimistic, comparative only)

  python -m tools.dataset.embedder_bench --train data/shelf-pilot/objects/train --test data/shelf-pilot/objects/val \
      --embedders dinov2-small,dinov2-base,siglip2-base,clip-base,dinov3-small --masks none,grabcut,sam

Frame-level single crops, three products. A ranking aid, not shopping accuracy.
"""
import argparse
import json
import time
from pathlib import Path
import numpy as np
from vision.fewshot import augment
from tools.dataset.fewshot_eval import load

def auroc(positive, negative):
    if not positive or not negative:
        return None
    pos, neg = np.asarray(positive), np.asarray(negative)
    wins = (pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()
    return float(wins / (len(pos) * len(neg)))

def nearest(gallery_vectors, gallery_labels, query):
    sims = gallery_vectors @ query
    best = {}
    for label, sim in zip(gallery_labels, sims):
        best[label] = max(best.get(label, -1.0), float(sim))
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    return ranked[0]

def gallery_from(train_vectors, k, rng, skip=None):
    vectors, labels = [], []
    for product, refs in train_vectors.items():
        if product == skip:
            continue
        picks = rng.choice(len(refs), size=min(k, len(refs)), replace=False)
        for i in picks:
            vectors.append(refs[i])
            labels += [product] * len(refs[i])
    return np.vstack(vectors), labels

def score(train_vectors, test_vectors, shots, repeats, unknown_rate=0.05):
    rng = np.random.default_rng(0)
    out = {}
    for k in shots:
        hits = total = 0
        for _ in range(repeats if k < 10_000 else 1):
            g, labels = gallery_from(train_vectors, k, rng)
            for product, queries in test_vectors.items():
                for q in queries:
                    hits += nearest(g, labels, q)[0] == product
                    total += 1
        out[f"top1@{'all' if k >= 10_000 else k}"] = round(hits / total, 3)
    for k in (3, 10_000):
        correct_scores, unknown_scores, wrong_scores = [], [], []
        for _ in range(repeats if k < 10_000 else 1):
            for held in train_vectors:
                g, labels = gallery_from(train_vectors, k, rng, skip=held)
                for product, queries in test_vectors.items():
                    for q in queries:
                        top, sim = nearest(g, labels, q)
                        if product == held:
                            unknown_scores.append(sim)
                        elif top == product:
                            correct_scores.append(sim)
                        else:
                            wrong_scores.append(sim)
        tag = "all" if k >= 10_000 else k
        out[f"auroc@{tag}"] = None if auroc(correct_scores, unknown_scores) is None else round(auroc(correct_scores, unknown_scores), 3)
        threshold = float(np.quantile(unknown_scores, 1 - unknown_rate)) if unknown_scores else 1.0
        known = len(correct_scores) + len(wrong_scores)
        out[f"known@5%@{tag}"] = round(sum(s > threshold for s in correct_scores) / known, 3) if known else None
        out[f"wrong@5%@{tag}"] = round(sum(s > threshold for s in wrong_scores) / known, 3) if known else None
        out[f"threshold@{tag}"] = round(threshold, 3)
    return out

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--train", required=True); p.add_argument("--test", required=True)
    p.add_argument("--embedders", default="dinov2-small,dinov2-base,siglip2-base,clip-base,dinov3-small")
    p.add_argument("--masks", default="none,grabcut,sam"); p.add_argument("--device", default="cuda:0")
    p.add_argument("--shots", default="1,3,10"); p.add_argument("--repeats", type=int, default=10)
    p.add_argument("--output", default="reports/embedder-bench.json")
    a = p.parse_args()
    from vision.embedders import make_embedder, EmbedderUnavailable
    from vision.segment import Masker
    train, test = load(a.train), load(a.test)
    shots = [int(x) for x in a.shots.split(",")] + [10_000]
    masked = {}
    for method in a.masks.split(","):
        try:
            masker = Masker(method, a.device)
        except Exception as exc:
            print(f"mask {method}: unavailable ({type(exc).__name__}: {str(exc)[:120]})")
            continue
        start = time.perf_counter()
        masked[method] = ({p: [masker(c)[0] for c in crops] for p, crops in train.items()},
                          {p: [masker(c)[0] for c in crops] for p, crops in test.items()})
        n = sum(len(v) for v in train.values()) + sum(len(v) for v in test.values())
        print(f"mask {method}: {1000 * (time.perf_counter() - start) / n:.0f} ms/crop")
    rows = []
    for key in a.embedders.split(","):
        try:
            embedder = make_embedder(key, a.device)
        except EmbedderUnavailable as exc:
            print(f"{key}: unavailable - {exc}")
            rows.append({"embedder": key, "available": False, "error": str(exc)})
            continue
        for method, (tr, te) in masked.items():
            start = time.perf_counter()
            train_vectors = {prod: [embedder.embed([img for _, img in augment(c)]) for c in crops] for prod, crops in tr.items()}
            test_vectors = {prod: embedder.embed(crops) for prod, crops in te.items()}
            n = sum(len(v) for v in te.values())
            t0 = time.perf_counter()
            embedder.embed([c for crops in te.values() for c in crops])
            per_crop = 1000 * (time.perf_counter() - t0) / n
            row = {"embedder": key, "model": embedder.name, "mask": method, "available": True,
                   "ms_per_crop": round(per_crop, 1), **score(train_vectors, test_vectors, shots, a.repeats)}
            rows.append(row)
            print(f"{key:13s} {method:8s} " + "  ".join(f"{k}={v}" for k, v in row.items() if k.startswith(("top1", "auroc", "known@")) or k == "ms_per_crop"), flush=True)
        del embedder
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass
    usable = [r for r in rows if r.get("available")]
    best = max(usable, key=lambda r: (r["known@5%@all"] or 0, r["auroc@all"] or 0)) if usable else None
    report = {"train": a.train, "test": a.test, "products": {k: len(v) for k, v in train.items()},
              "test_crops": {k: len(v) for k, v in test.items()}, "rows": rows, "best": best,
              "note": "Frame-level single crops; known@5% uses a threshold chosen on the same queries (optimistic). Ranking aid only."}
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    Path(a.output).write_text(json.dumps(report, indent=1))
    if best:
        print(f"best: {best['embedder']} + {best['mask']}  known@5%={best['known@5%@all']}  auroc={best['auroc@all']}")

if __name__ == "__main__":
    main()
