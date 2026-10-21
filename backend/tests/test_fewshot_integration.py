"""Vision few-shot workers against the real backend API (in-process), no GPU or model download."""
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from backend import references as refs
from backend import service as s
from backend.main import app
from backend.tests.test_units import unit_tray, weight_event, units
from vision.identify import Gallery
from vision.detector import Detection
from vision.references import ReferenceSync, EnrollmentWorker, TeachCollector

class ColourEmbedder:
    name = "test-colour"
    def embed(self, images):
        out = np.array([img.reshape(-1, 3).mean(0) for img in images], np.float32) - 120.0
        return out / np.linalg.norm(out, axis=1, keepdims=True)

def textured(seed=0):
    rng = np.random.default_rng(seed)
    return cv2.GaussianBlur(rng.integers(70, 120, (240, 320, 3)).astype(np.uint8), (9, 9), 0)

@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("EVIDENCE_ROOT", str(tmp_path))
    with TestClient(app, headers={"X-Device-Key": "local-demo-device"}) as client:
        yield client

def restock_with_stills(product, color, seed):
    before = textured(seed)
    after = before.copy()
    after[60:160, 50:120] = color  # fully inside tray-a (left half): a crop cut by the tray edge is rejected
    cv2.rectangle(after, (60, 90), (110, 120), (225, 225, 225), -1)  # label: gives the crop texture (not blown out: glare is rejected)
    root = refs.catalogue_root()
    cv2.imwrite(str(root / f"load-{seed}-b.jpg"), before); cv2.imwrite(str(root / f"load-{seed}-a.jpg"), after)
    load_id = s.open_load("tray-a", "restock", "staff")["load_id"] if seed else unit_tray()
    return s.add_unit(load_id, product, weight_event(331.4 + seed), None, [f"load-{seed}-b.jpg", f"load-{seed}-a.jpg"], "staff")["unit_id"]

def test_restock_stills_become_live_gallery_references(api):
    unit_id = restock_with_stills("coffee", (40, 40, 200), 0)
    assert EnrollmentWorker(api).run_once() == {unit_id: "OK"}
    assert units()[unit_id].reference_status == "DONE"
    gallery = Gallery("test-colour")
    sync = ReferenceSync(api, gallery, ColourEmbedder(), refs.catalogue_root().parent / "cache")
    assert sync.sync() and gallery.products == {"coffee": 7}  # one crop -> 7 augmented views
    assert not sync.sync()  # nothing new
    ref_id = api.get("/devices/vision-1/references").json()[0]["id"]
    refs.set_reference_status(ref_id, "REJECTED", "wrong item", "staff")
    assert sync.sync() and gallery.products == {}
    status = api.get("/staff/references", headers={"Authorization": "Bearer " + api.post("/auth/staff", json={"password": "local-demo-staff"}).json()["token"]}).json()["gallery_status"]
    assert status["live_references"] == 0

def test_restart_reuses_cached_embeddings(api):
    restock_with_stills("coffee", (40, 40, 200), 0)
    EnrollmentWorker(api).run_once()
    cache = refs.catalogue_root().parent / "cache"
    ReferenceSync(api, Gallery("test-colour"), ColourEmbedder(), cache).sync()
    class NoEmbedder(ColourEmbedder):
        def embed(self, images):
            raise AssertionError("cached reference must not be re-embedded")
    g2 = Gallery("test-colour")
    ReferenceSync(api, g2, NoEmbedder(), cache).sync()
    assert g2.products == {"coffee": 7}

def test_unchanged_stills_fail_cleanly(api):
    load_id = unit_tray()
    root = refs.catalogue_root()
    still = textured(3)
    cv2.imwrite(str(root / "same-b.jpg"), still); cv2.imwrite(str(root / "same-a.jpg"), still)
    unit_id = s.add_unit(load_id, "chips", weight_event(60.3), None, ["same-b.jpg", "same-a.jpg"], "staff")["unit_id"]
    assert EnrollmentWorker(api).run_once() == {unit_id: "NO_CHANGE"}
    assert units()[unit_id].reference_status == "FAILED:NO_CHANGE"

def test_teach_mode_keeps_diverse_views_and_new_product_is_recognised(api):
    refs.create_product("lip-balm", "Lip balm", 4900, "staff")
    teach = refs.start_teach("lip-balm", "tray-a", 30, 4, "learn", "staff")
    config = api.get("/devices/vision-1/config").json()["trays"]["tray-a"]["teach"]
    collector = TeachCollector(api, ColourEmbedder(), interval=0, max_similarity=0.995)
    sent = []
    for i, color in enumerate([(200, 60, 60), (200, 60, 60), (190, 90, 40), (170, 40, 120), (210, 100, 100)]):
        frame = textured(10 + i)
        frame[40:180, 60:200] = color
        cv2.putText(frame, "LB", (90, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 3)
        sent.append(collector.observe(config, frame, [Detection("UNKNOWN", 0.9, (60, 40, 200, 180), 1)], now=1.0 + i))
    assert sent.count(200) >= 3 and None in sent  # duplicate view skipped
    gallery = Gallery("test-colour")
    ReferenceSync(api, gallery, ColourEmbedder(), refs.catalogue_root().parent / "cache2").sync()
    assert "lip-balm" in gallery.products
    probe = textured(99); probe[40:180, 60:200] = (195, 75, 70)
    assert gallery.identify(ColourEmbedder().embed([probe[40:180, 60:200]])[0], 0.9, 0.0).product_id == "lip-balm"
