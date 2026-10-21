import time

from fastapi.testclient import TestClient

from backend.main import app
from backend import service
from backend.db import SessionLocal
from backend.models import Device,Tray
from backend.schemas import DeviceEvent,CalibrationReport,CellCalibration
from backend.auth import issue


def event(eid,kind,boot,seq,stamp,calibration=None):
    return DeviceEvent(event_id=eid,event_type=kind,device_id='sensor-1',boot_id=boot,
                       sequence_number=seq,capture_start=stamp,capture_end=stamp,monotonic_ms=1000,
                       clock_synced=False,clock_uncertainty_ms=1000,calibration_version='physical-v1',calibration=calibration)


def test_stale_backlog_cannot_replace_current_heartbeat_boot():
    now=time.time()
    service.ingest(event('live-health','HEALTH','live',20,now))
    service.ingest(event('old-rfid','RFID','old',2,0))
    calibration=event('live-cal','CALIBRATION','live',21,now,
        CalibrationReport(method='COMMAND',known_mass_g=273,cells=[CellCalibration(name='A',zero_raw=100,counts_per_g=10)]))
    service.ingest(calibration)
    with SessionLocal() as db:
        assert db.get(Device,'sensor-1').boot_id=='live'
        assert db.get(Tray,'tray-a').calibration_version=='physical-v1'


def test_staff_can_register_rfid_only_tag_without_invented_markers():
    with TestClient(app) as client:
        response=client.post('/staff/bracelets/P1/setup',headers={'Authorization':'Bearer '+issue('staff','staff')},
                             json={'rfid':'43D51829','markers':[],'cleaned':False,'reason':'Physical RFID-only tag checked'})
        assert response.status_code==200
        state=client.get('/staff/state',headers={'Authorization':'Bearer '+issue('staff','staff')}).json()
        tag=next(b for b in state['bracelets'] if b['id']=='P1')
        assert tag['markers']==[] and tag['session_id'] is None and not tag['clean']
