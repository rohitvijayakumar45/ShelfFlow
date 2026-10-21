import hashlib
import secrets
import time
import threading
import os
from contextlib import contextmanager
from sqlalchemy import select, text, func
from fastapi import HTTPException
from backend.db import SessionLocal
from backend.models import *
from backend.config import MODE
from backend.fusion import evaluate, evaluate_units, unit_tolerance
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

def correct(hypothesis_id, session_id, product_id, quantity_delta, inventory_delta, reason, key, actor, unit_ids=None):
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
        if tray.unit_tracking:
            correct_units(db,tray,p,session_id,quantity_delta,inventory_delta,unit_ids or [])
        elif p.id!=tray.product_id:
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
    if not tray.unit_tracking and p.id != tray.product_id:
        fail("Tray SKU mismatch")
    claim_sources(db,h.event_id,h.tray_id)
    q = proposal["quantity"]
    delta = q if proposal["action"] == "PICKUP" else -q
    item = db.scalar(select(CartItem).where(CartItem.session_id == s.id, CartItem.product_id == p.id))
    if delta < 0 and (not item or item.quantity < q or (not tray.unit_tracking and item.tray_id != tray.id)):
        fail("Return has no matching same-tray cart quantity")
    if tray.unit_tracking:
        move_units(db, tray, s, p, proposal, reviewed)
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
        # HEALTH is published live (best effort) while durable events drain from the
        # device flash queue later, so HEALTH must not advance the ordering watermark.
        ordered = event.event_type != "HEALTH"
        late = ordered and device.boot_id == event.boot_id and event.sequence_number <= device.max_sequence
        stale = time.time() - event.capture_end > 5 or event.capture_end > time.time() + 1
        if device.boot_id != event.boot_id:
            # Old boot replay is accepted durably but never automatically charged.
            old_boot = db.scalar(select(SensorEvent).where(SensorEvent.device_id == device.id, SensorEvent.boot_id == event.boot_id, SensorEvent.id != event.event_id))
            # A stale flash backlog must not replace the live boot announced by
            # HEALTH. Otherwise current calibration/weight reports become late
            # forever when old boot events drain after a fresh heartbeat.
            late = bool(old_boot) or stale
            if not ordered and not stale:
                device.boot_id = event.boot_id
                prior=db.scalar(select(func.max(SensorEvent.sequence_number)).where(
                    SensorEvent.device_id==device.id,SensorEvent.boot_id==event.boot_id,
                    SensorEvent.payload['event_type'].as_string()!='HEALTH'))
                device.max_sequence=prior if prior is not None else -1
                late=False
            if not late:
                if ordered:device.boot_id, device.max_sequence = event.boot_id, -1
        if ordered and not late:
            device.max_sequence = max(device.max_sequence, event.sequence_number)
        device.last_seen, device.health = time.time(), event.sensor_health.model_dump()
        if event.event_type == "BARRIER":
            for ref in event.evidence_refs:
                if ref.startswith("BARRIER:"):
                    checkpoint=db.get(FlushBarrier,ref[8:])
                    if checkpoint and checkpoint.device_id==event.device_id and checkpoint.requested <= event.capture_end and not late and not stale:
                        checkpoint.acknowledged_event=event.event_id
        if event.event_type == "CALIBRATION":
            tray = get(db, Tray, event.tray_id)
            if late or stale:
                audit(db, event.device_id, "DEVICE_CALIBRATION_IGNORED", {"event_id": event.event_id, "reason": "late or stale", "version": event.calibration_version})
            else:
                # Physical staff action at the shelf: from now on only events on this version are accepted.
                previous, tray.calibration_version = tray.calibration_version, event.calibration_version
                db.add(Calibration(tray_id=tray.id, metadata_={"source": "DEVICE", "device_id": event.device_id, "event_id": event.event_id,
                                                               "version": event.calibration_version, "previous": previous, **event.calibration.model_dump()}))
                audit(db, event.device_id, "DEVICE_CALIBRATION", {"tray_id": tray.id, "version": event.calibration_version, "previous": previous})
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
            if tray.unit_tracking:
                on_tray, in_cart = unit_pools(db, tray.id)
                elsewhere = {}
                for other in db.scalars(select(Tray).where(Tray.unit_tracking == True, Tray.id != tray.id)):
                    for sid, units in unit_pools(db, other.id)[1].items():
                        elsewhere.setdefault(sid, []).extend(units)
                veto, proposals, scores = evaluate_units(event, on_tray, in_cart, bindings, MODE == "simulation", elsewhere)
            else:
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
                    pid = proposal.get("product_id") if tray.unit_tracking else product.id
                    item = db.scalar(select(CartItem).where(CartItem.session_id == sid, CartItem.product_id == pid))
                    if not item or item.quantity < -delta or (not tray.unit_tracking and item.tray_id != tray.id):
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

