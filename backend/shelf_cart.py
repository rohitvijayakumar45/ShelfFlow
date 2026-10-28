"""RFID-attributed shelf cart (CART_ATTRIBUTION=rfid).

After check-in, the shopper at the shelf is the one whose bracelet was most recently tapped on either
shelf reader, or who checked in most recently, within SHELF_PRESENCE_S. Every settled shelf weight
step is that shopper's pickup (weight down) or return (weight up):

- product: on a tray with weighed units, the units whose weights add up to the change (the camera breaks
  ties and must not contradict); otherwise the product the camera stopped seeing (pickup) or started
  seeing (return), checked against the product's unit weight when one is set;
- all checks pass and AUTO_ADD is on: the cart changes at once (Hypothesis CONFIRMED);
- anything unproven: a Review queue item with the shopper and product already filled in.

One shopper at the shelf at a time. Two shoppers at once needs bracelet and hand tracking
(CART_ATTRIBUTION=camera), where the camera names the bracelet at the hand that moved the item.
"""
import time

from fastapi import HTTPException
from sqlalchemy import select

from backend import camera_watch, config as settings, service as svc
from backend.fusion import WEIGHT_FLOOR_G, unit_matches
from backend.models import (Audit, Bracelet, BraceletBinding, CartItem, Hypothesis, Product, SensorClaim, SensorEvent,
                            ShoppingSession, Tray, TrayLoad)

STEP_MIN_G = 5.0  # smaller settled steps are drift or a hand resting on the tray; items weigh >= ~5 g


def shoppers_at_shelf(db, now):
    """[(session_id, how it was decided)] of every shopper at the shelf, most recent first."""
    since = now - settings.SHELF_PRESENCE_S
    bindings = {b.rfid: b for b in db.scalars(select(BraceletBinding).where(BraceletBinding.ended == None))}
    seen = []  # (time, session_id, source)
    for b in bindings.values():
        s = db.get(ShoppingSession, b.session_id)
        bracelet = db.get(Bracelet, b.bracelet_id)
        if s and s.status == "ACTIVE" and bracelet and bracelet.state == "ASSIGNED" and since <= b.started <= now:
            seen.append((b.started, b.session_id, "check-in"))
    for e in db.scalars(select(SensorEvent).where(SensorEvent.received >= since, SensorEvent.received <= now,
                                                  SensorEvent.payload["event_type"].as_string() == "RFID")):
        uid, reader = svc.tap_of(e.payload)
        b = bindings.get(uid)
        if not b or e.received < b.started:
            continue  # the check-in tap itself, or a bracelet with no visit
        s = db.get(ShoppingSession, b.session_id)
        if s and s.status == "ACTIVE":
            seen.append((e.received, b.session_id, f"bracelet tap on {reader or 'a reader'}"))
    seen = [x for x in seen if not svc.checkout_request(db, x[1])]  # pressed Checkout: on the way out
    latest = {}
    for t, session_id, source in sorted(seen, reverse=True):
        latest.setdefault(session_id, source)
    return list(latest.items())


def shopper_at_shelf(db, now):
    """(session_id, how it was decided) of the shopper at the shelf (the most recent), or (None, None)."""
    present = shoppers_at_shelf(db, now)
    return present[0] if present else (None, None)


CAMERA_WAIT_S = 35  # hybrid: how long a shared-shelf step waits for a bracelet sighting before staff decide
BAND_BEFORE_S, BAND_AFTER_S = 20, 5  # a sighting from this long before to this long after the step can own it


def bracelet_seen(marker, tray_id, frames, seen_at):
    """The camera saw this bracelet pattern in `frames` frames in a row (vision/hands.py BandConfirmer)."""
    with svc.transaction() as db:
        svc.audit(db, "vision-1", "BRACELET_SEEN", {"marker": marker, "tray_id": tray_id, "frames": frames, "seen_at": seen_at})
    sweep()  # a shared-shelf step may have been waiting for exactly this sighting
    return {"ok": True}


