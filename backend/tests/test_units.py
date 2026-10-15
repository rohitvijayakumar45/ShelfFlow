"""Mixed-product trays tracked per physical unit: loading, weight-first matching, vision second."""
import time
import itertools
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend import service as s
from backend.main import app
from backend.db import SessionLocal
from backend.models import *
from backend.schemas import DeviceEvent, Weight
from backend.tests.test_store import enter, snapshot
from simulator.scenarios import interaction

_seq = itertools.count(1)

def weight_event(delta, pre=0.0, tray="tray-a", noise=0.5, settled=True, calibration="sim-v1", healthy=True, capture=None):
    now = capture if capture is not None else time.time()
    n = next(_seq)
    event = DeviceEvent(event_id=f"w-{n}", event_type="WEIGHT", device_id="sensor-1", boot_id="boot", sequence_number=n,
                        tray_id=tray, capture_start=now, capture_end=now, monotonic_ms=n, clock_synced=False, clock_uncertainty_ms=1000,
                        calibration_version=calibration, weight=Weight(pre_g=pre, post_g=pre + delta, noise_g=noise, settled=settled),
                        sensor_health={"ok": healthy, "flags": [] if healthy else ["DISCONNECTED"]})
    s.ingest(event)
    return event.event_id

def unit_tray(tray="tray-a"):
    with s.transaction() as db:
        db.get(Tray, tray).stock = 0
    s.set_unit_tracking(tray, True, "mixed tray demo", "staff")
    return s.open_load(tray, "initial load", "staff")["load_id"]

def load(load_id, product, grams, spot=None, tray="tray-a"):
    return s.add_unit(load_id, product, weight_event(grams, tray=tray), spot, [f"crop-{product}.jpg"], "staff")

def take(marker, delta, sequence, action="PICKUP", product="UNKNOWN", sku=False, quantity=1, **changes):
    event = interaction(marker=marker, tray="tray-a", action=action, quantity=quantity, sequence=sequence, product_id=product, sku=sku, **changes)
    event.weight = Weight(pre_g=2000, post_g=2000 + delta, noise_g=0.5, settled=True)
    return event

def units(status=None):
    with SessionLocal() as db:
        q = select(TrayUnit)
        if status:
            q = q.where(TrayUnit.status == status)
        return {u.id: u for u in db.scalars(q)}

def pending_reasons(sid):
    return [r for p in snapshot(sid)["pending"] for r in p["reasons"]]

@pytest.fixture
def mixed():
    """tray-a holding coffee 331.4 g, chips 60.3 g and bar 45.1 g, one per spot."""
    load_id = unit_tray()
    ids = {name: load(load_id, name, grams, spot)["unit_id"] for name, grams, spot in [("coffee", 331.4, 0), ("chips", 60.3, 1), ("bar", 45.1, 2)]}
    s.close_load(load_id, "staff")
    return ids

def test_loading_records_each_unit_weight_and_claims_the_event(mixed):
    with SessionLocal() as db:
        tray = db.get(Tray, "tray-a")
        assert tray.unit_tracking and tray.stock == 3
        coffee = db.get(TrayUnit, mixed["coffee"])
        assert coffee.weight_g == pytest.approx(331.4) and coffee.spot == 0 and coffee.status == "ON_TRAY"
        assert db.get(SensorClaim, coffee.source_event_id)
        assert db.get(Product, "coffee").weight_distribution["source"] == "TRAY_UNITS"
        assert db.scalar(select(InventoryMovement).where(InventoryMovement.reference == coffee.id)).reason == "LOAD"

def test_loading_refuses_reused_old_unsettled_or_unhealthy_weight():
    load_id = unit_tray()
    event_id = weight_event(331.4)
    s.add_unit(load_id, "coffee", event_id, None, [], "staff")
    with pytest.raises(HTTPException):
        s.add_unit(load_id, "coffee", event_id, None, [], "staff")  # same physical event twice
    for bad in (weight_event(60, capture=time.time() - 3600), weight_event(60, settled=False), weight_event(60, healthy=False),
                weight_event(60, calibration="old-cal"), weight_event(0.8), weight_event(-60)):
        with pytest.raises(HTTPException):
            s.add_unit(load_id, "chips", bad, None, [], "staff")

def test_tracking_mode_cannot_change_with_stock_or_units(mixed):
    with pytest.raises(HTTPException):
        s.set_unit_tracking("tray-a", False, "switch back", "staff")
    with pytest.raises(HTTPException):
        s.set_unit_tracking("tray-b", True, "tray-b still holds seeded stock", "staff")

