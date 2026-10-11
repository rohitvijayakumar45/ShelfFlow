"""Dataset capture, clip-level split, extraction, screening, validation, replay, metrics."""
import argparse
import json
import csv
import random
import time
import hashlib
from pathlib import Path

def capture(a):
    from vision.capture import Capture
    c=Capture(a.url,a.key);root=Path(a.output);root.mkdir(parents=True,exist_ok=True)
    metadata={'session_id':a.session,'scenario':a.scenario,'camera':'OV3660','original_jpeg':True,'frames':[]}
    try:
        for _ in range(a.frames):
            f=c.poll();name=f'{f.boot_id}-{f.sequence}.jpg';(root/name).write_bytes(f.jpeg)
            metadata['frames'].append({k:v for k,v in vars(f).items() if k!='jpeg'}|{'file':name})
            time.sleep(a.interval)
    finally:
        c.client.close();(root/'session.json').write_text(json.dumps(metadata,indent=2))

def mjpeg(a):
    import httpx
    root=Path(a.output);root.mkdir(parents=True,exist_ok=True);start=time.monotonic();count=0;buffer=b''
    with httpx.stream('GET',a.url,headers={'X-Camera-Key':a.key},timeout=5) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            buffer+=chunk
            while b'\xff\xd8' in buffer and b'\xff\xd9' in buffer:
                begin=buffer.find(b'\xff\xd8');end=buffer.find(b'\xff\xd9',begin)+2
                (root/f'{count:06}.jpg').write_bytes(buffer[begin:end]);count+=1;buffer=buffer[end:]
            if len(buffer)>8_000_000:raise ValueError('Malformed or oversized MJPEG frame')
            if time.monotonic()-start>=a.seconds:break
    (root/'session.json').write_text(json.dumps({'session_id':a.session,'scenario':a.scenario,'transport':'MJPEG','frames':count,'timing':'arrival only; source headers required for fusion'}))

def split(a):
    root=Path(a.root);groups={}
    for file in root.rglob('session.json'):
        metadata=json.loads(file.read_text());groups.setdefault(metadata['session_id'],[]).append(str(file.parent.resolve()))
    ids=sorted(groups);random.Random(a.seed).shuffle(ids)
    if len(ids)<3:raise ValueError('At least three independent recording sessions required')
    n=len(ids);nval=max(1,round(n*.15));ntest=max(1,round(n*.15));ntrain=n-nval-ntest
    result={name:[folder for id_ in selected for folder in groups[id_]] for name,selected in [('train',ids[:ntrain]),('val',ids[ntrain:ntrain+nval]),('test',ids[ntrain+nval:])]}
    Path(a.output).write_text(json.dumps({'seed':a.seed,'grouping':'recording_session','splits':result},indent=2))

def extract(a):
    import shutil
    manifest=json.loads(Path(a.manifest).read_text());out=Path(a.output)
    for split_,folders in manifest['splits'].items():
        target=out/'images'/split_;target.mkdir(parents=True,exist_ok=True)
        for folder in folders:
            files=sorted(Path(folder).glob('*.jpg'))
            if not files:
                import cv2
                for video in Path(folder).glob('*.mp4'):
                    cap=cv2.VideoCapture(str(video));i=0
                    while True:
                        ok,frame=cap.read()
                        if not ok:break
                        if i%a.stride==0:cv2.imwrite(str(target/f'{hashlib.sha256(str(video).encode()).hexdigest()[:12]}-{i}.jpg'),frame)
                        i+=1
                    cap.release()
            for file in files[::a.stride]:
                name=hashlib.sha256(str(file).encode()).hexdigest()[:12]+'-'+file.name
                shutil.copyfile(file,target/name)
    print('Extracted by preassigned session split. Video extraction re-encodes frames; original JPEG capture is preferred.')

def screen(a):
    import cv2
    exact={};hashes=[];pairs=[]
    for file in sorted(Path(a.root).rglob('*.jpg')):
        digest=hashlib.sha256(file.read_bytes()).hexdigest()
        if digest in exact:pairs.append({'a':exact[digest],'b':str(file),'type':'EXACT'})
        exact[digest]=str(file)
        img=cv2.imread(str(file),cv2.IMREAD_GRAYSCALE)
        if img is None:continue
        small=cv2.resize(img,(9,8));bits=(small[:,1:]>small[:,:-1]).flatten();value=sum(int(v)<<i for i,v in enumerate(bits))
        for old,path in hashes:
            distance=(value^old).bit_count()
            if distance<=a.distance:pairs.append({'a':path,'b':str(file),'type':'NEAR','distance':distance})
        hashes.append((value,str(file)))
    Path(a.output).write_text(json.dumps(pairs,indent=2));print(f'{len(pairs)} candidate pairs; manual inspection required, no automatic deletion')

def validate(a):
    root=Path(a.root);errors=[];labels=0
    for path in root.rglob('*.txt'):
        for line_number,line in enumerate(path.read_text().splitlines(),1):
            try:
                parts=line.split();assert len(parts)==5
                cls=int(parts[0]);x,y,w,h=map(float,parts[1:]);assert 0<=cls<a.classes
                assert 0<=x<=1 and 0<=y<=1 and 0<w<=1 and 0<h<=1
                assert x-w/2>=-1e-6 and x+w/2<=1.000001 and y-h/2>=-1e-6 and y+h/2<=1.000001
                labels+=1
            except (AssertionError,ValueError):errors.append(f'{path}:{line_number}: invalid detection label')
    print(json.dumps({'labels':labels,'errors':errors},indent=2))
    if errors:raise SystemExit(1)

