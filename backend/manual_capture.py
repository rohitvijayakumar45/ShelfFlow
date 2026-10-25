"""Durable capture queue. SAM runs on one background worker, outside request threads."""
import os
import threading
import time
import uuid

from sqlalchemy import select

from backend import service as svc
from backend.db import SessionLocal
from backend.models import ManualCapture, Product, ProductReference, Tray
from backend.references import catalogue_root

_stop = threading.Event()
_thread = None


def worker_running():
    return bool(_thread and _thread.is_alive() and not _stop.is_set())


def row_dict(row):
    return {column.key: getattr(row, column.key) for column in row.__mapper__.column_attrs}


def existing(db, key, product_id, tray_id):
    row = db.scalar(select(ManualCapture).where(ManualCapture.request_key == key))
    if row and (row.product_id != product_id or row.tray_id != tray_id):
        svc.fail('Capture request key belongs to another product or tray')
    return row


def snapshot(product_id, tray_id, key, reason, actor, fetch_original):
    import cv2
    import numpy as np
    with SessionLocal() as db:
        row = existing(db, key, product_id, tray_id)
        if row:
            return row_dict(row)
        svc.get(db, Product, product_id); svc.get(db, Tray, tray_id)
    started = time.monotonic()
    jpeg = fetch_original()  # Latest raw proxy frame, never a future YOLO/teach result.
    if not jpeg or len(jpeg) > 8_000_000:
        svc.fail('Camera image is missing or too large', 503)
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[0]*image.shape[1] > 8_000_000:
        svc.fail('Camera did not return a valid image', 503)
    with svc.transaction() as db:
        previous = existing(db, key, product_id, tray_id)
        if previous:
            return row_dict(previous)
        identifier = uuid.uuid4().hex
        name = f'capture-{identifier}-original.jpg'
        (catalogue_root()/name).write_bytes(jpeg)
        row = ManualCapture(id=identifier, request_key=key, product_id=product_id, tray_id=tray_id,
            original_file=name, width=image.shape[1], height=image.shape[0], details={
                'capture_ms': round((time.monotonic()-started)*1000), 'reason': reason,
                'source': 'RAW_CAMERA_PROXY', 'warnings': []})
        db.add(row); db.flush()
        svc.audit(db, actor, 'MANUAL_CAPTURE_SAVED', {'capture_id': row.id, 'product_id': product_id})
        return row_dict(row)


def segment(identifier, normalized_box, actor):
    with svc.transaction() as db:
        row = svc.get(db, ManualCapture, identifier)
        if row.status in ('APPROVED', 'DISCARDED', 'QUEUED', 'PROCESSING'):
            svc.fail('This capture cannot be segmented in its current state')
        x1, y1, x2, y2 = normalized_box
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            svc.fail('Draw a box inside the captured image')
        box = [int(x1*row.width), int(y1*row.height), min(row.width, int(x2*row.width)), min(row.height, int(y2*row.height))]
        if box[2]-box[0] < 4 or box[3]-box[1] < 4:
            svc.fail('Draw a larger product box (at least 4 pixels per side)')
        row.box, row.status, row.updated = box, 'QUEUED', time.time()
        row.revision += 1
        row.crop_file = row.plain_file = None
        row.details = {k:v for k,v in row.details.items() if k in ('capture_ms', 'source', 'reason')}
        svc.audit(db, actor, 'MANUAL_CAPTURE_SEGMENT_REQUESTED', {'capture_id': identifier, 'revision': row.revision, 'box': box})
        return row_dict(row)


