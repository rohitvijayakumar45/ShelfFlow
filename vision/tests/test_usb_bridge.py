import json

import httpx
import pytest

from tools.usb_bridge import EventStore
from vision.association import review_weight_candidates


def raw():
    return {'event_id':'sensor-1-boot-7','device_id':'sensor-1','boot_id':'boot','monotonic_ms':1000,
            'capture_start':0,'capture_end':0,'clock_synced':False,'clock_uncertainty_ms':1000,'device_key':'never-forward'}


def test_freezes_normalized_event_across_retries(tmp_path):
    store=EventStore(tmp_path);store.offsets['boot']=100
    first=store.prepare(raw())
    store.offsets['boot']=9999
    assert store.prepare(raw())==first
    assert first['capture_end']==101 and not first['clock_synced']
    assert 'device_key' not in first


@pytest.mark.parametrize('ack', [{'durable':False,'ack':'sensor-1-boot-7'}, {'durable':True,'ack':'other'}])
def test_never_delivers_on_wrong_or_nondurable_ack(tmp_path,ack):
    store=EventStore(tmp_path)
    with httpx.Client(base_url='http://test',transport=httpx.MockTransport(lambda r:httpx.Response(200,json=ack))) as client:
        assert not store.deliver(store.prepare(raw()),client,'test-key')
    assert not (tmp_path/'delivered').exists()


def test_matching_backend_ack_publishes_sensor_file(tmp_path):
    store=EventStore(tmp_path)
    def reply(request):
        assert request.headers['X-Device-Key']=='test-key'
        assert 'device_key' not in json.loads(request.content)
        return httpx.Response(200,json={'durable':True,'ack':'sensor-1-boot-7'})
    with httpx.Client(base_url='http://test',transport=httpx.MockTransport(reply)) as client:
        assert store.deliver(store.prepare(raw()),client,'test-key')
    assert (tmp_path/'delivered/sensor-1-boot-7.json').exists()


def test_changed_items_do_not_claim_ownership():
    result=review_weight_candidates(['vicks','sanitizer'],['sanitizer'],'clip','PICKUP')
    assert len(result)==1 and result[0].product_id=='vicks'
    assert result[0].sku and not result[0].identity and not result[0].trajectory
    assert not result[0].marker_ids


def test_unknown_weight_change_is_reviewable_action():
    result=review_weight_candidates(['sanitizer'],['sanitizer'],'clip','RETURN')
    assert result[0].product_id=='UNKNOWN' and result[0].action=='RETURN' and not result[0].sku
