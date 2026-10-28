"""RFID-attributed shelf cart: the shopper whose bracelet last tapped a shelf reader (or who just checked
in) owns each shelf weight step; the weighed units or the camera name the product."""
import time
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend import camera_watch, config as settings, main, service as s
from backend.auth import issue
from backend.db import SessionLocal
from backend.models import Audit, BraceletBinding, Hypothesis, SensorEvent, Tray, TrayLoad
from backend.tests import test_units
from backend.tests.test_full_workflow import items
from backend.tests.test_store import enter
from backend.tests.test_units import load, mixed, unit_tray, units, weight_event  # noqa: F401  (fixture)


@pytest.fixture
def rfid(monkeypatch):
    monkeypatch.setattr(s, "MODE", "hardware")
    monkeypatch.setattr(settings, "AUTO_ADD", True)
    monkeypatch.setattr(settings, "CART_ATTRIBUTION", "rfid")
    monkeypatch.setattr(settings, "EXIT_READER", "R2")
    monkeypatch.setattr(settings, "EXIT_NEEDS_CHECKOUT", True)  # R2 bills only after Checkout in the app
    camera_watch.clear()
    yield
    camera_watch.clear()


def tap(uid, reader):
    """A bracelet tap from the shelf controller, numbered in order with its weight events."""
    from backend.schemas import DeviceEvent
    n, now = next(test_units._seq), time.time()
    s.ingest(DeviceEvent(event_id=f"tap-{n}", event_type="RFID", device_id="sensor-1", boot_id="boot", sequence_number=n,
                         tray_id="tray-a", capture_start=now, capture_end=now, monotonic_ms=n, clock_synced=False,
                         clock_uncertainty_ms=1000, calibration_version="sim-v1", evidence_refs=[f"RFID:{uid}", f"RFID_READER:{reader}"]))


def camera(before, after):
    """The camera saw `before` until a few seconds ago and sees `after` now. Earlier shelf events are
    moved 30 s into the past, as if the shopper took that long between steps."""
    camera_watch.clear()
    with s.transaction() as db:
        for e in db.scalars(select(SensorEvent)):
            e.received -= 30
        for b in db.scalars(select(BraceletBinding)):
            b.started -= 30
    now = time.time()
    camera_watch.record(before, at=now - 10)
    camera_watch.record(after, at=now - 0.2)


def hypotheses():
    with SessionLocal() as db:
        return list(db.scalars(select(Hypothesis).order_by(Hypothesis.id)))


def pending():
    return [h for h in hypotheses() if h.status == "PENDING_REVIEW"]


def rfid_of(sid):
    from backend.models import Bracelet
    with SessionLocal() as db:
        return db.scalar(select(Bracelet).where(Bracelet.session_id == sid)).rfid


def test_pickup_goes_to_the_shopper_who_just_checked_in(rfid):
    sid, _, _ = enter("A", "a")
    camera(["oat", "oat"], ["oat"])
    weight_event(-1030, pre=5000)
    assert items(sid) == [("oat", 1)]
    with SessionLocal() as db:
        assert db.get(Tray, "tray-a").stock == 9
    h = hypotheses()[0]
    assert h.status == "CONFIRMED" and h.proposal["customer_source"] == "check-in"


def test_last_bracelet_tap_on_either_reader_wins(rfid):
    sid_a, _, _ = enter("A", "a")
    sid_b, _, _ = enter("B", "b")   # B checked in last
    tap(rfid_of(sid_a), "R2")       # A taps the second reader: A is at the shelf, and R2 no longer bills
    camera(["oat"], [])
    weight_event(-1030, pre=5000)
    assert items(sid_a) == [("oat", 1)] and items(sid_b) == []
    tap(rfid_of(sid_b), "R1")       # now B
    camera(["oat"], [])
    weight_event(-1030, pre=3970)
    assert items(sid_b) == [("oat", 1)] and items(sid_a) == [("oat", 1)]
    with SessionLocal() as db:
        assert not db.scalar(select(Audit).where(Audit.action == "EXIT_TAP"))


