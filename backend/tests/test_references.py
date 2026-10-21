"""Few-shot reference registry, teach mode, look-alike warnings and wrong-tray returns."""
import base64
import time
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend import service as s
from backend import references as refs
from backend.main import app
from backend.db import SessionLocal
from backend.models import *
from backend.schemas import Weight
from backend.tests.test_store import enter, snapshot
from backend.tests.test_units import unit_tray, weight_event, units, pending_reasons, take

JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"x" * 64).decode()
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"y" * 64).decode()

@pytest.fixture(autouse=True)
def evidence_root(tmp_path, monkeypatch):
    monkeypatch.setenv("EVIDENCE_ROOT", str(tmp_path))

def staff_headers(c):
    return {"Authorization": "Bearer " + c.post("/auth/staff", json={"password": "local-demo-staff"}).json()["token"]}

def load_with_stills(product="coffee", grams=331.4, tray="tray-a"):
    load_id = unit_tray(tray)
    return s.add_unit(load_id, product, weight_event(grams, tray=tray), None, ["load-x-before.jpg", "load-x-after.jpg"], "staff")["unit_id"]

def test_new_product_from_staff_without_any_photos():
    refs.create_product("hand-cream", "Hand cream", 14900, "staff")
    with SessionLocal() as db:
        p = db.get(Product, "hand-cream")
        assert p.weight_source == "UNMEASURED" and p.price_minor == 14900
    for bad in ("Hand Cream", "x", "a" * 41):
        with pytest.raises(HTTPException):
            refs.create_product(bad, "n", 1, "staff")
    with pytest.raises(HTTPException):
        refs.create_product("hand-cream", "dup", 1, "staff")

def test_restock_unit_becomes_enrolment_job_and_auto_reference():
    unit_id = load_with_stills()
    with SessionLocal() as db:
        jobs = refs.enrollment_jobs(db)
    assert [(j["unit_id"], j["before"], j["after"], j["product_id"]) for j in jobs] == [(unit_id, "load-x-before.jpg", "load-x-after.jpg", "coffee")]
    with pytest.raises(HTTPException):
        refs.add_reference("chips", "RESTOCK_AUTO", JPEG, [1, 2, 3, 4], {}, unit_id=unit_id)  # wrong product
    out = refs.add_reference("coffee", "RESTOCK_AUTO", JPEG, [1, 2, 3, 4], {"sharpness": 120}, unit_id=unit_id)
    assert (refs.catalogue_root() / out["file"]).read_bytes().startswith(b"\xff\xd8")
    assert units()[unit_id].reference_status == "DONE"
    with SessionLocal() as db:
        assert refs.enrollment_jobs(db) == []
    with pytest.raises(HTTPException):
        refs.add_reference("coffee", "RESTOCK_AUTO", JPEG, None, {}, unit_id=unit_id)  # only once per unit

def test_failed_crop_is_recorded_not_retried_forever():
    unit_id = load_with_stills()
    refs.job_failed(unit_id, "NO_CHANGE")
    assert units()[unit_id].reference_status == "FAILED:NO_CHANGE"
    with SessionLocal() as db:
        assert refs.enrollment_jobs(db) == []

def test_unit_without_stills_is_not_an_enrolment_job():
    load_id = unit_tray()
    unit_id = s.add_unit(load_id, "coffee", weight_event(331.4), None, [], "staff")["unit_id"]
    assert units()[unit_id].reference_status == "NO_STILLS"

def test_teach_session_accepts_capped_views_only_while_active():
    teach = refs.start_teach("chips", "tray-a", 20, 3, "learn new crisps", "staff")
    with pytest.raises(HTTPException):
        refs.start_teach("bar", "tray-a", 20, 3, "second at once", "staff")
    for _ in range(3):
        refs.add_reference("chips", "TEACH", PNG, None, {}, teach_id=teach["teach_id"])
    with pytest.raises(HTTPException):
        refs.add_reference("chips", "TEACH", PNG, None, {}, teach_id=teach["teach_id"])  # cap reached
    t2 = refs.start_teach("bar", "tray-a", 20, 5, "next product", "staff")
    with pytest.raises(HTTPException):
        refs.add_reference("chips", "TEACH", PNG, None, {}, teach_id=t2["teach_id"])  # wrong product
    with s.transaction() as db:
        db.get(TeachSession, t2["teach_id"]).until = time.time() - 1
    with pytest.raises(HTTPException):
        refs.add_reference("bar", "TEACH", PNG, None, {}, teach_id=t2["teach_id"])  # expired

def test_reference_images_are_validated():
    teach = refs.start_teach("chips", "tray-a", 20, 10, "t", "staff")
    for bad in ("not-base64!!", base64.b64encode(b"GIF89a....").decode(), base64.b64encode(b"\xff\xd8\xff" + b"0" * 1_600_000).decode()):
        with pytest.raises(HTTPException):
            refs.add_reference("chips", "TEACH", bad, None, {}, teach_id=teach["teach_id"])

