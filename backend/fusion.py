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
