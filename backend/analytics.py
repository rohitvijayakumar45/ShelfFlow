"""Operations analytics: every staff decision is ground truth.

kpis                 how often the shelf decides on its own, how often staff must step in, and how
                     often an automatic decision was later disputed or corrected.
recognition_tuning   labelled samples (vision scores + the product staff confirmed) and the gallery
                     threshold/margin that would have accepted the most of them with almost no wrong
                     product. Staff apply a recommendation explicitly; nothing changes by itself.
shrink_report        weight that left a tray without being explained, dismissed pickups, removed
                     and misplaced items.
drift_report         scale reading vs the items the records say are on the tray, drift flags,
                     calibration age.
suggestion           a pre-filled review decision with the reasons behind it.
timeline             everything around one interaction (weights, taps, frames) on one clock.
"""
import json
import os
import time
from collections import Counter
from math import sqrt
from pathlib import Path
from statistics import median
from sqlalchemy import select
from backend.models import *

def _event(db, cache, event_id):
    if event_id not in cache:
        row = db.get(SensorEvent, event_id)
        cache[event_id] = row
    return cache[event_id]

def _reviews(db):
    """hypothesis_id -> time of its staff review (from the audit trail)."""
    out = {}
    for a in db.scalars(select(Audit).where(Audit.action.in_(["REVIEW", "CORRECTION"])).order_by(Audit.created)):
        hid = (a.detail or {}).get("hypothesis_id")
        if hid and hid not in out:
            out[hid] = a.created
    return out

def kpis(db, since=0.0):
    cache = {}
    hypotheses = [h for h in db.scalars(select(Hypothesis)) if (_event(db, cache, h.event_id).received if _event(db, cache, h.event_id) else 0) >= since]
    reviewed_at = _reviews(db)
    decided = {d.hypothesis_id for d in db.scalars(select(ReviewDecision))}
    status = Counter(h.status for h in hypotheses)
    auto = [h for h in hypotheses if h.status in ("CONFIRMED", "DISPUTED", "CORRECTED") and not h.reason]
    auto_challenged = [h for h in auto if h.status in ("DISPUTED", "CORRECTED")]
    needed_review = [h for h in hypotheses if h.reason]
    waits = []
    for h in hypotheses:
        e = _event(db, cache, h.event_id)
        if h.id in reviewed_at and e:
            waits.append(max(0.0, reviewed_at[h.id] - e.received))
    reasons = Counter(r for h in needed_review for r in h.reason)
    agree = total = 0
    for h in hypotheses:
        if h.status != "CONFIRMED" or h.id not in decided:
            continue
        e = _event(db, cache, h.event_id)
        tops = [c.get("top_product") for c in (e.payload.get("candidates") or [])] if e else []
        tops = [t for t in tops if t]
        if tops:
            total += 1
            agree += h.proposal.get("product_id") in tops
    n = len(hypotheses)
    return {"interactions": n, "status": dict(status),
            "auto_decided": len(auto), "auto_rate": round(len(auto) / n, 3) if n else None,
            "review_rate": round(len(needed_review) / n, 3) if n else None,
            "auto_challenged": len(auto_challenged),
            "auto_precision": round(1 - len(auto_challenged) / len(auto), 3) if auto else None,
            "disputes": status.get("DISPUTED", 0) + sum(1 for a in db.scalars(select(Audit).where(Audit.action == "DISPUTE"))),
            "median_review_seconds": round(median(waits), 1) if waits else None,
            "pending": status.get("PENDING_REVIEW", 0),
            "top_reasons": reasons.most_common(8),
            "vision_agreement": round(agree / total, 3) if total else None, "vision_agreement_samples": total,
            "note": "auto_precision counts automatic decisions not later disputed or corrected; unnoticed errors are not visible here."}

# ---------------------------------------------------------------- recognition threshold tuning
MIN_SAMPLES = 30

def recognition_samples(db):
    """(similarity, margin, vision top product, staff-confirmed product) per reviewed candidate."""
    samples = []
    decided = {d.hypothesis_id for d in db.scalars(select(ReviewDecision))}
    for h in db.scalars(select(Hypothesis).where(Hypothesis.status.in_(["CONFIRMED", "CORRECTED"]))):
        if h.id not in decided:
            continue
        truth = h.proposal.get("product_id")
        e = db.get(SensorEvent, h.event_id)
        for c in (e.payload.get("candidates") or []) if e else []:
            if c.get("similarity") is None or not c.get("top_product") or not truth:
                continue
            samples.append({"similarity": c["similarity"], "margin": c.get("margin") or 0.0,
                            "top": c["top_product"], "truth": truth})
    for row in db.scalars(select(UncertainCrop).where(UncertainCrop.status == "LABELLED")):
        samples.append({"similarity": row.similarity, "margin": row.margin, "top": row.top_product, "truth": row.answer_product})
    return samples

