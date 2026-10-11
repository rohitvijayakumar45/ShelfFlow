import hashlib
import secrets
import time
import threading
import os
from contextlib import contextmanager
from sqlalchemy import select, text
from fastapi import HTTPException
from backend.db import SessionLocal
from backend.models import *
from backend.config import MODE
from backend.fusion import evaluate
from backend.schemas import DeviceEvent

_local_lock = threading.RLock()

@contextmanager
def transaction():
    # One lock boundary for this small prototype. PostgreSQL lock also covers workers.
    with _local_lock, SessionLocal() as db:
        if db.bind.dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        else:
            db.begin()
            db.execute(text("SELECT pg_advisory_xact_lock(783201)"))
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

def fail(detail, status=409):
    raise HTTPException(status, detail)

def get(db, model, id_):
    obj = db.get(model, id_)
    if not obj:
        fail(f"{model.__name__} not found", 404)
    return obj

def audit(db, actor, action, detail):
    db.add(Audit(actor=actor, action=action, detail=detail))

def emit(db, topic, payload):
    db.add(Outbox(topic=topic, payload=payload))

def new_customer(alias):
    with transaction() as db:
        customer = Customer(alias=alias)
        db.add(customer)
        db.flush()
        session = ShoppingSession(customer_id=customer.id)
        db.add(session)
        db.flush()
        return session.id

def entry_qr(session_id):
    with transaction() as db:
        session = get(db, ShoppingSession, session_id)
        if session.status != "ENTRY":
            fail("Session already entered")
        for old in db.scalars(select(EntryToken).where(EntryToken.session_id == session_id)):
            old.used = True
        token = secrets.token_urlsafe(32)
        db.add(EntryToken(digest=hashlib.sha256(token.encode()).hexdigest(), session_id=session_id, expires=time.time() + 120))
        return {"token": token, "expires_in": 120}

def dispense(token, command_id, actor):
    digest = hashlib.sha256(token.encode()).hexdigest()
    with transaction() as db:
        old = db.get(GateTransaction, command_id)
        if old:
            qr = db.get(EntryToken, digest)
            if not qr or qr.session_id != old.session_id:
                fail("Command belongs to another token")
            return gate_view(old)
        qr = get(db, EntryToken, digest)
        session = get(db, ShoppingSession, qr.session_id)
        if qr.used or qr.expires < time.time() or session.status != "ENTRY":
            fail("Token consumed or expired")
        b = db.scalar(select(Bracelet).where(Bracelet.state == "AVAILABLE", Bracelet.clean == True, Bracelet.session_id == None).order_by(Bracelet.id))
        if not b:
            fail("No clean bracelet available")
        qr.used = True
        b.state = "DISPENSING"
        b.session_id = session.id  # Reservation prevents competing assignments before release.
        gate = GateTransaction(id=command_id, session_id=session.id, bracelet_id=b.id)
        db.add(gate)
        db.flush()
        emit(db, "shelfflow/devices/gate-1/commands", {"command_id": command_id, "expected_uid": b.rfid, "action": "DISPENSE"})
        audit(db, actor, "DISPENSE_RESERVED", {"command_id": command_id, "bracelet_id": b.id})
        return gate_view(gate)

def gate_view(g):
    return {"command_id": g.id, "session_id": g.session_id, "bracelet_id": g.bracelet_id, "state": g.state, "detail": g.detail}

def gate_ack(command_id, actual_uid, released, position_ok, fault, actor):
    with transaction() as db:
        gate = get(db, GateTransaction, command_id)
        if gate.state == "ASSIGNED":
            return gate_view(gate)
        b = get(db, Bracelet, gate.bracelet_id)
        session = get(db, ShoppingSession, gate.session_id)
        if gate.state != "DISPENSING":
            fail("Uncertain release requires audited manual recovery")
        if time.time() - gate.created > 20 or fault or actual_uid != b.rfid or not released or not position_ok:
            gate.state = "FAULTED"
            gate.detail = fault or ("WRONG_UID" if actual_uid != b.rfid else "UNCERTAIN_RELEASE")
            b.state = "FAULTED"
        else:
            gate.state = b.state = "ASSIGNED"
            session.status = "ACTIVE"
            db.add(BraceletBinding(bracelet_id=b.id,session_id=session.id,rfid=b.rfid,markers=list(b.markers)))
        audit(db, actor, "GATE_ACK", {"command_id": command_id, "state": gate.state, "actual_uid": actual_uid, "released": released})
        emit(db, f"cart/{session.id}", {"version": session.cart_version, "status": session.status})
        return gate_view(gate)