def weight_trace(a):
    import paho.mqtt.client as mqtt
    import os
    with open(a.output,'w',newline='') as file:
        writer=csv.writer(file);writer.writerow(['receive_wall','event_id','device_id','boot_id','sequence','capture_start','capture_end','calibration','weight','health'])
        c=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2);c.username_pw_set(os.environ['MQTT_USERNAME'],os.environ['MQTT_PASSWORD'])
        def on_message(client,userdata,msg):
            data=json.loads(msg.payload);writer.writerow([time.time(),data['event_id'],data['device_id'],data['boot_id'],data['sequence_number'],data['capture_start'],data['capture_end'],data['calibration_version'],json.dumps(data.get('weight')),json.dumps(data['sensor_health'])]);file.flush()
        c.on_message=on_message;c.connect(a.host,1883);c.subscribe('shelfflow/devices/sensor-1/events',qos=1);c.loop_start();time.sleep(a.seconds);c.loop_stop();c.disconnect()

def replay(a):
    import httpx
    from backend.schemas import DeviceEvent
    with httpx.Client(base_url=a.backend,headers={'X-Device-Key':a.key},timeout=10) as client:
        for line in Path(a.file).read_text().splitlines():
            event=DeviceEvent.model_validate_json(line)
            # Preserve original capture times: late/offline playback MUST remain review-first.
            r=client.post(f'/devices/{event.device_id}/events',json=event.model_dump());r.raise_for_status();print(r.json())

def metrics(a):
    rows=[json.loads(line) for line in Path(a.file).read_text().splitlines()];n=len(rows)
    def ratio(num,den):return num/den if den else None
    accepted=[r for r in rows if r.get('confirmed')];truth=[r for r in rows if r.get('actual_event')]
    tp=sum(r.get('sku_correct',False) for r in accepted);false=sum(not r.get('charge_correct',False) for r in accepted)
    latency=sorted(r['latency_ms'] for r in rows if r.get('latency_ms') is not None)
    out={'sample_count':n,'sku_precision':ratio(tp,len(accepted)),'sku_recall':ratio(tp,len(truth)), 'quantity_accuracy':ratio(sum(r.get('quantity_correct',False) for r in accepted),len(accepted)), 'ownership_accuracy':ratio(sum(r.get('owner_correct',False) for r in accepted),len(accepted)), 'false_charge_rate':ratio(false,len(accepted)), 'missed_events':sum(r.get('actual_event',False) and not r.get('observed',False) for r in rows), 'pending_review_rate':ratio(sum(r.get('pending',False) for r in rows),n), 'identity_failures':sum(r.get('identity_failure',False) for r in rows),'data_loss':sum(r.get('data_loss',0) for r in rows),'latency_ms':{f'p{p}':latency[min(len(latency)-1,int((len(latency)-1)*p/100))] if latency else None for p in (50,95,99)}, 'source':str(a.file),'note':'Per-event SKU summary, not box-level mAP. Use YOLO val for detector box metrics. Null = no denominator.'}
    Path(a.output).write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))

def train(a):
    from ultralytics import YOLO
    model=YOLO(a.model)
    if a.action=='train':model.train(data=a.data,epochs=a.epochs,imgsz=a.imgsz,device=a.device,seed=42)
    elif a.action=='evaluate':model.val(data=a.data,split='test',device=a.device,imgsz=a.imgsz)
    else:model.export(format=a.format,device=a.device)

def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('capture');q.add_argument('url');q.add_argument('--key',required=True);q.add_argument('--session',required=True);q.add_argument('--scenario',required=True);q.add_argument('--frames',type=int,default=200);q.add_argument('--interval',type=float,default=.1);q.add_argument('--output',required=True);q.set_defaults(fn=capture)
    q=sub.add_parser('mjpeg');q.add_argument('url');q.add_argument('--key',required=True);q.add_argument('--session',required=True);q.add_argument('--scenario',required=True);q.add_argument('--seconds',type=int,default=60);q.add_argument('--output',required=True);q.set_defaults(fn=mjpeg)
    q=sub.add_parser('split');q.add_argument('root');q.add_argument('--seed',type=int,default=42);q.add_argument('--output',required=True);q.set_defaults(fn=split)
    q=sub.add_parser('extract');q.add_argument('manifest');q.add_argument('--stride',type=int,default=5);q.add_argument('--output',required=True);q.set_defaults(fn=extract)
    q=sub.add_parser('screen');q.add_argument('root');q.add_argument('--distance',type=int,default=5);q.add_argument('--output',required=True);q.set_defaults(fn=screen)
    q=sub.add_parser('validate');q.add_argument('root');q.add_argument('--classes',type=int,default=4);q.set_defaults(fn=validate)
    q=sub.add_parser('weights');q.add_argument('--host',default='localhost');q.add_argument('--seconds',type=int,default=60);q.add_argument('--output',required=True);q.set_defaults(fn=weight_trace)
    q=sub.add_parser('replay');q.add_argument('file');q.add_argument('--key',required=True);q.add_argument('--backend',default='http://127.0.0.1:8000');q.set_defaults(fn=replay)
    q=sub.add_parser('metrics');q.add_argument('file');q.add_argument('--output',required=True);q.set_defaults(fn=metrics)
    q=sub.add_parser('model');q.add_argument('action',choices=['train','evaluate','export']);q.add_argument('--model',default='yolo26n.pt');q.add_argument('--data',default='tools/dataset/data.example.yaml');q.add_argument('--device',default='cpu');q.add_argument('--epochs',type=int,default=100);q.add_argument('--imgsz',type=int,default=960);q.add_argument('--format',default='onnx');q.set_defaults(fn=train)
    a=p.parse_args();a.fn(a)

if __name__=='__main__':main()
