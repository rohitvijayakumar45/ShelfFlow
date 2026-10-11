from vision.weight import Calibration, WeightProcessor
import pytest

def test_calibration_and_explicit_tare():
    c=Calibration.known_mass(1000,11000,1000,"v1")
    assert c.grams(6000)==500
    w=WeightProcessor(c,window=3,settle_ms=100)
    with pytest.raises(ValueError):w.tare(6000,False)
    assert c.zero_raw==1000
    with pytest.raises(ValueError):Calibration.known_mass(0,0,1000,"v1")

def test_stable_removal_does_not_tare():
    w=WeightProcessor(Calibration(0,10,"v"),window=3,settle_ms=100)
    for t in range(0,600,50):w.feed(10000,t)
    found=[]
    for t in range(600,1300,50):
        r=w.feed(8000,t)
        if r['event']:found.append(r['event'])
    assert len(found)==1
    assert found[0]['post_g']-found[0]['pre_g']==-200
    assert w.calibration.zero_raw==0

def test_disconnect_stuck_overload():
    w=WeightProcessor(Calibration(0,1,"v"),window=3,settle_ms=100,max_g=500)
    assert w.feed(None,0)['health']==['DISCONNECTED']
    assert 'OVERLOAD' in w.feed(600,50)['health']
    for t in range(100,11000,50):r=w.feed(100,t)
    assert 'STUCK_READING' in r['health']

def test_slow_drift_and_out_of_order_health():
    w=WeightProcessor(Calibration(0,10,"v"),window=3,settle_ms=100,hysteresis_g=8)
    for t in range(0,1000,50):w.feed(10000+(t%100),t)
    # Stable gradual small change never triggers stock-sized hysteresis; it must be flagged.
    for t in range(1000,70000,100):
        raw=10000+(min(6,t//10000))*10+(t%200)//100
        result=w.feed(raw,t)
    assert 'DRIFT' in result['health']
    assert w.feed(10000,1)['health']==['OUT_OF_ORDER']
