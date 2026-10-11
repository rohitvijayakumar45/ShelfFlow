# Beginner dataset and model guide

Product-box labels identify packaging, not customer ownership. Keep interaction ground truth separate. No trained ShelfFlow weights are included. Fixture data and mock detections must not be reported as model accuracy.

1. Mount the sole OV3660 at its intended deployment position. First record 20–30 representative interactions; ask a human whether ownership can actually be established.
2. Choose 3–5 visually distinguishable demo SKUs. Put one SKU per tray at a time.
3. Capture original JPEG bytes, not screenshots or repeated recompression. Record session ID, volunteers as aliases, scene, resolution, clock metadata and lighting.
4. Record front, back, side, tilted, partly hidden, stacked and in-hand views.
5. Include full/sparse trays, glare, lighting variation, sleeves and different volunteers.
6. Capture reaches, pickups, returns, two-item actions, crossed hands and two simultaneous shoppers.
7. Include negatives: empty trays, hands without products and unrelated objects.
8. Label every identifiable target product consistently. Use boxes around its visible product extent under a documented policy.
9. Do not infer labels for fully hidden objects. Mark occlusion in interaction truth.
10. Split whole recording sessions/clips BEFORE selecting frames; do not leak near-identical images across partitions.

Initial target: 500–1,000 diverse images per SKU, not a guarantee. More copies of the same image do not add useful diversity. Iterate from measured failures. Suggested split 70/15/15 by session; with a small number of sessions the tool rounds while keeping each split populated. All clips from one session stay together.

## Collect

Install `.[vision]` and verify camera auth and capture headers. The pinned standard OpenCV package includes ArUco in its main objdetect module; do not install a second OpenCV wheel flavor into the same environment. See [OpenCV 4.7 release notes](https://opencv.org/blog/opencv-4-7-0/) and the [Python package guidance](https://pypi.org/project/opencv-python/).

```powershell
python -m tools.dataset.cli capture http://CAMERA-IP --key CAMERA_KEY --session session-001 --scenario two-shoppers --frames 400 --interval 0.1 --output data\raw\session-001\clip-01
python -m tools.dataset.cli weights --host localhost --seconds 90 --output data\raw\session-001\weights.csv
```

Run weight recording concurrently in another terminal with broker credentials set. It stores capture and receive times distinctly. Preserve a consented interaction truth CSV using `interaction-ground-truth.example.csv`: clip, customer alias, tray, SKU, action, quantity, timestamps, occlusion, simultaneous flag, weight trace reference.

For a carrier that actually provides MJPEG:

```powershell
python -m tools.dataset.cli mjpeg http://CAMERA-IP/stream --key CAMERA_KEY --session session-002 --scenario returns --seconds 60 --output data\raw\session-002\clip-01
```

The included camera firmware serves JPEG polling; it does not claim MJPEG. MJPEG recording preserves extracted JPEG frames, but arrival-only timing must not be used as precise capture time.

## Split, screen and annotate

```powershell
python -m tools.dataset.cli split data\raw --seed 42 --output data\split.json
python -m tools.dataset.cli extract data\split.json --stride 5 --output data\dataset
python -m tools.dataset.cli screen data\dataset --distance 5 --output data\duplicates.json
```

Inspect near-duplicate pairs manually. The tool does not delete them. Keep empty-image label files for valid negatives. Use your preferred local box annotator; export YOLO detection text files with the same image basename in `labels/train`, `labels/val`, `labels/test`.

```text
class_id x_center y_center width height
0 0.375 0.500 0.150 0.400
```

Class IDs are zero-based. Coordinates are normalized to the image that was labelled; if training on crops, do not reuse full-image labels without transforming them. Validate that class IDs, values and box edges are in range:

```powershell
python -m tools.dataset.cli validate data\dataset\labels --classes 4
```

Keep marker identities out of SKU classes. Never train a class per customer. Multiple marker faces map to one bracelet in the backend. Generate with `python -m vision.markers 1 data/marker-1.png`; print with adequate white border and check actual decode across motion, wrist rotation and distance.

## Train and evaluate YOLO26

Copy `data.example.yaml` and set an absolute dataset path for Windows. Start with both n and s, measuring recognition and interaction latency at the same resolution:

```powershell
python -m tools.dataset.cli model train --model yolo26n.pt --data data\data.yaml --device cpu --epochs 100 --imgsz 960
python -m tools.dataset.cli model train --model yolo26s.pt --data data\data.yaml --device 0 --epochs 100 --imgsz 960
python -m tools.dataset.cli model evaluate --model runs\detect\train\weights\best.pt --data data\data.yaml --device cpu --imgsz 960
python -m tools.dataset.cli model export --model runs\detect\train\weights\best.pt --device cpu --format onnx
```

GPU command requires a supported PyTorch GPU environment. CPU training is supported but may be slow. Measure actual laptop throughput. Do not assume packaged pretrained COCO weights know demo SKUs. `YoloDetector` requires an existing custom weight file and matching class map. Optional `yolo26n-pose.pt` is separate; box labels are not pose labels, and cropped arms do not prove opposite-hand ownership.

Ultralytics supports the YOLO26 names and train/val/export interfaces: [YOLO26 model documentation](https://docs.ultralytics.com/models/yolo26), [training documentation](https://docs.ultralytics.com/modes/train). Ultralytics library/model use is governed by AGPL-3.0 or its commercial licensing terms; review these before proprietary deployment. OpenCV uses Apache-2.0; inspect bundled third-party notices. Dependencies are pinned in pyproject and app lockfile; this repository does not redistribute downloaded model weights.

## Replay and separate metrics

Store version-1 device events as JSONL. `python -m tools.dataset.cli replay data/events.jsonl --key DEVICE_KEY` preserves original timestamps; old/offline events go to review. It does not refresh time to make stale evidence look live.

Ground-truth evaluation rows include `actual_event`, `observed`, `confirmed`, `pending`, `sku_correct`, `quantity_correct`, `owner_correct`, `charge_correct`, `identity_failure`, `data_loss`, `latency_ms`. Report:

```powershell
python -m tools.dataset.cli metrics data\evaluated-events.jsonl --output reports\physical-metrics.json
```

Report SKU precision/recall, quantity and ownership accuracy, false-charge rate, missed events, pending-review rate, identity failures, data loss and p50/p95/p99 separately. This event-level tool is not detector box-level mAP; use YOLO `val` for precision/recall/mAP per class and retain the test-set manifest. Null metrics mean no denominator, not perfect accuracy. No physical metrics currently exist.
