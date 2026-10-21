# Product recognition: training and enrolment (open-set)

Status: **A three-product camera pilot is trained on the RTX 3050 and has a small,
same-day validation recording. Human label review and an independent test remain pending.**

The pilot uses 99 training images (132 boxes) from session 001 and 19 validation
images (50 boxes) from session 002. The detector completed 40 epochs with validation
disabled during training; the final checkpoint is `models/shelf-pilot-item-detector.pt`.
References enrolled from training images are in `models/shelf-pilot-gallery.npz`.

Item-box validation recall was 94%. Matching 50 labelled validation crops at the
selected threshold 0.68 / margin 0.06 correctly accepted 82% and rejected 18%.
The threshold was selected on this validation set; these are not independent test
or checkout accuracy figures. No reviewed unknown-product examples were evaluated.
A fresh combined inference frame correctly named Sanitizer, Vicks and Sunscreen.
Local provenance and results are in `data/shelf-pilot/pilot-report.json` and
`data/shelf-pilot/fresh-predictions.jpg`. Raw data and model artifacts remain local.

The pilot is a full-frame feasibility check. The deployment workflow below also
requires tray-region data, varied objects, separate test sessions and interaction
validation before enabling automatic checkout.

## Why this design

Training one YOLO class per product means retraining for every new product. ShelfFlow instead splits the problem:

| Part | What it answers | Trained? |
|---|---|---|
| **Item detector**: YOLO26, **one class `item`** | *Where* is an item (in the tray, in a hand)? | Yes, once, on many different objects |
| **Gallery**: frozen **DINOv2 ViT-S/14** plus reference crops | *Which* product is it, or `UNKNOWN`? | **No.** A new product is enrolled with 10–30 crops |
| **Tray weights and position** (mixed trays) | Which unit and how many | Measured when loading |

A product a reviewer brings in at random needs **no retraining**: staff load it with the tray-loading wizard (its
weight and position) and enrol its reference crops (its look). The detector only has to know "this is a graspable
item", which generalises to unseen packaging far better than per-product classes.

Decisions are rule-based and conservative: a crop is named only when similarity ≥ threshold **and** it is clearly
ahead of the next product (margin), and only after several frames of the same track agree (`TrackVoter`).
Everything else is `UNKNOWN`, and fusion then relies on weight and position, or sends the event to staff review.
Similarity is a cosine score, **not** a probability.

