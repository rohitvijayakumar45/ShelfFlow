from fastapi.testclient import TestClient
from backend.main import app
from backend.db import SessionLocal
from backend.models import Account,Audit
from sqlalchemy import select

def staff_headers(c):return {'Authorization':'Bearer '+c.post('/auth/staff',json={'password':'local-demo-staff'}).json()['token']}

def test_account_provision_password_hash_and_session_resume():
    with TestClient(app) as c:
        h=staff_headers(c);password='A-local-passphrase-1'
        assert c.post('/staff/customer-accounts',headers=h,json={'username':'volunteer-a','password':password,'alias':'Volunteer A','reason':'Consented hardware trial'}).status_code==200
        assert c.post('/auth/customer',json={'alias':'volunteer-a','password':'wrong'}).status_code==401
        a=c.post('/auth/customer',json={'alias':'volunteer-a','password':password}).json()
        b=c.post('/auth/customer',json={'alias':'volunteer-a','password':password}).json()
        assert a['session_id']==b['session_id']
        with SessionLocal() as db:
            account=db.scalar(select(Account));assert password not in account.password_hash
            assert all(password not in str(a.detail) for a in db.scalars(select(Audit)))

def test_bracelet_uid_marker_conflicts_and_bound_relabel_rejected():
    with TestClient(app) as c:
        h=staff_headers(c)
        body={'rfid':'AABBCCDD','markers':[3],'cleaned':True,'reason':'Physical bracelet verified'}
        assert c.post('/staff/bracelets/B05/setup',headers=h,json=body).status_code==409
        body['markers']=[20,21]
        assert c.post('/staff/bracelets/B05/setup',headers=h,json=body).status_code==200
        assert c.post('/staff/bracelets/B06/setup',headers=h,json=body).status_code==409
        login=c.post('/auth/customer',json={'alias':'A'}).json();qr=c.post('/customer/entry-token',headers={'Authorization':'Bearer '+login['token']},json={}).json()
        gate=c.post('/gate/dispense',headers=h,json={'token':qr['token'],'command_id':'c'}).json()
        body['rfid']='11223344';body['markers']=[22]
        assert c.post('/staff/bracelets/'+gate['bracelet_id']+'/setup',headers=h,json=body).status_code==409
