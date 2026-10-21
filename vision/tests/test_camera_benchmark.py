from tools.camera_benchmark import frame_age_ms, summarize


def test_latency_keeps_clock_uncertainty_and_rejects_reboot():
    clock = {'offset_s': 100, 'uncertainty_s': .1, 'boot_id': 'boot-a'}
    headers = {'x-boot-id': 'boot-a', 'x-capture-us': '5000000'}
    assert frame_age_ms(clock, headers, 105.25) == 250
    assert frame_age_ms(clock, headers, 104.95) == -50
    headers['x-boot-id'] = 'boot-b'
    assert frame_age_ms(clock, headers, 105.25) is None


def test_fps_includes_outages_and_reports_missing_latency():
    records = [{'age_ms': None, 'bytes': 6000} for _ in range(10)]
    summary = summarize(records, 20, 15)
    assert summary['fps'] == .5
    assert summary['max_gap_s'] == 15
    assert summary['p95_age_ms'] is None
    assert summarize([], 20, 20)['fps'] == 0