## 0. Environment (once)

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-gpu.txt
.\.venv\Scripts\python.exe -m pip install -e ".[vision]" transformers==4.51.3
.\.venv\Scripts\python.exe -m tools.dataset.gpu_check --output data\hardware-backups\gpu-check.json
.\.venv\Scripts\python.exe -m tools.dataset.selftest --device 0
```
`selftest` runs the whole pipeline on a small **synthetic** dataset to prove the install works. Its numbers are
meaningless as accuracy.

## 1. Collect

Mount the camera in its final position (see docs/dataset.md for scenarios, sessions and consent). Record
**sessions**, split by session, then label full frames (`docs/dataset.md` steps). For the item detector, include the
demo products **plus 30–50 other everyday objects**, so it learns "item" rather than particular packages, and
include negatives (empty trays, empty hands).

Label every item with its product class in Label Studio. One labelling pass serves both the detector (classes merged
into `item`) and the gallery (per-product crops).

## 2. Train the item detector on tray crops

The runtime runs the detector on each tray's region, so train on the same crops:

```powershell
.\.venv\Scripts\python.exe -m tools.dataset.cli crop data\dataset --config data\vision-config.json --output data\crops --single-class
.\.venv\Scripts\python.exe -m tools.dataset.cli make-yaml data\crops --single-class --output data\items.yaml
.\.venv\Scripts\python.exe -m tools.dataset.cli model train --model yolo26n.pt --data data\items.yaml --device 0 --epochs 100 --imgsz 640 --single-class
.\.venv\Scripts\python.exe -m tools.dataset.cli model train --model yolo26s.pt --data data\items.yaml --device 0 --epochs 100 --imgsz 640 --single-class
.\.venv\Scripts\python.exe -m tools.dataset.cli model evaluate --model runs\detect\train\weights\best.pt --data data\items.yaml --device 0 --imgsz 640
```
- `crop` maps each label into each tray crop. A crop is **skipped** when an item is only a sliver inside it
  (below `--min-visible`, default 40 %). Crops are PNG, to avoid a second JPEG compression.
  `crop-manifest.json` records every crop's origin and size.
- Compare n and s on the **test sessions**: recall on in-hand and partly hidden items matters most, along with
  detection time on the laptop. Out of memory → `--batch 8`.
- Copy the chosen `best.pt` to `models\item-detector.pt`.

## 3. Build the gallery

```powershell
.\.venv\Scripts\python.exe -m tools.dataset.cli objects data\dataset --names data\data.yaml --output data\objects
.\.venv\Scripts\python.exe -m tools.dataset.cli enroll data\objects\train --output models\gallery.npz --device 0
```
`objects` writes `data\objects\<split>\<product_id>\*.png`. Enrol from **train** sessions only. Aim for
**15–30 crops per product** covering front, back, sides, tilted and in-hand views. `enroll` warns about products
with fewer than 8. Folder names must equal the catalogue product IDs. The gallery file records its embedder and a
content-hash version, and a tampered or mismatched gallery is refused at load.

**New product later:** put its crops in `data\objects\train\<new_id>\` and run `enroll` again. No detector retraining.

## 4. Choose thresholds with products held out (the "random item" test)

```powershell
.\.venv\Scripts\python.exe -m tools.dataset.cli gallery-eval --gallery-dir data\objects\train --query-dir data\objects\test --holdout soap,juice --max-error 0.01 --device 0 --output reports\gallery-eval.json
```
- `--holdout` keeps some products **out of the gallery**. Their test crops must come out `UNKNOWN`. This simulates a
  reviewer's surprise item that hasn't been enrolled yet.
- The tool sweeps threshold × margin and picks the point with the best known-product accuracy whose
  **error** (wrong product accepted, plus unseen items accepted) is ≤ `--max-error`.
- Put the chosen values in the vision config: `"identity_threshold"`, `"identity_margin"`. The defaults in code
  (0.6 / 0.05) are uncalibrated placeholders.
- These are **single-crop, frame-level** numbers. Track voting usually improves them, but measure it on recorded
  interactions before claiming anything.

## 5. Run

```powershell
.\.venv\Scripts\python.exe -m vision.run --camera http://CAMERA-IP --camera-key KEY --device-key DEVICE_KEY --config data\vision-config.json --item-weights models\item-detector.pt --gallery models\gallery.npz --device 0
```
Detections leave the detector as `UNKNOWN`. The identifier crops each one from the tray image, embeds it, matches it
against the gallery and votes per track. A candidate gets `sku=true` only once its track has settled on a product.
In hardware mode every physical event remains review-first until the whole chain has been validated.

## What to report (separately)

Detector box precision/recall/mAP (test sessions), gallery known-accuracy / rejection / unseen-accepted at the
chosen point, and, from recorded interactions, end-to-end SKU, quantity, ownership, false-charge, missed and
pending rates. Simulated or synthetic numbers are never reported as accuracy.

## Licences

Ultralytics YOLO is AGPL-3.0 (commercial licence otherwise). DINOv2 code and weights are Apache-2.0. Weights are
downloaded at first use and not stored in this repository.


## First physical collection (2026-10-16)

The local pilot under ignored `data/shelf-pilot` contains 491 original 640x480
frames in four clips from one recording session. User-confirmed product IDs:
`sanitizer`, `vicks`, `sunscreen`. A 100-image subset was reviewed; 99 images have approximate assistant
visual box drafts (132 instances and 11 negatives), with one ambiguous image
excluded and label overlays for review. These drafts are not a measured recognition result. All originals and
per-frame source timestamps remain intact. No random frame split has been made.

Before production training/evaluation, correct boxes and collect independent
validation/test sessions. Model training on the current collection alone can
only be a pilot. The laptop has an NVIDIA RTX 3050 6 GB; the original project
PyTorch installation was CPU-only. `requirements-gpu.txt` specifies a matched
CUDA wheel pair; `tools.dataset.gpu_check` performs a GPU optimizer step and
checks torchvision NMS before attempting detector training. Its synthetic
result establishes compatibility, never camera recognition accuracy.


GPU installation verified on 2026-10-16: PyTorch 2.10.0+cu128 and torchvision
0.25.0+cu128 on RTX 3050 6 GB. The GPU optimizer step, GPU torchvision NMS,
and an untrained YOLO26n GPU forward pass passed; pip found no dependency
conflicts. Raw results: ignored `data/hardware-backups/gpu-check.json`.
Seventeen related dataset, recognition, and camera tests also passed.
Start any pilot training with batch 4 on this 6 GB laptop and measure memory
before increasing it. Real product-model training and held-out accuracy remain
pending; these compatibility checks are not recognition results.