def review(hid, decision, session_id, quantity, reason, actor, unit_ids=None):
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
            if unit_ids is not None:
                # Staff name the physical units (e.g. choosing between weight alternatives).
                products = {get(db, TrayUnit, i).product_id for i in unit_ids}
                if len(products) != 1:
                    fail("Confirm one product per decision; split mixed units into separate corrections")
                proposal.update(unit_ids=list(unit_ids), product_id=products.pop(), quantity=len(unit_ids))
            apply_proposal(db, h, proposal, reviewed=True)
        else:
            h.status = "DISMISSED"
        db.add(ReviewDecision(hypothesis_id=h.id, actor=actor, decision={"decision": decision, "session_id": session_id, "quantity": quantity, "reason": reason, "unit_ids": unit_ids}))
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


# ---------------------------------------------------------------------------
# Unit-tracked (mixed-product) trays: staff loading, unit pools and unit moves.
# A unit's weight always comes from a real, settled, healthy tray WEIGHT event
# recorded during an open loading session. That event is claimed, so it can
# never also be counted as a customer pickup or return.
# ---------------------------------------------------------------------------

def unit_dict(u):
    return {"id": u.id, "product_id": u.product_id, "weight_g": u.weight_g, "noise_g": u.noise_g, "spot": u.spot, "x_mm": u.x_mm, "y_mm": u.y_mm}

def unit_pools(db, tray_id):
    units = list(db.scalars(select(TrayUnit).where(TrayUnit.tray_id == tray_id, TrayUnit.status.in_(["ON_TRAY", "IN_CART"])).order_by(TrayUnit.created)))
    on_tray = [unit_dict(u) for u in units if u.status == "ON_TRAY"]
    in_cart = {}
    for u in units:
        if u.status == "IN_CART":
            in_cart.setdefault(u.session_id, []).append(unit_dict(u))
    return on_tray, in_cart

def sync_unit_stock(db, tray):
    tray.stock = len(list(db.scalars(select(TrayUnit.id).where(TrayUnit.tray_id == tray.id, TrayUnit.status == "ON_TRAY"))))

def move_units(db, tray, session, product, proposal, reviewed=False):
    ids = list(proposal.get("unit_ids") or [])
    if len(ids) != proposal["quantity"] or len(set(ids)) != len(ids):
        fail("Unit-tracked tray requires the exact physical units for this quantity")
    for unit_id in ids:
        u = get(db, TrayUnit, unit_id)
        if u.product_id != product.id:
            fail("Unit does not belong to this product")
        if u.tray_id != tray.id:
            # Only a staff-confirmed return may move a unit to another tray (customer put it back elsewhere).
            if not (reviewed and proposal["action"] == "RETURN" and u.status == "IN_CART"):
                fail("Unit does not belong to this tray")
            audit(db, "staff", "UNIT_RELOCATED", {"unit_id": u.id, "from_tray": u.tray_id, "to_tray": tray.id})
            u.tray_id = tray.id
        if proposal["action"] == "PICKUP":
            if u.status != "ON_TRAY":
                fail("Unit is not on the tray")
            u.status, u.session_id = "IN_CART", session.id
        else:
            if u.status != "IN_CART" or u.session_id != session.id:
                fail("Returned unit is not in this cart")
            # The camera spot is unknown until re-observed; a tripod tray tells where the weight landed.
            where = proposal.get("return_position_mm")
            u.status, u.session_id, u.spot = "ON_TRAY", None, None
            u.x_mm, u.y_mm = (where[0], where[1]) if where else (None, None)
        u.updated = time.time()

# (quantity sign, inventory sign) -> (allowed current statuses, new status)
_CORRECTION_MOVES = {
    (1, -1): (("ON_TRAY",), "IN_CART"),     # missed pickup: charge it, unit left the tray
    (1, 0): (("REMOVED",), "IN_CART"),      # unit already off the tray is charged
    (-1, 1): (("IN_CART",), "ON_TRAY"),     # refund, unit is physically back
    (-1, 0): (("IN_CART",), "REMOVED"),     # refund, unit not back on the tray
    (0, -1): (("ON_TRAY",), "REMOVED"),
    (0, 1): (("REMOVED",), "ON_TRAY"),
}

