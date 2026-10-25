"""Video-to-YOLO training: frame quality, SAM box flags, dataset split and the trainer service flow."""
import threading
import time

import cv2
import numpy as np
import pytest

from vision import training as T


def jpeg(image):
    return cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()


def textured(seed=0, blur=0):
    rng = np.random.default_rng(seed)
    image = (rng.random((480, 640, 3)) * 255).astype(np.uint8)
    image = cv2.resize(cv2.resize(image, (160, 120)), (640, 480), interpolation=cv2.INTER_NEAREST)
    return cv2.GaussianBlur(image, (0, 0), blur) if blur else image


def test_sharpness_ranks_motion_blur_below_sharp_frames():
    sharp, blurred = T.sharpness(jpeg(textured())), T.sharpness(jpeg(textured(blur=4)))
    assert sharp > 3 * blurred
    threshold = T.auto_blur_threshold([sharp] * 9 + [blurred])
    assert blurred < threshold <= sharp


def test_box_flags_and_inclusion_rule():
    frames = [{"i": k, "sharp": 100.0, "box": [100, 100, 200, 200]} for k in range(20)]
    frames[5]["box"] = None                   # SAM lost the product
    frames[9]["box"] = [0, 0, 640, 470]       # grabbed the whole scene
    frames[12]["box"] = [100, 100, 300, 300]  # 4x the neighbours' area
    frames[15]["sharp"] = 5.0                 # blurred
    T.flag_boxes(frames, 640, 480)
    assert "no_object" in frames[5]["flags"] and "huge" in frames[9]["flags"] and "jump" in frames[12]["flags"]
    use = [f["i"] for f in frames if T.included(f, 20.0, "product")]
    assert 5 not in use and 9 not in use and 12 not in use and 15 not in use and 0 in use
    frames[12]["manual"] = True               # staff override wins
    assert T.included(frames[12], 20.0, "product")
    assert T.included({"i": 0, "sharp": 50.0}, 20.0, "background")


def make_session(root, sid, kind, product, n, box=(100, 120, 260, 300)):
    path = root / sid
    (path / "frames").mkdir(parents=True)
    frames = []
    for k in range(n):
        (path / "frames" / f"{k:06d}.jpg").write_bytes(jpeg(textured(k)))
        frames.append({"i": k, "t": k / 12, "sharp": 100.0, "box": list(box) if kind == "product" else None, "flags": []})
    s = T.Session(path)
    s.meta = {"kind": kind, "product_id": product, "width": 640, "height": 480, "status": "LABELLED", "blur_threshold": None}
    s.frames = frames
    s.save()
    return s


def test_dataset_split_keeps_neighbouring_frames_together(tmp_path):
    a = make_session(tmp_path / "s", "a", "product", "sanitizer", 120)
    b = make_session(tmp_path / "s", "b", "product", "vicks", 90)
    bg = make_session(tmp_path / "s", "c", "background", None, 40)
    out = T.build_dataset([a, b, bg], tmp_path / "ds")
    assert out["classes"] == ["sanitizer", "vicks"]
    counts = out["counts"]
    assert counts["sanitizer"]["val"] > 0 and counts["vicks"]["train"] > counts["vicks"]["val"] > 0
    assert counts["background"]["train"] + counts["background"]["val"] == 40
    # A whole ~1 s chunk lands in one split, so validation frames are never twins of training frames.
    val = {p.stem for p in (tmp_path / "ds/images/val").glob("a_*.jpg")}
    for name in val:
        chunk = int(name.split("_")[1]) // T.CHUNK
        assert all(f"a_{k:06d}" in val for k in range(chunk * T.CHUNK, min(120, (chunk + 1) * T.CHUNK)))
    label = (tmp_path / "ds/labels/train").glob("b_*.txt").__next__().read_text().split()
    assert label[0] == "1" and abs(float(label[1]) - 180 / 640) < 1e-4
    assert (tmp_path / "ds/labels/train").glob("c_*.txt").__next__().read_text() == ""
    assert "names:" in (tmp_path / "ds/data.yaml").read_text()


def test_dataset_refuses_unlabelled_or_too_short(tmp_path):
    with pytest.raises(ValueError):
        T.build_dataset([make_session(tmp_path / "s", "x", "background", None, 10)], tmp_path / "d1")
    with pytest.raises(ValueError, match="Too few"):
        T.build_dataset([make_session(tmp_path / "s", "y", "product", "oat", 8)], tmp_path / "d2")


def test_new_model_keeps_parent_products_and_reads_imported_frames(tmp_path):
    a = make_session(tmp_path / "s", "a", "product", "sanitizer", 60)
    parent = T.build_dataset([a], tmp_path / "p")
    assert parent["classes"] == ["sanitizer"]
    # Imported multi-product recording with its own split; the sanitizer recording is gone (not chosen).
    m = make_session(tmp_path / "s", "m", "imported", None, 10)
    for k, f in enumerate(m.frames):
        f.update(split="val" if k < 3 else "train", objects=[["vicks", 10, 20, 110, 220], ["oat", 300, 40, 400, 200]])
    m.save()
    out = T.build_dataset([m], tmp_path / "c", parent=tmp_path / "p")
    assert out["classes"] == ["oat", "sanitizer", "vicks"] and out["inherited_frames"] == 60
    assert out["counts"]["vicks"] == {"train": 7, "val": 3}
    assert out["counts"]["sanitizer"]["train"] == parent["counts"]["sanitizer"]["train"]
    rows = (tmp_path / "c/labels/val/m_000000.txt").read_text().split("\n")
    assert rows[0].split()[0] == "2" and rows[1].split()[0] == "0"
    assert (tmp_path / "c/labels/train/a_000000.txt").read_text().split()[0] == "1"  # remapped from parent's 0