def sweep(samples, max_wrong=0.01):
    table = []
    for t in [x / 100 for x in range(30, 96)]:
        for m in (0.0, 0.02, 0.04, 0.06, 0.08, 0.1, 0.12):
            accepted = [s for s in samples if s["similarity"] >= t and s["margin"] >= m]
            wrong = sum(s["top"] != s["truth"] for s in accepted)
            table.append({"threshold": t, "margin": m, "accepted": len(accepted),
                          "auto_rate": len(accepted) / len(samples), "wrong_rate": wrong / len(samples)})
    ok = [r for r in table if r["wrong_rate"] <= max_wrong]
    return max(ok, key=lambda r: (r["auto_rate"], r["threshold"])) if ok else None, table

def recognition_tuning(db, current):
    samples = recognition_samples(db)
    out = {"samples": len(samples), "current": current, "min_samples": MIN_SAMPLES,
           "vision_right": sum(s["top"] == s["truth"] for s in samples)}
    if len(samples) < MIN_SAMPLES:
        out["recommended"] = None
        out["advice"] = f"Need {MIN_SAMPLES - len(samples)} more staff-reviewed recognitions before recommending thresholds."
        return out
    best, _ = sweep(samples)
    out["recommended"] = None if not best else {"threshold": best["threshold"], "margin": best["margin"],
                                               "auto_rate": round(best["auto_rate"], 3), "wrong_rate": round(best["wrong_rate"], 3)}
    if current and best:
        now = [s for s in samples if s["similarity"] >= current["threshold"] and s["margin"] >= current["margin"]]
        out["current_auto_rate"] = round(len(now) / len(samples), 3)
        out["current_wrong_rate"] = round(sum(s["top"] != s["truth"] for s in now) / len(samples), 3)
    out["advice"] = ("Thresholds chosen on reviewed events; the hardest cases reach review, so real accuracy on easy "
                     "cases is usually higher. Re-check after every catalogue change.")
    return out

# ---------------------------------------------------------------- shrink and misplacement
def shrink_report(db, since=0.0, min_g=8.0):
    referenced = set()
    for e in db.scalars(select(SensorEvent).where(SensorEvent.payload["event_type"].as_string() == "INTERACTION")):
        referenced |= {r[13:] for r in e.payload.get("evidence_refs", []) if r.startswith("SENSOR_EVENT:")}
    claimed = {c.source_event_id for c in db.scalars(select(SensorClaim))}
    loads = list(db.scalars(select(TrayLoad)))
    priced = [p for p in db.scalars(select(Product)) if p.unit_g]
    unexplained = []
    for e in db.scalars(select(SensorEvent).where(SensorEvent.payload["event_type"].as_string() == "WEIGHT")):
        w = e.payload.get("weight") or {}
        if e.received < since or not w.get("settled") or e.id in referenced or e.id in claimed:
            continue
        delta = w["post_g"] - w["pre_g"]
        if delta > -min_g:
            continue
        # Unsynced device clocks report boot-relative seconds: fall back to the server receive time.
        t = e.payload["capture_end"] if e.payload.get("clock_synced") and e.payload["capture_end"] > 1e9 else e.received
        if any(l.tray_id == e.payload["tray_id"] and l.started <= t and (l.ended is None or t < l.ended) for l in loads):
            continue  # staff loading/unloading
        guess = min(priced, key=lambda p: abs(abs(delta) - p.unit_g), default=None)
        fits = guess is not None and abs(abs(delta) - guess.unit_g) <= max(guess.tolerance_g or 0, 3 * (w.get("noise_g") or 0) + 1)
        unexplained.append({"event_id": e.id, "tray_id": e.payload["tray_id"], "at": t, "delta_g": round(delta, 1),
                            "likely_product": guess.id if fits else None,
                            "estimated_value_minor": guess.price_minor if fits else None})
    dismissed = []
    for h in db.scalars(select(Hypothesis).where(Hypothesis.status == "DISMISSED")):
        if h.proposal.get("action") != "PICKUP":
            continue
        p = db.get(Product, h.proposal.get("product_id")) if h.proposal.get("product_id") else None
        dismissed.append({"hypothesis_id": h.id, "tray_id": h.tray_id, "product_id": p.id if p else None,
                          "quantity": h.proposal.get("quantity"), "value_minor": p.price_minor * (h.proposal.get("quantity") or 1) if p else None})
    removed = [{"unit_id": a.detail.get("unit_id"), "tray_id": a.detail.get("tray_id"), "reason": a.detail.get("reason"), "at": a.created}
               for a in db.scalars(select(Audit).where(Audit.action == "TRAY_UNIT_REMOVED", Audit.created >= since))]
    relocated = [{"unit_id": a.detail.get("unit_id"), "from_tray": a.detail.get("from_tray"), "to_tray": a.detail.get("to_tray"), "at": a.created}
                 for a in db.scalars(select(Audit).where(Audit.action == "UNIT_RELOCATED", Audit.created >= since))]
    wrong_tray_pending = [h.id for h in db.scalars(select(Hypothesis).where(Hypothesis.status == "PENDING_REVIEW")) if "RETURNED_TO_OTHER_TRAY" in h.reason]
    unexplained.sort(key=lambda u: -u["at"])
    return {"unexplained_weight_drops": unexplained,
            "unexplained_value_minor": sum(u["estimated_value_minor"] or 0 for u in unexplained),
            "dismissed_pickups": dismissed, "dismissed_value_minor": sum(d["value_minor"] or 0 for d in dismissed),
            "removed_units": removed, "relocated_units": relocated, "wrong_tray_pending": wrong_tray_pending,
            "note": "Estimates: a drop is valued at the product whose unit weight it matches; unmatched drops are listed without value."}

