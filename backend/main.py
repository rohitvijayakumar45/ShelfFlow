import hashlib
import hmac
import os
import time
import asyncio
from collections import defaultdict, deque
from pathlib import Path
from fastapi import FastAPI, Depends, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import select
from backend import service as svc
from backend.models import *
from backend.schemas import StrictModel, DeviceEvent, Weight, CellCalibration
from backend.auth import issue, decode, staff, customer,hash_password,verify_password
from backend.config import MODE, STAFF_PASSWORD
from backend.db import SessionLocal
from pydantic import Field
from typing import Literal

app = FastAPI(title="ShelfFlow", version="0.1.0", description="Continuous single-camera smart-store prototype. Simulation is explicitly labelled; real ownership is review-first.")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:5174").split(","), allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type", "X-Device-Key"])
limits = defaultdict(deque)

@app.middleware("http")
async def request_limits(request, call_next):
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    q = limits[key]
    while q and q[0] < now - 60:
        q.popleft()
    if len(q) >= 600:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
    q.append(now)
    if int(request.headers.get("content-length", "0")) > 2_000_000:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Payload too large"}, status_code=413)
    return await call_next(request)

class Login(StrictModel):
    alias: str = Field(default="Shopper", min_length=1, max_length=60)
    password: str = Field(default="",max_length=256)

@app.get("/health")
def health():
    return {"ok": True, "mode": MODE, "physical_validation": False, "ownership": "REVIEW_FIRST" if MODE != "simulation" else "MOCK"}

@app.post("/auth/customer")
def login_customer(body: Login):
    if body.password or MODE != "simulation":
        with svc.transaction() as db:
            account=db.scalar(select(Account).where(Account.username==body.alias))
            if not account or not verify_password(body.password,account.password_hash):
                raise HTTPException(401,"Invalid customer credentials")
            session=db.scalar(select(ShoppingSession).where(ShoppingSession.customer_id==account.customer_id,ShoppingSession.status.in_(['ENTRY','ACTIVE'])))
            if not session:
                session=ShoppingSession(customer_id=account.customer_id);db.add(session);db.flush()
            return {"token":issue('customer',session.id),"session_id":session.id}
    sid = svc.new_customer(body.alias)
    return {"token": issue("customer", sid), "session_id": sid}

@app.post("/auth/staff")
def login_staff(body: Login):
    if not hmac.compare_digest(body.password, STAFF_PASSWORD):
        raise HTTPException(401, "Incorrect password")
    return {"token": issue("staff", "staff")}

@app.get("/customer/cart")
def customer_cart(user=Depends(customer)):
    with SessionLocal() as db:
        return svc.cart(db, user["sub"])

@app.post("/customer/entry-token")
def qr(user=Depends(customer)):
    return svc.entry_qr(user["sub"])

class Dispense(StrictModel):
    token: str
    command_id: str = Field(min_length=1, max_length=100)

@app.post("/gate/dispense")
def dispense(body: Dispense, user=Depends(staff)):
    return svc.dispense(body.token, body.command_id, user["sub"])

class GateAck(StrictModel):
    command_id: str
    actual_uid: str
    released: bool
    position_ok: bool
    fault: str = ""

def device_auth(device_id, key):
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        if not device or not hmac.compare_digest(device.secret_digest, hashlib.sha256(key.encode()).hexdigest()):
            raise HTTPException(401, "Device authentication failed")

@app.post("/devices/gate-1/ack")
def acknowledge(body: GateAck, x_device_key: str = Header()):
    device_auth("gate-1", x_device_key)
    return svc.gate_ack(**body.model_dump(), actor="gate-1")

@app.post("/devices/{device_id}/events")
def event(device_id: str, body: DeviceEvent, x_device_key: str = Header()):
    device_auth(device_id, x_device_key)
    if body.device_id != device_id:
        raise HTTPException(403, "Device scope mismatch")
    return svc.ingest(body)

@app.get("/devices/vision-1/bindings")
def vision_bindings(x_device_key: str = Header()):
    device_auth("vision-1",x_device_key)
    with SessionLocal() as db:
        return [{"bracelet_id":b.id,"rfid_uid":b.rfid,"marker_ids":b.markers,"session_id":b.session_id} for b in db.scalars(select(Bracelet).where(Bracelet.state=="ASSIGNED"))]

@app.get("/devices/vision-1/config")
def vision_config(x_device_key: str = Header()):
    device_auth("vision-1",x_device_key)
    with SessionLocal() as db:
        trays={}
        for t in db.scalars(select(Tray)):
            on_tray=[] if not t.unit_tracking else svc.unit_pools(db,t.id)[0]
            trays[t.id]={"roi":t.roi,"product_id":None if t.unit_tracking else t.product_id,"calibration_version":t.calibration_version,
                         "load_open":bool(db.scalar(select(TrayLoad).where(TrayLoad.tray_id==t.id,TrayLoad.status=='OPEN'))),
                         "unit_tracking":t.unit_tracking,"units_on_tray":[{"id":u["id"],"product_id":u["product_id"],"spot":u["spot"]} for u in on_tray]}
            now=time.time()
            teach=db.scalar(select(TeachSession).where(TeachSession.tray_id==t.id,TeachSession.status=='ACTIVE',TeachSession.until>now,TeachSession.accepted<TeachSession.max_refs))
            trays[t.id]["teach"]={"teach_id":teach.id,"product_id":teach.product_id,"until":teach.until,"remaining":teach.max_refs-teach.accepted} if teach else None
        return {"trays":trays,"recognition":recognition_settings(db)}

