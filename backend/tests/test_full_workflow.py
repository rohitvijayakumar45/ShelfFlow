"""End-to-end shop workflow in hardware mode: check-in, verified pickups for two shoppers on one tray,
returns, exit billing and bracelet release."""
import time
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from backend import service as s
from backend import main
from backend.db import SessionLocal
from backend.models import Bracelet, BraceletBinding, Payment, SensorEvent
from backend.tests.test_auto_add import camera_event, hardware, reasons  # noqa: F401  (fixture)
from backend.tests.test_store import enter, snapshot
from backend.tests.test_units import load, mixed, unit_tray, units, weight_event  # noqa: F401  (fixture)


_tap_seq = iter(range(5000, 10**6))


def tap(uid, reader):
    """The shelf controller's RFID event for a bracelet tap on R1 (entry) or R2 (exit)."""
    from backend.schemas import DeviceEvent
    n, now = next(_tap_seq), time.time()
    s.ingest(DeviceEvent(event_id=f"tap-{n}", event_type="RFID", device_id="sensor-1", boot_id="boot", sequence_number=n,
                         tray_id="tray-a", capture_start=now, capture_end=now, monotonic_ms=n, clock_synced=False,
                         clock_uncertainty_ms=1000, calibration_version="sim-v1", evidence_refs=[f"RFID:{uid}", f"RFID_READER:{reader}"]))


def items(sid):
    return [(i["product_id"], i["quantity"]) for i in snapshot(sid)["items"]]


def bracelet_of(sid):
    with SessionLocal() as db:
        return db.scalar(select(Bracelet).where(Bracelet.session_id == sid))


def test_two_shoppers_on_one_tray_each_hand_named_are_both_charged(hardware):
    load_id = unit_tray()
    load(load_id, "coffee", 331.4), load(load_id, "chips", 60.3)
    s.close_load(load_id, "staff")
    sid_a, marker_a, _ = enter("A", "a")
    sid_b, marker_b, _ = enter("B", "b")
    e = camera_event(marker_a, -(331.4 + 60.3), 1, weight_event(-(331.4 + 60.3), pre=2000), product="coffee", sku=True)
    e.candidates.append(e.candidates[0].model_copy(update={"marker_ids": [marker_b], "product_id": "chips",
                                                           "product_track": "t-b", "hand_track": "h-b"}))
    s.ingest(e)
    assert items(sid_a) == [("coffee", 1)] and items(sid_b) == [("chips", 1)]
    with SessionLocal() as db:
        from backend.models import Tray
        assert db.get(Tray, "tray-a").stock == 0


def test_both_bracelets_at_one_hand_is_held_for_review(mixed, hardware):
    sid_a, marker_a, _ = enter("A", "a")
    sid_b, marker_b, _ = enter("B", "b")
    s.ingest(camera_event(marker_a, -331.6, 1, weight_event(-331.6, pre=2000), marker_ids=[marker_a, marker_b], identity=False))
    assert items(sid_a) == [] and items(sid_b) == []
    assert "IDENTITY_UNPROVEN" in reasons(sid_a)


def test_return_is_processed_once_and_never_goes_negative(mixed, hardware):
    sid, marker, _ = enter()
    s.ingest(camera_event(marker, -331.6, 1, weight_event(-331.6, pre=2000)))
    back = weight_event(331.5, pre=1668.4)
    s.ingest(camera_event(marker, 331.5, 2, back, action="RETURN"))
    s.ingest(camera_event(marker, 331.5, 3, back, action="RETURN"))   # the camera reports the same return again
    assert items(sid) == [] and units()[mixed["coffee"]].status == "ON_TRAY"
    assert "SENSOR_EVIDENCE_ALREADY_CONSUMED" in reasons(sid)
    with SessionLocal() as db:
        from backend.models import Tray
        assert db.get(Tray, "tray-a").stock == 3


def test_recent_unmatched_shelf_change_blocks_billing_until_it_settles(mixed, hardware):
    sid, marker, _ = enter()
    loose = weight_event(-331.6, pre=2000)   # the shelf changed; the camera has not reported yet
    with pytest.raises(HTTPException, match="still being matched"):
        s.staff_exit(sid, "staff")
    with SessionLocal() as db:
        assert [b["code"] for b in s.exit_blockers(db, sid)] == ["SHELF_EVENT_IN_FLIGHT"]
    with s.transaction() as db:
        db.get(SensorEvent, loose).received = time.time() - 60
    assert s.staff_exit(sid, "staff")["state"] == "SUCCESS"


