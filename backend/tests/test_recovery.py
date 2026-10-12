import time
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend.main import app
from backend import service as s
from backend.models import *
from backend.db import SessionLocal
from backend.jobs import maintenance
from simulator.scenarios import interaction
from backend.tests.test_store import enter,snapshot

def test_expired_qr_and_invalidated_old_token():
    sid=s.new_customer('A');old=s.entry_qr(sid)['token'];new=s.entry_qr(sid)['token']
    with pytest.raises(HTTPException):s.dispense(old,'old','staff')
    with s.transaction() as db:
        for token in db.scalars(select(EntryToken)):token.expires=time.time()-1
    with pytest.raises(HTTPException):s.dispense(new,'new','staff')

def test_lost_gate_ack_timeout_and_manual_recovery():
    sid=s.new_customer('A');token=s.entry_qr(sid)['token'];gate=s.dispense(token,'cmd','staff')
    with s.transaction() as db:db.get(GateTransaction,'cmd').created-=30
    maintenance()
    with SessionLocal() as db:assert db.get(Bracelet,gate['bracelet_id']).state=='FAULTED'
    s.manual_assign(sid,'B01','04AABB01',True,'Inspected physically and verified released bracelet','staff')
    assert snapshot(sid)['status']=='ACTIVE'
    with pytest.raises(HTTPException):s.manual_assign(sid,'B02','04AABB02',True,'Second bracelet forbidden','staff')

def test_clean_return_binding_release():
    sid,marker,_=enter();s.ingest(interaction(marker=marker));s.checkout(sid,'pay','SUCCESS',False,'','customer')
    with TestClient(app) as c:
        token=c.post('/auth/staff',json={'password':'local-demo-staff'}).json()['token'];h={'Authorization':'Bearer '+token}
        assert c.post('/staff/bracelets/B01/return',headers=h,json={'cleaned':False,'reason':'Physical return verified'}).status_code==200
        with SessionLocal() as db:
            b=db.get(Bracelet,'B01');assert b.session_id is None and b.state=='RETURNED' and not b.clean
        c.post('/staff/bracelets/B01/return',headers=h,json={'cleaned':True,'reason':'Cleaning verified'})
        with SessionLocal() as db:assert db.get(Bracelet,'B01').state=='AVAILABLE'

def test_pending_unknown_owner_prevents_bracelet_reuse():
    sid,m,_=enter();s.checkout(sid,'p','SUCCESS',False,'','customer')
    s.ingest(interaction(marker_ids=[],sequence=1))
    with TestClient(app) as c:
        token=c.post('/auth/staff',json={'password':'local-demo-staff'}).json()['token']
        r=c.post('/staff/bracelets/B01/return',headers={'Authorization':'Bearer '+token},json={'cleaned':True,'reason':'Returned'})
        assert r.status_code==409

def test_review_and_late_correction_preserve_receipt():
    sid,m,_=enter();s.ingest(interaction(marker=m,hand_product=False,sequence=1))
    pending=snapshot(sid)['pending'][0]['id'];s.review(pending,'CONFIRM',sid,1,'Frame chain reviewed','staff')
    assert snapshot(sid)['total_minor']==6500
    s.checkout(sid,'pay','SUCCESS',False,'','customer')
    original=snapshot(sid)['receipt']
    s.ingest(interaction(marker=m,sequence=2))
    h=snapshot(sid)['pending'][0]['id']
    s.correct(h,sid,'oat',1,-1,'Late pickup established from evidence','c1','staff')
    assert snapshot(sid)['total_minor']==13000
    assert snapshot(sid)['receipt']==original
    assert snapshot(sid)['corrections'][0]['amount_minor']==6500
    assert s.correct(h,sid,'oat',1,-1,'Duplicate','c1','staff')['duplicate']

def test_dispute_correction_reverses_charge_without_fake_restock():
    sid,m,_=enter();s.ingest(interaction(marker=m))
    hid=snapshot(sid)['confirmed_events'][0]['id']
    with s.transaction() as db:db.get(Hypothesis,hid).status='DISPUTED'
    with SessionLocal() as db:stock=db.get(Tray,'tray-a').stock
    s.correct(hid,sid,'oat',-1,0,'Ownership dispute upheld; stock still absent','undo','staff')
    assert snapshot(sid)['total_minor']==0
    with SessionLocal() as db:assert db.get(Tray,'tray-a').stock==stock

