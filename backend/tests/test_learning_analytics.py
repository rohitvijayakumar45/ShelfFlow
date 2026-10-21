"""Active learning, threshold tuning, KPIs, shrink/drift reports, review suggestions, timeline and thumbnails."""
import base64
import json
import time
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend import analytics
from backend import references as refs
from backend import service as s
from backend.db import SessionLocal
from backend.main import app
from backend.models import *
from backend.schemas import DeviceEvent, Weight
from backend.tests.test_store import enter, snapshot
from backend.tests.test_units import unit_tray, load, take, weight_event, units, pending_reasons
from backend.tests.test_references import staff_headers

JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"x" * 64).decode()

@pytest.fixture(autouse=True)
def evidence_root(tmp_path, monkeypatch):
    monkeypatch.setenv("EVIDENCE_ROOT", str(tmp_path))

def device():
    return TestClient(app, headers={"X-Device-Key": "local-demo-device"})

def uncertain(c, top="coffee", similarity=0.66, margin=0.1):
    return c.post("/devices/vision-1/uncertain", json={"track": "clip:1", "tray_id": "tray-a", "image_b64": JPEG, "top_product": top,
                                                      "second_product": "chips", "similarity": similarity, "margin": margin}).json()["id"]

def test_staff_answer_turns_an_uncertain_crop_into_a_reference():
    c = device()
    crop_id = uncertain(c)
    staff = staff_headers(c)
    rows = c.get("/staff/uncertain", headers=staff).json()
    assert [r["id"] for r in rows] == [crop_id] and rows[0]["top_product"] == "coffee"
    out = c.post(f"/staff/uncertain/{crop_id}/label", json={"product_id": "chips"}, headers=staff).json()
    assert out["status"] == "LABELLED"
    live = {r["id"]: r for r in c.get("/devices/vision-1/references").json()}
    assert live[out["reference_id"]]["product_id"] == "chips" and live[out["reference_id"]]["status"] == "ACTIVE"
    assert c.post(f"/staff/uncertain/{crop_id}/label", json={"product_id": "chips"}, headers=staff).status_code == 409
    other = uncertain(c)
    assert c.post(f"/staff/uncertain/{other}/label", json={"product_id": None}, headers=staff).json()["status"] == "DISCARDED"
    assert c.get("/staff/uncertain", headers=staff).json() == []

def test_uncertain_queue_is_bounded(monkeypatch):
    monkeypatch.setattr(refs, "MAX_OPEN_UNCERTAIN", 2)
    c = device()
    first = uncertain(c); uncertain(c); uncertain(c)
    with SessionLocal() as db:
        assert db.get(UncertainCrop, first).status == "DISCARDED"
        assert len(refs.uncertain_rows(db)) == 2

def test_tuning_needs_enough_reviewed_samples_then_recommends():
    with SessionLocal() as db:
        out = analytics.recognition_tuning(db, None)
    assert out["recommended"] is None and out["samples"] == 0
    samples = [{"similarity": 0.9, "margin": 0.2, "top": "a", "truth": "a"}] * 40 + [{"similarity": 0.62, "margin": 0.01, "top": "b", "truth": "a"}] * 5
    best, table = analytics.sweep(samples)
    assert best["wrong_rate"] <= 0.01 and best["auto_rate"] == pytest.approx(40 / 45)
    assert best["threshold"] > 0.62 or best["margin"] > 0.01

def test_staff_labels_and_reviews_become_tuning_samples():
    c = device()
    staff = staff_headers(c)
    for i in range(3):
        crop = uncertain(c, similarity=0.6 + i / 100)
        c.post(f"/staff/uncertain/{crop}/label", json={"product_id": "coffee"}, headers=staff)
    tuning = c.get("/staff/recognition/tuning", headers=staff).json()
    assert tuning["samples"] == 3 and tuning["vision_right"] == 3 and tuning["recommended"] is None
    assert c.post("/staff/recognition/settings", json={"threshold": 0.71, "margin": 0.04, "reason": "reviewed sweep"}, headers=staff).json()["ok"]
    assert c.get("/devices/vision-1/config").json()["recognition"]["threshold"] == 0.71
    assert c.get("/devices/vision-1/recognition").json()["margin"] == 0.04

@pytest.fixture
def mixed():
    load_id = unit_tray()
    ids = {name: load(load_id, name, grams, spot)["unit_id"] for name, grams, spot in [("coffee", 331.4, 0), ("chips", 60.3, 1), ("bar", 45.1, 2)]}
    s.close_load(load_id, "staff")
    return ids

def test_kpis_count_automatic_and_reviewed_decisions(mixed):
    sid, marker, _ = enter()
    s.ingest(take(marker, -331.6, 1))           # automatic
    s.ingest(take(marker, -500, 2))             # needs review
    with SessionLocal() as db:
        k = analytics.kpis(db)
    assert k["interactions"] == 2 and k["auto_decided"] == 1 and k["review_rate"] == 0.5 and k["auto_precision"] == 1.0
    assert ("WEIGHT_NO_UNIT_MATCH", 1) in [tuple(r) for r in k["top_reasons"]]