class Checkout(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=100)
    outcome: Literal["SUCCESS", "FAILURE", "TIMEOUT"] = "SUCCESS"

@app.post("/customer/checkout")
def pay(body: Checkout, user=Depends(customer)):
    return svc.checkout(user["sub"], body.idempotency_key, body.outcome, False, "", "customer")

class Reason(StrictModel):
    reason: str = Field(min_length=3, max_length=500)

class CancelGate(Reason):
    actual_uid: str
    physically_recovered: bool
    cleaned: bool = False

@app.post("/staff/gates/{command_id}/cancel")
def cancel_gate(command_id: str,body: CancelGate,user=Depends(staff)):
    with svc.transaction() as db:
        gate=svc.get(db,GateTransaction,command_id);b=svc.get(db,Bracelet,gate.bracelet_id);s=svc.get(db,ShoppingSession,gate.session_id)
        if s.status!='ENTRY' or gate.state not in ('FAULTED','DISPENSING') or not body.physically_recovered or body.actual_uid!=b.rfid:
            svc.fail('Cancellation requires physically recovered matching UID and unactivated session')
        if db.scalar(select(Hypothesis).where(Hypothesis.status.in_(['PENDING_REVIEW','DISPUTED']),(Hypothesis.session_id==s.id)|(Hypothesis.session_id==None))):
            svc.fail('Unresolved evidence prevents bracelet reuse')
        gate.state,s.status='CANCELLED','CANCELLED';b.session_id=None;b.clean=body.cleaned;b.state='AVAILABLE' if body.cleaned else 'RETURNED'
        svc.emit(db,'shelfflow/devices/gate-1/commands',{'action':'MANUAL_RESET','physical_inspected':True})
        svc.emit(db,'shelfflow/devices/gate-1/acks',{'command_id':command_id,'state':'CANCELLED','durable':True})
        svc.audit(db,'staff','GATE_CANCEL',{'command_id':command_id,**body.model_dump()})
    return {'ok':True}

class ProvisionAccount(Reason):
    username: str = Field(min_length=3,max_length=60,pattern=r'^[A-Za-z0-9_.-]+$')
    password: str = Field(min_length=12,max_length=256)
    alias: str = Field(min_length=1,max_length=60)

@app.post("/staff/customer-accounts")
def provision_account(body: ProvisionAccount,user=Depends(staff)):
    with svc.transaction() as db:
        if db.scalar(select(Account).where(Account.username==body.username)):
            svc.fail("Account already exists")
        c=Customer(alias=body.alias);db.add(c);db.flush()
        db.add(Account(username=body.username,customer_id=c.id,password_hash=hash_password(body.password)))
        svc.audit(db,'staff','ACCOUNT_PROVISION',{'customer_id':c.id,'username':body.username,'reason':body.reason})
    return {'ok':True}

@app.post("/customer/disputes/{hypothesis_id}")
def dispute(hypothesis_id: str, body: Reason, user=Depends(customer)):
    with svc.transaction() as db:
        h = svc.get(db, Hypothesis, hypothesis_id)
        if h.session_id != user["sub"] or h.status != "CONFIRMED":
            svc.fail("Only own confirmed events can be disputed")
        h.status = "DISPUTED"
        svc.audit(db, user["sub"], "DISPUTE", {"hypothesis_id": h.id, "reason": body.reason})

def serialize(obj):
    return {column.key: getattr(obj, column.key) for column in obj.__mapper__.column_attrs}

@app.get("/staff/state")
def state(user=Depends(staff)):
    with SessionLocal() as db:
        result = {}
        for name, model in [("products", Product), ("trays", Tray), ("bracelets", Bracelet), ("devices", Device), ("sessions", ShoppingSession), ("gates", GateTransaction), ("hypotheses", Hypothesis), ("receipts", Receipt), ("shelves", Shelf), ("barriers", FlushBarrier), ("tray_units", TrayUnit), ("tray_loads", TrayLoad)]:
            result[name] = [serialize(o) for o in db.scalars(select(model))]
        for d in result["devices"]:
            del d["secret_digest"]
        for session in result['sessions']:
            session['customer_alias']=db.get(Customer,session['customer_id']).alias
        for tray in result['trays']:
            latest=db.scalar(select(SensorEvent).where(SensorEvent.payload['tray_id'].as_string()==tray['id'],SensorEvent.payload['weight']['raw'].as_float().is_not(None)).order_by(SensorEvent.received.desc()).limit(1))
            tray['latest_sample']={'weight':latest.payload['weight'],'health':latest.payload['sensor_health'],'capture_end':latest.payload['capture_end'],'calibration_version':latest.payload['calibration_version']} if latest else None
        recent=list(db.scalars(select(SensorEvent).where(SensorEvent.payload['event_type'].as_string()=='RFID').order_by(SensorEvent.received.desc()).limit(20)))
        result['recent_taps']=[{'event_id':e.id,'captured':e.payload['capture_end'],
                               'uid':next((r[5:] for r in e.payload['evidence_refs'] if r.startswith('RFID:')),None),
                               'reader':next((r[12:] for r in e.payload['evidence_refs'] if r.startswith('RFID_READER:')),None)} for e in recent]
        return {**result, "mode": MODE}

