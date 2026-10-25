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
