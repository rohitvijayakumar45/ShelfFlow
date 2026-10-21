"""Tripod (3-cell) trays: position from the load split, device calibration events, geometry."""
import time
import itertools
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from backend import service as s
from backend.main import app
from backend.db import SessionLocal
from backend.models import *
from backend.schemas import DeviceEvent, Weight
from backend.tests.test_store import enter, snapshot
from backend.tests.test_units import unit_tray, units, pending_reasons, take

_seq = itertools.count(10_000)

def placed(delta, x, y, tray="tray-a"):
    """A settled WEIGHT event carrying the location (from the 3-cell split) where the weight landed."""
    n, now = next(_seq), time.time()
    event = DeviceEvent(event_id=f"t-{n}", event_type="WEIGHT", device_id="sensor-1", boot_id="boot-t", sequence_number=n,
                        tray_id=tray, capture_start=now, capture_end=now, monotonic_ms=n, clock_synced=False, clock_uncertainty_ms=1000,
                        calibration_version="sim-v1",
                        weight=Weight(pre_g=0, post_g=delta, noise_g=0.5, settled=True, position_mm={"x_mm": x, "y_mm": y}, geometry_version="g1",
                                      cells=[{"name": "A", "pre_g": 0, "post_g": delta / 3}, {"name": "B", "pre_g": 0, "post_g": delta / 3}, {"name": "C", "pre_g": 0, "post_g": delta / 3}]))
    s.ingest(event)
    return event.event_id

def take_at(marker, delta, sequence, x, y, **changes):
    event = take(marker, delta, sequence, **changes)
    event.weight = Weight(pre_g=2000, post_g=2000 + delta, noise_g=0.5, settled=True, position_mm={"x_mm": x, "y_mm": y})
    return event

@pytest.fixture
def lookalikes():
    """Crisps (60.3 g) at the left and a bar (59.6 g) at the right: weight alone cannot separate them."""
    load_id = unit_tray()
    chips = s.add_unit(load_id, "chips", placed(60.3, 40, 30), None, [], "staff")
    bar = s.add_unit(load_id, "bar", placed(59.6, 160, 30), None, [], "staff")
    return chips, bar

def test_loading_stores_where_each_unit_landed(lookalikes):
    chips, _ = lookalikes
    assert chips["position_mm"] == [40, 30]
    u = units()[chips["unit_id"]]
    assert (u.x_mm, u.y_mm) == (40, 30)

def test_position_resolves_weight_lookalikes(lookalikes):
    chips, bar = lookalikes
    sid, marker, _ = enter()
    s.ingest(take_at(marker, -60.0, 1, 158, 33))  # weight fits both; the change happened at the bar's place
    assert [i["product_id"] for i in snapshot(sid)["items"]] == ["bar"]
    assert units()[bar["unit_id"]].status == "IN_CART" and units()[chips["unit_id"]].status == "ON_TRAY"

def test_weight_fit_at_wrong_place_is_reviewed(lookalikes):
    sid, marker, _ = enter()
    s.ingest(take_at(marker, -60.0, 1, 100, 140))  # nothing of that weight was placed there
    assert snapshot(sid)["items"] == [] and "POSITION_CONFLICT" in pending_reasons(sid)

def test_return_records_the_new_place(lookalikes):
    chips, _ = lookalikes
    sid, marker, _ = enter()
    s.ingest(take_at(marker, -60.2, 1, 41, 29))
    s.ingest(take_at(marker, +60.3, 2, 120, 90, action="RETURN"))
    u = units()[chips["unit_id"]]
    assert u.status == "ON_TRAY" and (u.x_mm, u.y_mm) == (120, 90)