class SimInteraction(StrictModel):
    session_id: str
    tray_id: str
    scenario: Literal["PICKUP", "RETURN", "HIDDEN_MARKER", "CROSSED_HANDS", "CAMERA_FAILURE", "WEIGHT_FAULT"]
    unit_id: str | None = None  # mixed trays: which physical unit moves (default: first available)

@app.post("/staff/simulation/interaction")
def simulate(body: SimInteraction, user=Depends(staff)):
    if MODE != "simulation":
        svc.fail("Simulation disabled", 403)
    from simulator.scenarios import interaction
    from backend.schemas import Health
    with SessionLocal() as db:
        b = db.scalar(select(Bracelet).where(Bracelet.session_id == body.session_id, Bracelet.state == "ASSIGNED"))
        if not b:
            svc.fail("No active assigned bracelet")
        tray = svc.get(db, Tray, body.tray_id)
        device = svc.get(db, Device, "simulator")
        seq = max(int(time.time() * 1000), device.max_sequence + 1)
        marker = b.markers[0]
        unit_delta = None
        if tray.unit_tracking:
            # Weight-first demo: the chosen unit's measured weight moves; vision reports UNKNOWN.
            returning = body.scenario == "RETURN"
            q = select(TrayUnit).where(TrayUnit.tray_id == tray.id, TrayUnit.status == ("IN_CART" if returning else "ON_TRAY"))
            if returning:
                q = q.where(TrayUnit.session_id == body.session_id)
            if body.unit_id:
                q = q.where(TrayUnit.id == body.unit_id)
            unit = db.scalar(q.order_by(TrayUnit.created))
            if not unit:
                svc.fail("No matching unit " + ("in this cart from this tray" if returning else "on this tray"))
            unit_delta = unit.weight_g if returning else -unit.weight_g
    changes = {"HIDDEN_MARKER": {"marker_ids": []}, "CROSSED_HANDS": {"crossed": True}, "CAMERA_FAILURE": {"model_status": "MODEL_UNAVAILABLE"}, "WEIGHT_FAULT": {"sensor_health": Health(ok=False, flags=["DISCONNECTED"])}}
    extra = {"product_id": "UNKNOWN", "sku": False} if unit_delta is not None else {}
    event = interaction(marker=marker, tray=body.tray_id, action="RETURN" if body.scenario == "RETURN" else "PICKUP", sequence=seq, **extra, **changes.get(body.scenario, {}))
    if unit_delta is not None:
        event.weight = Weight(pre_g=2000, post_g=2000 + unit_delta, noise_g=0.5, settled=True)
    return svc.ingest(event)

class SimWeight(StrictModel):
    tray_id: str
    delta_g: float = Field(ge=-5000, le=5000)
    noise_g: float = Field(default=0.5, ge=0, le=50)

@app.post("/staff/simulation/weight")
def simulate_weight(body: SimWeight, user=Depends(staff)):
    """Simulation only: a settled tray weight change, as the shelf controller would report one."""
    if MODE != "simulation":
        svc.fail("Simulation disabled", 403)
    import uuid
    with SessionLocal() as db:
        tray = svc.get(db, Tray, body.tray_id)
        device = svc.get(db, Device, "simulator")
        seq = max(int(time.time() * 1000), device.max_sequence + 1)
        calibration = tray.calibration_version
    now = time.time()
    event = DeviceEvent(event_id=str(uuid.uuid4()), event_type="WEIGHT", device_id="simulator", boot_id="sim-boot", sequence_number=seq,
                        tray_id=body.tray_id, capture_start=now, capture_end=now, monotonic_ms=int(time.monotonic() * 1000),
                        clock_synced=True, clock_uncertainty_ms=10, calibration_version=calibration,
                        weight=Weight(pre_g=1000, post_g=1000 + body.delta_g, noise_g=body.noise_g, settled=True),
                        evidence_refs=["SIMULATED:no-physical-sensor"])
    svc.ingest(event)
    return {"event_id": event.event_id, "delta_g": body.delta_g, "simulated": True}

class SimGate(StrictModel):
    command_id: str
    scenario: Literal["SUCCESS", "JAM", "WRONG_UID", "UNCERTAIN_RELEASE"]

@app.post("/staff/simulation/gate")
def simulate_gate(body: SimGate, user=Depends(staff)):
    if MODE != "simulation":
        svc.fail("Simulation disabled", 403)
    with SessionLocal() as db:
        gate = svc.get(db, GateTransaction, body.command_id)
        b = svc.get(db, Bracelet, gate.bracelet_id)
        rfid = b.rfid
    return svc.gate_ack(body.command_id, "WRONG" if body.scenario == "WRONG_UID" else rfid, body.scenario != "UNCERTAIN_RELEASE", True, "JAM" if body.scenario == "JAM" else "", "simulated-gate")

class Review(Reason):
    decision: Literal["CONFIRM", "DISMISS"]
    session_id: str | None = None
    quantity: int = Field(default=1, ge=1, le=10)
    unit_ids: list[str] | None = Field(default=None, max_length=10)

@app.post("/staff/review/{hypothesis_id}")
def review(hypothesis_id: str, body: Review, user=Depends(staff)):
    svc.review(hypothesis_id, **body.model_dump(), actor=user["sub"])
    return {"ok": True}