def test_too_close_weights_warn_at_loading():
    load_id = unit_tray()
    load(load_id, "chips", 60.3)
    result = load(load_id, "bar", 59.0)
    assert result["warnings"] and result["warnings"][0]["code"] == "WEIGHT_TOO_CLOSE"

def test_unique_weight_match_confirms_even_when_vision_is_unknown(mixed):
    sid, marker, _ = enter()
    s.ingest(take(marker, -331.6, 1))
    assert [(i["product_id"], i["quantity"]) for i in snapshot(sid)["items"]] == [("coffee", 1)]
    coffee = units()[mixed["coffee"]]
    assert coffee.status == "IN_CART" and coffee.session_id == sid
    with SessionLocal() as db:
        assert db.get(Tray, "tray-a").stock == 2

def test_vision_contradicting_weight_goes_to_review(mixed):
    sid, marker, _ = enter()
    s.ingest(take(marker, -331.6, 1, product="chips", sku=True))
    assert snapshot(sid)["items"] == [] and "VISION_WEIGHT_CONFLICT" in pending_reasons(sid)
    assert units()[mixed["coffee"]].status == "ON_TRAY"

def test_weight_with_no_fitting_unit_goes_to_review(mixed):
    sid, marker, _ = enter()
    s.ingest(take(marker, -500, 1))
    assert snapshot(sid)["items"] == [] and "WEIGHT_NO_UNIT_MATCH" in pending_reasons(sid)

def test_similar_weights_are_ambiguous_unless_vision_names_the_product():
    load_id = unit_tray()
    chips = load(load_id, "chips", 60.3)["unit_id"]
    load(load_id, "bar", 59.6)
    sid, marker, _ = enter()
    s.ingest(take(marker, -60.0, 1))
    assert snapshot(sid)["items"] == [] and "WEIGHT_AMBIGUOUS" in pending_reasons(sid)
    s.ingest(take(marker, -60.0, 2, product="chips", sku=True))
    assert [i["product_id"] for i in snapshot(sid)["items"]] == ["chips"]
    assert units()[chips].status == "IN_CART"

def test_interchangeable_units_of_one_product_are_not_ambiguous():
    load_id = unit_tray()
    a, b = load(load_id, "coffee", 330.2)["unit_id"], load(load_id, "coffee", 331.0)["unit_id"]
    sid, marker, _ = enter()
    s.ingest(take(marker, -330.9, 1))
    assert snapshot(sid)["items"][0]["quantity"] == 1
    assert units()[b].status == "IN_CART" and units()[a].status == "ON_TRAY"  # closest weight chosen

def test_two_items_lifted_together_split_into_products(mixed):
    sid, marker, _ = enter()
    s.ingest(take(marker, -(331.4 + 60.3), 1, quantity=2))
    assert sorted((i["product_id"], i["quantity"]) for i in snapshot(sid)["items"]) == [("chips", 1), ("coffee", 1)]
    assert {u.id for u in units("IN_CART").values()} == {mixed["coffee"], mixed["chips"]}

def test_hand_count_disagreeing_with_weight_goes_to_review(mixed):
    sid, marker, _ = enter()
    s.ingest(take(marker, -(331.4 + 60.3), 1, quantity=1))
    assert snapshot(sid)["items"] == [] and "QUANTITY_MISMATCH" in pending_reasons(sid)

def test_emptied_spot_must_hold_the_matched_unit(mixed):
    sid, marker, _ = enter()
    s.ingest(take(marker, -331.4, 1, spot=1))  # weight says coffee (spot 0), camera saw spot 1 empty
    assert snapshot(sid)["items"] == [] and "SPOT_CONFLICT" in pending_reasons(sid)
    s.ingest(take(marker, -331.4, 2, spot=0))
    assert [i["product_id"] for i in snapshot(sid)["items"]] == ["coffee"]

def test_return_matches_a_unit_in_this_cart_only(mixed):
    sid, marker, _ = enter()
    s.ingest(take(marker, -60.3, 1))
    s.ingest(take(marker, +45.1, 2, action="RETURN"))  # bar was never taken by this shopper
    assert "WEIGHT_NO_UNIT_MATCH" in pending_reasons(sid)
    s.ingest(take(marker, +60.4, 3, action="RETURN"))
    assert snapshot(sid)["items"] == []
    assert units()[mixed["chips"]].status == "ON_TRAY"

def test_zero_net_change_with_activity_is_reviewed(mixed):
    sid, marker, _ = enter()
    s.ingest(take(marker, -0.4, 1))
    assert snapshot(sid)["items"] == [] and "ZERO_NET_REQUIRES_REVIEW" in pending_reasons(sid)

