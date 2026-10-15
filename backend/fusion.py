"""Conservative joint attribution. An unresolved candidate vetoes the entire tray event.

The geometric laptop baseline deliberately does not assert a proven grasp. Only a
validated evidence extractor or audited review may approve real ownership.
"""
from collections import Counter
from backend.schemas import DeviceEvent

def evaluate(event: DeviceEvent, product, bindings: dict, simulation: bool):
    veto = []
    scores = []
    if not event.sensor_health.ok or event.sensor_health.flags or event.sensor_health.queue_overflow:
        veto.append("SENSOR_HEALTH")
    if not event.clock_synced or event.clock_uncertainty_ms > 100:
        veto.append("CLOCK_UNCERTAINTY")
    if event.frame_age_ms > 750 or event.blur or event.frame_gap_ms > 500:
        veto.append("CAMERA_EVIDENCE")
    if event.model_status == "MODEL_UNAVAILABLE" or (event.model_status == "MOCK" and not simulation):
        veto.append("MODEL_UNAVAILABLE")
    if not event.weight or not event.weight.settled:
        veto.append("UNSETTLED_WEIGHT")
    if not event.candidates:
        veto.append("NO_OWNERSHIP_CANDIDATE")
    if product.unit_g is None or product.tolerance_g is None:
        veto.append("UNMEASURED_UNIT_WEIGHT")
    if product.weight_source == "SIMULATED" and not simulation:
        veto.append("SIMULATED_WEIGHT")
    tracks = Counter(c.product_track for c in event.candidates)
    proposals = []
    for c in event.candidates:
        owners = {bindings[m][0] for m in c.marker_ids if m in bindings}
        valid_ids = all(m in bindings for m in c.marker_ids)
        owner = next(iter(owners)) if len(owners) == 1 and valid_ids else None
        dimensions = {"identity": bool(owner and c.identity), "hand_product": c.hand_product,
                      "sku": c.sku and c.product_id == product.id, "quantity": True,
                      "temporal": c.temporal, "weight": False, "sensor_health": event.sensor_health.ok}
        scores.append(dimensions)
        if not owner or not c.marker_ids or not c.identity:
            veto.append("IDENTITY_UNPROVEN")
        if c.rfid_uid and any(bindings[m][1] != c.rfid_uid for m in c.marker_ids if m in bindings):
            veto.append("RFID_MARKER_CONFLICT")
        if not all((c.hand_product, c.bracelet_hand, c.trajectory, c.temporal, c.sku)) or not c.chain_refs:
            veto.append("OWNERSHIP_CHAIN_UNPROVEN")
        if c.opposite_hand or c.handoff or c.crossed or c.occluded:
            veto.append("UNSUPPORTED_OR_OCCLUDED_CHAIN")
        if c.product_id != product.id:
            veto.append("TRAY_SKU_MISMATCH")
        if tracks[c.product_track] > 1:
            veto.append("DUPLICATE_PRODUCT_TRACK")
        proposals.append({"session_id": owner, "product_id": c.product_id, "quantity": c.quantity,
                          "action": c.action, "product_track": c.product_track})
    if event.weight and product.unit_g and product.tolerance_g is not None:
        delta = event.weight.post_g - event.weight.pre_g
        net = sum(c.quantity * (-1 if c.action == "PICKUP" else 1) for c in event.candidates)
        total = sum(c.quantity for c in event.candidates)
        tolerance = total * product.tolerance_g + 3 * event.weight.noise_g
        if abs(delta - net * product.unit_g) > tolerance:
            veto.append("WEIGHT_MISMATCH")
        # Quantity only unique if neighbouring integer net counts cannot also fit.
        if tolerance * 2 >= product.unit_g:
            veto.append("QUANTITY_NOT_UNIQUE")
        if len(event.candidates) > 1 and len({c.action for c in event.candidates}) > 1:
            # Net-zero can establish neither individual quantity nor ownership with this baseline.
            veto.append("CANCELLING_ACTIONS_REQUIRE_REVIEW")
        for score in scores:
            score["weight"] = "WEIGHT_MISMATCH" not in veto
            score["quantity"] = "QUANTITY_NOT_UNIQUE" not in veto
    return sorted(set(veto)), proposals, scores