def test_return_takes_it_back_out_of_the_same_cart(rfid):
    sid, _, _ = enter()
    camera(["oat"], [])
    weight_event(-1030, pre=5000)
    camera([], ["oat"])
    weight_event(1031, pre=3970)
    assert items(sid) == []
    assert sorted(h.proposal["action"] for h in hypotheses()) == ["PICKUP", "RETURN"]


def test_without_the_camera_it_waits_for_staff_with_the_shopper_filled_in(rfid):
    sid, _, _ = enter()
    weight_event(-1030, pre=5000)          # camera offline: no frames
    assert items(sid) == []
    [h] = pending()
    assert "CAMERA_UNAVAILABLE" in h.reason and h.session_id == sid and h.proposal["product_id"] == "oat"
    s.review(h.id, "CONFIRM", sid, 1, "Saw it in the clip", "staff", product_id="oat")
    assert items(sid) == [("oat", 1)]


def test_staff_must_name_the_product_when_nothing_could(rfid):
    sid, _, _ = enter()
    camera(["oat", "coffee"], [])           # two products left: the camera cannot say which one
    weight_event(-1030, pre=5000)
    [h] = pending()
    assert "CAMERA_AMBIGUOUS" in h.reason and h.proposal["product_id"] is None
    with pytest.raises(Exception, match="Choose the product"):
        s.review(h.id, "CONFIRM", sid, 1, "checked", "staff")
    s.review(h.id, "CONFIRM", sid, 1, "checked", "staff", product_id="oat")
    assert items(sid) == [("oat", 1)]


def test_weight_must_fit_the_product_the_camera_saw(rfid):
    sid, _, _ = enter()
    camera(["oat"], [])
    weight_event(-200, pre=5000)            # oat weighs 1030 g
    assert items(sid) == [] and "WEIGHT_PRODUCT_MISMATCH" in pending()[0].reason


def test_no_recent_tap_means_no_shopper(rfid, monkeypatch):
    enter()
    monkeypatch.setattr(settings, "SHELF_PRESENCE_S", 0.0)
    camera(["oat"], [])
    weight_event(-1030, pre=5000)
    [h] = pending()
    assert "NO_SHOPPER_AT_SHELF" in h.reason and h.session_id is None


def test_return_without_it_in_the_cart_is_reviewed(rfid):
    sid, _, _ = enter()
    camera([], ["oat"])
    weight_event(1030, pre=5000)
    assert items(sid) == [] and "RETURN_WITHOUT_MATCHING_CART" in pending()[0].reason


def test_small_steps_and_tray_loading_are_not_shopping(rfid):
    enter()
    weight_event(-3, pre=5000)               # drift / a hand resting
    with s.transaction() as db:
        db.add(TrayLoad(tray_id="tray-a", actor="staff", reason="restock", status="OPEN"))
    weight_event(1030, pre=4997)
    assert hypotheses() == []


def test_weighed_units_name_the_product_even_without_the_camera(rfid, mixed):
    sid, _, _ = enter()
    weight_event(-60.4, pre=436.8)
    assert items(sid) == [("chips", 1)] and units()[mixed["chips"]].status == "IN_CART"


def test_camera_breaks_a_weight_tie_and_must_not_contradict(rfid):
    load_id = unit_tray()
    load(load_id, "chips", 60.3), load(load_id, "bar", 60.1)
    s.close_load(load_id, "staff")
    sid, _, _ = enter()
    camera(["chips", "bar"], ["chips"])     # the bar left
    weight_event(-60.2, pre=120.4)
    assert items(sid) == [("bar", 1)]


def test_a_decided_shelf_step_does_not_hold_up_the_exit(rfid):
    sid, _, _ = enter()
    camera(["oat"], [])
    weight_event(-1030, pre=5000)
    with SessionLocal() as db:
        assert s.exit_blockers(db, sid) == []


def test_camera_interaction_is_evidence_only(rfid):
    from backend.tests.test_auto_add import camera_event
    sid, marker, _ = enter()
    camera(["oat"], [])
    source = weight_event(-1030, pre=5000)
    s.ingest(camera_event(marker, -1030, 1, source, product="oat", sku=True))
    assert items(sid) == [("oat", 1)] and len(hypotheses()) == 1


