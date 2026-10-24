"""Live pilot detector preview using the existing shared camera proxy."""
import argparse
import json
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

from tools import camera_preview
from vision.identify import Dinov2Embedder, Gallery


class DetectorPreview(camera_preview.SharedCamera):
    def __init__(self, source, weights, gallery, device, backend=None, device_key=None):
        super().__init__(source, '')
        self.source = camera_preview.SharedCamera(source, '')
        self.model = YOLO(weights)
        self.gallery = Gallery.load(gallery)
        self.embedder = Dinov2Embedder(device, self.gallery.embedder_name)
        self.device = device
        self.predictions = []
        self.processing_ms = None
        # Same live references as the vision service (teach, restock, staff labels), so a
        # newly taught product is named here too instead of showing as Unknown.
        self.sync, self.synced = None, 0.0
        if backend and device_key:
            import httpx
            from vision.references import ReferenceSync
            api = httpx.Client(base_url=backend, headers={'X-Device-Key': device_key}, timeout=5, trust_env=False)
            cache = Path(__file__).resolve().parents[1] / 'data/integration/reference-cache'
            self.sync = ReferenceSync(api, self.gallery, self.embedder, cache, report=False)

    def start(self):
        self.source.start()
        super().start()

    def close(self):
        super().close()
        self.source.close()

    def status(self):
        result = super().status()
        with self.condition:
            result.update(predictions=self.predictions, processing_ms=self.processing_ms)
        return result

    def _run(self):
        last = -1
        while not self.stopping.is_set():
            with self.source.condition:
                ready = self.source.condition.wait_for(
                    lambda: self.source.serial != last and self.source.jpeg,
                    timeout=1)
                if not ready or time.monotonic() - self.source.received > 2:
                    continue
                jpeg, headers, last = self.source.jpeg, self.source.headers.copy(), self.source.serial
                received = self.source.received
            started = time.monotonic()
            if self.sync and started - self.synced > 10:
                self.synced = started
                try:
                    self.sync.sync()
                except Exception as exc:  # backend down: keep the gallery we have
                    print('Reference sync failed:', type(exc).__name__, flush=True)
            frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                continue
            h, w = frame.shape[:2]
            detected = self.model.predict(frame, device=self.device, imgsz=640, conf=.25, verbose=False)[0]
            boxes, crops = [], []
            for box in detected.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                x1, y1, x2, y2 = max(0,x1), max(0,y1), min(w,x2), min(h,y2)
                if x2 > x1 and y2 > y1:
                    boxes.append((x1,y1,x2,y2))
                    crops.append(frame[y1:y2,x1:x2])
            predictions = []
            if crops:
                for box, vector in zip(boxes, self.embedder.embed(crops)):
                    match = self.gallery.identify(vector, .68, .06)
                    x1,y1,x2,y2 = box
                    color = (40,210,40) if match.known else (0,175,255)
                    label = match.product_id.title() if match.known else 'Unknown'
                    cv2.rectangle(frame,(x1,y1),(x2,y2),color,2)
                    (tw,th),base = cv2.getTextSize(label,cv2.FONT_HERSHEY_SIMPLEX,.6,2)
                    ty = max(th+7,y1)
                    cv2.rectangle(frame,(x1,ty-th-7),(min(w-1,x1+tw+8),ty+base),color,-1)
                    cv2.putText(frame,label,(x1+4,ty-3),cv2.FONT_HERSHEY_SIMPLEX,.6,(0,0,0),2,cv2.LINE_AA)
                    predictions.append({'product':match.product_id,'box':box})
            ok, encoded = cv2.imencode('.jpg',frame,[cv2.IMWRITE_JPEG_QUALITY,85])
            if ok and time.monotonic()-received < 2:
                with self.condition:
                    self.jpeg, self.headers = encoded.tobytes(), headers
                    self.received = received
                    self.predictions = predictions
                    self.processing_ms = round((time.monotonic()-started)*1000,1)
                    self.serial += 1
                    self.condition.notify_all()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',default='http://127.0.0.1:8765/stream')
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--weights',default='models/shelf-pilot-item-detector.pt')
    parser.add_argument('--gallery',default='models/shelf-pilot-gallery.npz')
    parser.add_argument('--device',default='cuda:0')
    parser.add_argument('--backend',default='http://127.0.0.1:8000')
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / 'data/integration/runtime.env')
    camera_preview.PAGE = camera_preview.PAGE.replace(b'OV3660 live feed',b'Live product detection').replace(b'<p id="status">',b'<p>Pilot recognition: uncertain items may be misidentified. No cart actions.</p><p id="status">')
    preview = DetectorPreview(args.source,args.weights,args.gallery,args.device,args.backend,os.getenv('DEVICE_SECRET'))
    server = camera_preview.make_server(preview,args.port)
    preview.start()
    print(f'Live detector: http://127.0.0.1:{args.port}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        preview.close()


if __name__ == '__main__':
    main()