def correct_units(db, tray, product, session_id, quantity_delta, inventory_delta, unit_ids):
    sign = lambda v: (v > 0) - (v < 0)
    if not quantity_delta and not inventory_delta:
        return
    if (quantity_delta and inventory_delta and abs(quantity_delta) != abs(inventory_delta)) or sign(quantity_delta) == sign(inventory_delta):
        fail("Unit-tracked correction needs matching cart and inventory magnitudes")
    count = max(abs(quantity_delta), abs(inventory_delta))
    if len(unit_ids) != count or len(set(unit_ids)) != count:
        fail("Unit-tracked correction must name each physical unit")
    allowed, new = _CORRECTION_MOVES[(sign(quantity_delta), sign(inventory_delta))]
    for unit_id in unit_ids:
        u = get(db, TrayUnit, unit_id)
        if u.tray_id != tray.id or u.product_id != product.id or u.status not in allowed:
            fail("Unit state does not allow this correction")
        if u.status == "IN_CART" and u.session_id != session_id:
            fail("Unit belongs to another cart")
        u.status, u.session_id, u.updated = new, (session_id if new == "IN_CART" else None), time.time()
        if new == "ON_TRAY":
            u.spot = u.x_mm = u.y_mm = None  # put back by staff: place unknown until re-observed

def set_unit_tracking(tray_id, enabled, reason, actor):
    with transaction() as db:
        tray = get(db, Tray, tray_id)
        active = db.scalar(select(TrayUnit).where(TrayUnit.tray_id == tray.id, TrayUnit.status.in_(["ON_TRAY", "IN_CART"])))
        if tray.stock or active or db.scalar(select(TrayLoad).where(TrayLoad.tray_id == tray.id, TrayLoad.status == "OPEN")):
            fail("Empty and reconcile the tray before changing its tracking mode")
        tray.unit_tracking = enabled
        audit(db, actor, "TRAY_UNIT_TRACKING", {"tray_id": tray.id, "enabled": enabled, "reason": reason})
        return {"tray_id": tray.id, "unit_tracking": enabled}

def open_load(tray_id, reason, actor):
    with transaction() as db:
        tray = get(db, Tray, tray_id)
        if not tray.unit_tracking:
            fail("Enable unit tracking on this tray first")
        if db.scalar(select(TrayLoad).where(TrayLoad.tray_id == tray.id, TrayLoad.status == "OPEN")):
            fail("A loading session is already open for this tray")
        load = TrayLoad(tray_id=tray.id, actor=actor, reason=reason)
        db.add(load)
        db.flush()
        audit(db, actor, "TRAY_LOAD_OPEN", {"load_id": load.id, "tray_id": tray.id, "reason": reason})
        return {"load_id": load.id, "tray_id": tray.id, "started": load.started}