def test_suggestion_prefills_the_unique_weight_match_and_only_shopper():
    load_id = unit_tray()
    chips = load(load_id, "chips", 60.3)["unit_id"]
    load(load_id, "bar", 59.6)
    sid, marker, _ = enter()
    event = take(marker, -60.0, 1, product="UNKNOWN")
    event.candidates[0].top_product = "chips"
    s.ingest(event)
    hid = snapshot(sid)["pending"][0]["id"]
    with SessionLocal() as db:
        out = analytics.suggestion(db, db.get(Hypothesis, hid))
    assert out["decision"] == "CONFIRM" and out["unit_ids"] == [chips] and out["session_id"] == sid
    assert any("Camera recognised" in w for w in out["why"])

def test_suggestion_dismisses_weight_that_matches_nothing(mixed):
    sid, marker, _ = enter()
    s.ingest(take(marker, -500, 1))
    hid = snapshot(sid)["pending"][0]["id"]
    c = TestClient(app)
    out = c.get(f"/staff/review/{hid}/suggestion", headers=staff_headers(c)).json()
    assert out["decision"] == "DISMISS"

def test_unexplained_weight_drop_and_dismissed_pickup_show_in_shrink(mixed):
    weight_event(-331.0, pre=436.8)  # nobody in view, no interaction event: unexplained
    sid, marker, _ = enter()
    s.ingest(take(marker, -500, 1))
    hid = snapshot(sid)["pending"][0]["id"]
    s.review(hid, "DISMISS", None, 1, "foreign object", "staff")
    with SessionLocal() as db:
        r = analytics.shrink_report(db)
    drop = r["unexplained_weight_drops"]
    assert len(drop) == 1 and drop[0]["likely_product"] == "coffee" and r["unexplained_value_minor"] > 0
    assert len(r["dismissed_pickups"]) == 1

def test_drift_compares_scale_reading_with_recorded_items(mixed):
    now = time.time()
    total = 331.4 + 60.3 + 45.1
    def reading(filtered):
        e = DeviceEvent(event_id=f"h-{filtered}", event_type="WEIGHT", device_id="sensor-1", boot_id="boot", sequence_number=int(filtered * 10) + 10_000,
                        tray_id="tray-a", capture_start=now, capture_end=now, monotonic_ms=1, clock_synced=False, clock_uncertainty_ms=1000,
                        calibration_version="sim-v1", weight=Weight(pre_g=filtered, post_g=filtered, filtered=filtered, noise_g=0.5, settled=True))
        s.ingest(e)
    reading(total + 1.0)
    with SessionLocal() as db:
        tray = next(t for t in analytics.drift_report(db) if t["tray_id"] == "tray-a")
    assert tray["status"] == "OK" and abs(tray["difference_g"] - 1.0) < 0.01
    time.sleep(0.05)  # receive times order the readings (Windows clock resolution)
    reading(total - 60.0)
    with SessionLocal() as db:
        tray = next(t for t in analytics.drift_report(db) if t["tray_id"] == "tray-a")
    assert tray["status"] == "MISMATCH" and "less" in tray["advice"]

def test_timeline_collects_weights_taps_and_frames(tmp_path, mixed):
    sid, marker, _ = enter()
    event = take(marker, -500, 1)
    event.evidence_refs = ["ev-boot-1.jpg"]
    (tmp_path / "ev-boot-1.json").write_text(json.dumps({"capture_wall": event.capture_end, "annotations": {"trays": {}}, "faces_redacted": 1, "original_bytes": False}))
    weight_event(-500, pre=1000, capture=event.capture_end)
    s.ingest(event)
    c = TestClient(app)
    t = c.get(f"/staff/evidence/{event.event_id}/timeline", headers=staff_headers(c)).json()
    assert t["frames"][0]["faces_redacted"] == 1 and not t["frames"][0]["original"]
    assert any(abs(w["post_g"] - w["pre_g"] + 500) < 0.01 for w in t["weights"])

def test_event_replay_ignores_newer_empty_schema_fields():
    stored = {"a": 1, "candidates": [{"x": 1}]}
    assert s.same_payload(stored, {"a": 1, "model_versions": {}, "candidates": [{"x": 1, "similarity": None}]})
    assert not s.same_payload(stored, {"a": 1, "model_versions": {"gallery": "g"}, "candidates": [{"x": 1}]})
    assert not s.same_payload(stored, {"a": 2, "candidates": [{"x": 1}]})

def test_model_versions_are_kept_with_the_event(mixed):
    sid, marker, _ = enter()
    event = take(marker, -331.6, 1)
    event.model_versions = {"detector": "item.pt@abc", "gallery": "gallery-123"}
    s.ingest(event)
    with SessionLocal() as db:
        assert db.get(SensorEvent, event.event_id).payload["model_versions"]["gallery"] == "gallery-123"

def test_customer_sees_product_thumbnail_and_event_times(mixed):
    out = refs.add_reference("coffee", "TEACH", JPEG, None, {"score": 0.8},
                             teach_id=refs.start_teach("coffee", "tray-a", 30, 5, "learn", "staff")["teach_id"])
    sid, marker, _ = enter()
    s.ingest(take(marker, -331.6, 1))
    cart = snapshot(sid)
    assert cart["items"][0]["has_image"] and cart["confirmed_events"][0]["name"] and cart["confirmed_events"][0]["at"]
    c = TestClient(app)
    from backend.auth import issue
    r = c.get("/customer/product-image/coffee", headers={"Authorization": "Bearer " + issue("customer", sid)})
    assert r.status_code == 200 and r.content.startswith(b"\xff\xd8")
    assert c.get("/customer/product-image/chips", headers={"Authorization": "Bearer " + issue("customer", sid)}).status_code == 404
    assert c.get("/customer/product-image/coffee").status_code in (401, 403, 422)
