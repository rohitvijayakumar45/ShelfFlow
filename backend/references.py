"""Few-shot product references: registry for the recognition gallery.

New products are learned without retraining or photo sessions:
  * restock auto-crops - every unit loaded with before/after stills becomes an enrolment job;
                         vision cuts the item out and posts it back as a reference.
  * teach mode         - staff turn a product in front of the camera for a few seconds;
                         vision posts a capped number of diverse views.
Staff can reject any reference. Vision mirrors the ACTIVE set into its live gallery and
reports which products it confuses (look-alikes) back here.
"""
import base64
import os
import re
import time
import uuid
from pathlib import Path
from sqlalchemy import select
from backend.models import Product, Tray, TrayUnit, ProductReference, TeachSession, Shelf
from backend.service import transaction, get, fail, audit

MAX_IMAGE_BYTES = 1_500_000
PRODUCT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")

def catalogue_root():
    # Product stills live apart from shopper evidence and are not deleted by evidence retention.
    root = Path(os.getenv("EVIDENCE_ROOT", "evidence")).resolve() / "catalogue"
    root.mkdir(parents=True, exist_ok=True)
    return root

def _image_suffix(data):
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    fail("Reference must be a JPEG or PNG image")

def create_product(product_id, name, price_minor, actor):
    if not PRODUCT_ID.match(product_id):
        fail("Product id: 2-40 lowercase letters, digits or '-'")
    with transaction() as db:
        if db.get(Product, product_id):
            fail("Product id already exists")
        db.add(Product(id=product_id, name=name, price_minor=price_minor, unit_g=None, tolerance_g=None, weight_source="UNMEASURED"))
        audit(db, actor, "PRODUCT_CREATED", {"product_id": product_id, "name": name, "price_minor": price_minor})
        return {"product_id": product_id}

def _expire(db, session, now):
    if session.status == "ACTIVE" and (now > session.until or session.accepted >= session.max_refs):
        session.status = "DONE"

def active_teach(db, tray_id, now=None):
    now = now or time.time()
    for session in db.scalars(select(TeachSession).where(TeachSession.tray_id == tray_id, TeachSession.status == "ACTIVE")):
        _expire(db, session, now)
        if session.status == "ACTIVE":
            return session
    return None

def start_teach(product_id, tray_id, seconds, max_refs, reason, actor):
    with transaction() as db:
        get(db, Product, product_id)
        get(db, Tray, tray_id)
        if active_teach(db, tray_id):
            fail("A teach session is already running on this tray")
        session = TeachSession(product_id=product_id, tray_id=tray_id, actor=actor, until=time.time() + seconds, max_refs=max_refs)
        db.add(session)
        db.flush()
        audit(db, actor, "TEACH_START", {"teach_id": session.id, "product_id": product_id, "tray_id": tray_id, "seconds": seconds, "reason": reason})
        return {"teach_id": session.id, "until": session.until}

def stop_teach(teach_id, actor):
    with transaction() as db:
        session = get(db, TeachSession, teach_id)
        if session.status == "ACTIVE":
            session.status = "STOPPED"
        audit(db, actor, "TEACH_STOP", {"teach_id": teach_id, "accepted": session.accepted})
        return {"teach_id": teach_id, "status": session.status, "accepted": session.accepted}

def enrollment_jobs(db):
    jobs = []
    for unit in db.scalars(select(TrayUnit).where(TrayUnit.reference_status == "PENDING").order_by(TrayUnit.created).limit(20)):
        stills = [r for r in unit.crop_refs if r.endswith((".jpg", ".png"))]
        tray = db.get(Tray, unit.tray_id)
        jobs.append({"unit_id": unit.id, "product_id": unit.product_id, "tray_id": unit.tray_id, "roi": tray.roi,
                     "before": stills[0], "after": stills[-1]})
    return jobs

def job_failed(unit_id, reason):
    with transaction() as db:
        unit = get(db, TrayUnit, unit_id)
        if unit.reference_status != "PENDING":
            fail("No pending enrolment for this unit")
        unit.reference_status = "FAILED:" + reason[:60]
        audit(db, "vision-1", "REFERENCE_CROP_FAILED", {"unit_id": unit_id, "reason": reason[:200]})
        return {"unit_id": unit_id, "reference_status": unit.reference_status}

def add_reference(product_id, source, image_b64, box, quality, unit_id=None, teach_id=None):
    try:
        data = base64.b64decode(image_b64, validate=True)
    except ValueError:
        fail("Reference image is not valid base64")
    if not data or len(data) > MAX_IMAGE_BYTES:
        fail("Reference image missing or too large")
    suffix = _image_suffix(data)
    with transaction() as db:
        get(db, Product, product_id)
        if source == "RESTOCK_AUTO":
            unit = get(db, TrayUnit, unit_id) if unit_id else None
            if not unit or unit.reference_status != "PENDING" or unit.product_id != product_id:
                fail("Restock reference must match a pending enrolment of the same product")
            unit.reference_status = "DONE"
        elif source == "TEACH":
            session = get(db, TeachSession, teach_id) if teach_id else None
            if not session:
                fail("Teach reference needs a teach session")
            _expire(db, session, time.time())
            if session.status != "ACTIVE" or session.product_id != product_id:
                fail("Teach session is not active for this product")
            session.accepted += 1
        else:
            fail("Unknown reference source")
        name = f"ref-{product_id}-{uuid.uuid4().hex}{suffix}"
        (catalogue_root() / name).write_bytes(data)
        ref = ProductReference(product_id=product_id, unit_id=unit_id, teach_id=teach_id, source=source, file=name,
                               box=list(box) if box else None, quality=quality or {})
        db.add(ref)
        db.flush()
        audit(db, "vision-1", "REFERENCE_ADDED", {"reference_id": ref.id, "product_id": product_id, "source": source, "unit_id": unit_id, "teach_id": teach_id})
        return {"reference_id": ref.id, "file": name}

def set_reference_status(reference_id, status, reason, actor):
    with transaction() as db:
        ref = get(db, ProductReference, reference_id)
        ref.status, ref.updated = status, time.time()
        audit(db, actor, "REFERENCE_" + status, {"reference_id": reference_id, "product_id": ref.product_id, "reason": reason})
        return {"reference_id": reference_id, "status": status}

def reference_rows(db, product_id=None):
    q = select(ProductReference).order_by(ProductReference.created)
    if product_id:
        q = q.where(ProductReference.product_id == product_id)
    return [{"id": r.id, "product_id": r.product_id, "file": r.file, "status": r.status, "source": r.source,
             "unit_id": r.unit_id, "teach_id": r.teach_id, "box": r.box, "quality": r.quality, "created": r.created, "updated": r.updated}
            for r in db.scalars(q)]

def save_gallery_status(payload):
    with transaction() as db:
        for shelf in db.scalars(select(Shelf)):
            config = dict(shelf.camera_config or {})
            config["gallery_status"] = {**payload, "received": time.time()}
            shelf.camera_config = config
        return {"ok": True}