def manual_assign(session_id, bracelet_id, actual_uid, physical_confirmed, reason, actor):
    if not physical_confirmed or not reason.strip():
        fail("Actual UID, physical release confirmation and audit reason required")
    with transaction() as db:
        s = get(db, ShoppingSession, session_id)
        b = get(db, Bracelet, bracelet_id)
        if s.status != "ENTRY" or actual_uid != b.rfid or not b.clean or b.session_id not in (None, session_id):
            fail("Unsafe assignment")
        if db.scalar(select(Bracelet).where(Bracelet.session_id == session_id, Bracelet.id != bracelet_id)):
            fail("Session already reserves a different bracelet")
        b.state, b.session_id, s.status = "ASSIGNED", session_id, "ACTIVE"
        db.add(BraceletBinding(bracelet_id=b.id,session_id=s.id,rfid=b.rfid,markers=list(b.markers)))
        for gate in db.scalars(select(GateTransaction).where(GateTransaction.session_id == session_id)):
            gate.state, gate.detail = "ASSIGNED", "MANUAL_RECOVERY"
        audit(db, actor, "MANUAL_ASSIGN", {"session_id": session_id, "bracelet_id": b.id, "reason": reason})

def cart(db, session_id):
    s = get(db, ShoppingSession, session_id)
    items = []
    for i in db.scalars(select(CartItem).where(CartItem.session_id == s.id, CartItem.quantity > 0).order_by(CartItem.product_id)):
        p = get(db, Product, i.product_id)
        items.append({"product_id": p.id, "name": p.name, "quantity": i.quantity, "price_minor": i.price_minor})
    pending = list(db.scalars(select(Hypothesis).where(Hypothesis.status.in_(["PENDING_REVIEW", "DISPUTED"]))))
    # Unknown ownership is relevant to every active session until staff reconciles it.
    pending = [h for h in pending if h.session_id in (None, s.id)]
    bracelet = db.scalar(select(Bracelet).where(Bracelet.session_id == s.id))
    order = db.scalar(select(Order).where(Order.session_id == s.id))
    receipt = db.scalar(select(Receipt).where(Receipt.order_id == order.id)) if order else None
    confirmed = list(db.scalars(select(Hypothesis).where(Hypothesis.session_id == s.id, Hypothesis.status == "CONFIRMED")))
    corrections = list(db.scalars(select(Correction).where(Correction.session_id == s.id)))
    return {"session_id": s.id, "status": s.status, "version": s.cart_version, "items": items,
            "total_minor": sum(i["quantity"] * i["price_minor"] for i in items), "currency": "INR",
            "pending": [{"id": h.id, "tray_id": h.tray_id, "reasons": h.reason, "status": h.status} for h in pending],
            "bracelet": {"id": bracelet.id, "state": bracelet.state} if bracelet else None,
            "receipt": receipt.content if receipt else None, "mode": MODE,
            "confirmed_events": [{"id":h.id,"action":h.proposal.get("action"),"product_id":h.proposal.get("product_id"),"quantity":h.proposal.get("quantity")} for h in confirmed],
            "corrections": [{"id":c.id,**c.detail} for c in corrections]}

