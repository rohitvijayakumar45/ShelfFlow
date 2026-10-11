import time
import uuid
from backend.schemas import DeviceEvent, Candidate, Weight

def interaction(marker=1, tray="tray-a", action="PICKUP", quantity=1, grams=None, sequence=1, **changes):
    now = time.time()
    unit = grams if grams is not None else (1030 if tray == "tray-a" else 330)
    c = Candidate(marker_ids=[marker], product_id="oat" if tray == "tray-a" else "coffee", product_track=str(uuid.uuid4()), hand_track=f"hand-{marker}", action=action, quantity=quantity, identity=True, hand_product=True, trajectory=True, sku=True, temporal=True, bracelet_hand=True, chain_refs=["SIMULATED:marker-wrist-grasp-trajectory"], detector_confidence=0.95)
    for key in list(changes):
        if key in Candidate.model_fields:
            setattr(c, key, changes.pop(key))
    data = dict(event_id=str(uuid.uuid4()), event_type="INTERACTION", device_id="simulator", boot_id="sim-boot", sequence_number=sequence, tray_id=tray, capture_start=now - 0.4, capture_end=now, monotonic_ms=int(time.monotonic() * 1000), clock_synced=True, clock_uncertainty_ms=10, calibration_version="sim-v1", weight=Weight(pre_g=unit * 10, post_g=unit * (10 + quantity * (-1 if action == "PICKUP" else 1)), noise_g=0.5, settled=True), candidates=[c], model_status="MOCK", evidence_refs=["SIMULATED:no-physical-video"])
    data.update(changes)
    return DeviceEvent(**data)