def test_weight_direction_must_match_action(mixed):
    sid, marker, _ = enter()
    s.ingest(take(marker, +331.4, 1, action="PICKUP"))
    assert "ACTION_WEIGHT_CONFLICT" in pending_reasons(sid)

def test_two_shoppers_different_products_need_each_hand_named():
    load_id = unit_tray()
    coffee, chips = load(load_id, "coffee", 331.4)["unit_id"], load(load_id, "chips", 60.3)["unit_id"]
    sid_a, marker_a, _ = enter("A", "a")
    sid_b, marker_b, _ = enter("B", "b")
    base = take(marker_a, -(331.4 + 60.3), 1)
    second = base.candidates[0].model_copy(update={"marker_ids": [marker_b], "product_track": "t-b", "hand_track": "h-b"})
    base.candidates.append(second)
    s.ingest(base)
    assert "OWNER_PRODUCT_UNRESOLVED" in pending_reasons(sid_a)
    named = take(marker_a, -(331.4 + 60.3), 2, product="coffee", sku=True)
    named.candidates.append(named.candidates[0].model_copy(update={"marker_ids": [marker_b], "product_track": "t-b2", "hand_track": "h-b", "product_id": "chips"}))
    s.ingest(named)
    assert [i["product_id"] for i in snapshot(sid_a)["items"]] == ["coffee"]
    assert [i["product_id"] for i in snapshot(sid_b)["items"]] == ["chips"]
    assert units()[coffee].session_id == sid_a and units()[chips].session_id == sid_b

def test_staff_review_picks_units_for_an_ambiguous_event():
    load_id = unit_tray()
    chips = load(load_id, "chips", 60.3)["unit_id"]
    load(load_id, "bar", 59.6)
    sid, marker, _ = enter()
    s.ingest(take(marker, -60.0, 1))
    hid = snapshot(sid)["pending"][0]["id"]
    s.review(hid, "CONFIRM", sid, 1, "Video shows the chips packet", "staff", unit_ids=[chips])
    assert [i["product_id"] for i in snapshot(sid)["items"]] == ["chips"]
    assert units()[chips].status == "IN_CART"

def test_unit_correction_refund_puts_unit_back(mixed):
    sid, marker, _ = enter()
    s.ingest(take(marker, -331.4, 1))
    with SessionLocal() as db:
        h = db.scalar(select(Hypothesis).where(Hypothesis.session_id == sid, Hypothesis.status == "CONFIRMED"))
        h.status = "DISPUTED"
        db.commit()
        hid = h.id
    with pytest.raises(HTTPException):
        s.correct(hid, sid, "coffee", -1, 1, "Customer did not take it", "k1", "staff")  # units must be named
    s.correct(hid, sid, "coffee", -1, 1, "Customer did not take it", "k2", "staff", unit_ids=[mixed["coffee"]])
    assert snapshot(sid)["items"] == []
    assert units()[mixed["coffee"]].status == "ON_TRAY"
    with SessionLocal() as db:
        assert db.get(Tray, "tray-a").stock == 3

def test_staff_remove_unit_and_api_loading_flow():
    with TestClient(app) as c:
        headers = {"Authorization": "Bearer " + c.post("/auth/staff", json={"password": "local-demo-staff"}).json()["token"]}
        with s.transaction() as db:
            db.get(Tray, "tray-a").stock = 0
        assert c.post("/staff/trays/tray-a/unit-tracking", headers=headers, json={"enabled": True, "reason": "mixed demo"}).status_code == 200
        load_id = c.post("/staff/trays/tray-a/loads", headers=headers, json={"reason": "first load"}).json()["load_id"]
        event_id = weight_event(331.4)
        listed = c.get("/staff/trays/tray-a/weight-events", headers=headers).json()
        assert listed[0]["event_id"] == event_id and listed[0]["delta_g"] == pytest.approx(331.4)
        r = c.post(f"/staff/loads/{load_id}/units", headers=headers, json={"product_id": "coffee", "weight_event_id": event_id, "spot": 0})
        assert r.status_code == 200
        assert all(e["event_id"] != event_id for e in c.get("/staff/trays/tray-a/weight-events", headers=headers).json())
        unit_id = r.json()["unit_id"]
        assert c.post(f"/staff/loads/{load_id}/close", headers=headers).json()["units"] == 1
        assert c.post(f"/staff/units/{unit_id}/remove", headers=headers, json={"reason": "damaged pack"}).json()["status"] == "REMOVED"
        assert c.get("/staff/trays/tray-a/units", headers=headers).json()[0]["status"] == "REMOVED"
        state = c.get("/staff/state", headers=headers).json()
        assert state["tray_units"] and state["tray_loads"]