def correct(hypothesis_id, session_id, product_id, quantity_delta, inventory_delta, reason, key, actor):
    with transaction() as db:
        old = db.get(Correction,key)
        if old:
            if old.hypothesis_id != hypothesis_id or old.session_id != session_id:
                fail("Correction key belongs to another event")
            return {"id":old.id,"duplicate":True}
        h=get(db,Hypothesis,hypothesis_id)
        if h.status not in ("PENDING_REVIEW","DISPUTED"):
            fail("Correction requires an unresolved event or dispute")
        session=get(db,ShoppingSession,session_id)
        if h.session_id not in (None,session_id):
            fail("Correction owner mismatch")
        if quantity_delta>0:
            claim_sources(db,h.event_id,h.tray_id)
        p=get(db,Product,product_id);tray=get(db,Tray,h.tray_id)
        if p.id!=tray.product_id:
            fail("Correction SKU mismatch")
        item=db.scalar(select(CartItem).where(CartItem.session_id==session_id,CartItem.product_id==p.id))
        if quantity_delta<0 and (not item or item.quantity < -quantity_delta):
            fail("Correction exceeds matching quantity")
        if tray.stock+inventory_delta<0:
            fail("Correction would make inventory negative")
        if not item:
            item=CartItem(session_id=session_id,product_id=p.id,tray_id=tray.id,quantity=0,price_minor=p.price_minor)
            db.add(item)
        item.quantity+=quantity_delta;tray.stock+=inventory_delta;session.cart_version+=1
        detail={"product_id":p.id,"quantity_delta":quantity_delta,"inventory_delta":inventory_delta,"amount_minor":quantity_delta*item.price_minor,"currency":"INR","reason":reason,"payment":"SIMULATED_ADJUSTMENT","original_receipt_unchanged":session.status=="COMPLETE"}
        h.status="CORRECTED";h.session_id=session_id
        db.add(Correction(id=key,session_id=session_id,hypothesis_id=h.id,detail=detail))
        db.add(InventoryMovement(tray_id=tray.id,delta=inventory_delta,reason="CORRECTION",reference=key))
        db.add(ReviewDecision(hypothesis_id=h.id,actor=actor,decision=detail))
        audit(db,actor,"CORRECTION",{"hypothesis_id":h.id,"session_id":session_id,**detail})
        emit(db,f"cart/{session_id}",{"version":session.cart_version})
        return {"id":key,"duplicate":False}

def source_vetoes(db,event_id,tray_id):
    event=get(db,SensorEvent,event_id)
    refs=[ref[13:] for ref in event.payload['evidence_refs'] if ref.startswith('SENSOR_EVENT:')]
    sources=refs or [event_id]
    reasons=[]
    for source_id in sources:
        source=db.get(SensorEvent,source_id)
        if not source or (refs and (source.payload['event_type']!='WEIGHT' or source.payload['tray_id']!=tray_id or source.payload.get('weight')!=event.payload.get('weight'))):
            reasons.append('SENSOR_SOURCE_UNAVAILABLE_OR_MISMATCH')
        claim=db.get(SensorClaim,source_id)
        if claim and claim.interaction_event_id!=event_id:
            reasons.append('SENSOR_EVIDENCE_ALREADY_CONSUMED')
    return reasons,sources

def claim_sources(db,event_id,tray_id):
    reasons,sources=source_vetoes(db,event_id,tray_id)
    if reasons:fail('; '.join(reasons))
    for source_id in sources:
        if not db.get(SensorClaim,source_id):db.add(SensorClaim(source_event_id=source_id,interaction_event_id=event_id))
    db.flush()

def apply_proposal(db, h, proposal, reviewed=False):
    s = get(db, ShoppingSession, proposal["session_id"])
    if s.status != "ACTIVE":
        fail("Closed/non-active session requires audited correction")
    tray = get(db, Tray, h.tray_id)
    p = get(db, Product, proposal["product_id"])
    if p.id != tray.product_id:
        fail("Tray SKU mismatch")
    claim_sources(db,h.event_id,h.tray_id)
    q = proposal["quantity"]
    delta = q if proposal["action"] == "PICKUP" else -q
    item = db.scalar(select(CartItem).where(CartItem.session_id == s.id, CartItem.product_id == p.id))
    if delta < 0 and (not item or item.quantity < q or item.tray_id != tray.id):
        fail("Return has no matching same-tray cart quantity")
    if delta > 0 and tray.stock < q:
        fail("Insufficient tray stock")
    if not item:
        item = CartItem(session_id=s.id, product_id=p.id, tray_id=tray.id, quantity=0, price_minor=p.price_minor)
        db.add(item)
    item.quantity += delta
    tray.stock -= delta
    s.cart_version += 1
    h.status = "CONFIRMED"
    h.session_id = s.id
    db.add(CartEvent(hypothesis_id=h.id, session_id=s.id, version=s.cart_version, delta=delta, product_id=p.id))
    db.add(InventoryMovement(tray_id=tray.id, delta=-delta, reason="REVIEW" if reviewed else "FUSION", reference=h.id))
    emit(db, f"cart/{s.id}", {"version": s.cart_version})