class FakeCamera:
    def __init__(self):
        self.condition = threading.Condition()
        self.jpeg, self.headers, self.serial, self.received = b"", {}, 0, 0.0
        self.stop = threading.Event()

    def feed(self, fps=60):
        k = 0
        while not self.stop.is_set():
            data = jpeg(textured(k % 7, blur=5 if k % 10 == 9 else 0))
            with self.condition:
                self.jpeg, self.headers = data, {"x-width": "640", "x-height": "480"}
                self.serial += 1
                self.received = time.monotonic()
                self.condition.notify_all()
            k += 1
            time.sleep(1 / fps)


def test_trainer_records_every_frame_and_reviews(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.auth import issue
    from tools import trainer
    monkeypatch.setattr(trainer, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setattr(trainer, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(trainer, "ACTIVE", tmp_path / "active.json")  # never build on (or train from) the real model in use
    camera = FakeCamera()
    trainer.S.camera, trainer.S.source = camera, "fake"
    threading.Thread(target=camera.feed, daemon=True).start()
    threading.Thread(target=trainer.pump, daemon=True).start()
    client = TestClient(trainer.app)
    auth = {"Authorization": "Bearer " + issue("staff", "tester")}
    try:
        assert client.get("/status").status_code in (401, 403)
        time.sleep(0.3)
        assert client.get("/status", headers=auth).json()["camera"]["connected"]
        assert client.post("/recordings", json={"kind": "product"}, headers=auth).status_code == 422
        sid = client.post("/recordings", json={"kind": "product", "product_id": "oat", "product_name": "Oat"}, headers=auth).json()["id"]
        start = camera.serial
        time.sleep(1.0)
        live = client.get("/status", headers=auth).json()["recording"]
        assert live["frames"] > 20
        summary = client.post("/recordings/stop", json={}, headers=auth).json()
        produced = camera.serial - start
        assert summary["frame_count"] >= produced - 3          # no frame lost to the recorder
        assert summary["width"] == 640 and summary["status"] == "RECORDED"
        assert summary["blurry"] > 0                          # every 10th frame was blurred
        detail = client.get(f"/sessions/{sid}", headers=auth).json()
        assert len(detail["frames"]) == summary["frame_count"]
        thumb = client.get(f"/sessions/{sid}/frames/0?thumb=true", headers=auth)
        assert thumb.status_code == 200 and thumb.content[:2] == b"\xff\xd8"
        assert client.post(f"/sessions/{sid}/label", json={"frame": 0}, headers=auth).status_code == 422
        reviewed = client.post(f"/sessions/{sid}/review", json={"blur_threshold": 0, "manual": {"0": False}}, headers=auth).json()
        assert reviewed["threshold"] == 0
        assert client.get(f"/sessions/{sid}", headers=auth).json()["frames"][0]["manual"] is False
        assert client.post("/train", json={"sessions": [sid]}, headers=auth).status_code == 422  # not labelled yet
        assert client.post(f"/sessions/{sid}/delete", json={}, headers=auth).json()["ok"]
        assert client.get("/sessions", headers=auth).json() == []
    finally:
        camera.stop.set()


class ColourEmbedder:
    """Stand-in for DINOv2: a crop's mean colour, normalised (red crops look alike, blue ones differ)."""
    name = "test-colour"

    def embed(self, images):
        v = np.array([img.reshape(-1, 3).mean(0) + 1 for img in images], np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_unknown_check_flags_objects_unlike_their_product(tmp_path):
    from vision import verify
    from vision.detector import Detection
    ds = tmp_path / "ds"
    for split in ("train", "val"):
        (ds / "images" / split).mkdir(parents=True)
        (ds / "labels" / split).mkdir(parents=True)
        for k in range(6):
            image = np.zeros((240, 320, 3), np.uint8)
            image[60:180, 80:240] = (20, 30, 220 + k)  # red product (BGR)
            cv2.imwrite(str(ds / "images" / split / f"s_{k:06d}.jpg"), image)
            (ds / "labels" / split / f"s_{k:06d}.txt").write_text("0 0.5 0.5 0.5 0.5\n")
    T.write_json(ds / "summary.json", {"classes": ["sanitizer"]})
    weights = tmp_path / "best.pt"
    thresholds = verify.build(ds, verify.refs_path(weights), embedder=ColourEmbedder())
    assert verify.MIN_THRESHOLD <= thresholds["sanitizer"] <= verify.MAX_THRESHOLD
    v = verify.Verifier.for_weights(weights, embedder=ColourEmbedder())
    scene = np.zeros((240, 320, 3), np.uint8)
    scene[20:100, 20:100] = (20, 30, 225)    # looks like the sanitizer
    scene[120:220, 180:300] = (230, 40, 10)  # blue object the model also called sanitizer
    found = [Detection("sanitizer", 0.9, (20, 20, 100, 100), 1), Detection("sanitizer", 0.9, (180, 120, 300, 220), 2),
             Detection("vicks", 0.9, (180, 120, 300, 220), None)]  # no references for vicks: left as is
    out = verify.TrackCheck(v).apply(scene, found, "tray-a")
    assert [d.sku for d in out] == ["sanitizer", "UNKNOWN", "vicks"]
    assert verify.Verifier.for_weights(tmp_path / "other.pt") is None  # no references: no check at all