# ---------------------------------------------------------------------------
# Unit-tracked (mixed-product) trays.
#
# Every physical unit was weighed individually when staff loaded it, so a tray
# weight change is matched against the units actually on the tray (pickups) or
# in the candidate shoppers' carts from this tray (returns). Weight is the first
# signal; the detector/gallery verdict and the emptied spot are the second. A
# match is accepted only when exactly one PRODUCT combination fits; several
# interchangeable units of the same product are not an ambiguity.
# ---------------------------------------------------------------------------
from itertools import combinations
from math import sqrt

MAX_UNITS_PER_EVENT = 3
WEIGHT_FLOOR_G = 1.0  # absolute allowance for rounding/placement effects

def unit_tolerance(units, event_noise_g):
    """3-sigma allowance from the event noise plus each unit's own loading noise."""
    return 3 * sqrt(event_noise_g ** 2 + sum(u["noise_g"] ** 2 for u in units)) + WEIGHT_FLOOR_G

def unit_matches(target_g, units, event_noise_g, max_units=MAX_UNITS_PER_EVENT):
    """Return {sorted product tuple: (unit_ids, error_g)} for every combination that fits target_g."""
    best = {}
    for size in range(1, min(max_units, len(units)) + 1):
        for combo in combinations(units, size):
            error = abs(sum(u["weight_g"] for u in combo) - target_g)
            if error <= unit_tolerance(combo, event_noise_g):
                key = tuple(sorted(u["product_id"] for u in combo))
                if key not in best or error < best[key][1]:
                    best[key] = ([u["id"] for u in combo], error)
    return best

def _owner(c, bindings):
    owners = {bindings[m][0] for m in c.marker_ids if m in bindings}
    return next(iter(owners)) if len(owners) == 1 and all(m in bindings for m in c.marker_ids) else None