def test_restock_never_removes_owned_purchase_and_is_idempotent():
    sid,m,_=enter();s.ingest(interaction(marker=m))
    with TestClient(app) as c:
        token=c.post('/auth/staff',json={'password':'local-demo-staff'}).json()['token'];headers={'Authorization':'Bearer '+token}
        for _ in range(2):assert c.post('/staff/trays/tray-a/restock',headers=headers,json={'delta':2,'reason':'Physical staff restock','idempotency_key':'restock-key'}).status_code==200
    assert snapshot(sid)['total_minor']==6500
    with SessionLocal() as db:assert db.get(Tray,'tray-a').stock==11

@pytest.mark.parametrize('change,reason',[({'calibration_version':'wrong'},'CALIBRATION_MISMATCH'),({'weight':None},'UNSETTLED_WEIGHT'),({'clock_synced':False},'CLOCK_UNCERTAINTY')])
def test_calibration_and_health_guards(change,reason):
    sid,m,_=enter();s.ingest(interaction(marker=m,**change));assert reason in snapshot(sid)['pending'][0]['reasons']

def test_uncertain_quantity_and_queue_loss():
    sid,m,_=enter();e=interaction(marker=m);e.weight.noise_g=300;e.sensor_health.queue_overflow=1;s.ingest(e)
    assert {'QUANTITY_NOT_UNIQUE','SENSOR_HEALTH'}<=set(snapshot(sid)['pending'][0]['reasons'])

def test_two_simultaneous_updates_same_owner_are_atomic():
    from concurrent.futures import ThreadPoolExecutor
    sid,m,_=enter();a=interaction(marker=m);b=interaction(marker=m,tray='tray-b',sequence=1);b.device_id='vision-1'
    with ThreadPoolExecutor(2) as pool:list(pool.map(s.ingest,[a,b]))
    assert snapshot(sid)['version']==2 and snapshot(sid)['total_minor']==16000

def test_retention_does_not_delete_audit(tmp_path,monkeypatch):
    sid,m,_=enter();e=interaction(marker=m);e.evidence_refs=['frame.jpg'];s.ingest(e);(tmp_path/'frame.jpg').write_bytes(b'x')
    monkeypatch.setenv('EVIDENCE_ROOT',str(tmp_path))
    with s.transaction() as db:db.get(Evidence,e.event_id).expires=time.time()-1
    maintenance();assert not (tmp_path/'frame.jpg').exists()
    with SessionLocal() as db:
        assert db.get(Evidence,e.event_id).references==['DELETED_BY_RETENTION']
        assert list(db.scalars(select(Audit)))

def test_device_scope_auth_and_schema_validation():
    with TestClient(app) as c:
        e=interaction()
        assert c.post('/devices/simulator/events',json=e.model_dump(),headers={'X-Device-Key':'wrong'}).status_code==401
        assert c.post('/devices/vision-1/events',json=e.model_dump(),headers={'X-Device-Key':'local-demo-device'}).status_code==403
        body=e.model_dump();body['guessed_charge']=True
        assert c.post('/devices/simulator/events',json=body,headers={'X-Device-Key':'local-demo-device'}).status_code==422

def test_flush_barrier_scope_and_durable_ack():
    sid,m,_=enter()
    with s.transaction() as db:
        b=FlushBarrier(session_id=sid,device_id='sensor-1',requested=time.time()-1);db.add(b);db.flush();id_=b.id
    e=interaction(marker=m);e.event_type='BARRIER';e.evidence_refs=['BARRIER:'+id_];e.device_id='vision-1';s.ingest(e)
    with SessionLocal() as db:assert db.get(FlushBarrier,id_).acknowledged_event is None
    e2=interaction(marker=m);e2.event_type='BARRIER';e2.evidence_refs=['BARRIER:'+id_];e2.device_id='sensor-1';s.ingest(e2)
    with SessionLocal() as db:assert db.get(FlushBarrier,id_).acknowledged_event==e2.event_id

def test_hardware_mode_mock_never_charges_and_requires_assisted_exit(monkeypatch):
    sid,m,_=enter();monkeypatch.setattr(s,'MODE','hardware');s.ingest(interaction(marker=m))
    assert snapshot(sid)['items']==[] and 'PHYSICAL_OWNERSHIP_NOT_VALIDATED' in snapshot(sid)['pending'][0]['reasons']
    h=snapshot(sid)['pending'][0]['id'];s.review(h,'DISMISS',None,1,'Inspected unsupported fixture','staff')
    with pytest.raises(HTTPException):s.checkout(sid,'p','SUCCESS',False,'','customer')
    assert s.checkout(sid,'assist','SUCCESS',True,'Offline devices inspected and reconciled','staff')['state']=='SUCCESS'

