import time

from sqlalchemy import select, func
from backend import service
from backend.db import SessionLocal
from backend.models import Tray, SensorEvent, Calibration, Hypothesis, CartEvent
from backend.schemas import DeviceEvent


def test_tare_audit_is_committed_without_calibration_or_cart_changes():
    now = time.time()
    event = DeviceEvent(
        event_id='sensor-1-tare-test-1', event_type='HEALTH', device_id='sensor-1',
        boot_id='tare-test', sequence_number=1, tray_id='tray-a',
        capture_start=now, capture_end=now, monotonic_ms=1000,
        clock_synced=False, clock_uncertainty_ms=1000,
        calibration_version='sim-v1',
        evidence_refs=['MANUAL_TARE:APPLY', 'TARE_OFFSET_G:59.7', 'TARE_REF:tare-test:1'])
    response = service.ingest(event)
    assert response['durable'] and response['ack'] == event.event_id
    with SessionLocal() as db:
        stored = db.get(SensorEvent, event.event_id)
        assert stored.payload['evidence_refs'] == event.evidence_refs
        assert db.get(Tray, 'tray-a').calibration_version == 'sim-v1'
        for model in (Calibration, Hypothesis, CartEvent):
            assert db.scalar(select(func.count()).select_from(model)) == 0
