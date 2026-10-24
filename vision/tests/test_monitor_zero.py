import json
from types import SimpleNamespace

import pytest

from tools.usb_bridge import consume_monitor_command


@pytest.mark.parametrize('action,boot,age,expected', [
    ('tareempty', 'current', 0, True),
    ('tareempty', 'old', 0, False),
    ('tareempty', 'current', 11, False),
    ('tareempty', 'current', -1, False),
    ('calibrate', 'current', 0, False),
    ('cal empty', 'current', 0, True),
    ('cal a', 'current', 0, True),
    ('cal c', 'current', 0, True),
    ('cal b', 'current', 0, False),  # Cell B was removed from the hardware.
    ('cal save', 'old', 0, False),
    ('calc b off', 'current', 0, False),
    ('calc b on', 'current', 0, False),
    ('qc start', 'current', 0, True),
    ('qc next', 'current', 0, True),
    ('qc cancel', 'current', 0, True),
    ('qc next', 'old', 0, False),
    ('qc apply', 'current', 0, False),
])
def test_monitor_zero_is_one_shot_and_scoped(tmp_path, monkeypatch, action, boot, age, expected):
    monkeypatch.setattr('tools.usb_bridge.time.time', lambda: 100)
    path = tmp_path / 'monitor-zero-request.json'
    path.write_text(json.dumps({'action': action, 'boot_id': boot, 'created_s': 100-age}))
    writes = []
    connection = SimpleNamespace(write=writes.append)
    consume_monitor_command(tmp_path, connection, {'boot_id': 'current'})
    consume_monitor_command(tmp_path, connection, {'boot_id': 'current'})
    assert writes == ([('\n'+action+'\nusbstatus\n').encode()] if expected else [])
    assert not path.exists()


def test_raw_channels_visible_without_calibration_and_missing_b(tmp_path):
    from tools.weight_monitor import Samples
    log = tmp_path / 'readings.jsonl'
    log.write_text(json.dumps({'received_s': 100, 'line':
        'RAW sample_ms=100 raw_A=412345 age_A=1 valid_A=1 raw_B=-2147483648 age_B=100 valid_B=0 raw_C=-57120 age_C=2 valid_C=1'})+'\n')
    samples = Samples(log)
    rows = samples.read()
    assert len(rows) == 1
    assert rows[0]['raw_A'] == 412345 and rows[0]['raw_C'] == -57120
    assert rows[0]['valid_B'] == 0
    assert 'net_g' not in rows[0]  # No invented weights before calibration.
    assert len(samples.read()) == 1  # Incremental read must not duplicate samples.
