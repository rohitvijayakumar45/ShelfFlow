import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from tools.usb_bridge import EventStore
from vision.association import review_weight_candidates


def test_slow_backend_does_not_stop_serial_capture_or_ack_early(tmp_path, monkeypatch):
    import tools.usb_bridge as bridge
    started, release, observed, acked, stop = (threading.Event() for _ in range(5))
    writes = []
    failures = []
    class FakeSerial:
        in_waiting = 8192
        initial = True
        diagnostic = False
        def open(self): pass
        def close(self): pass
        def write(self, data):
            writes.append(data)
            if data.startswith(b'ack '): acked.set()
        def read(self, size):
            if stop.is_set(): raise bridge.serial.SerialException('test finished')
            if self.initial:
                self.initial = False
                state = {'boot_id': 'boot', 'monotonic_ms': 1000, 'queued': 1}
                return ('SF_STATE '+json.dumps(state)+'\nSF_EVENT '+json.dumps(raw())+'\n').encode()
            if started.is_set() and not self.diagnostic:
                self.diagnostic = True
                return b'DIAG RFID reader=R2 uid=43D51829 captured_ms=1001 dispatch_ms=1\n'
            time.sleep(.01)
            return b''
    class FakeClient:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
    def delayed_delivery(self, event, client, key):
        started.set()
        assert release.wait(5)
        return True
    original_record = bridge.record_diagnostic
    def record(path, line):
        original_record(path, line)
        if 'reader=R2' in line: observed.set()
    monkeypatch.setattr(bridge.serial, 'Serial', lambda **kwargs: FakeSerial())
    monkeypatch.setattr(bridge.httpx, 'Client', FakeClient)
    monkeypatch.setattr(bridge.EventStore, 'deliver', delayed_delivery)
    monkeypatch.setattr(bridge, 'record_diagnostic', record)
    monkeypatch.setattr(bridge, 'load_dotenv', lambda *args, **kwargs: None)
    monkeypatch.setenv('SENSOR_DEVICE_KEY', 'test')
    monkeypatch.setenv('USB_EVENT_ROOT', str(tmp_path))
    def run():
        try: bridge.run(SimpleNamespace(env='', port='test'))
        except bridge.serial.SerialException: pass
        except BaseException as exc: failures.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert started.wait(2)
        assert observed.wait(2), 'Serial receiver stalled while backend was blocked'
        assert not any(data.startswith(b'ack ') for data in writes)
        release.set()
        assert acked.wait(2), 'ACK should follow successful backend commit'
    finally:
        release.set(); stop.set(); thread.join(5)
    assert not thread.is_alive()
    assert not failures


def test_diagnostic_log_does_not_include_device_payloads_or_credentials(tmp_path):
    from tools.usb_bridge import record_diagnostic
    path = tmp_path / 'diagnostics.jsonl'
    record_diagnostic(path, 'SF_EVENT '+json.dumps(raw()))
    record_diagnostic(path, 'SF_HEALTH secret')
    record_diagnostic(path, 'DIAG RFID reader=R1 uid=43D51829 captured_ms=3 dispatch_ms=1')
    rows = path.read_text().splitlines()
    assert len(rows) == 1
    assert 'never-forward' not in rows[0] and 'SF_EVENT' not in rows[0]


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