def test_camera_before_window_starts_at_the_previous_step():
    camera_watch.clear()
    now = time.time()
    camera_watch.record(["oat"], at=now - 15)     # before an earlier pickup
    camera_watch.record([], at=now - 8)           # after it
    camera_watch.record(["oat"], at=now - 0.2)    # put back now
    assert camera_watch.change(now) is None or camera_watch.change(now) == {}
    assert camera_watch.change(now, since=now - 10) == {"oat": 1}
    camera_watch.clear()


def test_staff_socket_pushes_live_weight_and_changes(rfid):
    client = TestClient(main.app)
    with client.websocket_connect("/ws/staff") as ws:
        ws.send_json({"token": issue("staff", "tester")})
        main.note_live("tray-a", 1031.4, 1030.0)
        message = ws.receive_json()
        while "tray-a" not in message.get("trays", {}):
            message = ws.receive_json()
        assert message["trays"]["tray-a"]["display_g"] == 1030.0   # within 3 g of the held weight: held
        main.note_live("tray-a", 1500.2, 1030.0)
        message = ws.receive_json()
        assert message["type"] == "live" and message["trays"]["tray-a"]["display_g"] == 1500.2
        enter()
        while message["type"] != "changed":
            message = ws.receive_json()


def test_customer_socket_is_refused_on_the_staff_channel(rfid):
    client = TestClient(main.app)
    with client.websocket_connect("/ws/staff") as ws:
        ws.send_json({"token": issue("customer", "someone")})
        with pytest.raises(Exception):
            ws.receive_json()


def test_only_the_accepted_historical_loss_count_is_healthy(monkeypatch):
    old = {"ok": False, "flags": [], "queue_overflow": 67}
    assert not s.sensor_healthy(old)                      # nothing accepted: any loss count is unhealthy
    monkeypatch.setattr(settings, "SENSOR_ACCEPTED_OVERFLOW", 67)
    assert s.sensor_healthy(old)
    assert not s.sensor_healthy({**old, "queue_overflow": 68})   # a new loss
    assert not s.sensor_healthy({**old, "flags": ["DISCONNECTED_A"]})
    assert not s.sensor_healthy({**old, "queue_overflow": 0})    # ok=false with no losses: storage fault
    assert s.sensor_healthy({"ok": True, "flags": [], "queue_overflow": 0})


def receipt(sid):
    from backend.tests.test_store import snapshot
    return snapshot(sid)["receipt"]


def test_checkout_in_the_app_then_exit_tap_bills_the_visit(rfid):
    sid, _, _ = enter()
    camera(["oat"], [])
    weight_event(-1030, pre=5000)
    uid = rfid_of(sid)
    tap(uid, "R2")                                       # not checked out yet: just at the shelf
    assert receipt(sid) is None
    s.request_checkout(sid)
    from backend.tests.test_store import snapshot
    assert snapshot(sid)["checkout_requested"] and snapshot(sid)["checkout_needs_tap"]
    tap(uid, "R2")                                       # handed over at the exit reader
    s.process_exit_requests()
    bill = receipt(sid)
    assert bill and bill["total_minor"] == 6500
    from backend.models import Bracelet
    with SessionLocal() as db:
        b = db.scalar(select(Bracelet).where(Bracelet.rfid == uid))
        assert b.state == "AVAILABLE" and b.session_id is None   # handed over at the exit: ready for the next customer
    sid2, _, _ = enter("Next", "next")                          # and it can be handed out again
    assert rfid_of(sid2)


def test_keep_shopping_withdraws_the_checkout(rfid):
    sid, _, _ = enter()
    s.request_checkout(sid)
    s.request_checkout(sid, withdraw=True)
    tap(rfid_of(sid), "R2")
    s.process_exit_requests()
    assert receipt(sid) is None


def test_a_shopper_on_the_way_out_is_not_at_the_shelf(rfid):
    sid_a, _, _ = enter("A", "a")
    sid_b, _, _ = enter("B", "b")
    tap(rfid_of(sid_a), "R1")
    s.request_checkout(sid_a)                            # A is leaving; B is still shopping
    camera(["oat"], [])
    weight_event(-1030, pre=5000)
    assert items(sid_a) == [] and items(sid_b) == [("oat", 1)]