def marker_owner(db, marker, at):
    """Session of the active visit whose bracelet carries this pattern marker at time `at`, or None."""
    for b in db.scalars(select(BraceletBinding).where(BraceletBinding.ended == None)):
        visit = db.get(ShoppingSession, b.session_id)
        if marker in (b.markers or []) and b.started <= at and visit and visit.status == "ACTIVE":
            return visit.id
    return None


def band_owner(db, at, tray_id):
    """(session_id, sighting id, marker, seen_at) of the unused bracelet sighting nearest in time to `at`, or None.
    Each sighting decides one weight step only."""
    used = {a.detail.get("band_sighting") for a in db.scalars(select(Audit).where(
        Audit.action == "SHELF_WEIGHT_STEP", Audit.created >= at - 3600))}
    best = None
    for a in db.scalars(select(Audit).where(Audit.action == "BRACELET_SEEN", Audit.created >= at - BAND_BEFORE_S - 60)):
        seen = a.detail.get("seen_at", a.created)
        if a.id in used or not (at - BAND_BEFORE_S <= seen <= at + BAND_AFTER_S) or a.detail.get("tray_id") not in (None, tray_id):
            continue
        session_id = marker_owner(db, a.detail.get("marker"), seen)
        if session_id and (best is None or abs(seen - at) < best[0]):
            best = (abs(seen - at), session_id, a.id, a.detail.get("marker"), seen)
    return best[1:] if best else None


def shared_shelf_owner(db, row, event, tray):
    """Hybrid: the bracelet pattern the camera confirmed nearest in time to the weight change owns it, with one
    shopper at the shelf or several. With no sighting, one shopper is decided by the tap/check-in (rfid);
    several wait for a sighting (sweep()). Returns (owner, waiting)."""
    if settings.CART_ATTRIBUTION != "hybrid":
        return None, False
    owner = band_owner(db, event.capture_end, tray.id)
    if owner:
        return owner, False
    present = shoppers_at_shelf(db, time.time())
    if len(present) < 2:
        return None, False
    if not owner:
        svc.audit(db, "shelf-cart", "SHELF_WEIGHT_WAITING_FOR_CAMERA", {"event_id": row.id, "shoppers": [s for s, _ in present]})
    return owner, owner is None


def sweep(now=None):
    """Hybrid: a shared-shelf step still waiting goes to the bracelet sighted nearest in time once one arrives;
    with none within CAMERA_WAIT_S it goes to the Review queue, with the shoppers who were at the shelf. Safe to repeat."""
    from backend.schemas import DeviceEvent
    now = time.time() if now is None else now
    with svc.transaction() as db:
        for a in db.scalars(select(Audit).where(Audit.action == "SHELF_WEIGHT_WAITING_FOR_CAMERA", Audit.created >= now - 3600)):
            event_id = a.detail.get("event_id")
            row = db.get(SensorEvent, event_id)
            if not row or db.scalar(select(Hypothesis.id).where(Hypothesis.event_id == event_id).limit(1)) or db.get(SensorClaim, event_id):
                continue
            event = DeviceEvent(**row.payload)
            owner = band_owner(db, event.capture_end, event.tray_id)
            if owner:
                weight_step(db, row, event, False, False, owner=owner)
                continue
            if a.created > now - CAMERA_WAIT_S:
                continue
            w = row.payload["weight"]
            action = "PICKUP" if w["post_g"] < w["pre_g"] else "RETURN"
            db.add(Hypothesis(event_id=event_id, session_id=None, tray_id=row.payload["tray_id"],
                              reason=["NO_BRACELET_SEEN", "SEVERAL_SHOPPERS_AT_SHELF"],
                              proposal={"session_id": None, "product_id": None, "quantity": 1, "action": action, "unit_ids": [],
                                        "unit_alternatives": [], "product_track": f"shelf:{event_id}:None", "source": "SHELF_WEIGHT",
                                        "customer_source": None, "weight_delta_g": round(w["post_g"] - w["pre_g"], 1),
                                        "camera_moves": None, "shoppers_present": a.detail.get("shoppers", [])}))
            svc.audit(db, "shelf-cart", "SHELF_WEIGHT_TO_REVIEW", {"event_id": event_id, "reason": "no bracelet seen"})


