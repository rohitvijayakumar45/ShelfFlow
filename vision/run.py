"""Native laptop pipeline: continuous JPEG + markers + crop detections + MQTT weights.

All real interactions are review-first. Motion alone collects evidence (including
zero-net exchanges). An RFID read marks capture; it never grants authorization.
"""
import argparse
import json
import os
import time
import uuid
import threading
from collections import deque
from pathlib import Path
import cv2
import numpy as np
import httpx
import paho.mqtt.client as mqtt
from backend.schemas import DeviceEvent, Weight, Health
from vision.capture import Capture
from vision.markers import decode
from vision.detector import YoloDetector, UnavailableDetector
from vision.association import ReviewFirstAssociator, roi_pixels

def run(args):
    config=json.loads(Path(args.config).read_text())
    capture=Capture(args.camera,args.camera_key,args.mode,transport=args.transport)
    detectors={tray: YoloDetector(args.weights,{int(k):v for k,v in config['sku_map'].items()},args.device) if args.weights else UnavailableDetector() for tray in config['trays']}
    markers=[]; previous={}; active={}; raw_events=deque(maxlen=1000); lock=threading.Lock(); overflow=[0];barriers=deque(maxlen=20);rfid_reads=deque(maxlen=100)
    boot=uuid.uuid4().hex; seq=0; last_frame=None; associations=ReviewFirstAssociator()
    api=httpx.Client(base_url=args.backend,headers={'X-Device-Key':args.device_key},timeout=5)
    mqtt_client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id='shelfflow-vision')
    mqtt_client.username_pw_set(os.getenv('VISION_MQTT_USERNAME','vision-1'),os.getenv('VISION_MQTT_PASSWORD','change-me'))
    if os.getenv('MQTT_CA'):mqtt_client.tls_set(os.environ['MQTT_CA'])
    mqtt_client.reconnect_delay_set(1,30)
    def connected(c,_,flags,rc,props):
        if rc==0:
            c.subscribe('shelfflow/devices/sensor-1/events',qos=1)
            c.subscribe('shelfflow/devices/vision-1/commands',qos=1)
    def message(c,_,msg):
        try:
            data=json.loads(msg.payload);data.pop('device_key',None)
            if msg.topic.endswith('/commands'):
                if data.get('action')=='FLUSH':
                    with lock:barriers.append(data['barrier_id'])
                return
            event=DeviceEvent(**data)
            with lock:
                if len(raw_events)==raw_events.maxlen:overflow[0]+=1
                raw_events.append(event)
        except Exception:overflow[0]+=1
    mqtt_client.on_connect,mqtt_client.on_message=connected,message
    mqtt_client.connect(args.mqtt,int(os.getenv('MQTT_PORT','1883')),30);mqtt_client.loop_start()
    spool=Path(args.spool);spool.mkdir(parents=True,exist_ok=True)
    high=[None]
    def set_high(enabled):
        # Mode B switches only on state change; a failed switch is recorded, never fatal to capture.
        if args.mode!='B' or high[0]==enabled:return
        try:capture.high_resolution(enabled);high[0]=enabled
        except Exception:
            for collector in active.values():collector['sources'].add('RESOLUTION_SWITCH_FAILED')
    last_heartbeat=0;cooldown={}; frame_detections={}; last_gap=0;blur=False;last_config=0
    def trigger(tray,source,weight=None):
        now=time.monotonic()
        if tray in active:
            if weight:active[tray]['weight']=weight
            active[tray]['sources'].add(source)
            active[tray]['finish']=min(now+config.get('post_seconds',2),active[tray]['deadline'])
            return
        active[tray]={'id':uuid.uuid4().hex,'start':now-config.get('pre_seconds',2),'finish':now+config.get('post_seconds',2),'deadline':now+config.get('max_collection_seconds',15),'weight':weight,'sources':{source},'candidates':[]}
        set_high(True)
    def spool_event(event):
        # Crash-safe laptop queue: write once per event, not every frame.
        if len(list(spool.glob('*.json'))) >= 1000:
            overflow[0]+=1
            (spool/'OVERFLOW.txt').write_text('Evidence queue overflow. Assisted reconciliation required.')
            return
        tmp=spool/(event.event_id+'.tmp');tmp.write_text(event.model_dump_json());tmp.replace(spool/(event.event_id+'.json'))
    try:
        while True:
            camera_failed=False
            if time.monotonic()-last_config>10:
                try:
                    remote=api.get('/devices/vision-1/config');remote.raise_for_status()
                    for tray,settings in remote.json()['trays'].items():
                        if tray in config['trays']:config['trays'][tray]['roi']=settings['roi']
                    last_config=time.monotonic()
                except Exception:
                    # Keep capture alive; stale configuration is explicitly part of review-first operation.
                    last_config=time.monotonic()
            try:
                frame=capture.poll()
                image=cv2.imdecode(np.frombuffer(frame.jpeg,np.uint8),cv2.IMREAD_COLOR)
                if image is None:raise ValueError('JPEG decode failed')
                markers=decode(image)  # Full detail marker decode, before crop resizing.
                blur=float(cv2.Laplacian(cv2.cvtColor(image,cv2.COLOR_BGR2GRAY),cv2.CV_64F).var())<config.get('blur_threshold',60)
                last_gap=0 if last_frame is None else (frame.received_monotonic-last_frame.received_monotonic)*1000
                last_frame=frame
                for tray,settings in config['trays'].items():
                    x1,y1,x2,y2=roi_pixels(settings['roi'],image.shape[1],image.shape[0]);crop=image[y1:y2,x1:x2]
                    gray=cv2.resize(cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY),(160,120))
                    moving=tray in previous and np.mean(cv2.absdiff(gray,previous[tray]))>settings.get('motion_threshold',8)
                    previous[tray]=gray
                    detections=detectors[tray].detect(crop);frame_detections[tray]=detections
                    if moving:trigger(tray,'MOTION')
                    if tray in active and detections:
                        active[tray]['candidates']=associations.observe(detections,markers,active[tray]['id'])
            except Exception as exc:
                camera_failed=True
                time.sleep(0.1)
            with lock:
                incoming=list(raw_events);raw_events.clear()
            for event in incoming:
                if event.tray_id not in config['trays']:continue
                if event.event_type=='RFID':
                    for ref in event.evidence_refs:
                        if ref.startswith('RFID:'):rfid_reads.append((time.monotonic(),ref[5:]))
                    trigger(event.tray_id,'RFID')
                elif event.event_type=='WEIGHT' and event.weight and abs(event.weight.post_g-event.weight.pre_g)>=config['trays'][event.tray_id].get('hysteresis_g',8):
                    trigger(event.tray_id,'WEIGHT',event.weight)
                    active[event.tray_id]['sensor_event']=event
            now=time.monotonic()
            for tray,collector in list(active.items()):
                if now<collector['finish']:continue
                seq+=1;cooldown[tray]=now
                refs=capture.buffer.preserve(args.evidence,collector['id'],collector['start'],now)
                if collector.get('sensor_event'):refs.append('SENSOR_EVENT:'+collector['sensor_event'].event_id)
                f=last_frame;sensor=collector.get('sensor_event');weight=collector['weight']
                candidates=collector['candidates']
                uids={uid for timestamp,uid in rfid_reads if collector['start']<=timestamp<=now}
                flags=[]
                if len(uids)>1:flags.append('MULTIPLE_RFID_OBSERVATIONS')
                elif len(uids)==1:
                    uid=next(iter(uids))
                    for candidate in candidates:candidate.rfid_uid=uid # Corroboration only; identity remains unproven.
                if now>=collector['deadline']:flags.append('COLLECTION_LIMIT_REACHED')
                with capture.buffer.lock:
                    if not capture.buffer.frames or capture.buffer.frames[0].received_monotonic>collector['start']+0.5:flags.append('PRE_EVIDENCE_TRUNCATED')
                if weight:
                    for c in candidates:c.action='PICKUP' if weight.post_g<weight.pre_g else 'RETURN'
                wall=time.time()
                event=DeviceEvent(event_id=collector['id'],event_type='INTERACTION',device_id='vision-1',boot_id=boot,sequence_number=seq,tray_id=tray,capture_start=f.capture_wall-config.get('pre_seconds',2) if f and f.capture_wall else wall-4,capture_end=f.capture_wall if f and f.capture_wall else wall,monotonic_ms=int(now*1000),clock_synced=bool(f and sensor and sensor.clock_synced),clock_uncertainty_ms=max(f.clock_uncertainty_ms if f else 9999,sensor.clock_uncertainty_ms if sensor else 9999),calibration_version=sensor.calibration_version if sensor else 'UNSET',weight=weight,candidates=candidates,evidence_refs=refs,model_status=detectors[tray].status,sensor_health=Health(ok=not camera_failed and bool(sensor and sensor.sensor_health.ok),flags=flags+(['CAMERA_FAILURE'] if camera_failed else [])+(sensor.sensor_health.flags if sensor else ['NO_MATCHING_WEIGHT']),queue_overflow=overflow[0]),frame_age_ms=max(0,(wall-f.capture_wall)*1000) if f and f.capture_wall else 9999,frame_gap_ms=last_gap,blur=blur)
                spool_event(event);del active[tray]
            if not active:set_high(False)
            if now-last_heartbeat>10:
                seq+=1;wall=time.time();spool_event(DeviceEvent(event_id=uuid.uuid4().hex,event_type='HEALTH',device_id='vision-1',boot_id=boot,sequence_number=seq,capture_start=wall,capture_end=wall,monotonic_ms=int(now*1000),clock_synced=False,clock_uncertainty_ms=9999,calibration_version='UNSET',sensor_health=Health(ok=not camera_failed,flags=['CAMERA_FAILURE'] if camera_failed else [],queue_overflow=overflow[0])))
                last_heartbeat=now
            for file in sorted(spool.glob('*.json'),key=lambda p:p.stat().st_mtime)[:10]:
                try:
                    r=api.post('/devices/vision-1/events',json=json.loads(file.read_text()));r.raise_for_status()
                    if r.json().get('durable'):file.unlink()
                except Exception:break
            if not active and not list(spool.glob('*.json')):
                with lock:checkpoint=barriers.popleft() if barriers else None
                if checkpoint:
                    seq+=1;wall=time.time();spool_event(DeviceEvent(event_id=uuid.uuid4().hex,event_type='BARRIER',device_id='vision-1',boot_id=boot,sequence_number=seq,capture_start=wall,capture_end=wall,monotonic_ms=int(time.monotonic()*1000),clock_synced=False,clock_uncertainty_ms=9999,calibration_version='UNSET',evidence_refs=['BARRIER:'+checkpoint],sensor_health=Health(ok=not camera_failed,queue_overflow=overflow[0])))
    finally:
        capture.close();api.close();mqtt_client.loop_stop();mqtt_client.disconnect()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--camera',required=True);p.add_argument('--camera-key',required=True);p.add_argument('--config',default='vision/config.example.json');p.add_argument('--weights');p.add_argument('--device',default='cpu');p.add_argument('--mode',choices=['A','B'],default='A');p.add_argument('--transport',choices=['poll','stream'],default='poll');p.add_argument('--backend',default='http://127.0.0.1:8000');p.add_argument('--device-key',required=True);p.add_argument('--mqtt',default='localhost');p.add_argument('--spool',default='data/vision-spool');p.add_argument('--evidence',default='evidence');run(p.parse_args())
