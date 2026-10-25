"""Strict auto-add in hardware mode: a camera event backed by an on-time shelf weight event updates the
cart without review only when every fusion check passes; anything else stays pending."""
import time
import pytest
from sqlalchemy import select
from backend import service as s
from backend import config as settings
from backend.db import SessionLocal
from backend.models import Hypothesis, SensorEvent
from backend.tests.test_store import enter, snapshot
from backend.tests.test_units import mixed, take, units, weight_event  # noqa: F401  (fixture)


@pytest.fixture
def hardware(monkeypatch):
    monkeypatch.setattr(s, "MODE", "hardware")
    monkeypatch.setattr(settings, "AUTO_ADD", True)


def camera_event(marker, delta, sequence, source, **changes):
    """What vision-1 sends: the shelf's weight copied verbatim, citing it, with the shelf's rough clock."""
    e = take(marker, delta, sequence, **changes)
    with SessionLocal() as db:
        e.weight = type(e.weight)(**db.get(SensorEvent, source).payload["weight"])
    e.device_id, e.evidence_refs = "vision-1", ["SENSOR_EVENT:" + source]
    e.clock_synced, e.clock_uncertainty_ms, e.model_status = False, 1000, "TRAINED"
    return e


def reasons(sid):
    return [r for p in snapshot(sid)["pending"] for r in p["reasons"]]


def test_verified_pickup_is_added_instantly_and_only_once(mixed, hardware):
    sid, marker, _ = enter()
    source = weight_event(-331.6, pre=2000)
    s.ingest(camera_event(marker, -331.6, 1, source))
    assert [(i["product_id"], i["quantity"]) for i in snapshot(sid)["items"]] == [("coffee", 1)]
    assert units()[mixed["coffee"]].session_id == sid
    # The camera sees the same pickup again: the weight event is already consumed, nothing more is charged.
    s.ingest(camera_event(marker, -331.6, 2, source))
    assert [(i["product_id"], i["quantity"]) for i in snapshot(sid)["items"]] == [("coffee", 1)]
    assert "SENSOR_EVIDENCE_ALREADY_CONSUMED" in reasons(sid)


def test_verified_return_removes_it_again(mixed, hardware):
    sid, marker, _ = enter()
    s.ingest(camera_event(marker, -331.6, 1, weight_event(-331.6, pre=2000)))
    s.ingest(camera_event(marker, 331.5, 2, weight_event(331.5, pre=1668.4), action="RETURN"))
    assert snapshot(sid)["items"] == [] and units()[mixed["coffee"]].status == "ON_TRAY"


def test_auto_add_off_or_no_timely_weight_stays_pending(mixed, hardware, monkeypatch):
    sid, marker, _ = enter()
    late = weight_event(-331.6, pre=2000)
    with s.transaction() as db:
        db.get(SensorEvent, late).received = time.time() - 120   # weight arrived long before this camera window
    s.ingest(camera_event(marker, -331.6, 1, late))
    assert snapshot(sid)["items"] == [] and "PHYSICAL_OWNERSHIP_NOT_VALIDATED" in reasons(sid)
    monkeypatch.setattr(settings, "AUTO_ADD", False)
    s.ingest(camera_event(marker, -331.6, 2, weight_event(-331.6, pre=2000)))
    assert snapshot(sid)["items"] == []


@pytest.mark.parametrize("changes,reason", [({"marker_ids": []}, "IDENTITY_UNPROVEN"), ({"hand_product": False}, "OWNERSHIP_CHAIN_UNPROVEN"),
                                            ({"product": "chips", "sku": True}, "VISION_WEIGHT_CONFLICT")])
def test_any_missing_evidence_goes_to_review(mixed, hardware, changes, reason):
    sid, marker, _ = enter()
    s.ingest(camera_event(marker, -331.6, 1, weight_event(-331.6, pre=2000), **changes))
    assert snapshot(sid)["items"] == []
    with SessionLocal() as db:
        assert reason in db.scalars(select(Hypothesis)).first().reason


def test_two_shoppers_sharing_one_weight_change_are_held_unless_each_hand_names_its_product(hardware):
    from backend.tests.test_units import load, unit_tray
    load_id = unit_tray()
    load(load_id, "coffee", 331.4), load(load_id, "chips", 60.3)
    sid_a, marker_a, _ = enter("A", "a")
    sid_b, marker_b, _ = enter("B", "b")
    e = camera_event(marker_a, -(331.4 + 60.3), 1, weight_event(-(331.4 + 60.3), pre=2000))
    e.candidates.append(e.candidates[0].model_copy(update={"marker_ids": [marker_b], "product_track": "t-b", "hand_track": "h-b"}))
    s.ingest(e)
    assert snapshot(sid_a)["items"] == [] and snapshot(sid_b)["items"] == []
    assert "OWNER_PRODUCT_UNRESOLVED" in reasons(sid_a)


def test_check_in_binds_the_tapped_bracelet_once():
    from fastapi import HTTPException
    from backend.models import Bracelet, ShoppingSession
    sid = s.new_customer("Asha")
    token = s.entry_qr(sid)["token"]
    with SessionLocal() as db:
        b = db.scalars(select(Bracelet).where(Bracelet.state == "AVAILABLE")).first()
        uid, markers = b.rfid, list(b.markers)
    assert s.check_in_preview(token)["customer_alias"] == "Asha"
    with pytest.raises(HTTPException):
        s.check_in(token, "00000000", "staff")                  # not a registered bracelet; QR stays usable
    assert s.check_in(token, uid, "staff")["markers"] == markers
    with SessionLocal() as db:
        assert db.get(ShoppingSession, sid).status == "ACTIVE"
    with pytest.raises(HTTPException):
        s.check_in(token, uid, "staff")                         # QR is single-use
    other = s.new_customer("Ben")
    with pytest.raises(HTTPException, match="not free"):
        s.check_in(s.entry_qr(other)["token"], uid, "staff")   # never two active customers on one bracelet


def test_staff_exit_bills_once_and_waits_for_pending_events(mixed, hardware):
    from fastapi import HTTPException
    from backend.models import Order, Payment, Receipt
    sid, marker, _ = enter()
    s.ingest(camera_event(marker, -331.6, 1, weight_event(-331.6, pre=2000)))
    s.ingest(camera_event(marker, -60.3, 2, weight_event(-60.3, pre=1668.4), marker_ids=[]))   # who took it is unknown
    with pytest.raises(HTTPException, match="Resolve pending"):
        s.staff_exit(sid, "staff")
    h = snapshot(sid)["pending"][0]["id"]
    s.review(h, "DISMISS", None, 1, "Nothing was taken", "staff")
    first = s.staff_exit(sid, "staff")
    again = s.staff_exit(sid, "staff")                         # repeated exit: same bill, no second charge
    assert first == again and first["state"] == "SUCCESS"
    with SessionLocal() as db:
        assert len(list(db.scalars(select(Order)))) == 1 and len(list(db.scalars(select(Payment)))) == 1
        assert len(list(db.scalars(select(Receipt)))) == 1
    cart = snapshot(sid)
    assert cart["status"] == "COMPLETE" and cart["receipt"]["total_minor"] == cart["total_minor"] and cart["receipt"]["payment"] == "SIMULATED"