class CorrectionBody(Reason):
    session_id: str
    product_id: str
    quantity_delta: int = Field(ge=-10, le=10)
    inventory_delta: int = Field(ge=-10, le=10)
    idempotency_key: str
    unit_ids: list[str] | None = Field(default=None, max_length=10)

@app.post("/staff/corrections/{hypothesis_id}")
def correction(hypothesis_id: str, body: CorrectionBody, user=Depends(staff)):
    values=body.model_dump();key=values.pop("idempotency_key")
    return svc.correct(hypothesis_id,**values,key=key,actor=user["sub"])

class AssistedPayment(Checkout, Reason):
    session_id: str

class Reconcile(Reason):
    session_id: str

@app.post("/staff/reconciliation")
def reconciliation(body: Reconcile, user=Depends(staff)):
    with svc.transaction() as db:
        session=svc.get(db,ShoppingSession,body.session_id)
        if session.status != "ACTIVE":
            svc.fail("Only active sessions need exit reconciliation")
        ids=[]
        for device in ("sensor-1","vision-1","gate-1"):
            checkpoint=FlushBarrier(session_id=session.id,device_id=device)
            db.add(checkpoint);db.flush();ids.append(checkpoint.id)
            svc.emit(db,f"shelfflow/devices/{device}/commands",{"action":"FLUSH","barrier_id":checkpoint.id})
        svc.audit(db,"staff","FLUSH_REQUEST",body.model_dump())
    return {"barrier_ids":ids,"require_assistance_if_offline":True}

@app.post("/staff/order-reconcile")
def order_reconcile(body: Reconcile, user=Depends(staff)):
    with svc.transaction() as db:
        order=db.scalar(select(Order).where(Order.session_id==body.session_id))
        if not order or order.status=="PAID":
            svc.fail("Only unpaid attempt snapshots can be reconciled")
        snapshot=svc.cart(db,body.session_id)
        svc.audit(db,"staff","UNPAID_ORDER_RECONCILE",{"old":order.snapshot,"new":snapshot['items'],"reason":body.reason})
        order.snapshot,order.total_minor=snapshot['items'],snapshot['total_minor']
    return {"ok":True}

@app.post("/staff/assisted-checkout")
def assisted_payment(body: AssistedPayment, user=Depends(staff)):
    return svc.checkout(body.session_id,body.idempotency_key,body.outcome,True,body.reason,"staff")

@app.post("/staff/gate-reset")
def gate_reset(body: Reason, user=Depends(staff)):
    with svc.transaction() as db:
        svc.emit(db,"shelfflow/devices/gate-1/commands",{"action":"MANUAL_RESET","physical_inspected":True})
        svc.audit(db,"staff","GATE_MANUAL_RESET",body.model_dump())
    return {"ok":True}

def camera_jpeg():
    import httpx
    url=os.getenv("CAMERA_URL")
    if not url:
        raise HTTPException(503,"Camera URL not configured")
    try:
        r=httpx.get(url.rstrip('/')+'/jpeg',headers={"X-Camera-Key":os.environ["CAMERA_KEY"]},timeout=3)
        r.raise_for_status()
        if not r.headers.get('content-type','').startswith('image/jpeg') or len(r.content)>8_000_000 or not r.content.startswith(b'\xff\xd8'):
            raise ValueError('Invalid JPEG response')
        return r.content
    except Exception:
        raise HTTPException(503,"Camera unavailable; check connection and credentials")

@app.get("/staff/camera/preview")
def camera_preview(user=Depends(staff)):
    from fastapi.responses import Response
    return Response(camera_jpeg(),media_type='image/jpeg',headers={'Cache-Control':'no-store'})

def catalogue_root():
    from backend.references import catalogue_root as root
    return root()

@app.post("/staff/loads/{load_id}/snapshot")
def load_snapshot(load_id: str, user=Depends(staff)):
    """Save one original camera still for the unit being loaded (before or after placement)."""
    import uuid
    with SessionLocal() as db:
        if svc.get(db, TrayLoad, load_id).status != "OPEN":
            svc.fail("Loading session is closed")
    jpeg = camera_jpeg()
    name = f"load-{load_id}-{uuid.uuid4().hex}.jpg"
    (catalogue_root() / name).write_bytes(jpeg)
    return {"ref": name, "bytes": len(jpeg)}

@app.get("/staff/catalogue-file/{file_name}")
def catalogue_file(file_name: str, user=Depends(staff)):
    root = catalogue_root()
    path = (root / file_name).resolve()
    if path.parent != root or path.suffix.lower() != ".jpg" or not path.is_file():
        raise HTTPException(404, "Catalogue image unavailable")
    return FileResponse(path)

class Manual(Reason):
    session_id: str
    bracelet_id: str
    actual_uid: str
    physical_confirmed: bool

@app.post("/staff/manual-assignment")
def manual(body: Manual, user=Depends(staff)):
    svc.manual_assign(**body.model_dump(), actor=user["sub"])
    return {"ok": True}

class BraceletReturn(Reason):
    cleaned: bool = False

class BraceletSetup(Reason):
    rfid: str = Field(pattern=r'^(?:[0-9A-F]{8}|[0-9A-F]{14}|[0-9A-F]{20})$')
    markers: list[int] = Field(default_factory=list,max_length=8)
    cleaned: bool