def test_checkout_needs_an_active_visit_with_a_bracelet(rfid):
    sid = s.new_customer("No bracelet")
    with pytest.raises(Exception, match="active visit"):
        s.request_checkout(sid)


def test_after_the_bill_the_customer_starts_a_fresh_visit(rfid):
    sid, _, _ = enter()
    with pytest.raises(Exception, match="Finish this visit"):
        s.next_visit(sid)                               # still shopping
    s.request_checkout(sid)
    tap(rfid_of(sid), "R2")
    s.process_exit_requests()
    new = s.next_visit(sid)
    assert new != sid and s.next_visit(sid) == new      # pressing it twice gives the same fresh visit
    assert s.entry_qr(new)["token"]
    from backend.tests.test_store import snapshot
    assert snapshot(new)["status"] == "ENTRY" and snapshot(sid)["receipt"]


def test_a_pickup_nobody_is_charged_for_still_leaves_the_stock(rfid, mixed, monkeypatch):
    monkeypatch.setattr(settings, "SHELF_PRESENCE_S", 0.0)  # nobody at the shelf
    weight_event(-60.4, pre=436.8)
    [h] = pending()
    assert "NO_SHOPPER_AT_SHELF" in h.reason and h.proposal["unit_ids"] == [mixed["chips"]]
    s.review(h.id, "LEFT_TRAY", None, 1, "Staff took it for display", "staff")
    assert units()[mixed["chips"]].status == "REMOVED"
    with SessionLocal() as db:
        assert db.get(Tray, "tray-a").stock == 2
    with pytest.raises(Exception, match="Already resolved"):
        s.review(h.id, "LEFT_TRAY", None, 1, "again", "staff")


def test_an_item_no_cart_holds_is_recorded_as_stock(rfid, mixed):
    sid, _, _ = enter()
    weight_event(40.2, pre=436.8)                       # something new put on the tray
    [h] = pending()
    assert h.proposal["action"] == "RETURN"
    with pytest.raises(Exception, match="record it as stock"):
        s.review(h.id, "CONFIRM", sid, 1, "put back", "staff", product_id="bar")
    s.review(h.id, "RESTOCK", None, 1, "Staff restocked", "staff", product_id="bar")
    added = [u for u in units().values() if u.source_event_id == h.event_id]
    assert len(added) == 1 and added[0].status == "ON_TRAY" and added[0].weight_g == pytest.approx(40.2)
    with SessionLocal() as db:
        assert db.get(Tray, "tray-a").stock == 4
    weight_event(-40.3, pre=477.0)                      # and it can be bought like any weighed item
    assert items(sid) == [("bar", 1)]


# ---------------- Hybrid: RFID while one shopper is at the shelf, the camera when two are ----------------

@pytest.fixture
def hybrid(rfid, monkeypatch):
    monkeypatch.setattr(settings, "CART_ATTRIBUTION", "hybrid")


def two_at_the_shelf():
    sid_a, marker_a, _ = enter("A", "a")
    sid_b, marker_b, _ = enter("B", "b")
    with s.transaction() as db:                             # checked in a minute ago, as in a real visit
        for binding in db.scalars(select(BraceletBinding)):
            binding.started -= 60
    tap(rfid_of(sid_a), "R1")
    tap(rfid_of(sid_b), "R2")
    return (sid_a, marker_a), (sid_b, marker_b)


def test_hybrid_one_shopper_is_still_rfid(hybrid, mixed):
    sid, marker, _ = enter()
    from backend.tests.test_auto_add import camera_event
    source = weight_event(-60.4, pre=436.8)
    assert items(sid) == [("chips", 1)]                      # decided by RFID + weight at once
    s.ingest(camera_event(marker, -60.4, 1, source))          # the camera's report is evidence only
    assert items(sid) == [("chips", 1)] and len(hypotheses()) == 1











def test_unowned_review_items_concern_only_shoppers_who_were_at_the_shelf(hybrid, mixed, monkeypatch):
    from backend import shelf_cart
    (sid_a, _), (sid_b, _) = two_at_the_shelf()
    weight_event(-331.6, pre=436.8)
    shelf_cart.sweep(now=time.time() + shelf_cart.CAMERA_WAIT_S + 1)     # camera silent: unowned review item
    from backend.tests.test_store import snapshot
    assert len(snapshot(sid_a)["pending"]) == 1 and len(snapshot(sid_b)["pending"]) == 1
    sid_c, _, _ = enter("C", "c")                                         # arrives afterwards
    assert snapshot(sid_c)["pending"] == []
    with SessionLocal() as db:
        assert s.exit_blockers(db, sid_c) == [] and s.exit_blockers(db, sid_a)


