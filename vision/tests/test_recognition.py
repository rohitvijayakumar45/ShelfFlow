"""Open-set recognition: crops/labels, gallery identification, voting, held-out evaluation.

A colour-based stand-in embedder keeps these tests offline and deterministic. It exercises the
pipeline logic only; real accuracy comes from DINOv2 on real camera crops (gallery-eval).
"""
import json
import cv2
import numpy as np
import pytest
from vision.identify import Gallery, TrackVoter, GalleryIdentifier, UNKNOWN, Match
from vision.detector import Detection
from vision.association import ReviewFirstAssociator
from tools.dataset.recognition import transform_box, crop_dataset, object_crops, enroll, evaluate_open_set, load_folder

COLORS = {"coffee": (40, 40, 200), "chips": (40, 200, 40), "bar": (200, 40, 40), "soap": (70, 190, 90)}  # soap deliberately resembles chips

class ColourEmbedder:
    name = "test-colour"
    dim = 3
    def embed(self, images):
        out = np.array([img.reshape(-1, 3).mean(0) for img in images], np.float32) - 120.0
        return out / np.linalg.norm(out, axis=1, keepdims=True)

def patch(color, noise=0, seed=0):
    rng = np.random.default_rng(seed)
    img = np.full((32, 32, 3), color, np.float32) + rng.normal(0, noise, (32, 32, 3))
    return np.clip(img, 0, 255).astype(np.uint8)

def write_refs(root, products, n=10, noise=6):
    for p in products:
        (root / p).mkdir(parents=True, exist_ok=True)
        for i in range(n):
            cv2.imwrite(str(root / p / f"{i}.png"), patch(COLORS[p], noise, seed=i))

def test_transform_box_clips_and_drops_partial_items():
    roi = (100, 0, 200, 100)
    row, vis = transform_box((3, 120, 10, 160, 50), roi, 0.4)
    assert vis == 1 and row == pytest.approx((3, 0.4, 0.3, 0.4, 0.4))
    row, vis = transform_box((3, 80, 10, 120, 50), roi, 0.4)  # half inside: clipped to the crop edge
    assert vis == pytest.approx(0.5) and row[1] == pytest.approx(0.1) and row[3] == pytest.approx(0.2)
    row, vis = transform_box((3, 70, 10, 110, 50), roi, 0.4)  # 25 % inside: dropped
    assert row is None and vis == pytest.approx(0.25)

def test_crop_dataset_writes_tray_crops_and_skips_ambiguous_partials(tmp_path):
    src = tmp_path / "full"
    (src / "images" / "train").mkdir(parents=True); (src / "labels" / "train").mkdir(parents=True)
    cv2.imwrite(str(src / "images" / "train" / "f1.jpg"), np.zeros((100, 200, 3), np.uint8))
    # One item fully in the left tray, one item mostly in the right tray with a sliver across the split.
    (src / "labels" / "train" / "f1.txt").write_text("0 0.25 0.5 0.2 0.4\n2 0.53 0.5 0.1 0.4\n")
    m = crop_dataset(src, {"tray-a": [0, 0, 0.5, 1], "tray-b": [0.5, 0, 1, 1]}, tmp_path / "crops", single_class=True)
    # The left crop holds a 20 % sliver of the right item, so it is skipped; the right crop keeps 80 % of it.
    assert m["skipped_partial"] == 1 and [c["tray"] for c in m["crops"]] == ["tray-b"]
    label = (tmp_path / "crops" / "labels" / "train" / "f1__tray-b.txt").read_text().split()
    assert label[0] == "0" and float(label[1]) == pytest.approx(0.08) and float(label[3]) == pytest.approx(0.16)
    assert (tmp_path / "crops" / "images" / "train" / "f1__tray-b.png").is_file()

def test_object_crops_sorted_by_product(tmp_path):
    src = tmp_path / "full"
    (src / "images" / "val").mkdir(parents=True); (src / "labels" / "val").mkdir(parents=True)
    img = np.zeros((100, 100, 3), np.uint8); img[20:60, 20:60] = COLORS["coffee"]
    cv2.imwrite(str(src / "images" / "val" / "a.png"), img)
    (src / "labels" / "val" / "a.txt").write_text("1 0.4 0.4 0.4 0.4\n")
    counts = object_crops(src, ["chips", "coffee"], tmp_path / "objects")
    assert counts == {"val": {"coffee": 1}} and len(list((tmp_path / "objects" / "val" / "coffee").glob("*.png"))) == 1

