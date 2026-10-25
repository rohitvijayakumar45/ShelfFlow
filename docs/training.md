# Training products from video (YOLO26n + SAM 2.1)

Staff app → **Train products**. One short clip per product replaces photo-by-photo capture.

| Step | What happens | Where |
|---|---|---|
| Record | Every frame of the camera's MJPEG stream is written as received (original JPEG bytes, no decode or re-encode), so capture keeps up with the stream. Sharpness is scored per frame while recording; the page warns when frames turn blurry. Max 120 s / 2400 frames. | `tools/trainer.py` `pump` |
| Mark | One click on the product (or a dragged box) on any frame. SAM 2.1 video tracking follows it forwards and backwards through the clip. Fast = SAM 2.1 tiny, Precise = SAM 2.1 base. | `vision/training.py` `label_session` |
| Review | Automatically left out: blurred frames (below 35 % of the clip's 90th-percentile sharpness; adjustable cut-off), frames where SAM lost the product, boxes that are tiny/whole-frame or jump vs their neighbours. Click any frame to include/exclude it; your choice wins. | `flag_boxes`, `included` |
| Train | One YOLO class per product (class name = product ID), empty-tray clips as negatives, COCO-pretrained `yolo26n.pt`, 640 px, train/val split by ~1 s chunks so validation frames are never near-duplicates of training frames. Runs in a subprocess; progress, ETA and per-epoch mAP50 on the page; Stop finishes the current batch and keeps the best weights. | `build_dataset`, `train_main` |
| Test / use | *Test live* draws the model's boxes on the live stream. *Use this model* copies it to `models/trained/active.pt`; `tools.local_app vision` then runs it (`SHELFFLOW_DETECTOR=gallery` keeps the DINOv2 gallery path). | `tools/trainer.py`, `tools/local_app.py` |

## Run

```powershell
.\.venv\Scripts\python.exe -m tools.local_app trainer        # hardware: raw shared preview on :8765
.\.venv\Scripts\python.exe -m tools.trainer --source webcam:0 # try the flow with the laptop webcam
```

The staff Vite app proxies `/trainer` to port 8001 and uses the normal staff login. The service needs the
`vision` extra and, for usable speed, the CUDA PyTorch wheels (`requirements-gpu.txt`). Weights
(`yolo26n.pt`, `sam2.1_t.pt`, `sam2.1_b.pt`) are downloaded into `models/` on first start.

Data stays local and ignored: clips in `data/training/sessions/<id>/`, runs (dataset, `train.log`,
`progress.json`, `result.json`, `best.pt`) in `data/training/runs/<id>/`.

## Measured so far

Synthetic check only (no physical clip yet): SAM 2.1 tiny boxed 40/40 frames of a moving object with a
median corner error of ~1 px; the dataset → YOLO26n subprocess → `best.pt`/`result.json` path completed.
On CPU, SAM took ~5 s per frame; use the GPU. Accuracy on real products is unmeasured until clips are
recorded on the shelf.

DINOv2 gallery recognition (`docs/recognition.md`, Products & recognition page) is unchanged and remains available.