def test_exit_bills_once_ends_the_binding_and_holds_the_bracelet_until_returned(mixed, hardware):
    sid, marker, _ = enter()
    s.ingest(camera_event(marker, -331.6, 1, weight_event(-331.6, pre=2000)))
    first = s.staff_exit(sid, "staff")
    assert s.staff_exit(sid, "staff") == first
    cart = snapshot(sid)
    assert cart["status"] == "COMPLETE" and cart["receipt"]["bill_status"] == "FINAL"
    assert cart["receipt"]["payment_status"] == "SIMULATED_NOT_COLLECTED" and cart["receipt"]["items"][0]["product_id"] == "coffee"
    b = bracelet_of(sid)
    assert b.state == "AWAITING_RETURN"
    with SessionLocal() as db:
        assert len(list(db.scalars(select(Payment)))) == 1
        assert all(x.ended for x in db.scalars(select(BraceletBinding).where(BraceletBinding.session_id == sid)))
    # The camera no longer maps this bracelet to anyone, and it cannot be handed to the next customer yet.
    with SessionLocal() as db:
        assert db.get(Bracelet, b.id).state != "ASSIGNED"   # vision-1 bindings list only ASSIGNED bracelets
    other = s.new_customer("Next")
    with pytest.raises(HTTPException, match="not free"):
        s.check_in(s.entry_qr(other)["token"], b.rfid, "staff")
    # Something the old bracelet touches after exit is never charged to the finished visit.
    s.ingest(camera_event(marker, -60.3, 2, weight_event(-60.3, pre=1668.4)))
    assert items(sid) == [("coffee", 1)]
    s.review(snapshot(sid)["pending"][0]["id"], "DISMISS", None, 1, "Taken after the visit ended", "staff")
    main.return_bracelet(b.id, main.BraceletReturn(reason="Returned at exit and cleaned", cleaned=True), user={"sub": "staff"})
    assert bracelet_of(sid) is None
    token = s.entry_qr(other)["token"]
    with pytest.raises(HTTPException, match="entry reader"):
        s.check_in(token, b.rfid, "staff")                      # hardware check-in needs the tap on R1
    tap(b.rfid, "R2")
    with pytest.raises(HTTPException, match="entry reader"):
        s.check_in(token, b.rfid, "staff")                      # a tap on the exit reader does not count
    tap(b.rfid, "R1")
    assert s.check_in(token, b.rfid, "staff")["bracelet_id"] == b.id


def test_exit_reader_tap_bills_the_visit_once(mixed, hardware):
    sid, marker, _ = enter()
    s.ingest(camera_event(marker, -331.6, 1, weight_event(-331.6, pre=2000)))
    uid = bracelet_of(sid).rfid
    tap(uid, "R1")                                              # the entry reader never checks anyone out
    assert s.process_exit_requests() == [] and snapshot(sid)["status"] == "ACTIVE"
    tap(uid, "R2")
    assert snapshot(sid)["exit_requested"]
    [bill] = s.process_exit_requests()
    tap(uid, "R2"), tap(uid, "R2")                              # held on the reader: repeated taps
    assert s.process_exit_requests() == []
    cart = snapshot(sid)
    assert cart["status"] == "COMPLETE" and cart["receipt"]["items"][0]["product_id"] == "coffee" and bill["state"] == "SUCCESS"
    with SessionLocal() as db:
        assert len(list(db.scalars(select(Payment)))) == 1
    assert bracelet_of(sid).state == "AWAITING_RETURN"


def test_exit_tap_waits_while_something_is_pending_and_expires(mixed, hardware):
    sid, marker, _ = enter()
    s.ingest(camera_event(marker, -60.3, 1, weight_event(-60.3, pre=2000), marker_ids=[]))   # who took it is unknown
    tap(bracelet_of(sid).rfid, "R2")
    assert s.process_exit_requests() == []
    with SessionLocal() as db:
        [waiting] = s.pending_exits(db)
        assert waiting["session_id"] == sid and waiting["blockers"][0]["code"] == "PENDING_REVIEW"
    s.review(snapshot(sid)["pending"][0]["id"], "DISMISS", None, 1, "Nothing was taken", "staff")
    assert [b["order_id"] for b in s.process_exit_requests()]   # resolved while waiting: billed on the next pass
    sid2, _, _ = enter("Late", "late")
    tap(bracelet_of(sid2).rfid, "R2")
    with s.transaction() as db:
        from backend.models import Audit
        for a in db.scalars(select(Audit).where(Audit.action == "EXIT_TAP")):
            a.created -= s.EXIT_REQUEST_TTL_S + 1                # tapped long ago and kept shopping
    assert s.process_exit_requests() == [] and snapshot(sid2)["status"] == "ACTIVE"