def test_gallery_identifies_known_and_rejects_unknown(tmp_path):
    write_refs(tmp_path / "refs", ["coffee", "chips", "bar"])
    result = enroll(tmp_path / "refs", tmp_path / "g.npz", ColourEmbedder(), min_refs=8)
    assert result["products"] == {"bar": 10, "chips": 10, "coffee": 10} and not result["few_references"]
    gallery = Gallery.load(tmp_path / "g.npz")
    assert gallery.version == result["version"]
    e = ColourEmbedder()
    assert gallery.identify(e.embed([patch(COLORS["chips"], 4, 99)])[0], 0.9, 0.02).product_id == "chips"
    assert gallery.identify(e.embed([patch(COLORS["soap"])])[0], 0.95, 0.02).product_id == UNKNOWN  # never enrolled

def test_margin_rejects_lookalike_products():
    g = Gallery("test-colour")
    g.add("a", [[1, 0, 0]], ["a"]); g.add("b", [[0.999, 0.0447, 0]], ["b"])
    v = np.array([1, 0.02, 0], np.float32); v /= np.linalg.norm(v)
    assert g.identify(v, 0.5, 0.0).product_id in ("a", "b")
    assert g.identify(v, 0.5, 0.05).product_id == UNKNOWN

def test_tampered_gallery_refused(tmp_path):
    g = Gallery("test-colour"); g.add("a", [[1, 0, 0]], ["x"]); g.save(tmp_path / "g.npz")
    meta = json.loads((tmp_path / "g.json").read_text()); meta["version"] = "gallery-forged"
    (tmp_path / "g.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError):
        Gallery.load(tmp_path / "g.npz")

def test_track_voting_needs_repeated_agreement():
    v = TrackVoter(min_votes=3, min_share=0.6)
    for p in ["chips", UNKNOWN, "chips"]:
        v.add("t", Match(p, 0.9, 0.1, None))
    assert v.decision("t") == UNKNOWN  # two known votes are not enough
    v.add("t", Match("chips", 0.9, 0.1, None))
    assert v.decision("t") == "chips"
    for p in ["bar", "bar", "bar"]:
        v.add("t", Match(p, 0.9, 0.1, None))
    assert v.decision("t") == UNKNOWN  # split vote: no product reaches the required share

def test_identifier_labels_detections_through_votes(tmp_path):
    write_refs(tmp_path / "refs", ["coffee", "chips"])
    enroll(tmp_path / "refs", tmp_path / "g.npz", ColourEmbedder())
    ident = GalleryIdentifier(Gallery.load(tmp_path / "g.npz"), ColourEmbedder(), threshold=0.9, margin=0.02)
    frame = np.zeros((64, 64, 3), np.uint8); frame[0:48, 0:48] = COLORS["coffee"]  # padding stays inside the item
    seen = []
    for _ in range(3):
        d = ident.label(frame, [Detection("UNKNOWN", 0.8, (8, 8, 40, 40), 7)], "clip")[0]
        seen.append(d.sku)
    assert seen == [UNKNOWN, UNKNOWN, "coffee"]
    candidate = ReviewFirstAssociator().observe([d], [], "clip")[0]
    assert candidate.product_id == "coffee" and candidate.sku
    assert not ReviewFirstAssociator().observe([Detection("UNKNOWN", 0.8, (0, 0, 1, 1), 1)], [], "c")[0].sku

def test_identifier_refuses_mismatched_embedder(tmp_path):
    g = Gallery("other-model"); g.add("a", [[1, 0, 0]], ["x"])
    with pytest.raises(ValueError):
        GalleryIdentifier(g, ColourEmbedder(), 0.9, 0.02)

def test_open_set_evaluation_picks_safe_operating_point(tmp_path):
    write_refs(tmp_path / "refs", ["coffee", "chips", "bar"])
    write_refs(tmp_path / "query", ["coffee", "chips", "bar", "soap"], n=6, noise=10)
    e = ColourEmbedder()
    vectors, labels, sources = load_folder(tmp_path / "refs", e, products={"coffee", "chips"})  # bar held out
    g = Gallery(e.name)
    for p in sorted(set(labels)):
        idx = [i for i, l in enumerate(labels) if l == p]
        g.add(p, vectors[idx], [sources[i] for i in idx])
    qv, ql, _ = load_folder(tmp_path / "query", e)
    report = evaluate_open_set(g, qv, ql, np.arange(0.5, 0.9995, 0.005), [0.0, 0.05], max_error=0.0)
    assert report["unknown_products"] == ["bar", "soap"]
    chosen = report["chosen"]
    assert chosen and chosen["known_accuracy"] == 1.0 and chosen["unknown_accepted"] == 0.0
    loose = next(r for r in report["table"] if r["threshold"] == 0.5 and r["margin"] == 0.0)
    assert loose["unknown_accepted"] > 0  # a permissive threshold would wrongly accept unseen items
