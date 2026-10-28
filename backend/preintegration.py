"""Staff-confirmed workflow; no inferred measurements or camera-only charging.

While weight integration is unavailable, the camera and the RFID readers still produce evidence.
`suggestions` turns each pending camera interaction into a pre-filled movement (product from the
gallery, direction from the hand/item motion, customer from the bracelet marker or a nearby RFID
tap). Nothing changes a cart until staff confirm it with `from_evidence`, which records the same
audited MOVE as a manual entry and resolves the camera event in one transaction.
"""
from sqlalchemy import select

from backend import config, service as svc
from backend.models import (AssistedAction, AssistedStock, Audit, Bracelet, BraceletBinding, CartItem,
                            Hypothesis, Product, ReviewDecision, SensorEvent, ShoppingSession, Tray)

RFID_WINDOW_S = 15.0  # a bracelet tap this close to the interaction suggests its owner


def require_assisted():
    if config.WEIGHTS_ENABLED:
        svc.fail('Assisted preintegration requires weight integration to be disabled', 403)


def _record(db, key, kind, payload, actor):
    if not payload.get('physical_confirmed') or not payload.get('reason', '').strip():
        svc.fail('Confirm the physical count or item movement and give a reason')
    previous = db.get(AssistedAction, key)
    if previous:
        if previous.kind != kind or previous.payload != payload:
            svc.fail('Idempotency key reused with different details')
        return {'id': key, 'duplicate': True}
    tray = svc.get(db, Tray, payload['tray_id'])
    product = svc.get(db, Product, payload['product_id'])
    stock = db.get(AssistedStock, (tray.id, product.id))
    session_id = None
    if kind == 'COUNT':
        if not stock:
            stock = AssistedStock(tray_id=tray.id, product_id=product.id)
            db.add(stock)
        stock.quantity = payload['quantity']
    else:
        session_id = payload['session_id']
        session = svc.get(db, ShoppingSession, session_id)
        if session.status != 'ACTIVE' or not db.scalar(select(Bracelet).where(
                Bracelet.session_id == session.id, Bracelet.state == 'ASSIGNED')):
            svc.fail('An active session with a physically assigned bracelet is required')
        # Never silently merge this temporary ledger into an existing weighed cart.
        for item in db.scalars(select(CartItem).where(CartItem.session_id == session.id, CartItem.quantity > 0)):
            signed = sum(a.payload['quantity_delta'] for a in db.scalars(select(AssistedAction).where(
                AssistedAction.session_id == session.id, AssistedAction.kind == 'MOVE'))
                if a.payload['product_id'] == item.product_id)
            if item.quantity != signed:
                svc.fail('Existing sensor-based cart requires reconciliation before assisted use')
        delta = payload['quantity_delta']
        item = db.scalar(select(CartItem).where(CartItem.session_id == session.id, CartItem.product_id == product.id))
        if item and item.tray_id != tray.id:
            svc.fail('Use the original tray for this product')
        if not stock:
            svc.fail('Count this product on the tray before recording movements')
        if delta > 0 and stock.quantity < delta:
            svc.fail('Not enough counted stock')
        if delta < 0 and (not item or item.quantity < -delta):
            svc.fail('Return exceeds this customer cart quantity')
        if not item:
            item = CartItem(session_id=session.id, product_id=product.id, tray_id=tray.id,
                            quantity=0, price_minor=product.price_minor)
            db.add(item)
        item.quantity += delta
        stock.quantity -= delta
        session.cart_version += 1
        svc.emit(db, f'cart/{session.id}', {'version': session.cart_version})
    db.add(AssistedAction(id=key, kind=kind, session_id=session_id, payload=payload))
    svc.audit(db, actor, 'ASSISTED_'+kind, {'id': key, **payload, 'weight_verified': False})
    return {'id': key, 'duplicate': False}


def record(key, kind, payload, actor):
    require_assisted()
    with svc.transaction() as db:
        return _record(db, key, kind, payload, actor)


# ---------------------------------------------------------------- camera + RFID suggestions
def _bindings_at(db, t0, t1):
    """marker id -> session id for bracelets bound during [t0, t1] (ambiguous markers excluded)."""
    owners = {}
    for b in db.scalars(select(BraceletBinding).where(BraceletBinding.started <= t1,
                                                      (BraceletBinding.ended == None) | (BraceletBinding.ended >= t0))):
        for m in b.markers:
            owners[m] = None if m in owners and owners[m] != b.session_id else b.session_id
    return owners


def _rfid_sessions(db, t0, t1):
    """Sessions whose bracelet UID was read by a shelf reader in the window, with the reader names."""
    found = {}
    for e in db.scalars(select(SensorEvent).where(SensorEvent.payload['event_type'].as_string() == 'RFID',
                                                  SensorEvent.payload['capture_end'].as_float() >= t0,
                                                  SensorEvent.payload['capture_end'].as_float() <= t1)):
        refs = e.payload.get('evidence_refs', [])
        uid = next((r[5:] for r in refs if r.startswith('RFID:')), None)
        reader = next((r[12:] for r in refs if r.startswith('RFID_READER:')), '?')
        if reader in svc.registry_readers():
            continue  # entry/exit registry taps say nothing about who was at the shelf
        b = db.scalar(select(Bracelet).where(Bracelet.rfid == uid, Bracelet.state == 'ASSIGNED')) if uid else None
        if b and b.session_id:
            found.setdefault(b.session_id, set()).add(reader)
    return found