def add_unit(load_id, product_id, weight_event_id, spot, crop_refs, actor):
    with transaction() as db:
        load = get(db, TrayLoad, load_id)
        if load.status != "OPEN":
            fail("Loading session is closed")
        tray, product = get(db, Tray, load.tray_id), get(db, Product, product_id)
        event = get(db, SensorEvent, weight_event_id)
        e = event.payload
        w = e.get("weight") or {}
        health = e.get("sensor_health") or {}
        if e["event_type"] != "WEIGHT" or e["tray_id"] != tray.id:
            fail("Unit weight must come from this tray's WEIGHT event")
        if not w.get("settled") or not health.get("ok", False) or health.get("flags") or health.get("queue_overflow"):
            fail("Weight event is unsettled or the sensor is unhealthy")
        if e["calibration_version"] != tray.calibration_version or tray.calibration_version == "UNSET":
            fail("Weight event calibration does not match the tray's current calibration")
        if e["capture_end"] < load.started:
            fail("Weight event predates this loading session")
        delta = w["post_g"] - w["pre_g"]
        noise = float(w.get("noise_g") or 0)
        if delta <= 3 * noise + 1.0:
            fail("Weight increase is not distinguishable from noise; place one item and wait until stable")
        if db.get(SensorClaim, event.id) or db.scalar(select(TrayUnit).where(TrayUnit.source_event_id == event.id)):
            fail("Weight event already used")
        if spot is not None and db.scalar(select(TrayUnit).where(TrayUnit.tray_id == tray.id, TrayUnit.status == "ON_TRAY", TrayUnit.spot == spot)):
            fail("Another unit already occupies that spot")
        where = w.get("position_mm") or {}
        unit = TrayUnit(tray_id=tray.id, product_id=product.id, load_id=load.id, source_event_id=event.id,
                        weight_g=delta, noise_g=noise, spot=spot, crop_refs=list(crop_refs),
                        x_mm=where.get("x_mm"), y_mm=where.get("y_mm"),
                        # Before/after stills let vision cut this unit out as a reference crop (no manual photos).
                        reference_status="PENDING" if len(crop_refs) >= 2 else "NO_STILLS")
        db.add(unit)
        db.add(SensorClaim(source_event_id=event.id, interaction_event_id=event.id))
        db.flush()
        # Warn when a different product on this tray could be confused with this one by weight alone.
        warnings = []
        for other in db.scalars(select(TrayUnit).where(TrayUnit.tray_id == tray.id, TrayUnit.status == "ON_TRAY", TrayUnit.product_id != product.id)):
            if abs(other.weight_g - delta) <= 2 * unit_tolerance([unit_dict(other), unit_dict(unit)], noise):
                warnings.append({"code": "WEIGHT_TOO_CLOSE", "product_id": other.product_id, "unit_id": other.id,
                                 "difference_g": round(abs(other.weight_g - delta), 2)})
        # Worst case: the camera also confuses the two products (reported by vision's look-alike check).
        status = (get(db, Shelf, tray.shelf_id).camera_config or {}).get("gallery_status") or {}
        lookalikes = {frozenset((p["a"], p["b"])) for p in status.get("pairs", [])}
        for w_ in warnings:
            if frozenset((w_["product_id"], product.id)) in lookalikes:
                w_["code"] = "LOOKALIKE_AND_WEIGHT_TOO_CLOSE"
                w_["advice"] = "Camera and scale may both confuse these products: put them on different trays."
        # The product's measured distribution is rebuilt from every individually weighed unit.
        from statistics import mean, pstdev
        samples = [u.weight_g for u in db.scalars(select(TrayUnit).where(TrayUnit.product_id == product.id))]
        product.unit_g = mean(samples)
        product.tolerance_g = max(3 * pstdev(samples), 1.0) if len(samples) > 1 else max(3 * noise, 1.0)
        product.weight_source = "MEASURED"
        product.weight_distribution = {"source": "TRAY_UNITS", "samples_g": samples, "count": len(samples), "mean_g": product.unit_g,
                                       "std_g": pstdev(samples), "min_g": min(samples), "max_g": max(samples)}
        sync_unit_stock(db, tray)
        db.add(InventoryMovement(tray_id=tray.id, delta=1, reason="LOAD", reference=unit.id))
        audit(db, actor, "TRAY_UNIT_LOADED", {"unit_id": unit.id, "load_id": load.id, "tray_id": tray.id, "product_id": product.id,
                                              "weight_g": delta, "noise_g": noise, "spot": spot, "source_event_id": event.id, "warnings": warnings})
        return {"unit_id": unit.id, "weight_g": round(delta, 2), "noise_g": noise, "warnings": warnings,
                "position_mm": [unit.x_mm, unit.y_mm] if unit.x_mm is not None else None}

def close_load(load_id, actor):
    with transaction() as db:
        load = get(db, TrayLoad, load_id)
        if load.status != "OPEN":
            fail("Loading session already closed")
        load.status, load.ended = "CLOSED", time.time()
        units = list(db.scalars(select(TrayUnit).where(TrayUnit.load_id == load.id)))
        audit(db, actor, "TRAY_LOAD_CLOSE", {"load_id": load.id, "units": [u.id for u in units]})
        return {"load_id": load.id, "units": len(units)}

def remove_unit(unit_id, reason, actor):
    with transaction() as db:
        u = get(db, TrayUnit, unit_id)
        if u.status != "ON_TRAY":
            fail("Only units on the tray can be removed by staff; carts change through review or correction")
        u.status, u.updated = "REMOVED", time.time()
        tray = get(db, Tray, u.tray_id)
        sync_unit_stock(db, tray)
        db.add(InventoryMovement(tray_id=tray.id, delta=-1, reason="UNIT_REMOVED", reference=f"remove:{u.id}"))
        audit(db, actor, "TRAY_UNIT_REMOVED", {"unit_id": u.id, "tray_id": tray.id, "reason": reason})
        return {"unit_id": u.id, "status": u.status}