def sighted(marker, seconds_ago=0.0):
    """The camera confirmed this bracelet pattern (N frames in a row) `seconds_ago` seconds ago."""
    from backend import shelf_cart
    shelf_cart.bracelet_seen(marker, "tray-a", 5, time.time() - seconds_ago)


def test_hybrid_two_shoppers_the_bracelet_seen_nearest_in_time_gets_it(hybrid, mixed):
    (sid_a, marker_a), (sid_b, marker_b) = two_at_the_shelf()
    sighted(marker_b, 12)                                   # B's bracelet earlier
    sighted(marker_a, 2)                                    # A's bracelet just before the weight dropped
    weight_event(-331.6, pre=436.8)
    assert items(sid_a) == [("coffee", 1)] and items(sid_b) == []
    weight_event(-60.4, pre=105.2)                          # B's sighting is still unused: B's
    assert items(sid_b) == [("chips", 1)]


def test_hybrid_sighting_after_the_weight_still_decides(hybrid, mixed):
    (sid_a, _), (sid_b, marker_b) = two_at_the_shelf()
    weight_event(-331.6, pre=436.8)
    assert hypotheses() == []                               # no bracelet confirmed yet: waits
    sighted(marker_b)                                       # the camera confirms B's bracelet a moment later
    assert items(sid_b) == [("coffee", 1)] and items(sid_a) == []


def test_hybrid_one_sighting_decides_one_weight_change(hybrid, mixed):
    (sid_a, marker_a), (sid_b, _) = two_at_the_shelf()
    sighted(marker_a, 1)
    weight_event(-331.6, pre=436.8)
    weight_event(-60.4, pre=105.2)                          # nobody's bracelet confirmed for this one
    assert items(sid_a) == [("coffee", 1)] and len(hypotheses()) == 1


def test_hybrid_no_bracelet_seen_goes_to_review_with_who_was_there(hybrid, mixed):
    from backend import shelf_cart
    (sid_a, _), (sid_b, _) = two_at_the_shelf()
    source = weight_event(-331.6, pre=436.8)
    shelf_cart.sweep(now=time.time() + 5)
    assert hypotheses() == []
    shelf_cart.sweep(now=time.time() + shelf_cart.CAMERA_WAIT_S + 1)
    [h] = pending()
    assert h.event_id == source and "NO_BRACELET_SEEN" in h.reason
    assert set(h.proposal["shoppers_present"]) == {sid_a, sid_b}
    shelf_cart.sweep(now=time.time() + shelf_cart.CAMERA_WAIT_S + 2)
    assert len(hypotheses()) == 1


def test_hybrid_sighting_of_a_bracelet_with_no_visit_is_ignored(hybrid, mixed):
    (sid_a, marker_a), (sid_b, _) = two_at_the_shelf()
    sighted(999, 1)                                         # a pattern no checked-in bracelet carries
    weight_event(-331.6, pre=436.8)
    assert hypotheses() == []


def test_hybrid_camera_pickup_reports_are_evidence_only(hybrid, mixed):
    from backend.tests.test_auto_add import camera_event
    (sid_a, marker_a), (sid_b, marker_b) = two_at_the_shelf()
    sighted(marker_a, 1)
    source = weight_event(-331.6, pre=436.8)
    s.ingest(camera_event(marker_b, -331.6, 1, source))    # the camera's own chain names B: ignored
    assert items(sid_a) == [("coffee", 1)] and items(sid_b) == []


