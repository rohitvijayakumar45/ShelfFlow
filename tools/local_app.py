"""Prepare and run the real one-tray prototype without replacing the simulation DB."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets

from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1]
LOCAL=ROOT/'data/integration'


def macro(path,name):
    match=re.search(r'^\s*#define\s+'+name+r'\s+"([^"\r\n]+)"',path.read_text(),re.MULTILINE)
    if not match:raise RuntimeError('Missing local '+name)
    return match[1]


def prepare():
    LOCAL.mkdir(parents=True,exist_ok=True)
    env=LOCAL/'runtime.env'
    if not env.exists():
        values={'SHELFFLOW_MODE':'hardware','DATABASE_URL':'sqlite:///'+(LOCAL/'store.db').as_posix(),
                'JWT_SECRET':secrets.token_urlsafe(36),'DEVICE_SECRET':secrets.token_urlsafe(32),
                'SENSOR_DEVICE_KEY':macro(ROOT/'firmware/shelf-controller/include/secrets.h','DEVICE_KEY'),
                'STAFF_PASSWORD':'ShelfFlow-Staff-2026!',
                'CAMERA_URL':'http://127.0.0.1:8766',
                'CAMERA_KEY':macro(ROOT/'firmware/camera/include/secrets.h','CAMERA_KEY'),
                'EVIDENCE_ROOT':(ROOT/'evidence').as_posix(),
                'USB_EVENT_ROOT':(LOCAL/'usb').as_posix(),
                'CORS_ORIGINS':'http://localhost:5173,http://localhost:5174,http://127.0.0.1:5173,http://127.0.0.1:5174'}
        env.write_text('\n'.join(k+'='+json.dumps(v) for k,v in values.items())+'\n',encoding='utf-8')
    load_dotenv(env,override=True)
    new=not (LOCAL/'store.db').exists()
    from alembic.config import Config
    from alembic import command
    command.upgrade(Config(str(ROOT/'alembic.ini')),'head')
    from backend.seed import seed
    seed()
    if new:
        from backend.db import SessionLocal
        from backend.models import Product,Tray,Device,Bracelet,Customer,Account,Shelf
        from backend.auth import hash_password
        with SessionLocal.begin() as db:
            for pid,name,price in [('sanitizer','Sanitizer',2500),('vicks','Vicks',19900),('sunscreen','Sunscreen',9900)]:
                db.add(Product(id=pid,name=name,price_minor=price,unit_g=None,tolerance_g=None,weight_source='UNMEASURED'))
            db.flush()
            tray=db.get(Tray,'tray-a');tray.product_id='sanitizer';tray.roi=[0,0,1,1];tray.unit_tracking=True
            db.delete(db.get(Tray,'tray-b'));db.flush()
            for pid in ('oat','coffee','bar','chips'):db.delete(db.get(Product,pid))
            db.get(Device,'sensor-1').secret_digest=hashlib.sha256(os.environ['SENSOR_DEVICE_KEY'].encode()).hexdigest()
            db.get(Shelf,'shelf-1').camera_config={'status':'OV3660_VERIFIED_PILOT','mode':'A','integration_setup':True}
            db.add(Bracelet(id='B01',rfid='43D51829',markers=[],clean=False,state='RETURNED'))
            customer=Customer(alias='Shopper');db.add(customer);db.flush()
            db.add(Account(username='shopper',customer_id=customer.id,password_hash=hash_password('ShelfFlow-Shopper-2026!')))
    config={'pre_seconds':2,'post_seconds':2,'max_collection_seconds':8,'weight_driven':True,
            'identity_threshold':.68,'identity_margin':.06,'blur_threshold':30,
            'trays':{'tray-a':{'roi':[0,0,1,1],'hysteresis_g':8,'motion_threshold':8}}}
    (LOCAL/'vision-config.json').write_text(json.dumps(config,indent=2))
    print('Hardware application prepared with an empty, one-tray inventory.',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('service',choices=['setup','backend','vision'])
    args=parser.parse_args()
    os.chdir(ROOT)
    if args.service=='setup':prepare();return
    load_dotenv(LOCAL/'runtime.env',override=True)
    if args.service=='backend':
        import uvicorn
        uvicorn.run('backend.main:app',host='127.0.0.1',port=8000,access_log=False)
    else:
        import sys
        import runpy
        camera=os.getenv('PHYSICAL_CAMERA_URL','http://10.92.123.251')
        # Camera tuning is not stored on the camera: re-apply the measured best setting at every start.
        # 8 MHz / 640x480 / quality 12 gave ~10 fps; 20 MHz hung the camera, quality 10 fell below 1 fps.
        clock,quality,fps,resolution=os.getenv('CAMERA_TUNE','8,12,20,640x480').split(',')
        try:
            import httpx
            httpx.post(camera.rstrip('/')+'/tune',headers={'X-Camera-Key':os.environ['CAMERA_KEY']},timeout=10,
                       params={'clock_mhz':clock,'quality':quality,'fps':fps,'resolution':resolution}).raise_for_status()
            print(f'Camera tuned: {resolution} {clock} MHz quality {quality}',flush=True)
        except Exception as exc:
            print('Camera tuning not applied ('+type(exc).__name__+'); continuing with the camera default',flush=True)
        sys.argv=['vision.run','--camera',camera,'--camera-key',os.environ['CAMERA_KEY'],
                  '--config',str(LOCAL/'vision-config.json'),'--item-weights','models/shelf-pilot-item-detector.pt',
                  '--gallery','models/shelf-pilot-gallery.npz','--device','cuda:0','--transport','stream',
                  '--stream-url','http://127.0.0.1:8765/stream','--sensor-dir',str(LOCAL/'usb/delivered'),
                  '--device-key',os.environ['DEVICE_SECRET'],'--spool',str(LOCAL/'vision-spool')]
        runpy.run_module('vision.run',run_name='__main__')


if __name__=='__main__':main()