# ---------------------------------------------------------------- scale drift
def drift_report(db, window_s=86400):
    now = time.time()
    out = []
    for tray in db.scalars(select(Tray)):
        rows = list(db.scalars(select(SensorEvent).where(SensorEvent.payload["tray_id"].as_string() == tray.id,
                                                         SensorEvent.payload["event_type"].as_string().in_(["WEIGHT", "HEALTH"]))
                               .order_by(SensorEvent.received.desc()).limit(400)))
        latest = next((e for e in rows if (e.payload.get("weight") or {}).get("filtered") is not None), None)
        flags = Counter(f for e in rows if now - e.received <= window_s for f in (e.payload.get("sensor_health") or {}).get("flags", []))
        units = list(db.scalars(select(TrayUnit).where(TrayUnit.tray_id == tray.id, TrayUnit.status == "ON_TRAY")))
        calibration = db.scalar(select(Calibration).where(Calibration.tray_id == tray.id).order_by(Calibration.created.desc()).limit(1))
        item = {"tray_id": tray.id, "calibration_version": tray.calibration_version,
                "calibration_age_days": round((now - calibration.created) / 86400, 1) if calibration else None,
                "drift_flags_24h": flags.get("DRIFT", 0), "health_flags_24h": dict(flags), "status": "OK", "advice": None}
        if latest and tray.unit_tracking:
            expected = sum(u.weight_g for u in units)
            reading = latest.payload["weight"]["filtered"]
            allowance = 3 * sqrt(sum(u.noise_g ** 2 for u in units) + (latest.payload["weight"].get("noise_g") or 0) ** 2) + 5 + 0.01 * expected
            item.update(scale_g=round(reading, 1), records_g=round(expected, 1), difference_g=round(reading - expected, 1),
                        allowance_g=round(allowance, 1), reading_age_s=round(now - latest.received))
            if abs(reading - expected) > allowance:
                item["status"] = "MISMATCH"
                item["advice"] = ("Scale reads more than the recorded items: an unloaded item or object is on the tray, or the zero drifted."
                                  if reading > expected else
                                  "Scale reads less than the recorded items: an item left unnoticed, or the zero drifted. Check the tray, then re-zero if empty.")
        if item["drift_flags_24h"]:
            item["status"] = "DRIFT" if item["status"] == "OK" else item["status"]
            item["advice"] = item["advice"] or "The empty-tray baseline is creeping: re-zero the tray in service mode when it is empty."
        if item["calibration_age_days"] is not None and item["calibration_age_days"] > 30 and item["status"] == "OK":
            item["status"], item["advice"] = "STALE_CALIBRATION", "Calibration is over 30 days old: verify with the known mass."
        out.append(item)
    return out