def calibration_event(version, sequence, late_boot=False, **overrides):
    now = time.time()
    data = dict(event_id=f"cal-{version}-{sequence}", event_type="CALIBRATION", device_id="sensor-1", boot_id="boot-old" if late_boot else "boot-cal",
                sequence_number=sequence, tray_id="tray-a", capture_start=now, capture_end=now, monotonic_ms=1, clock_synced=False,
                clock_uncertainty_ms=1000, calibration_version=version,
                calibration={"method": "SERVICE_MODE_3_POSITION", "known_mass_g": 500, "verify_total_g": 499.6,
                             "cells": [{"name": "A", "zero_raw": 8100, "counts_per_g": 412.5}, {"name": "B", "zero_raw": -2000, "counts_per_g": -398.1},
                                       {"name": "C", "zero_raw": 300, "counts_per_g": 405.0}]})
    data.update(overrides)
    return DeviceEvent(**data)

def test_device_calibration_event_sets_tray_version_and_audit():
    s.ingest(calibration_event("dev-1", 1))
    with SessionLocal() as db:
        assert db.get(Tray, "tray-a").calibration_version == "dev-1"
        row = db.scalar(select(Calibration).where(Calibration.tray_id == "tray-a"))
        assert row.metadata_["source"] == "DEVICE" and row.metadata_["previous"] == "sim-v1" and len(row.metadata_["cells"]) == 3
        assert db.scalar(select(Audit).where(Audit.action == "DEVICE_CALIBRATION"))

def test_stale_device_calibration_does_not_change_version():
    s.ingest(calibration_event("dev-old", 1, capture_start=time.time() - 3600, capture_end=time.time() - 3600))
    with SessionLocal() as db:
        assert db.get(Tray, "tray-a").calibration_version == "sim-v1"
        assert db.scalar(select(Audit).where(Audit.action == "DEVICE_CALIBRATION_IGNORED"))

def test_calibration_report_required_exactly_for_calibration_events():
    with pytest.raises(ValidationError):
        calibration_event("x", 1, calibration=None)
    with pytest.raises(ValidationError):
        calibration_event("x", 1, event_type="WEIGHT")

def test_geometry_and_per_cell_calibration_endpoints():
    with TestClient(app) as c:
        headers = {"Authorization": "Bearer " + c.post("/auth/staff", json={"password": "local-demo-staff"}).json()["token"]}
        cells = [{"name": "A", "x_mm": 20, "y_mm": 20}, {"name": "B", "x_mm": 230, "y_mm": 20}, {"name": "C", "x_mm": 125, "y_mm": 180}]
        assert c.post("/staff/trays/tray-a/geometry", headers=headers, json={"version": "g1", "cells": cells, "reason": "measured supports"}).status_code == 200
        line = [{"name": "A", "x_mm": 0, "y_mm": 0}, {"name": "B", "x_mm": 100, "y_mm": 0}, {"name": "C", "x_mm": 200, "y_mm": 0.5}]
        assert c.post("/staff/trays/tray-a/geometry", headers=headers, json={"version": "g2", "cells": line, "reason": "bad layout"}).status_code == 409
        body = {"known_mass_g": 500, "noise_g": 0.5, "version": "cell-v1", "reason": "three-position calibration",
                "cells": [{"name": "A", "zero_raw": 1, "counts_per_g": 400}, {"name": "B", "zero_raw": 2, "counts_per_g": 401}, {"name": "C", "zero_raw": 3, "counts_per_g": 399}]}
        assert c.post("/staff/trays/tray-a/calibration", headers=headers, json=body).status_code == 200
        body["cells"][0]["counts_per_g"] = 0
        assert c.post("/staff/trays/tray-a/calibration", headers=headers, json={**body, "version": "cell-v2"}).status_code == 409
    with SessionLocal() as db:
        tray = db.get(Tray, "tray-a")
        assert tray.geometry["version"] == "g1" and tray.calibration_version == "cell-v1"
        commands = [o.payload for o in db.scalars(select(Outbox).where(Outbox.topic == "shelfflow/devices/sensor-1/commands"))]
        assert any(p["action"] == "GEOMETRY" and len(p["cells"]) == 3 for p in commands)
        assert any(p["action"] == "CALIBRATE" and p["cells"] and p["cells"][2]["counts_per_g"] == 399 for p in commands)
