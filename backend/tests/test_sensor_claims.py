import pytest
from fastapi import HTTPException
from backend import service as s
from backend.db import SessionLocal
from backend.models import SensorClaim
from backend.tests.test_store import enter,snapshot
from simulator.scenarios import interaction
from sqlalchemy import select

def source_event(quantity=1):
    e=interaction(quantity=quantity,sequence=1);e.event_type='WEIGHT';e.device_id='sensor-1';e.candidates=[];s.ingest(e);return e

def test_sensor_delta_cannot_be_charged_by_two_interaction_groups():
    sid,m,_=enter();source=source_event()
    a=interaction(marker=m,sequence=1,evidence_refs=['SENSOR_EVENT:'+source.event_id]);a.device_id='vision-1';s.ingest(a)
    b=interaction(marker=m,sequence=2,evidence_refs=['SENSOR_EVENT:'+source.event_id]);b.device_id='vision-1';s.ingest(b)
    assert snapshot(sid)['version']==1
    assert 'SENSOR_EVIDENCE_ALREADY_CONSUMED' in snapshot(sid)['pending'][0]['reasons']
    with pytest.raises(HTTPException):s.review(snapshot(sid)['pending'][0]['id'],'CONFIRM',sid,1,'Repeated source cannot be allocated again','staff')

def test_one_delta_supports_one_joint_same_tray_group():
    sid1,m1,_=enter(command='a');sid2,m2,_=enter(command='b');source=source_event(quantity=2)
    e=interaction(marker=m1,sequence=1,evidence_refs=['SENSOR_EVENT:'+source.event_id]);e.device_id='vision-1';e.candidates.append(interaction(marker=m2).candidates[0]);e.weight=source.weight;s.ingest(e)
    assert snapshot(sid1)['total_minor']==snapshot(sid2)['total_minor']==6500
    with SessionLocal() as db:assert len(list(db.scalars(select(SensorClaim))))==1