@app.post("/staff/bracelets/{bracelet_id}/setup")
def bracelet_setup(bracelet_id: str,body: BraceletSetup,user=Depends(staff)):
    if len(set(body.markers))!=len(body.markers) or any(m<0 or m>=250 for m in body.markers):
        svc.fail("Distinct ArUco IDs in DICT_4X4_250 required")
    with svc.transaction() as db:
        b=db.get(Bracelet,bracelet_id)
        if b and (b.session_id or b.state not in ['AVAILABLE','RETURNED','FAULTED']):
            svc.fail("Bound/reserved bracelets cannot be relabelled")
        for other in db.scalars(select(Bracelet).where(Bracelet.id!=bracelet_id)):
            if other.rfid==body.rfid or set(other.markers)&set(body.markers):
                svc.fail("UID or marker IDs already registered")
        if not b:
            b=Bracelet(id=bracelet_id,rfid=body.rfid,markers=body.markers);db.add(b)
        b.rfid,b.markers,b.clean,b.state=body.rfid,body.markers,body.cleaned,'AVAILABLE' if body.cleaned else 'RETURNED'
        svc.audit(db,'staff','BRACELET_SETUP',{'bracelet_id':bracelet_id,**body.model_dump()})
    return {'ok':True}

@app.post("/staff/bracelets/{bracelet_id}/return")
def return_bracelet(bracelet_id: str, body: BraceletReturn, user=Depends(staff)):
    with svc.transaction() as db:
        b = svc.get(db, Bracelet, bracelet_id)
        if b.session_id:
            s = svc.get(db, ShoppingSession, b.session_id)
            if s.status != "COMPLETE" or db.scalar(select(Hypothesis).where(Hypothesis.status.in_(["PENDING_REVIEW", "DISPUTED"]), (Hypothesis.session_id == s.id) | (Hypothesis.session_id == None))):
                svc.fail("Previous session not reconciled")
            for binding in db.scalars(select(BraceletBinding).where(BraceletBinding.bracelet_id==b.id,BraceletBinding.ended==None)):
                binding.ended=time.time()
        b.session_id, b.clean, b.state = None, body.cleaned, "AVAILABLE" if body.cleaned else "RETURNED"
        svc.audit(db, user["sub"], "BRACELET_RETURN", {"bracelet_id": b.id, **body.model_dump()})
    return {"ok": True}

class Restock(Reason):
    delta: int = Field(ge=1, le=1000)
    idempotency_key: str

@app.post("/staff/trays/{tray_id}/restock")
def restock(tray_id: str, body: Restock, user=Depends(staff)):
    with svc.transaction() as db:
        if db.scalar(select(InventoryMovement).where(InventoryMovement.reference == body.idempotency_key)):
            return {"duplicate": True}
        tray = svc.get(db, Tray, tray_id)
        tray.stock += body.delta
        db.add(InventoryMovement(tray_id=tray.id, delta=body.delta, reason="RESTOCK", reference=body.idempotency_key))
        svc.audit(db, "staff", "RESTOCK", {"tray_id": tray.id, **body.model_dump()})
    return {"ok": True}