# ---------------------------------------------------------------- review suggestion
def suggestion(db, h):
    p = h.proposal or {}
    e = db.get(SensorEvent, h.event_id)
    payload = e.payload if e else {}
    candidates = payload.get("candidates") or []
    why, decision, confidence = [], "CONFIRM", "weak"
    session_id, unit_ids, quantity = p.get("session_id"), list(p.get("unit_ids") or []), p.get("quantity") or 1
    if "action" not in p:
        return {"decision": "DISMISS", "session_id": None, "unit_ids": [], "quantity": 1, "confidence": "weak",
                "why": ["No product movement was observed; dismiss unless the video shows an item leaving."]}
    if "WEIGHT_NO_UNIT_MATCH" in h.reason:
        decision = "DISMISS"
        why.append("The weight change matches no recorded item: likely a foreign object or an item placed without loading. Check the video.")
    alternatives = p.get("unit_alternatives") or []
    if not unit_ids and alternatives:
        tops = {c.get("top_product") for c in candidates if c.get("top_product")}
        pick = next((a for a in alternatives if set(a["products"]) <= tops), None) if len(alternatives) > 1 else alternatives[0]
        if pick:
            unit_ids = list(pick["unit_ids"])
            why.append(("Only one item combination fits the weight" if len(alternatives) == 1 else "Camera recognised")
                       + f": {', '.join(pick['products'])} ({pick['error_g']} g off).")
        else:
            why.append(f"{len(alternatives)} item combinations fit the weight; choose from the video.")
    if unit_ids:
        quantity = len(unit_ids)
    if not session_id:
        uids = {c.get("rfid_uid") for c in candidates if c.get("rfid_uid")}
        if len(uids) == 1:
            b = db.scalar(select(Bracelet).where(Bracelet.rfid == next(iter(uids)), Bracelet.state == "ASSIGNED"))
            if b and b.session_id:
                session_id = b.session_id
                why.append("The bracelet read at the shelf belongs to this visit.")
        if not session_id:
            active = list(db.scalars(select(ShoppingSession).where(ShoppingSession.status == "ACTIVE")))
            if len(active) == 1:
                session_id = active[0].id
                why.append("Only one shopper was in the store.")
            elif active:
                why.append(f"{len(active)} shoppers active: pick the owner from the bracelet in the video.")
    else:
        why.append("Bracelet markers at the hand identify this visit.")
    proof = [c for c in candidates if c.get("hand_product") and c.get("trajectory")]
    if proof:
        why.append("A hand touched the item and it " + ("left the tray." if p.get("action") == "PICKUP" else "arrived on the tray."))
    if decision == "CONFIRM" and session_id and unit_ids and len(alternatives) <= 1 and proof:
        confidence = "strong"
    elif decision == "CONFIRM" and session_id and (unit_ids or not alternatives):
        confidence = "medium"
    return {"decision": decision, "session_id": session_id, "unit_ids": unit_ids, "quantity": quantity,
            "confidence": confidence, "why": why}

# ---------------------------------------------------------------- evidence timeline
def timeline(db, event_id, pad_s=8.0):
    e = db.get(SensorEvent, event_id)
    if not e:
        return None
    start, end = e.payload["capture_start"] - pad_s, e.payload["capture_end"] + pad_s
    tray = e.payload.get("tray_id")
    weights, taps = [], []
    for row in db.scalars(select(SensorEvent).where(SensorEvent.payload["capture_end"].as_float() >= start,
                                                    SensorEvent.payload["capture_end"].as_float() <= end)):
        p = row.payload
        if p["event_type"] == "WEIGHT" and p.get("tray_id") == tray and p.get("weight"):
            w = p["weight"]
            weights.append({"t": p["capture_end"], "pre_g": w["pre_g"], "post_g": w["post_g"], "filtered": w.get("filtered"),
                            "position_mm": w.get("position_mm"), "event_id": row.id})
        elif p["event_type"] == "RFID":
            refs = p.get("evidence_refs", [])
            taps.append({"t": p["capture_end"], "uid": next((r[5:] for r in refs if r.startswith("RFID:")), None),
                         "reader": next((r[12:] for r in refs if r.startswith("RFID_READER:")), None)})
    root = Path(os.getenv("EVIDENCE_ROOT", "evidence")).resolve()
    frames = []
    for ref in e.payload.get("evidence_refs", []):
        if not ref.endswith(".jpg"):
            continue
        meta = root / ref.replace(".jpg", ".json")
        info = {}
        if meta.parent == root and meta.is_file():
            try:
                info = json.loads(meta.read_text(encoding="utf-8"))
            except ValueError:
                info = {}
        frames.append({"ref": ref, "t": info.get("capture_wall"), "annotations": info.get("annotations"),
                       "faces_redacted": info.get("faces_redacted", 0), "original": info.get("original_bytes", True)})
    weight = e.payload.get("weight")
    return {"event_id": e.id, "tray_id": tray, "window": [e.payload["capture_start"], e.payload["capture_end"]],
            "weight": weight, "weights": sorted(weights, key=lambda w: w["t"]), "taps": sorted(taps, key=lambda t: t["t"]),
            "frames": frames, "candidates": e.payload.get("candidates") or [], "model_versions": e.payload.get("model_versions") or {}}