def _camera_moves(db, row, action, before_end=None):
    """{product: count} the camera saw leave (pickup) or arrive (return) since the tray's previous weight
    step; None when the camera has no view."""
    previous = db.scalar(select(SensorEvent.received).where(
        SensorEvent.id != row.id, SensorEvent.payload["event_type"].as_string() == "WEIGHT",
        SensorEvent.payload["tray_id"].as_string() == row.payload["tray_id"]).order_by(SensorEvent.received.desc()).limit(1))
    seen = camera_watch.change(since=previous, before_end=before_end)
    if seen is None:
        return None
    sign = -1 if action == "PICKUP" else 1
    moved = {p: d * sign for p, d in seen.items() if d * sign > 0}
    if any(p != "UNKNOWN" for p in moved):
        moved.pop("UNKNOWN", None)  # the half-hidden item in the hand (or the hand) beside a named product
    return moved


def _by_units(db, tray, action, session_id, delta, noise, camera, veto):
    """[(product_id, quantity, unit_ids)] from the weighed units on the tray (or in this cart, for a return)."""
    on_tray, in_cart = svc.unit_pools(db, tray.id)
    pool = on_tray if action == "PICKUP" else in_cart.get(session_id, [])
    matches = unit_matches(abs(delta), pool, noise)
    named = tuple(sorted(p for p, n in (camera or {}).items() if p != "UNKNOWN" for _ in range(n)))
    if not matches:
        veto.append("WEIGHT_NO_UNIT_MATCH")
        return []
    if len(matches) == 1:
        chosen = next(iter(matches))
    elif named in matches:
        chosen = named  # the camera names exactly one of the combinations that fit the weight
    else:
        veto.append("WEIGHT_AMBIGUOUS")
        return []
    if named and named != chosen:
        veto.append("VISION_WEIGHT_CONFLICT")
    ids = matches[chosen][0]
    units = {u["id"]: u for u in pool}
    out = {}
    for i in ids:
        out.setdefault(units[i]["product_id"], []).append(i)
    return [(p, len(unit_ids), unit_ids) for p, unit_ids in sorted(out.items())]


def _by_camera(db, tray, delta, noise, camera, veto):
    """[(product_id, quantity, [])] from what the camera saw move, checked against the unit weight."""
    if camera is None:
        veto.append("CAMERA_UNAVAILABLE")
        return [(tray.product_id, 1, [])] if not tray.unit_tracking else []
    if not camera:
        veto.append("CAMERA_NO_CHANGE")
        return [(tray.product_id, 1, [])] if not tray.unit_tracking else []
    if len(camera) > 1:
        veto.append("CAMERA_AMBIGUOUS")
        return []
    product_id, quantity = next(iter(camera.items()))
    product = db.get(Product, product_id)
    if product_id == "UNKNOWN" or not product:
        veto.append("CAMERA_PRODUCT_UNKNOWN")
        return []
    if not tray.unit_tracking and product_id != tray.product_id:
        veto.append("VISION_WEIGHT_CONFLICT")  # this tray holds one product
    if product.unit_g:
        by_weight = round(abs(delta) / product.unit_g)
        allowed = (product.tolerance_g or 0) * max(by_weight, 1) + 3 * noise + WEIGHT_FLOOR_G
        if by_weight < 1 or abs(abs(delta) - by_weight * product.unit_g) > allowed:
            veto.append("WEIGHT_PRODUCT_MISMATCH")
        elif by_weight != quantity:
            veto.append("QUANTITY_MISMATCH")
    return [(product_id, quantity, [])]