def test_measured_catalogue_requires_sample_distribution():
    with TestClient(app) as c:
        token=c.post('/auth/staff',json={'password':'local-demo-staff'}).json()['token'];headers={'Authorization':'Bearer '+token}
        body={'name':'Measured oat milk','price_minor':6500,'unit_g':1030,'tolerance_g':3,'measured':True}
        assert c.post('/staff/products/oat',headers=headers,json=body).status_code==409
        body['sample_weights_g']=[1029,1030,1031]
        assert c.post('/staff/products/oat',headers=headers,json=body).status_code==200
        with SessionLocal() as db:
            p=db.get(Product,'oat');assert p.weight_distribution['count']==3 and p.unit_g==1030 and p.weight_source=='MEASURED'

def test_delayed_evidence_uses_previous_binding_after_bracelet_reuse():
    sid,m,_=enter();late=interaction(marker=m,sequence=1)
    s.checkout(sid,'pay','SUCCESS',False,'','customer')
    with TestClient(app) as c:
        token=c.post('/auth/staff',json={'password':'local-demo-staff'}).json()['token']
        assert c.post('/staff/bracelets/B01/return',headers={'Authorization':'Bearer '+token},json={'cleaned':True,'reason':'Returned and cleaned'}).status_code==200
    sid2,m2,_=enter(command='new')
    assert m2==m
    s.ingest(late)
    assert snapshot(sid2)['items']==[] and snapshot(sid2)['pending']==[]
    assert len(snapshot(sid)['pending'])==1
    with SessionLocal() as db:
        assert len(list(db.scalars(select(BraceletBinding))))==2

def test_cancel_uncertain_gate_requires_recovered_matching_uid():
    sid=s.new_customer('A');token=s.entry_qr(sid)['token'];s.dispense(token,'jam','staff');s.gate_ack('jam','04AABB01',False,False,'JAM','gate')
    with TestClient(app) as c:
        token=c.post('/auth/staff',json={'password':'local-demo-staff'}).json()['token'];headers={'Authorization':'Bearer '+token}
        body={'actual_uid':'04AABB01','physically_recovered':False,'cleaned':True,'reason':'Jam cleared and bracelet recovered'}
        assert c.post('/staff/gates/jam/cancel',headers=headers,json=body).status_code==409
        body['physically_recovered']=True
        assert c.post('/staff/gates/jam/cancel',headers=headers,json=body).status_code==200
    with SessionLocal() as db:
        assert db.get(Bracelet,'B01').session_id is None and db.get(ShoppingSession,sid).status=='CANCELLED'

def test_best_effort_health_does_not_make_queued_barrier_late():
    # Firmware publishes HEALTH live but sends BARRIER from its flash queue afterwards.
    sid,m,_=enter()
    with s.transaction() as db:
        b=FlushBarrier(session_id=sid,device_id='sensor-1',requested=time.time()-1);db.add(b);db.flush();id_=b.id
    barrier=interaction(marker=m,sequence=5);barrier.event_type='BARRIER';barrier.evidence_refs=['BARRIER:'+id_];barrier.device_id='sensor-1';barrier.candidates=[]
    health=interaction(marker=m,sequence=6);health.event_type='HEALTH';health.device_id='sensor-1';health.candidates=[]
    s.ingest(health);s.ingest(barrier)
    with SessionLocal() as db:assert db.get(FlushBarrier,id_).acknowledged_event==barrier.event_id

def test_durable_event_ordering_still_rejects_older_barrier():
    sid,m,_=enter()
    with s.transaction() as db:
        b=FlushBarrier(session_id=sid,device_id='sensor-1',requested=time.time()-1);db.add(b);db.flush();id_=b.id
    weight=interaction(marker=m,sequence=6);weight.event_type='WEIGHT';weight.device_id='sensor-1';weight.candidates=[]
    barrier=interaction(marker=m,sequence=5);barrier.event_type='BARRIER';barrier.evidence_refs=['BARRIER:'+id_];barrier.device_id='sensor-1';barrier.candidates=[]
    s.ingest(weight);s.ingest(barrier)
    with SessionLocal() as db:assert db.get(FlushBarrier,id_).acknowledged_event is None