def test_items_sold_on_a_billed_visit_do_not_make_the_tray_weighed(rfid):
    load_id = unit_tray()
    load(load_id, "chips", 60.3)
    s.close_load(load_id, "staff")
    sid_a, _, _ = enter("A", "a")
    weight_event(-60.4, pre=60.3)                            # A takes the only weighed item
    assert items(sid_a) == [("chips", 1)]
    s.request_checkout(sid_a)
    tap(rfid_of(sid_a), "R2")
    s.process_exit_requests()                                # A is billed: the chips record stays "in cart"
    sid_b, _, _ = enter("B", "b")
    camera(["coffee"], [])                                   # nothing on record on the tray: the camera names it
    weight_event(-330.4, pre=330.3)
    assert items(sid_b) == [("coffee", 1)]


def test_an_unknown_blob_beside_the_named_product_does_not_make_it_ambiguous(rfid):
    sid, _, _ = enter()
    camera(["oat", "UNKNOWN"], [])                           # the hand-held item was also seen as "unknown"
    weight_event(-1030, pre=5000)
    assert items(sid) == [("oat", 1)]


def test_only_an_unknown_leaving_still_goes_to_review(rfid):
    sid, _, _ = enter()
    camera(["UNKNOWN"], [])
    weight_event(-1030, pre=5000)
    assert items(sid) == [] and "CAMERA_PRODUCT_UNKNOWN" in pending()[0].reason


def test_a_weight_review_item_plays_the_camera_clip_that_cites_it(rfid):
    from backend import analytics
    from backend.tests.test_auto_add import camera_event
    sid, marker, _ = enter()
    source = weight_event(-1030, pre=5000)                    # camera offline for the shelf: goes to review
    [h] = pending()
    e = camera_event(marker, -1030, 1, source)
    e.evidence_refs = e.evidence_refs + ["clip-a.jpg", "clip-b.jpg"]
    s.ingest(e)                                               # evidence only, but it holds the video
    with SessionLocal() as db:
        assert analytics.clip_event(db, db.get(SensorEvent, h.event_id)).id == e.event_id
        assert [f["ref"] for f in analytics.timeline(db, h.event_id)["frames"]] == ["clip-a.jpg", "clip-b.jpg"]


def test_a_product_last_sold_on_a_billed_visit_is_added_by_the_camera(rfid):
    load_id = unit_tray()
    load(load_id, "chips", 60.3)
    s.close_load(load_id, "staff")
    sid_a, _, _ = enter("A", "a")
    weight_event(-60.4, pre=60.3)
    s.request_checkout(sid_a); tap(rfid_of(sid_a), "R2"); s.process_exit_requests()   # chips sold, visit billed
    sid_b, _, _ = enter("B", "b")
    camera(["chips"], [])                                    # another bag of chips, never weighed in
    weight_event(-60.2, pre=60.2)
    assert items(sid_b) == [("chips", 1)]


def test_a_refused_cart_change_goes_to_review_and_the_shelf_event_is_kept(rfid, monkeypatch):
    from fastapi import HTTPException
    def refuse(*a, **k):
        raise HTTPException(409, "something refused it")
    monkeypatch.setattr(s, "apply_proposal", refuse)
    sid, _, _ = enter()
    camera(["oat"], [])
    source = weight_event(-1030, pre=5000)                   # must not raise: the bridge would retry it
    [h] = pending()
    assert h.event_id == source and h.reason == ["CART_UPDATE_REFUSED"] and "refused" in h.proposal


def test_hybrid_one_shopper_the_bracelet_seen_decides_and_it_goes_straight_to_the_cart(hybrid):
    sid, marker, _ = enter()
    with s.transaction() as db:
        for binding in db.scalars(select(BraceletBinding)):
            binding.started -= 60
    camera(["oat"], [])
    sighted(marker, 4)                                       # the bracelet came into view before the pickup
    weight_event(-1030, pre=5000)
    assert items(sid) == [("oat", 1)] and pending() == []
    [h] = hypotheses()
    assert h.proposal["customer_source"] == f"bracelet {marker} seen on camera"


def test_camera_before_view_ends_when_the_bracelet_appeared():
    camera_watch.clear()
    now = time.time()
    camera_watch.record(["oat", "chips"], at=now - 9)        # tray before anyone came
    camera_watch.record(["chips"], at=now - 4)               # hand already over the oat (after the bracelet)
    camera_watch.record(["chips"], at=now - 0.2)             # oat gone
    assert camera_watch.change(now, before_end=now - 6) == {"oat": -1}
    camera_watch.clear()