def evaluate_units(event: DeviceEvent, on_tray: list, in_cart: dict, bindings: dict, simulation: bool):
    """Fusion for a unit-tracked tray.

    on_tray: unit dicts (id, product_id, weight_g, noise_g, spot) currently on the tray.
    in_cart: {session_id: [unit dicts]} units from this tray currently in each cart.
    Returns (vetoes, proposals, scores); proposals carry the exact unit_ids to move.
    """
    veto = []
    if not event.sensor_health.ok or event.sensor_health.flags or event.sensor_health.queue_overflow:
        veto.append("SENSOR_HEALTH")
    if not event.clock_synced or event.clock_uncertainty_ms > 100:
        veto.append("CLOCK_UNCERTAINTY")
    if event.frame_age_ms > 750 or event.blur or event.frame_gap_ms > 500:
        veto.append("CAMERA_EVIDENCE")
    if event.model_status == "MODEL_UNAVAILABLE" or (event.model_status == "MOCK" and not simulation):
        veto.append("MODEL_UNAVAILABLE")
    if not event.weight or not event.weight.settled:
        veto.append("UNSETTLED_WEIGHT")
    if not event.candidates:
        veto.append("NO_OWNERSHIP_CANDIDATE")
    if len({c.action for c in event.candidates}) > 1:
        veto.append("CANCELLING_ACTIONS_REQUIRE_REVIEW")
    tracks = Counter(c.product_track for c in event.candidates)
    owners = []
    for c in event.candidates:
        owner = _owner(c, bindings)
        owners.append(owner)
        if not owner or not c.marker_ids or not c.identity:
            veto.append("IDENTITY_UNPROVEN")
        if c.rfid_uid and any(bindings[m][1] != c.rfid_uid for m in c.marker_ids if m in bindings):
            veto.append("RFID_MARKER_CONFLICT")
        # Product identity is decided by weight + vision below, so the chain does not require c.sku.
        if not all((c.hand_product, c.bracelet_hand, c.trajectory, c.temporal)) or not c.chain_refs:
            veto.append("OWNERSHIP_CHAIN_UNPROVEN")
        if c.opposite_hand or c.handoff or c.crossed or c.occluded:
            veto.append("UNSUPPORTED_OR_OCCLUDED_CHAIN")
        if tracks[c.product_track] > 1:
            veto.append("DUPLICATE_PRODUCT_TRACK")

    action = event.candidates[0].action if event.candidates else None
    total = sum(c.quantity for c in event.candidates)
    vision = sorted(c.product_id for c in event.candidates if c.sku and c.product_id != "UNKNOWN" for _ in range(c.quantity))
    chosen, alternatives, units_by_id, matches = None, [], {}, {}
    if event.weight and event.candidates:
        delta = event.weight.post_g - event.weight.pre_g
        noise = event.weight.noise_g
        if abs(delta) <= 3 * noise + WEIGHT_FLOOR_G:
            veto.append("ZERO_NET_REQUIRES_REVIEW")  # e.g. simultaneous equal-weight pickup and return
        elif (delta < 0) != (action == "PICKUP"):
            veto.append("ACTION_WEIGHT_CONFLICT")
        else:
            if action == "PICKUP":
                pool = on_tray
            else:
                known = [o for o in owners if o]
                pool = [u for sid in (known or list(in_cart)) for u in in_cart.get(sid, [])]
            units_by_id = {u["id"]: u for u in pool}
            matches = unit_matches(abs(delta), pool, noise)
            alternatives = [{"products": list(k), "unit_ids": v[0], "error_g": round(v[1], 2)} for k, v in sorted(matches.items(), key=lambda kv: kv[1][1])]
            if not matches:
                veto.append("WEIGHT_NO_UNIT_MATCH")
            elif len(matches) == 1:
                chosen = next(iter(matches))
            elif len(vision) == total and tuple(vision) in matches:
                chosen = tuple(vision)  # vision resolves a weight tie only when it names every unit
            else:
                veto.append("WEIGHT_AMBIGUOUS")
    proposals, scores = [], []
    if chosen:
        chosen_ids = list(matches[chosen][0])
        if Counter(vision) - Counter(chosen):
            veto.append("VISION_WEIGHT_CONFLICT")
        if len(chosen) != total:
            veto.append("QUANTITY_MISMATCH")
        for c in event.candidates:
            if c.spot is not None and action == "PICKUP" and not any(units_by_id[i].get("spot") == c.spot for i in chosen_ids):
                veto.append("SPOT_CONFLICT")
        # Hand each candidate its units. With several shoppers and several products every
        # hand must name its product; otherwise which person took which item is unproven.
        remaining = list(chosen_ids)
        single = len(event.candidates) == 1
        for c, owner in zip(event.candidates, owners):
            if single:
                mine = remaining
            elif len(set(chosen)) == 1:
                mine = remaining[:c.quantity]
            elif c.sku and c.product_id != "UNKNOWN":
                mine = [i for i in remaining if units_by_id[i]["product_id"] == c.product_id][:c.quantity]
            else:
                mine = []
            if not single and len(mine) != c.quantity:
                veto.append("OWNER_PRODUCT_UNRESOLVED")
            remaining = [i for i in remaining if i not in mine]
            by_product = {}
            for i in mine:
                by_product.setdefault(units_by_id[i]["product_id"], []).append(i)
            for product_id, ids in sorted(by_product.items()):
                proposals.append({"session_id": owner, "product_id": product_id, "quantity": len(ids), "action": c.action,
                                  "product_track": c.product_track if len(by_product) == 1 else f"{c.product_track}:{product_id}",
                                  "unit_ids": ids, "unit_alternatives": alternatives})
    if not proposals:
        for c, owner in zip(event.candidates, owners):
            proposals.append({"session_id": owner, "product_id": c.product_id, "quantity": c.quantity, "action": c.action,
                              "product_track": c.product_track, "unit_ids": [], "unit_alternatives": alternatives})
    for c, owner in zip(event.candidates, owners):
        scores.append({"identity": bool(owner and c.identity), "hand_product": c.hand_product, "sku": bool(c.sku and c.product_id != "UNKNOWN"),
                       "quantity": chosen is not None and "QUANTITY_MISMATCH" not in veto, "temporal": c.temporal,
                       "weight": chosen is not None, "sensor_health": event.sensor_health.ok})
    return sorted(set(veto)), proposals, scores