class ProductSetup(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    price_minor: int = Field(ge=0)
    unit_g: float = Field(gt=0)
    tolerance_g: float = Field(ge=0)
    measured: bool
    sample_weights_g: list[float] = Field(default_factory=list, max_length=1000)

@app.post("/staff/products/{product_id}")
def product_setup(product_id: str, body: ProductSetup, user=Depends(staff)):
    from statistics import mean,pstdev
    if body.measured and (len(body.sample_weights_g)<3 or any(g<=0 for g in body.sample_weights_g)):
        svc.fail("Measured catalogue requires at least three positive actual unit-mass samples")
    with svc.transaction() as db:
        p = svc.get(db, Product, product_id)
        for key in ("name", "price_minor", "unit_g", "tolerance_g"):
            setattr(p, key, getattr(body, key))
        p.weight_source = "MEASURED" if body.measured else "SIMULATED"
        if body.measured:
            p.unit_g=mean(body.sample_weights_g)
            p.weight_distribution={"samples_g":body.sample_weights_g,"count":len(body.sample_weights_g),"mean_g":p.unit_g,"std_g":pstdev(body.sample_weights_g),"min_g":min(body.sample_weights_g),"max_g":max(body.sample_weights_g)}
            if max(abs(g-p.unit_g) for g in body.sample_weights_g)>body.tolerance_g:
                svc.fail("Tolerance is below observed sample deviation")
        svc.audit(db, "staff", "PRODUCT_SETUP", {"product_id": p.id, **body.model_dump()})
    return {"ok": True}

class TraySetup(StrictModel):
    product_id: str
    roi: list[float] = Field(min_length=4, max_length=4)

@app.post("/staff/trays/{tray_id}/setup")
def tray_setup(tray_id: str, body: TraySetup, user=Depends(staff)):
    x1, y1, x2, y2 = body.roi
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        svc.fail("ROI uses normalized full-frame coordinates")
    with svc.transaction() as db:
        tray = svc.get(db, Tray, tray_id)
        svc.get(db, Product, body.product_id)
        if tray.product_id != body.product_id and tray.stock:
            svc.fail("Empty/reconcile tray before changing SKU")
        tray.product_id, tray.roi = body.product_id, body.roi
        db.add(TrayAssignment(tray_id=tray.id,product_id=tray.product_id))
        svc.audit(db, "staff", "TRAY_SETUP", {"tray_id": tray.id, **body.model_dump()})
    return {"ok": True}

class UnitTracking(Reason):
    enabled: bool

@app.post("/staff/trays/{tray_id}/unit-tracking")
def unit_tracking(tray_id: str, body: UnitTracking, user=Depends(staff)):
    return svc.set_unit_tracking(tray_id, body.enabled, body.reason, user["sub"])

@app.post("/staff/trays/{tray_id}/loads")
def open_load(tray_id: str, body: Reason, user=Depends(staff)):
    return svc.open_load(tray_id, body.reason, user["sub"])

class LoadUnit(StrictModel):
    product_id: str
    weight_event_id: str
    spot: int | None = Field(default=None, ge=0, le=31)
    crop_refs: list[str] = Field(default_factory=list, max_length=50)

@app.post("/staff/loads/{load_id}/units")
def load_unit(load_id: str, body: LoadUnit, user=Depends(staff)):
    return svc.add_unit(load_id, body.product_id, body.weight_event_id, body.spot, body.crop_refs, user["sub"])

@app.post("/staff/loads/{load_id}/close")
def close_load(load_id: str, user=Depends(staff)):
    return svc.close_load(load_id, user["sub"])

@app.post("/staff/units/{unit_id}/remove")
def remove_unit(unit_id: str, body: Reason, user=Depends(staff)):
    return svc.remove_unit(unit_id, body.reason, user["sub"])

@app.get("/staff/trays/{tray_id}/units")
def tray_units(tray_id: str, user=Depends(staff)):
    with SessionLocal() as db:
        svc.get(db, Tray, tray_id)
        return [serialize(u) for u in db.scalars(select(TrayUnit).where(TrayUnit.tray_id == tray_id).order_by(TrayUnit.created))]

@app.get("/staff/trays/{tray_id}/weight-events")
def recent_weight_events(tray_id: str, since: float = 0, user=Depends(staff)):
    """Settled weight changes not yet used, newest first: the loading wizard picks the one after each placement."""
    with SessionLocal() as db:
        svc.get(db, Tray, tray_id)
        rows = db.scalars(select(SensorEvent).where(SensorEvent.payload['event_type'].as_string() == 'WEIGHT', SensorEvent.payload['tray_id'].as_string() == tray_id).order_by(SensorEvent.received.desc()).limit(50))
        out = []
        for e in rows:
            w = e.payload.get('weight') or {}
            if e.payload['capture_end'] < since or db.get(SensorClaim, e.id) or not w.get('settled'):
                continue
            out.append({"event_id": e.id, "delta_g": round(w['post_g'] - w['pre_g'], 2), "noise_g": w.get('noise_g'),
                        "capture_end": e.payload['capture_end'], "calibration_version": e.payload['calibration_version'], "sensor_health": e.payload['sensor_health']})
        return out

class Calibrate(Reason):
    zero_raw: float | None = None
    mass_raw: float | None = None
    known_mass_g: float = Field(gt=0)
    noise_g: float = Field(ge=0)
    version: str = Field(min_length=1, max_length=60)
    # Multi-cell (tripod) trays: one entry per load cell; replaces zero_raw/mass_raw.
    cells: list[CellCalibration] | None = Field(default=None, min_length=1, max_length=8)

@app.post("/staff/trays/{tray_id}/calibration")
def calibrate(tray_id: str, body: Calibrate, user=Depends(staff)):
    if body.cells:
        if any(c.counts_per_g == 0 for c in body.cells) or len({c.name for c in body.cells}) != len(body.cells):
            svc.fail("Each cell needs a distinct name and a non-zero counts-per-gram scale")
    elif body.zero_raw is None or body.mass_raw is None or body.zero_raw == body.mass_raw:
        svc.fail("Calibration span is zero")
    with svc.transaction() as db:
        tray = svc.get(db, Tray, tray_id)
        tray.calibration_version = body.version
        db.add(Calibration(tray_id=tray.id, metadata_=body.model_dump()))
        svc.emit(db, "shelfflow/devices/sensor-1/commands", {"action": "CALIBRATE", "tray_id": tray.id, **body.model_dump()})
        svc.audit(db, "staff", "CALIBRATION", {"tray_id": tray.id, **body.model_dump()})
    return {"ok": True}

class GeometryCell(StrictModel):
    name: str = Field(min_length=1, max_length=4)
    x_mm: float = Field(ge=-2000, le=2000)
    y_mm: float = Field(ge=-2000, le=2000)

class Geometry(Reason):
    version: str = Field(min_length=1, max_length=60)
    cells: list[GeometryCell] = Field(min_length=3, max_length=8)

@app.post("/staff/trays/{tray_id}/geometry")
def tray_geometry(tray_id: str, body: Geometry, user=Depends(staff)):
    """Load-cell support positions (mm) used to locate where weight changed on a tripod tray."""
    names = [c.name for c in body.cells]
    if len(set(names)) != len(names):
        svc.fail("Cell names must be distinct")
    (ax, ay), (bx, by), (cx, cy) = [(c.x_mm, c.y_mm) for c in body.cells[:3]]
    if abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) < 100:  # twice the triangle area, mm^2
        svc.fail("Support points are (nearly) in a line; spread them into a triangle")
    with svc.transaction() as db:
        tray = svc.get(db, Tray, tray_id)
        tray.geometry = {"version": body.version, "cells": [c.model_dump() for c in body.cells]}
        svc.emit(db, "shelfflow/devices/sensor-1/commands", {"action": "GEOMETRY", "tray_id": tray.id, "version": body.version, "cells": [c.model_dump() for c in body.cells]})
        svc.audit(db, "staff", "TRAY_GEOMETRY", {"tray_id": tray.id, **body.model_dump()})
    return {"ok": True}