def ingest(event: DeviceEvent):
    with transaction() as db:
        old = db.get(SensorEvent, event.event_id)
        if old:
            if old.payload != event.model_dump():
                fail("Event ID replay with different payload")
            return {"ack": event.event_id, "durable": True, "duplicate": True}
        duplicate_sequence = db.scalar(select(SensorEvent).where(SensorEvent.device_id == event.device_id, SensorEvent.boot_id == event.boot_id, SensorEvent.sequence_number == event.sequence_number))
        if duplicate_sequence:
            fail("Sequence reused with different event ID")
        device = get(db, Device, event.device_id)
        row = SensorEvent(id=event.event_id, device_id=event.device_id, boot_id=event.boot_id, sequence_number=event.sequence_number, payload=event.model_dump())
        db.add(row)
        db.flush()
        late = device.boot_id == event.boot_id and event.sequence_number <= device.max_sequence
        stale = time.time() - event.capture_end > 5 or event.capture_end > time.time() + 1
        if device.boot_id != event.boot_id:
            # Old boot replay is accepted durably but never automatically charged.
            old_boot = db.scalar(select(SensorEvent).where(SensorEvent.device_id == device.id, SensorEvent.boot_id == event.boot_id, SensorEvent.id != event.event_id))
            late = bool(old_boot)
            if not late:
                device.boot_id, device.max_sequence = event.boot_id, -1
        if not late:
            device.max_sequence = max(device.max_sequence, event.sequence_number)
        device.last_seen, device.health = time.time(), event.sensor_health.model_dump()
        if event.event_type == "BARRIER":
            for ref in event.evidence_refs:
                if ref.startswith("BARRIER:"):
                    checkpoint=db.get(FlushBarrier,ref[8:])
                    if checkpoint and checkpoint.device_id==event.device_id and checkpoint.requested <= event.capture_end and not late and not stale:
                        checkpoint.acknowledged_event=event.event_id
        if event.event_type == "INTERACTION":
            tray = get(db, Tray, event.tray_id)
            product = get(db, Product, tray.product_id)
            bindings = {}
            histories=db.scalars(select(BraceletBinding).where(BraceletBinding.started<=event.capture_end,(BraceletBinding.ended==None)|(BraceletBinding.ended>=event.capture_start)))
            for b in histories:
                for m in b.markers:
                    if m in bindings:
                        bindings[m]=(None,'CONFLICTING_BINDING_INTERVAL')
                    else:
                        bindings[m] = (b.session_id, b.rfid)
            veto, proposals, scores = evaluate(event, product, bindings, MODE == "simulation")
            source_reasons,_=source_vetoes(db,row.id,tray.id)
            veto.extend(source_reasons)
            if late or stale:
                veto.append("LATE_OR_OUT_OF_ORDER")
            if tray.calibration_version != event.calibration_version:
                veto.append("CALIBRATION_MISMATCH")
            if MODE != "simulation":
                # Real chain extraction is intentionally review-first until physical validation.
                veto.append("PHYSICAL_OWNERSHIP_NOT_VALIDATED")
            # Validate the whole group before applying any member, including same-owner net returns.
            net_by_session = {}
            total_delta = 0
            for proposal in proposals:
                sid = proposal["session_id"]
                s = db.get(ShoppingSession, sid) if sid else None
                if not s or s.status != "ACTIVE":
                    veto.append("SESSION_NOT_ACTIVE")
                delta = proposal["quantity"] * (1 if proposal["action"] == "PICKUP" else -1)
                net_by_session[sid] = net_by_session.get(sid, 0) + delta
                total_delta += delta
                if delta < 0:
                    item = db.scalar(select(CartItem).where(CartItem.session_id == sid, CartItem.product_id == product.id))
                    if not item or item.quantity < -delta or item.tray_id != tray.id:
                        veto.append("RETURN_WITHOUT_MATCHING_CART")
            if total_delta > tray.stock:
                veto.append("INSUFFICIENT_STOCK")
            # Tracker IDs must be scoped to clip; a claimed trajectory cannot charge twice.
            tracks = {p["product_track"] for p in proposals}
            recent = db.scalars(select(Hypothesis).where(Hypothesis.status == "CONFIRMED"))
            if any(h.proposal.get("product_track") in tracks for h in recent):
                veto.append("ALREADY_CONSUMED_PRODUCT_TRAJECTORY")
            if not proposals:
                proposals = [{"session_id": None}]
            for proposal in proposals:
                h = Hypothesis(event_id=row.id, session_id=proposal.get("session_id"), tray_id=tray.id, reason=sorted(set(veto)), proposal={**proposal, "scores": scores})
                db.add(h)
                db.flush()
                if not veto:
                    apply_proposal(db, h, proposal)
            db.add(Evidence(id=row.id, event_id=row.id, references=event.evidence_refs, expires=time.time() + 86400 * int(os.getenv("EVIDENCE_RETENTION_DAYS","7"))))
        row.processed = True
        emit(db, f"shelfflow/devices/{device.id}/acks", {"event_id": event.event_id, "boot_id": event.boot_id, "sequence_number": event.sequence_number, "durable": True})
        return {"ack": event.event_id, "durable": True, "duplicate": False}

