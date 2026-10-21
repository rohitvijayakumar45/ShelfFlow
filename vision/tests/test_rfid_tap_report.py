import pytest

from tools.rfid_tap_test import summarize


def recordings():
    rows = [{'received_s': n, 'line': f'DIAG RFID reader={reader} uid=43D51829 captured_ms={n * 1000} dispatch_ms=12'}
            for reader in ('R1', 'R2') for n in range(7)]
    rows.append({'received_s': 15, 'line': 'DIAG RFID_QUEUE durable=14/256 ram=0 scan_gap_max_ms=80/81 historical_overflow=0'})
    return rows


def test_seven_taps_per_reader_with_healthy_queue():
    report = summarize(recordings(), {'R1': 7, 'R2': 7})
    assert report['passed']
    assert report['captured_taps'] == {'R1': 7, 'R2': 7}
    assert report['max_dispatch_ms'] == 12


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'write_failure', 'no_queue_health', 'historical_loss'])
def test_count_or_storage_failures_cannot_pass(change):
    rows = recordings()
    if change == 'missing':
        del rows[0]
    elif change == 'duplicate':
        rows.append(rows[0])
    elif change == 'write_failure':
        rows.append({'received_s': 16, 'line': 'RFID_PERSIST_FAILED: tap observed but not durable'})
    elif change == 'no_queue_health':
        rows.pop()
    else:
        rows[-1]['line'] = rows[-1]['line'].replace('historical_overflow=0', 'historical_overflow=8')
    assert not summarize(rows, {'R1': 7, 'R2': 7})['passed']
