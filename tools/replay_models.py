"""Replay recorded shelf evidence through a candidate detector + gallery before switching to it.

Every staff-reviewed interaction is a labelled example: the frames are in the evidence store and
the confirmed product is in the database. This tool re-runs those frames through the models you
want to try and compares the result with (a) what staff confirmed and (b) what the recorded
models said at the time (candidate top_product), so a model change is judged on this shelf's
own history instead of on a benchmark.

  python -m tools.replay_models --db data/integration/store.db --evidence evidence \
      --item-weights models/shelf-pilot-item-detector.pt --gallery data/gallery.npz --device cuda:0

Read-only: nothing is written to the database or the evidence store.
"""
import argparse
import json
import os
from collections import Counter
from pathlib import Path

def labelled_events(db_url, since=0.0):
    """[(event payload, truth product or None for dismissed, tray roi)] from reviewed interactions."""
    from sqlalchemy import create_engine, text
    engine = create_engine(db_url)
    rows = []
    with engine.connect() as c:
        trays = {r.id: json.loads(r.roi) if isinstance(r.roi, str) else r.roi for r in c.execute(text("select id, roi from trays"))}
        decided = {r.hypothesis_id for r in c.execute(text("select hypothesis_id from review_decisions"))}
        for h in c.execute(text("select id, event_id, tray_id, status, proposal from interaction_hypotheses")):
            if h.id not in decided or h.status not in ("CONFIRMED", "CORRECTED", "DISMISSED"):
                continue
            e = c.execute(text("select payload, received from sensor_events where id=:id"), {"id": h.event_id}).first()
            if not e or e.received < since:
                continue
            payload = json.loads(e.payload) if isinstance(e.payload, str) else e.payload
            proposal = json.loads(h.proposal) if isinstance(h.proposal, str) else h.proposal
            truth = None if h.status == "DISMISSED" else proposal.get("product_id")
            rows.append((payload, truth, trays.get(h.tray_id)))
    return rows

def predict(frames, roi, detector, identifier, clip):
    import cv2
    from vision.association import roi_pixels
    votes = Counter()
    for path in frames:
        image = cv2.imread(str(path))
        if image is None:
            continue
        x1, y1, x2, y2 = roi_pixels(roi, image.shape[1], image.shape[0])
        crop = image[y1:y2, x1:x2]
        detections = detector.detect(crop)
        if detections:
            for d in identifier.label(crop, detections, clip):
                if d.sku != "UNKNOWN":
                    votes[d.sku] += 1
    return votes.most_common(1)[0][0] if votes else None

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", required=True, help="SQLite file or SQLAlchemy URL"); p.add_argument("--evidence", default="evidence")
    p.add_argument("--item-weights", required=True); p.add_argument("--gallery", required=True)
    p.add_argument("--threshold", type=float, default=0.6); p.add_argument("--margin", type=float, default=0.05)
    p.add_argument("--device", default="cpu"); p.add_argument("--since", type=float, default=0.0)
    p.add_argument("--output", default="reports/model-replay.json")
    a = p.parse_args()
    url = a.db if "://" in a.db else "sqlite:///" + Path(a.db).resolve().as_posix()
    from vision.detector import AgnosticDetector
    from vision.identify import Gallery, GalleryIdentifier
    from vision.embedders import make_embedder
    from vision.segment import Masker
    gallery = Gallery.load(a.gallery)
    identifier = GalleryIdentifier(gallery, make_embedder(gallery.embedder_name, a.device), a.threshold, a.margin,
                                   masker=Masker(gallery.mask, a.device) if gallery.mask != "none" else None)
    detector = AgnosticDetector(a.item_weights, a.device)
    root = Path(a.evidence)
    results = []
    for payload, truth, roi in labelled_events(url, a.since):
        frames = [root / r for r in payload.get("evidence_refs", []) if r.endswith(".jpg") and (root / r).is_file()]
        if not frames or not roi:
            continue
        recorded = next((c.get("top_product") for c in payload.get("candidates") or [] if c.get("top_product")), None)
        new = predict(frames, roi, detector, identifier, payload["event_id"])
        results.append({"event_id": payload["event_id"], "truth": truth, "recorded_top": recorded, "replayed": new,
                        "recorded_models": payload.get("model_versions") or {}, "frames": len(frames)})
    scored = [r for r in results if r["truth"]]
    def rate(key):
        named = [r for r in scored if r[key]]
        return {"named": len(named), "right": sum(r[key] == r["truth"] for r in named),
                "precision": round(sum(r[key] == r["truth"] for r in named) / len(named), 3) if named else None,
                "coverage": round(len(named) / len(scored), 3) if scored else None}
    report = {"events": len(results), "with_product": len(scored), "dismissed": len(results) - len(scored),
              "recorded": rate("recorded_top"), "replayed": rate("replayed"),
              "replayed_on_dismissed": sum(1 for r in results if not r["truth"] and r["replayed"]),
              "candidate": {"item_weights": a.item_weights, "gallery": gallery.version, "embedder": gallery.embedder_name,
                            "mask": gallery.mask, "threshold": a.threshold, "margin": a.margin},
              "rows": results,
              "note": "Only staff-reviewed events; hard cases are over-represented. Precision = right / named; coverage = named / events with a product."}
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    Path(a.output).write_text(json.dumps(report, indent=1))
    print(json.dumps({k: report[k] for k in ("events", "with_product", "recorded", "replayed", "replayed_on_dismissed")}, indent=2))

if __name__ == "__main__":
    main()
