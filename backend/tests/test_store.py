import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend import service as s
from backend.main import app
from backend.db import SessionLocal
from backend.models import *
from simulator.scenarios import interaction

def enter(alias="Shopper", command="cmd"):
    sid = s.new_customer(alias)
    token = s.entry_qr(sid)["token"]
    gate = s.dispense(token, command, "staff")
    with SessionLocal() as db:
        b = db.get(Bracelet, gate["bracelet_id"])
        marker, rfid = b.markers[0], b.rfid
    s.gate_ack(command, rfid, True, True, "", "gate")
    return sid, marker, token

def snapshot(sid):
    with SessionLocal() as db:
        return s.cart(db, sid)

def test_vertical_slice_and_payment_idempotency():
    sid, marker, _ = enter()
    s.ingest(interaction(marker=marker, sequence=1))
    assert snapshot(sid)["total_minor"] == 6500
    s.ingest(interaction(marker=marker, action="RETURN", sequence=2))
    assert snapshot(sid)["items"] == []
    s.ingest(interaction(marker=marker, sequence=3))
    assert s.checkout(sid, "timeout", "TIMEOUT", False, "", "customer")["state"] == "TIMEOUT"
    assert s.checkout(sid, "timeout", "SUCCESS", False, "", "customer")["state"] == "TIMEOUT"
    s.checkout(sid, "retry", "SUCCESS", False, "", "customer")
    assert snapshot(sid)["receipt"]["total_minor"] == 6500
    assert snapshot(sid)["status"] == "COMPLETE"

def test_qr_replay_and_dispense_duplicate():
    sid = s.new_customer("A")
    token = s.entry_qr(sid)["token"]
    a = s.dispense(token, "same", "staff")
    assert s.dispense(token, "same", "staff") == a
    with pytest.raises(HTTPException):
        s.dispense(token, "other", "staff")

@pytest.mark.parametrize("uid,released,position,fault", [("WRONG", True, True, ""), ("04AABB01", False, True, ""), ("04AABB01", True, False, ""), ("04AABB01", True, True, "JAM")])
def test_gate_faults(uid, released, position, fault):
    sid = s.new_customer("A")
    token = s.entry_qr(sid)["token"]
    s.dispense(token, "c", "staff")
    assert s.gate_ack("c", uid, released, position, fault, "gate")["state"] == "FAULTED"
    assert snapshot(sid)["status"] == "ENTRY"
    with pytest.raises(HTTPException):
        s.gate_ack("c", "04AABB01", True, True, "", "gate")

@pytest.mark.parametrize("changes,reason", [({"hand_product": False}, "OWNERSHIP_CHAIN_UNPROVEN"), ({"marker_ids": []}, "IDENTITY_UNPROVEN"), ({"crossed": True}, "UNSUPPORTED_OR_OCCLUDED_CHAIN"), ({"opposite_hand": True}, "UNSUPPORTED_OR_OCCLUDED_CHAIN"), ({"handoff": True}, "UNSUPPORTED_OR_OCCLUDED_CHAIN"), ({"occluded": True}, "UNSUPPORTED_OR_OCCLUDED_CHAIN"), ({"blur": True}, "CAMERA_EVIDENCE"), ({"frame_age_ms": 900}, "CAMERA_EVIDENCE"), ({"frame_gap_ms": 700}, "CAMERA_EVIDENCE"), ({"model_status": "MODEL_UNAVAILABLE"}, "MODEL_UNAVAILABLE"), ({"clock_uncertainty_ms": 200}, "CLOCK_UNCERTAINTY"), ({"rfid_uid": "WRONG"}, "RFID_MARKER_CONFLICT")])
def test_evidence_vetoes(changes, reason):
    sid, marker, _ = enter()
    s.ingest(interaction(marker=marker, **changes))
    cart = snapshot(sid)
    assert cart["items"] == []
    assert reason in cart["pending"][0]["reasons"]
    with pytest.raises(HTTPException):
        s.checkout(sid, "pay", "SUCCESS", False, "", "customer")

def test_duplicate_delivery_and_sequence_conflict():
    sid, marker, _ = enter()
    e = interaction(marker=marker)
    assert not s.ingest(e)["duplicate"]
    assert s.ingest(e)["duplicate"]
    assert snapshot(sid)["version"] == 1
    with pytest.raises(HTTPException):
        s.ingest(interaction(marker=marker))

