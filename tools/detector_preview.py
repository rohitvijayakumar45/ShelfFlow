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
from vision.verify import Verifier


class DetectorPreview(camera_preview.SharedCamera):
    def __init__(self, source, weights, gallery, device, backend=None, device_key=None, products_model=False):
        super().__init__(source, '')
        self.source = camera_preview.SharedCamera(source, '')
        self.model = YOLO(weights)
        # products_model: a YOLO26n model from the Train products page names products itself (class = product ID).
        self.products_model = products_model
        self.gallery = None if products_model else Gallery.load(gallery)
        self.embedder = None if products_model else Dinov2Embedder(device, self.gallery.embedder_name)
        self.device = device
        self.predictions = []
        self.processing_ms = None
        # Follow the model switched on in Train products: reload when active.pt changes (no restart needed).
        self.trained = Path(__file__).resolve().parents[1] / 'models/trained/active.pt'
        self.follow = os.getenv('SHELFFLOW_DETECTOR', 'trained') != 'gallery'
        self.loaded_mtime = self.trained.stat().st_mtime if products_model and self.trained.is_file() else None
        self.checked = 0.0
        # Unknown check: detections that do not look like the product the model named show as Unknown.
        self.verifier = Verifier.for_weights(self.trained, device) if products_model and Path(weights) == self.trained else None
        # Same live references as the vision service (teach, restock, staff labels), so a
        # newly taught product is named here too instead of showing as Unknown.
        self.sync, self.synced = None, 0.0
        if backend and device_key and not products_model:
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
            if self.follow and started - self.checked > 3:
                self.checked = started
                try:
                    mtime = self.trained.stat().st_mtime if self.trained.is_file() else None
                    if mtime and mtime != self.loaded_mtime:
                        self.model, self.products_model, self.loaded_mtime = YOLO(str(self.trained)), True, mtime
                        self.verifier = Verifier.for_weights(self.trained, self.device, self.verifier.embedder if self.verifier else None)
                        print('Switched to the trained product model', self.trained, flush=True)
                except Exception as exc:  # half-written or unreadable: keep the current model, retry next check
                    print('Model reload failed:', type(exc).__name__, flush=True)
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
            detected = self.model.predict(frame, device=self.device, imgsz=self.model.overrides.get('imgsz', 640) if self.products_model else 640, conf=.25, verbose=False)[0]
            boxes, crops = [], []
            for box in detected.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                x1, y1, x2, y2 = max(0,x1), max(0,y1), min(w,x2), min(h,y2)
                if x2 > x1 and y2 > y1:
                    boxes.append((x1,y1,x2,y2))
                    crops.append(frame[y1:y2,x1:x2])
            predictions = []
            if crops and self.products_model:
                labelled = [(b, detected.names[int(d.cls.item())], float(d.conf.item())) for b, d in zip(boxes, detected.boxes)]
                if self.verifier is not None:
                    try:
                        sims = self.verifier.similarities(frame, boxes, [p for _, p, _ in labelled])
                        labelled = [(b, p if self.verifier.known(p, sim) else None, c) for (b, p, c), sim in zip(labelled, sims)]
                    except Exception as exc:  # never lose the live view over the check
                        print('Unknown check failed:', type(exc).__name__, flush=True)
            elif crops:
                labelled = []
                for box, vector in zip(boxes, self.embedder.embed(crops)):
                    match = self.gallery.identify(vector, .68, .06)
                    labelled.append((box, match.product_id if match.known else None, None))
            if crops:
                for box, product, score in labelled:
                    x1,y1,x2,y2 = box
                    color = (40,210,40) if product else (0,175,255)
                    label = (product.replace('-', ' ').title() if product else 'Unknown') + (f' {score:.2f}' if score is not None else '')
                    cv2.rectangle(frame,(x1,y1),(x2,y2),color,2)
                    (tw,th),base = cv2.getTextSize(label,cv2.FONT_HERSHEY_SIMPLEX,.6,2)
                    ty = max(th+7,y1)
                    cv2.rectangle(frame,(x1,ty-th-7),(min(w-1,x1+tw+8),ty+base),color,-1)
                    cv2.putText(frame,label,(x1+4,ty-3),cv2.FONT_HERSHEY_SIMPLEX,.6,(0,0,0),2,cv2.LINE_AA)
                    predictions.append({'product':product or 'UNKNOWN','box':box,**({'score':round(score,3)} if score is not None else {})})
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
    parser.add_argument('--weights',help='default: the model switched on in Train products, else the pilot item detector')
    parser.add_argument('--gallery',default='models/shelf-pilot-gallery.npz')
    parser.add_argument('--device',default='cuda:0')
    parser.add_argument('--backend',default='http://127.0.0.1:8000')
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / 'data/integration/runtime.env')
    camera_preview.PAGE = camera_preview.PAGE.replace(b'OV3660 live feed',b'Live product detection').replace(b'<p id="status">',b'<p>Pilot recognition: uncertain items may be misidentified. No cart actions.</p><p id="status">')
    root = Path(__file__).resolve().parents[1]
    trained = root / 'models/trained/active.pt'
    products_model = not args.weights and trained.is_file() and os.getenv('SHELFFLOW_DETECTOR', 'trained') != 'gallery'
    weights = args.weights or (str(trained) if products_model else 'models/shelf-pilot-item-detector.pt')
    print('Detector:', 'trained YOLO26n product model' if products_model else 'item detector + DINOv2 gallery', weights, flush=True)
    preview = DetectorPreview(args.source,weights,args.gallery,args.device,args.backend,os.getenv('DEVICE_SECRET'),products_model)
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
