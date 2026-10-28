"""No-scale assisted workflow: camera + RFID suggestions confirmed by staff."""
import time
import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from backend import config, preintegration as pre, service as s
from backend.db import SessionLocal
from backend.models import *
from backend.schemas import Candidate, DeviceEvent, Health
from backend.tests.test_store import enter, snapshot

@pytest.fixture(autouse=True)
def no_weights(monkeypatch):
    monkeypatch.setattr(config, "WEIGHTS_ENABLED", False)

def camera_event(product="oat", sku=True, markers=(), action="PICKUP", sequence=1, at=None):
    now = at or time.time()
    c = Candidate(marker_ids=list(markers), product_id=product if sku else "UNKNOWN", product_track=f"clip:{sequence}",
                  hand_track="UNPROVEN", action=action, quantity=1, sku=sku, hand_product=True, trajectory=True,
                  similarity=0.81, top_product=product, second_product=None, via="gallery")
    event = DeviceEvent(event_id=str(uuid.uuid4()), event_type="INTERACTION", device_id="vision-1", boot_id="vb", sequence_number=sequence,
                        tray_id="tray-a", capture_start=now - 3, capture_end=now, monotonic_ms=sequence, clock_synced=False,
                        clock_uncertainty_ms=1000, calibration_version="UNSET", candidates=[c], model_status="TRAINED",
                        sensor_health=Health(ok=False, flags=["NO_MATCHING_WEIGHT"]), evidence_refs=["ev-1.jpg", "ev-2.jpg"])
    s.ingest(event)
    return event

def rfid_tap(uid, reader="R1", at=None):
    now = at or time.time()
    s.ingest(DeviceEvent(event_id=str(uuid.uuid4()), event_type="RFID", device_id="sensor-1", boot_id="sb",
                         sequence_number=int(now * 1000) % 10_000_000, tray_id="tray-a", capture_start=now, capture_end=now,
                         monotonic_ms=1, clock_synced=False, clock_uncertainty_ms=1000, calibration_version="UNSET",
                         evidence_refs=[f"RFID:{uid}", f"RFID_READER:{reader}"]))

def count(product="oat", qty=5):
    pre.record(str(uuid.uuid4()), "COUNT", {"tray_id": "tray-a", "product_id": product, "quantity": qty,
                                            "physical_confirmed": True, "reason": "counted"}, "staff")

def suggestions():
    with SessionLocal() as db:
        return pre.suggestions(db)

def bracelet_of(sid):
    with SessionLocal() as db:
        return db.scalar(select(Bracelet).where(Bracelet.session_id == sid))

def test_camera_pickup_is_suggested_with_the_bracelet_marker_owner_and_recorded_by_staff():
    count()
    sid_a, marker_a, _ = enter("A", "a")
    enter("B", "b")
    event = camera_event(markers=[marker_a])
    [sg] = suggestions()
    assert sg["product_id"] == "oat" and sg["action"] == "PICKUP" and sg["session_id"] == sid_a
    assert sg["confidence"] == "strong" and sg["frames"] == ["ev-1.jpg", "ev-2.jpg"] and sg["stock"] == 5
    assert snapshot(sid_a)["items"] == []  # nothing charged before staff confirm
    out = pre.from_evidence(sg["hypothesis_id"], {"product_id": "oat", "session_id": sid_a, "quantity_delta": 1,
                                                  "physical_confirmed": True, "reason": "video shows A taking oats"}, "k1", "staff")
    assert not out["duplicate"]
    assert [(i["product_id"], i["quantity"]) for i in snapshot(sid_a)["items"]] == [("oat", 1)]
    assert suggestions() == []
    with SessionLocal() as db:
        h = db.get(Hypothesis, sg["hypothesis_id"])
        assert h.status == "ASSISTED" and db.get(AssistedStock, ("tray-a", "oat")).quantity == 4
        assert db.get(AssistedAction, "k1").payload["evidence_event_id"] == event.event_id
    assert pre.from_evidence(sg["hypothesis_id"], {"product_id": "oat", "session_id": sid_a, "quantity_delta": 1,
                                                   "physical_confirmed": True, "reason": "video shows A taking oats"}, "k1", "staff")["duplicate"]

def test_rfid_tap_names_the_customer_when_no_marker_was_seen(monkeypatch):
    monkeypatch.setattr(config, "ENTRY_READER", ""), monkeypatch.setattr(config, "EXIT_READER", "")  # both readers on the shelf
    count()
    enter("A", "a")
    sid_b, _, _ = enter("B", "b")
    rfid_tap(bracelet_of(sid_b).rfid, "R2")
    camera_event()
    [sg] = suggestions()
    assert sg["session_id"] == sid_b and any("R2" in w for w in sg["why"])

def test_entry_and_exit_reader_taps_never_name_a_shelf_customer():
    count()
    enter("A", "a")
    sid_b, _, _ = enter("B", "b")
    rfid_tap(bracelet_of(sid_b).rfid, "R1")   # entry registry, not the shelf
    camera_event()
    [sg] = suggestions()
    assert sg["session_id"] is None

def test_two_bracelets_nearby_leave_the_customer_for_staff():
    count()
    sid_a, _, _ = enter("A", "a")
    sid_b, _, _ = enter("B", "b")
    rfid_tap(bracelet_of(sid_a).rfid, "R1")
    rfid_tap(bracelet_of(sid_b).rfid, "R2")
    camera_event()
    [sg] = suggestions()
    assert sg["session_id"] is None and sg["confidence"] == "weak"

def test_unknown_item_is_offered_without_a_product_and_cannot_be_recorded_without_one():
    count()
    sid, marker, _ = enter()
    camera_event(product="oat", sku=False, markers=[marker])
    [sg] = suggestions()
    assert sg["product_id"] == "oat" and "best guess" in sg["why"][0]  # top guess shown but marked uncertain
    with pytest.raises(HTTPException):
        pre.from_evidence(sg["hypothesis_id"], {"product_id": "oat", "session_id": sid, "quantity_delta": 1,
                                                "physical_confirmed": False, "reason": "unchecked"}, "k2", "staff")
    assert snapshot(sid)["items"] == []

def test_camera_return_takes_the_item_back_out_of_the_cart():
    count()
    sid, marker, _ = enter()
    camera_event(markers=[marker], sequence=1)
    first = suggestions()[0]
    pre.from_evidence(first["hypothesis_id"], {"product_id": "oat", "session_id": sid, "quantity_delta": 1,
                                               "physical_confirmed": True, "reason": "took it"}, "p1", "staff")
    camera_event(markers=[marker], action="RETURN", sequence=2)
    back = suggestions()[0]
    assert back["action"] == "RETURN"
    pre.from_evidence(back["hypothesis_id"], {"product_id": "oat", "session_id": sid, "quantity_delta": -1,
                                              "physical_confirmed": True, "reason": "put it back"}, "r1", "staff")
    assert snapshot(sid)["items"] == []

def test_assisted_endpoints_refuse_while_weights_are_enabled(monkeypatch):
    monkeypatch.setattr(config, "WEIGHTS_ENABLED", True)
    with pytest.raises(HTTPException):
        pre.from_evidence("x", {}, "k", "staff")

def test_vision_learns_the_mode_from_its_config():
    from fastapi.testclient import TestClient
    from backend.main import app
    c = TestClient(app, headers={"X-Device-Key": "local-demo-device"})
    assert c.get("/devices/vision-1/config").json()["weights_enabled"] is False