def test_out_of_order_reboot_and_old_boot_replay():
    sid, m, _ = enter()
    s.ingest(interaction(marker=m, sequence=10))
    s.ingest(interaction(marker=m, sequence=9))
    s.ingest(interaction(marker=m, boot_id="reboot", sequence=0))
    s.ingest(interaction(marker=m, sequence=11))
    assert snapshot(sid)["version"] == 2
    assert len(snapshot(sid)["pending"]) == 2

def test_two_customers_concurrent_different_trays():
    sid1, m1, _ = enter(command="a")
    sid2, m2, _ = enter(command="b")
    events = [interaction(marker=m1, sequence=1), interaction(marker=m2, tray="tray-b", sequence=2)]
    # Distinct devices: delivery ordering is independent, not a single shopper rule.
    events[1].device_id = "vision-1"
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(s.ingest, events))
    assert snapshot(sid1)["total_minor"] == 6500
    assert snapshot(sid2)["total_minor"] == 9500

def test_same_tray_joint_and_product_not_double_assigned():
    sid1, m1, _ = enter(command="a")
    sid2, m2, _ = enter(command="b")
    e = interaction(marker=m1)
    e.candidates.append(interaction(marker=m2).candidates[0])
    e.weight.post_g -= 1030
    s.ingest(e)
    assert snapshot(sid1)["total_minor"] == snapshot(sid2)["total_minor"] == 6500
    e2 = interaction(marker=m1, sequence=2)
    e2.candidates.append(e2.candidates[0].model_copy(update={"marker_ids": [m2]}))
    e2.weight.post_g -= 1030
    s.ingest(e2)
    assert "DUPLICATE_PRODUCT_TRACK" in snapshot(sid1)["pending"][0]["reasons"]

def test_equal_weight_exchange_requires_review():
    sid1, m1, _ = enter(command="a")
    sid2, m2, _ = enter(command="b")
    s.ingest(interaction(marker=m2, sequence=1))
    e = interaction(marker=m1, sequence=2)
    e.candidates.append(interaction(marker=m2, action="RETURN").candidates[0])
    e.weight.post_g = e.weight.pre_g
    s.ingest(e)
    assert snapshot(sid1)["items"] == []
    assert "CANCELLING_ACTIONS_REQUIRE_REVIEW" in snapshot(sid1)["pending"][0]["reasons"]

def test_weight_fault_quantity_and_unmatched_return():
    sid, m, _ = enter()
    a = interaction(marker=m, action="RETURN", sequence=1)
    s.ingest(a)
    assert "RETURN_WITHOUT_MATCHING_CART" in snapshot(sid)["pending"][0]["reasons"]
    b = interaction(marker=m, sequence=2)
    b.weight.post_g += 300
    b.sensor_health.flags = ["DRIFT"]
    s.ingest(b)
    assert {"SENSOR_HEALTH", "WEIGHT_MISMATCH"} <= set(snapshot(sid)["pending"][1]["reasons"])

def test_http_authorization_websocket_snapshot():
    with TestClient(app) as c:
        assert c.get("/staff/state").status_code == 403
        login = c.post("/auth/customer", json={"alias": "A"}).json()
        assert c.get("/staff/state", headers={"Authorization": "Bearer " + login["token"]}).status_code == 403
        with c.websocket_connect("/ws/cart") as ws:
            ws.send_json({"token": login["token"]})
            first = ws.receive_json()
            assert first["type"] == "snapshot" and first["version"] == 0
        with c.websocket_connect("/ws/cart") as ws:
            ws.send_json({"token": login["token"]})
            assert ws.receive_json()["version"] == 0

def test_expired_capture_durable_but_pending():
    sid, m, _ = enter()
    e = interaction(marker=m)
    e.capture_start -= 60
    e.capture_end -= 60
    assert s.ingest(e)["durable"]
    assert snapshot(sid)["version"] == 0

def test_reused_trajectory_never_billed_twice():
    sid, m, _ = enter()
    e = interaction(marker=m, sequence=1)
    s.ingest(e)
    e2 = interaction(marker=m, sequence=2)
    e2.candidates[0].product_track = e.candidates[0].product_track
    s.ingest(e2)
    assert snapshot(sid)["version"] == 1

def test_expired_websocket_auth_closes_with_policy_code():
    import jwt
    from starlette.websockets import WebSocketDisconnect
    from backend.config import JWT_SECRET
    expired=jwt.encode({'role':'customer','sub':'old-session','iat':0,'exp':1},JWT_SECRET,algorithm='HS256')
    with TestClient(app) as c:
        with c.websocket_connect('/ws/cart') as ws:
            ws.send_json({'token':expired})
            with pytest.raises(WebSocketDisconnect) as failure:ws.receive_json()
            assert failure.value.code==1008