def review(hid, decision, session_id, quantity, reason, actor):
    if not reason.strip():
        fail("Review reason required")
    with transaction() as db:
        h = get(db, Hypothesis, hid)
        if h.status not in ("PENDING_REVIEW", "DISPUTED"):
            fail("Already resolved")
        if h.status == "DISPUTED" and decision == "CONFIRM":
            fail("Confirmed dispute requires separate audited correction; dismiss to uphold charge")
        if decision == "CONFIRM":
            proposal = {**h.proposal, "session_id": session_id, "quantity": quantity}
            if "action" not in proposal:
                fail("No observed action to confirm; use reconciliation correction")
            apply_proposal(db, h, proposal, reviewed=True)
        else:
            h.status = "DISMISSED"
        db.add(ReviewDecision(hypothesis_id=h.id, actor=actor, decision={"decision": decision, "session_id": session_id, "quantity": quantity, "reason": reason}))
        audit(db, actor, "REVIEW", {"hypothesis_id": h.id, "decision": decision, "reason": reason})

def checkout(session_id, key, outcome, assisted, reason, actor):
    with transaction() as db:
        s = get(db, ShoppingSession, session_id)
        old = db.get(Payment, key)
        if old:
            order = get(db, Order, old.order_id)
            if order.session_id != session_id:
                fail("Payment key belongs to another session")
            return {"payment_id": old.id, "state": old.state, "order_id": old.order_id}
        if s.status != "ACTIVE":
            fail("Session not active")
        if db.scalar(select(Hypothesis).where(Hypothesis.status.in_(["PENDING_REVIEW", "DISPUTED"]), (Hypothesis.session_id == session_id) | (Hypothesis.session_id == None))):
            fail("Resolve pending events before payment; egress always remains open")
        if db.scalar(select(SensorEvent).where(SensorEvent.processed == False)):
            fail("Durably ingested events still processing")
        if MODE != "simulation":
            # Require audited reconciliation even with recent barriers; offline unseen events cannot be inferred absent.
            if not assisted or not reason.strip() or actor != "staff":
                fail("Hardware mode requires staff reconciliation and device flush-barrier inspection")
            checkpoints=list(db.scalars(select(FlushBarrier).where(FlushBarrier.session_id==session_id)))
            audit(db,actor,"EXIT_RECONCILIATION",{"session_id":session_id,"barriers":[{"device":b.device_id,"event":b.acknowledged_event,"requested":b.requested} for b in checkpoints],"offline_override_reason":reason})
        snapshot = cart(db, session_id)
        order = db.scalar(select(Order).where(Order.session_id == session_id))
        if order and order.snapshot != snapshot["items"]:
            fail("Cart changed since payment attempt; staff must reconcile order")
        if not order:
            order = Order(session_id=session_id, total_minor=snapshot["total_minor"], snapshot=snapshot["items"])
            db.add(order)
            db.flush()
        db.add(Payment(id=key, order_id=order.id, state=outcome))
        if outcome == "SUCCESS":
            order.status, s.status = "PAID", "COMPLETE"
            db.add(Receipt(order_id=order.id, content={"order_id": order.id, "session_id": session_id, "items": order.snapshot, "total_minor": order.total_minor, "currency": "INR", "payment": "SIMULATED", "created": time.time()}))
        audit(db, actor, "SIMULATED_PAYMENT", {"session_id": session_id, "outcome": outcome, "assisted": assisted, "reason": reason})
        emit(db, f"cart/{s.id}", {"version": s.cart_version, "status": s.status})
        return {"payment_id": key, "state": outcome, "order_id": order.id}