@app.get("/staff/evidence/{event_id}")
def evidence_metadata(event_id: str, user=Depends(staff)):
    with SessionLocal() as db:
        e = svc.get(db, Evidence, event_id)
        event=svc.get(db,SensorEvent,event_id)
        return {**serialize(e),"capture_start":event.payload['capture_start'],"capture_end":event.payload['capture_end'],"weight":event.payload.get('weight'),"clock_uncertainty_ms":event.payload['clock_uncertainty_ms'],"sensor_health":event.payload['sensor_health'],"model_status":event.payload['model_status']}

@app.get("/staff/evidence-file/{file_name}")
def evidence_file(file_name: str, user=Depends(staff)):
    root = Path(os.getenv("EVIDENCE_ROOT", "evidence")).resolve()
    path = (root / file_name).resolve()
    if path.parent != root or path.suffix.lower() not in (".jpg", ".json") or not path.is_file():
        raise HTTPException(404, "Evidence unavailable")
    return FileResponse(path)

@app.websocket("/ws/cart")
async def websocket(ws: WebSocket):
    # First message auth avoids putting secrets in URL/access logs.
    origin = ws.headers.get("origin")
    allowed = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:5174").split(",")
    if origin and origin not in allowed:
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        message = await asyncio.wait_for(ws.receive_json(), timeout=5)
        user = decode(message.get("token", ""))
        if user["role"] != "customer":
            await ws.close(code=1008)
            return
        last = None
        while True:
            if user["exp"] < time.time():
                await ws.close(code=1008)
                return
            with SessionLocal() as db:
                snapshot = svc.cart(db, user["sub"])
            if snapshot != last:
                await ws.send_json({"type": "snapshot", **snapshot})
                last = snapshot
            else:
                await ws.send_json({"type": "heartbeat", "version": snapshot["version"]})
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        return
    except (asyncio.TimeoutError, HTTPException):
        await ws.close(code=1008)


# ---------------- Few-shot product references (restock auto-crops, teach mode) ----------------
from backend import references as refs

class NewProduct(StrictModel):
    product_id: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=1, max_length=100)
    price_minor: int = Field(ge=0)

@app.post("/staff/catalogue")
def create_product(body: NewProduct, user=Depends(staff)):
    return refs.create_product(body.product_id, body.name, body.price_minor, user["sub"])

class Teach(Reason):
    tray_id: str
    seconds: int = Field(default=20, ge=5, le=90)
    max_refs: int = Field(default=20, ge=3, le=40)

@app.post("/staff/products/{product_id}/teach")
def start_teach(product_id: str, body: Teach, user=Depends(staff)):
    return refs.start_teach(product_id, body.tray_id, body.seconds, body.max_refs, body.reason, user["sub"])

@app.post("/staff/teach/{teach_id}/stop")
def stop_teach(teach_id: str, user=Depends(staff)):
    return refs.stop_teach(teach_id, user["sub"])

@app.get("/staff/references")
def staff_references(product_id: str | None = None, user=Depends(staff)):
    with SessionLocal() as db:
        teach=[serialize(t) for t in db.scalars(select(TeachSession).order_by(TeachSession.started.desc()).limit(20))]
        return {"references": refs.reference_rows(db, product_id), "teach_sessions": teach,
                "gallery_status": (db.get(Shelf, "shelf-1").camera_config or {}).get("gallery_status") if db.get(Shelf, "shelf-1") else None}

@app.post("/staff/references/{reference_id}/reject")
def reject_reference(reference_id: str, body: Reason, user=Depends(staff)):
    return refs.set_reference_status(reference_id, "REJECTED", body.reason, user["sub"])

@app.post("/staff/references/{reference_id}/restore")
def restore_reference(reference_id: str, body: Reason, user=Depends(staff)):
    return refs.set_reference_status(reference_id, "ACTIVE", body.reason, user["sub"])

@app.get("/devices/vision-1/enrollment-jobs")
def enrollment_jobs(x_device_key: str = Header()):
    device_auth("vision-1", x_device_key)
    with SessionLocal() as db:
        return refs.enrollment_jobs(db)

class JobFailed(StrictModel):
    reason: str = Field(min_length=2, max_length=200)

@app.post("/devices/vision-1/enrollment-jobs/{unit_id}/failed")
def enrollment_failed(unit_id: str, body: JobFailed, x_device_key: str = Header()):
    device_auth("vision-1", x_device_key)
    return refs.job_failed(unit_id, body.reason)

@app.get("/devices/vision-1/catalogue-file/{file_name}")
def device_catalogue_file(file_name: str, x_device_key: str = Header()):
    device_auth("vision-1", x_device_key)
    root = catalogue_root()
    path = (root / file_name).resolve()
    if path.parent != root or path.suffix.lower() not in (".jpg", ".png") or not path.is_file():
        raise HTTPException(404, "Catalogue image unavailable")
    return FileResponse(path)

class NewReference(StrictModel):
    product_id: str
    source: Literal["RESTOCK_AUTO", "TEACH"]
    image_b64: str = Field(max_length=2_100_000)
    box: list[float] | None = Field(default=None, min_length=4, max_length=4)
    quality: dict = Field(default_factory=dict)
    unit_id: str | None = None
    teach_id: str | None = None

