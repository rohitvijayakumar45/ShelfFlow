import json
from types import SimpleNamespace
import pytest
from tools.dataset.cli import split,metrics,validate
from vision.detector import YoloDetector,UnavailableDetector,MockDetector,OptionalPose

def test_grouped_split_no_session_leak(tmp_path):
    for i in range(10):
        for clip in range(2):
            folder=tmp_path/f's{i}'/f'c{clip}';folder.mkdir(parents=True);(folder/'session.json').write_text(json.dumps({'session_id':f's{i}'}))
    output=tmp_path/'split.json';split(SimpleNamespace(root=tmp_path,seed=42,output=output));data=json.loads(output.read_text())['splits']
    session_sets=[{str(__import__('pathlib').Path(p).parent) for p in paths} for paths in data.values()]
    assert all(not a&b for i,a in enumerate(session_sets) for b in session_sets[i+1:])

def test_annotation_validation_rejects_outside_box(tmp_path):
    (tmp_path/'ok.txt').write_text('0 0.5 0.5 0.2 0.2');validate(SimpleNamespace(root=tmp_path,classes=4))
    (tmp_path/'bad.txt').write_text('0 0.95 0.5 0.2 0.2')
    with pytest.raises(SystemExit):validate(SimpleNamespace(root=tmp_path,classes=4))

def test_metrics_separate_dimensions_and_no_fake_perfect_score(tmp_path):
    file=tmp_path/'rows.jsonl';out=tmp_path/'metrics.json';file.write_text(json.dumps({'actual_event':True,'observed':True,'pending':True,'confirmed':False,'identity_failure':True,'data_loss':2,'latency_ms':100}))
    metrics(SimpleNamespace(file=file,output=out));report=json.loads(out.read_text())
    assert report['sku_precision'] is None and report['pending_review_rate']==1 and report['data_loss']==2

def test_no_custom_weights_no_claimed_exact_sku(tmp_path):
    assert UnavailableDetector().status=='MODEL_UNAVAILABLE'
    assert MockDetector().status=='MOCK'
    with pytest.raises(FileNotFoundError):YoloDetector(tmp_path/'none.pt',{0:'oat'})
    with pytest.raises(ValueError):OptionalPose(full_body_verified=False)