def suggestion_for(db, h):
    e = db.get(SensorEvent, h.event_id)
    if not e:
        return None
    p = e.payload
    candidates = p.get('candidates') or []
    c = candidates[0] if candidates else {}
    why = []
    product = c.get('product_id') if c.get('sku') and c.get('product_id') not in (None, 'UNKNOWN') else c.get('top_product')
    if product and not db.get(Product, product):
        product = None
    if c.get('sku') and product:
        why.append(f"Camera recognised the item (similarity {c.get('similarity')}).")
    elif product:
        why.append(f"Camera's best guess, not certain (similarity {c.get('similarity')}): check the video.")
    else:
        why.append('Camera could not identify the item: choose the product from the video.')
    action = c.get('action') or 'PICKUP'
    if c.get('trajectory') and c.get('hand_product'):
        why.append('A hand touched the item and it ' + ('left the tray.' if action == 'PICKUP' else 'arrived on the tray.'))
    t0, t1 = p['capture_start'], p['capture_end']
    session_id, source = None, None
    markers = c.get('marker_ids') or []
    if markers:
        owners = _bindings_at(db, t0, t1)
        sessions = {owners.get(m) for m in markers}
        if len(sessions) == 1 and None not in sessions:
            session_id, source = sessions.pop(), 'bracelet marker at the hand'
    if not session_id:
        taps = _rfid_sessions(db, t0 - RFID_WINDOW_S, t1 + RFID_WINDOW_S)
        if len(taps) == 1:
            session_id, readers = next(iter(taps.items()))
            source = 'bracelet read by ' + '/'.join(sorted(readers))
        elif len(taps) > 1:
            why.append(f'{len(taps)} bracelets were read nearby: choose the customer from the video.')
    if not session_id:
        active = list(db.scalars(select(ShoppingSession).where(ShoppingSession.status == 'ACTIVE')))
        if len(active) == 1:
            session_id, source = active[0].id, 'only active shopper'
    if source:
        why.append(f'Customer from {source}.')
    stock = db.get(AssistedStock, (h.tray_id, product)) if product else None
    if product and not stock:
        why.append('This product has no counted stock on this tray yet: count it first.')
    # strong = camera recognised the product AND a bracelet (marker or RFID) names the customer
    confidence = 'strong' if c.get('sku') and source and source.startswith('bracelet') else ('medium' if product and session_id else 'weak')
    return {'hypothesis_id': h.id, 'event_id': h.event_id, 'tray_id': h.tray_id, 'captured': t1,
            'product_id': product, 'action': action, 'quantity': int(c.get('quantity') or 1),
            'session_id': session_id, 'similarity': c.get('similarity'), 'confidence': confidence, 'why': why,
            'frames': [r for r in p.get('evidence_refs', []) if r.endswith('.jpg')][:80],
            'stock': stock.quantity if stock else None}


def suggestions(db, limit=20):
    rows = db.scalars(select(Hypothesis).where(Hypothesis.status == 'PENDING_REVIEW'))
    out = []
    for h in rows:
        e = db.get(SensorEvent, h.event_id)
        if not e or e.payload.get('device_id') != 'vision-1' or not e.payload.get('candidates'):
            continue  # only camera interactions; other pending events stay in Evidence review
        s = suggestion_for(db, h)
        if s:
            out.append(s)
    return sorted(out, key=lambda s: -s['captured'])[:limit]


def from_evidence(hypothesis_id, payload, key, actor):
    """Staff checked the camera evidence: record the movement and resolve the camera event together."""
    require_assisted()
    with svc.transaction() as db:
        h = svc.get(db, Hypothesis, hypothesis_id)
        previous = db.get(AssistedAction, key)
        if previous and h.status == 'ASSISTED':
            return {'id': key, 'duplicate': True}
        if h.status != 'PENDING_REVIEW':
            svc.fail('This camera event is already resolved')
        payload = {**payload, 'tray_id': h.tray_id, 'evidence_event_id': h.event_id}
        result = _record(db, key, 'MOVE', payload, actor)
        h.status, h.session_id = 'ASSISTED', payload['session_id']
        db.add(ReviewDecision(hypothesis_id=h.id, actor=actor, decision={
            'decision': 'ASSISTED_MOVE', 'assisted_action_id': key, 'reason': payload['reason'],
            'product_id': payload['product_id'], 'quantity_delta': payload['quantity_delta'], 'session_id': payload['session_id']}))
        svc.audit(db, actor, 'ASSISTED_FROM_EVIDENCE', {'hypothesis_id': h.id, 'assisted_action_id': key})
        return result