@app.post("/devices/vision-1/references")
def add_reference(body: NewReference, x_device_key: str = Header()):
    device_auth("vision-1", x_device_key)
    return refs.add_reference(body.product_id, body.source, body.image_b64, body.box, body.quality, body.unit_id, body.teach_id)

@app.get("/devices/vision-1/references")
def device_references(x_device_key: str = Header()):
    device_auth("vision-1", x_device_key)
    with SessionLocal() as db:
        return [{"id": r["id"], "product_id": r["product_id"], "file": r["file"], "status": r["status"], "updated": r["updated"]} for r in refs.reference_rows(db)]

class GalleryStatus(StrictModel):
    version: str = Field(max_length=60)
    products: dict[str, int]
    live_references: int = Field(ge=0)
    pairs: list[dict] = Field(default_factory=list, max_length=200)
    groups: list[dict] = Field(default_factory=list, max_length=100)

@app.post("/devices/vision-1/gallery-status")
def gallery_status(body: GalleryStatus, x_device_key: str = Header()):
    device_auth("vision-1", x_device_key)
    return refs.save_gallery_status(body.model_dump())


# ---------------- Learning loop, analytics and review tools ----------------
from backend import analytics

class Uncertain(StrictModel):
    track: str = Field(min_length=1, max_length=100)
    tray_id: str | None = Field(default=None, max_length=40)
    image_b64: str = Field(max_length=2_100_000)
    top_product: str | None = Field(default=None, max_length=64)
    second_product: str | None = Field(default=None, max_length=64)
    similarity: float = Field(ge=-1, le=1)
    margin: float = Field(ge=-2, le=2)

@app.post("/devices/vision-1/uncertain")
def add_uncertain(body: Uncertain, x_device_key: str = Header()):
    device_auth("vision-1", x_device_key)
    return refs.add_uncertain(body.image_b64, body.track, body.tray_id, body.top_product, body.second_product, body.similarity, body.margin)

@app.get("/staff/uncertain")
def staff_uncertain(status: str = "OPEN", user=Depends(staff)):
    with SessionLocal() as db:
        return refs.uncertain_rows(db, status or None)

class UncertainAnswer(StrictModel):
    product_id: str | None = Field(default=None, max_length=40)

@app.post("/staff/uncertain/{crop_id}/label")
def label_uncertain(crop_id: str, body: UncertainAnswer, user=Depends(staff)):
    return refs.label_uncertain(crop_id, body.product_id, user["sub"])

@app.get("/staff/kpis")
def staff_kpis(since: float = 0, user=Depends(staff)):
    with SessionLocal() as db:
        return analytics.kpis(db, since)

def recognition_settings(db):
    shelf = db.get(Shelf, "shelf-1")
    return ((shelf.camera_config or {}) if shelf else {}).get("recognition")

@app.get("/staff/recognition/tuning")
def recognition_tuning(user=Depends(staff)):
    with SessionLocal() as db:
        return analytics.recognition_tuning(db, recognition_settings(db))

class RecognitionSettings(Reason):
    threshold: float = Field(ge=0.2, le=0.99)
    margin: float = Field(ge=0, le=0.3)

@app.post("/staff/recognition/settings")
def set_recognition(body: RecognitionSettings, user=Depends(staff)):
    """Staff-approved gallery thresholds; vision picks them up on its next config poll."""
    with svc.transaction() as db:
        for shelf in db.scalars(select(Shelf)):
            config = dict(shelf.camera_config or {})
            config["recognition"] = {"threshold": body.threshold, "margin": body.margin, "applied_by": user["sub"], "applied_at": time.time(), "reason": body.reason}
            shelf.camera_config = config
        svc.audit(db, user["sub"], "RECOGNITION_SETTINGS", body.model_dump())
    return {"ok": True}

@app.get("/devices/vision-1/recognition")
def device_recognition(x_device_key: str = Header()):
    device_auth("vision-1", x_device_key)
    with SessionLocal() as db:
        return recognition_settings(db) or {}

@app.get("/staff/reports/shrink")
def shrink(since: float = 0, user=Depends(staff)):
    with SessionLocal() as db:
        return analytics.shrink_report(db, since)

@app.get("/staff/reports/drift")
def drift(user=Depends(staff)):
    with SessionLocal() as db:
        return analytics.drift_report(db)

@app.get("/staff/review/{hypothesis_id}/suggestion")
def review_suggestion(hypothesis_id: str, user=Depends(staff)):
    with SessionLocal() as db:
        return analytics.suggestion(db, svc.get(db, Hypothesis, hypothesis_id))

@app.get("/staff/evidence/{event_id}/timeline")
def evidence_timeline(event_id: str, user=Depends(staff)):
    with SessionLocal() as db:
        result = analytics.timeline(db, event_id)
        if result is None:
            raise HTTPException(404, "Event not found")
        return result

@app.get("/customer/product-image/{product_id}")
def product_image(product_id: str, user=Depends(customer)):
    """Thumbnail for cart lines and receipts: the clearest learned reference of the product."""
    with SessionLocal() as db:
        name = refs.best_image(db, product_id)
    if not name:
        raise HTTPException(404, "No product image yet")
    root = catalogue_root()
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():
        raise HTTPException(404, "No product image yet")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=600"})