def test_device_and_staff_endpoints_round_trip():
    unit_id = load_with_stills()
    with TestClient(app) as c:
        h = staff_headers(c)
        dev = {"X-Device-Key": "local-demo-device"}
        assert c.post("/staff/catalogue", headers=h, json={"product_id": "lip-balm", "name": "Lip balm", "price_minor": 4900}).status_code == 200
        teach = c.post("/staff/products/lip-balm/teach", headers=h, json={"tray_id": "tray-a", "seconds": 15, "max_refs": 5, "reason": "new item"}).json()
        config = c.get("/devices/vision-1/config", headers=dev).json()
        assert config["trays"]["tray-a"]["teach"]["product_id"] == "lip-balm"
        jobs = c.get("/devices/vision-1/enrollment-jobs", headers=dev).json()
        assert jobs[0]["unit_id"] == unit_id
        assert c.get("/devices/vision-1/enrollment-jobs").status_code == 422  # device key required
        r = c.post("/devices/vision-1/references", headers=dev, json={"product_id": "lip-balm", "source": "TEACH", "image_b64": JPEG, "teach_id": teach["teach_id"]}).json()
        listed = c.get("/devices/vision-1/references", headers=dev).json()
        assert [x["id"] for x in listed] == [r["reference_id"]]
        assert c.get(f"/devices/vision-1/catalogue-file/{r['file']}", headers=dev).content.startswith(b"\xff\xd8")
        assert c.get("/devices/vision-1/catalogue-file/..%2Fsecret.jpg", headers=dev).status_code == 404
        assert c.post(f"/staff/references/{r['reference_id']}/reject", headers=h, json={"reason": "blurry hand"}).json()["status"] == "REJECTED"
        assert c.get("/devices/vision-1/references", headers=dev).json()[0]["status"] == "REJECTED"
        assert c.post(f"/staff/teach/{teach['teach_id']}/stop", headers=h).json()["status"] == "STOPPED"
        status = {"version": "gallery-abc", "products": {"lip-balm": 4}, "live_references": 4, "pairs": [{"a": "bar", "b": "chips", "similarity": 0.91}], "groups": []}
        assert c.post("/devices/vision-1/gallery-status", headers=dev, json=status).status_code == 200
        assert c.get("/staff/references", headers=h).json()["gallery_status"]["pairs"][0]["a"] == "bar"

def test_lookalike_and_close_weight_get_strongest_warning():
    refs.save_gallery_status({"version": "g", "products": {}, "live_references": 0, "pairs": [{"a": "bar", "b": "chips", "similarity": 0.92}], "groups": []})
    load_id = unit_tray()
    s.add_unit(load_id, "chips", weight_event(60.3), None, [], "staff")
    w = s.add_unit(load_id, "bar", weight_event(59.6), None, [], "staff")["warnings"]
    assert w[0]["code"] == "LOOKALIKE_AND_WEIGHT_TOO_CLOSE" and "different trays" in w[0]["advice"]

def test_return_on_wrong_tray_is_held_then_staff_relocates_unit():
    load_a = unit_tray("tray-a")
    coffee = s.add_unit(load_a, "coffee", weight_event(331.4, tray="tray-a"), None, [], "staff")["unit_id"]
    unit_tray("tray-b")
    sid, marker, _ = enter()
    s.ingest(take(marker, -331.5, 1))
    assert units()[coffee].status == "IN_CART"
    back = take(marker, +331.3, 2, action="RETURN")
    back.tray_id = "tray-b"
    s.ingest(back)
    assert "RETURNED_TO_OTHER_TRAY" in pending_reasons(sid) and snapshot(sid)["items"][0]["product_id"] == "coffee"
    hid = next(p["id"] for p in snapshot(sid)["pending"] if "RETURNED_TO_OTHER_TRAY" in p["reasons"])
    s.review(hid, "CONFIRM", sid, 1, "Video shows coffee put back on tray B", "staff")
    u = units()[coffee]
    assert snapshot(sid)["items"] == [] and u.status == "ON_TRAY" and u.tray_id == "tray-b"
    with SessionLocal() as db:
        assert db.get(Tray, "tray-b").stock == 1 and db.get(Tray, "tray-a").stock == 0
        assert db.scalar(select(Audit).where(Audit.action == "UNIT_RELOCATED"))

def test_unreviewed_relocation_is_refused():
    load_a = unit_tray("tray-a")
    coffee = s.add_unit(load_a, "coffee", weight_event(331.4), None, [], "staff")["unit_id"]
    sid, _, _ = enter()
    with s.transaction() as db:
        u = db.get(TrayUnit, coffee); u.status, u.session_id = "IN_CART", sid
        tray_b = db.get(Tray, "tray-b"); tray_b.unit_tracking = True
        with pytest.raises(HTTPException):
            s.move_units(db, tray_b, db.get(ShoppingSession, sid), db.get(Product, "coffee"),
                         {"unit_ids": [coffee], "quantity": 1, "action": "RETURN"}, reviewed=False)