def weight_step(db, row, event, late, stale, owner=None):
    """Turn one shelf WEIGHT event into the at-shelf shopper's pickup or return (or a review item).
    owner: (session_id, sighting id, marker) when a bracelet sighting decided whose it was (hybrid)."""
    w = event.weight
    if not w or not w.settled or abs(w.post_g - w.pre_g) < STEP_MIN_G:
        return
    tray = db.get(Tray, event.tray_id)
    if not tray or db.scalar(select(TrayLoad.id).where(TrayLoad.tray_id == tray.id, TrayLoad.status == "OPEN")):
        return  # staff are loading the tray: these steps are stock, not shopping
    if owner is None:
        owner, waiting = shared_shelf_owner(db, row, event, tray)
        if waiting:
            return
    delta = w.post_g - w.pre_g
    action = "PICKUP" if delta < 0 else "RETURN"
    veto = []
    if late or stale:
        veto.append("LATE_OR_OUT_OF_ORDER")
    if not svc.sensor_healthy(event.sensor_health.model_dump()):
        veto.append("SENSOR_HEALTH")
    if tray.calibration_version != event.calibration_version:
        veto.append("CALIBRATION_MISMATCH")
    if not settings.WEIGHTS_ENABLED:
        veto.append("WEIGHT_INTEGRATION_DISABLED_STAFF_CONFIRMATION_REQUIRED")
    if not settings.AUTO_ADD:
        veto.append("STAFF_CONFIRMATION_REQUIRED")
    if owner:
        session_id, source = owner[0], f"bracelet {owner[2]} seen on camera"
    else:
        session_id, source = shopper_at_shelf(db, time.time())
    if not session_id:
        veto.append("NO_SHOPPER_AT_SHELF")
    # "Before" ends just before the bracelet appeared: the item is still on the tray, not yet in the hand.
    camera = _camera_moves(db, row, action, before_end=owner[3] - 1.0 if owner else None)
    on_tray, in_cart = svc.unit_pools(db, tray.id)
    in_cart = {sid: units for sid, units in in_cart.items() if (v := db.get(ShoppingSession, sid)) and v.status == "ACTIVE"}  # billed visits' items are sold
    if tray.unit_tracking and (on_tray or in_cart):
        moves = _by_units(db, tray, action, session_id, delta, w.noise_g, camera, veto)
    else:
        moves = _by_camera(db, tray, delta, w.noise_g, camera, veto)
    if not moves:
        moves = [(None, 1, [])]
    for product_id, quantity, _ in moves:
        if action == "RETURN" and session_id and product_id:
            item = db.scalar(select(CartItem).where(CartItem.session_id == session_id, CartItem.product_id == product_id))
            if not item or item.quantity < quantity:
                veto.append("RETURN_WITHOUT_MATCHING_CART")
    veto = sorted(set(veto))
    for product_id, quantity, unit_ids in moves:
        proposal = {"session_id": session_id, "product_id": product_id, "quantity": quantity, "action": action,
                    "product_track": f"shelf:{row.id}:{product_id}", "unit_ids": unit_ids, "unit_alternatives": [],
                    "source": "SHELF_WEIGHT", "customer_source": source, "weight_delta_g": round(delta, 1),
                    "camera_moves": camera}
        h = Hypothesis(event_id=row.id, session_id=session_id, tray_id=tray.id, reason=veto, proposal=proposal)
        db.add(h)
        db.flush()
        if not veto:
            try:
                with db.begin_nested():  # a refused cart change must not reject the shelf event itself
                    svc.apply_proposal(db, h, proposal)
            except HTTPException as exc:
                # The bridge would retry the whole event until it is stale; staff decide this one instead.
                h.reason = ["CART_UPDATE_REFUSED"]
                h.proposal = {**proposal, "refused": str(exc.detail)[:200]}
                h.status = "PENDING_REVIEW"
    svc.audit(db, "shelf-cart", "SHELF_WEIGHT_STEP", {"event_id": row.id, "action": action, "delta_g": round(delta, 1),
                                                      "session_id": session_id, "customer_source": source,
                                                      "band_sighting": owner[1] if owner else None,
                                                      "products": [m[0] for m in moves], "reasons": veto})