def approve(identifier, revision, physical_confirmed, actor):
    if not physical_confirmed:
        svc.fail('Confirm that the preview contains the selected product')
    with svc.transaction() as db:
        row = svc.get(db, ManualCapture, identifier)
        if row.revision != revision:
            svc.fail('The crop changed; review the latest preview')
        if row.status == 'APPROVED':
            return row_dict(row)  # Double-click/retry cannot add duplicate references.
        if row.status != 'READY' or not row.crop_file or not row.plain_file:
            svc.fail('Wait for segmentation and review the crop before approval')
        ids = []
        for variant, file in [('tight_original', row.plain_file), ('sam_cutout', row.crop_file)]:
            if not (catalogue_root()/file).is_file():
                svc.fail('Crop file is unavailable; segment the original again')
            quality = {**row.details.get('quality', {}), 'capture_id': row.id, 'revision': revision,
                       'variant': variant, 'segmenter': 'sam2.1_t', 'staff_approved': True}
            ref = ProductReference(product_id=row.product_id, source='MANUAL_SAM', file=file, box=row.box, quality=quality)
            db.add(ref); db.flush(); ids.append(ref.id)
        row.reference_ids, row.status, row.updated = ids, 'APPROVED', time.time()
        svc.audit(db, actor, 'MANUAL_CAPTURE_APPROVED', {'capture_id': row.id, 'reference_ids': ids,
            'warnings_acknowledged': row.details.get('warnings', [])})
        return row_dict(row)


def discard(identifier, actor):
    with svc.transaction() as db:
        row = svc.get(db, ManualCapture, identifier)
        if row.status == 'APPROVED':
            svc.fail('This capture is already in the gallery; reject its references instead')
        row.status, row.updated = 'DISCARDED', time.time()
        svc.audit(db, actor, 'MANUAL_CAPTURE_DISCARDED', {'capture_id': identifier})
        return row_dict(row)


def process_next(get_segmenter):
    with svc.transaction() as db:
        row = db.scalar(select(ManualCapture).where(ManualCapture.status == 'QUEUED').order_by(ManualCapture.updated).limit(1))
        if not row:
            return False
        row.status, row.updated = 'PROCESSING', time.time()
        job = row_dict(row)
    try:
        import cv2
        image = cv2.imread(str(catalogue_root()/job['original_file']))
        if image is None:
            raise ValueError('Original image is unavailable')
        plain, cutout, details = get_segmenter()(image, job['box'])
        stem = f"capture-{job['id']}-r{job['revision']}"
        plain_file, crop_file = stem+'-plain.jpg', stem+'-sam.jpg'
        for file, image in [(plain_file, plain), (crop_file, cutout)]:
            if not cv2.imwrite(str(catalogue_root()/file), image, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                raise ValueError('Could not save the segmented crop')
        with svc.transaction() as db:
            row = svc.get(db, ManualCapture, job['id'])
            if row.status == 'PROCESSING' and row.revision == job['revision']:
                row.plain_file, row.crop_file = plain_file, crop_file
                row.details = {**row.details, **details}
                row.status, row.updated = 'READY', time.time()
    except Exception as exc:
        with svc.transaction() as db:
            row = svc.get(db, ManualCapture, job['id'])
            if row.status == 'PROCESSING' and row.revision == job['revision']:
                message = str(exc) if isinstance(exc, ValueError) else f'{type(exc).__name__}: segmentation failed. Original saved; retry.'
                row.details = {**row.details, 'error': message[:240]}
                row.status, row.updated = 'FAILED', time.time()
    return True


def start_worker():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    with svc.transaction() as db:
        for row in db.scalars(select(ManualCapture).where(ManualCapture.status == 'PROCESSING')):
            row.status = 'QUEUED'  # Durable recovery after interrupted inference.
    def loop():
        segmenter = None
        def get_segmenter():
            nonlocal segmenter
            if segmenter is None:
                from vision.manual_segment import ManualSegmenter
                from pathlib import Path
                segmenter = ManualSegmenter(Path(__file__).resolve().parents[1]/'models/sam2.1_t.pt',
                                             os.getenv('CAPTURE_SEGMENT_DEVICE', 'cuda:0'))
            return segmenter
        while not _stop.is_set():
            try:
                worked = process_next(get_segmenter)
            except Exception:
                worked = False  # A temporary DB error must not kill the worker.
            if not worked:
                _stop.wait(.25)
    _thread = threading.Thread(target=loop, name='manual-sam', daemon=True)
    _thread.start()


def stop_worker():
    _stop.set()
    if _thread:
        _thread.join(timeout=2)
