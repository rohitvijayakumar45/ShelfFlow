import uuid

import cv2
import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend import manual_capture as captures
from backend import main
from backend.db import SessionLocal
from backend.models import ManualCapture, ProductReference, TeachSession
from vision.manual_segment import make_crops


@pytest.fixture(autouse=True)
def catalogue(tmp_path, monkeypatch):
    monkeypatch.setenv('EVIDENCE_ROOT', str(tmp_path))


def jpeg():
    image = np.full((120, 160, 3), 90, np.uint8)  # Deliberately blurry: capture must still save it.
    image[25:100, 40:110] = (30, 160, 50)
    return cv2.imencode('.jpg', image)[1].tobytes()


def saved(key=None):
    return captures.snapshot('oat', 'tray-a', key or str(uuid.uuid4()), 'manual test', 'staff', jpeg)


def segmenter():
    def run(image, box):
        mask = np.zeros(image.shape[:2], np.uint8); mask[30:95, 45:105] = 1
        return make_crops(image, mask, box)
    return run


def ready():
    row = saved()
    row = captures.segment(row['id'], [.1, .1, .9, .9], 'staff')
    assert captures.process_next(segmenter)
    with SessionLocal() as db:
        return captures.row_dict(db.get(ManualCapture, row['id']))


def test_snapshot_ignores_teach_detector_and_quality_but_is_idempotent():
    row = saved('same-key')
    assert row['status'] == 'CAPTURED' and row['width'] == 160
    retry = captures.snapshot('oat', 'tray-a', 'same-key', 'manual test', 'staff', lambda: pytest.fail('Must reuse original'))
    assert retry['id'] == row['id']
    with SessionLocal() as db:
        assert list(db.scalars(select(ProductReference))) == []
        assert list(db.scalars(select(TeachSession))) == []
    with pytest.raises(HTTPException):
        captures.snapshot('coffee', 'tray-a', 'same-key', 'test', 'staff', jpeg)


def test_sam_preview_does_not_enter_gallery_until_approved_and_retries_do_not_duplicate():
    row = ready()
    assert row['status'] == 'READY' and row['details']['segmenter'] == 'sam2.1_t'
    with SessionLocal() as db:
        assert list(db.scalars(select(ProductReference))) == []
    with pytest.raises(HTTPException):
        captures.approve(row['id'], row['revision'], False, 'staff')
    approved = captures.approve(row['id'], row['revision'], True, 'staff')
    assert len(approved['reference_ids']) == 2
    assert captures.approve(row['id'], row['revision'], True, 'staff')['reference_ids'] == approved['reference_ids']
    with SessionLocal() as db:
        refs = list(db.scalars(select(ProductReference)))
        assert len(refs) == 2 and {r.quality['variant'] for r in refs} == {'tight_original', 'sam_cutout'}
        assert all(r.product_id == 'oat' and r.source == 'MANUAL_SAM' for r in refs)


def test_changed_crop_requires_review_of_latest_revision():
    row = ready()
    next_row = captures.segment(row['id'], [.2, .2, .8, .85], 'staff')
    assert next_row['revision'] == row['revision']+1
    with pytest.raises(HTTPException):
        captures.approve(row['id'], row['revision'], True, 'staff')
    with pytest.raises(HTTPException):
        captures.approve(row['id'], next_row['revision'], True, 'staff')  # Still queued.


@pytest.mark.parametrize('box', [[.5,.1,.4,.9], [-.1,0,1,1], [0,0,0.001,1], [0,0,float('nan'),1]])
def test_invalid_selection_does_not_destroy_original(box):
    row = saved()
    with pytest.raises(HTTPException):
        captures.segment(row['id'], box, 'staff')
    assert (captures.catalogue_root()/row['original_file']).is_file()


def test_failure_and_discard_preserve_original_and_cannot_add_reference():
    row = saved(); captures.segment(row['id'], [0,0,1,1], 'staff')
    def broken():
        raise ValueError('No mask')
    captures.process_next(broken)
    with SessionLocal() as db:
        result = db.get(ManualCapture, row['id'])
        assert result.status == 'FAILED' and result.details['error'] == 'No mask'
    captures.segment(row['id'], [0,0,1,1], 'staff')
    def discard_during_inference():
        captures.discard(row['id'], 'staff')
        return segmenter()
    captures.process_next(discard_during_inference)
    with SessionLocal() as db:
        assert db.get(ManualCapture, row['id']).status == 'DISCARDED'
        assert list(db.scalars(select(ProductReference))) == []


def test_sam_mask_is_limited_to_operator_selection_and_empty_mask_rejected():
    image = np.full((100,100,3), 230, np.uint8)
    mask = np.ones((100,100), np.uint8)
    plain, cutout, details = make_crops(image, mask, [20,30,80,90])
    assert details['bounds'] == [20,30,80,90] and plain.shape == (60,60,3)
    assert 'BLURRED' in details['warnings']  # Warning only, not rejection.
    with pytest.raises(ValueError):
        make_crops(image, mask*0, [20,30,80,90])


def test_staff_only_endpoints_capture_original_and_expose_queue(monkeypatch):
    monkeypatch.setattr(main, 'raw_camera_jpeg', jpeg)
    with TestClient(main.app) as client:
        body = {'product_id':'oat','tray_id':'tray-a','idempotency_key':'api-test','reason':'save original'}
        assert client.post('/staff/manual-captures', json=body).status_code == 403
        token = client.post('/auth/staff', json={'password':'local-demo-staff'}).json()['token']
        headers = {'Authorization':'Bearer '+token}
        result = client.post('/staff/manual-captures', headers=headers, json=body)
        assert result.status_code == 200 and result.json()['status'] == 'CAPTURED'
        rows = client.get('/staff/manual-captures', headers=headers).json()['captures']
        assert len(rows) == 1
        assert client.get('/staff/catalogue-file/'+rows[0]['original_file'], headers=headers).status_code == 200
        assert client.get('/staff/manual-captures/'+rows[0]['id']).status_code == 403
        assert client.post('/staff/manual-captures/'+rows[0]['id']+'/segment', headers=headers,
                           json={'box':[0,0,1,1]}).status_code == 503  # Explicit failure, never wait forever.
