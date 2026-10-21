# Model stack, pipeline and learning loop

Status 2026-10-21. Covers the models used for recognition, how one shelf interaction flows
through them, and the features built around them. Hardware bring-up is in
`sensor-controller-bringup.md`; few-shot learning details are in `fewshot-learning.md`.

## Models

| Job | Model | Where |
|---|---|---|
| Find items (and hands) in the tray crop | YOLO26n, class `item`, optional second class `hand`, ByteTrack | `vision/detector.py` `AgnosticDetector` |
| Which product | Frozen embedder + gallery of reference crops (no retraining) | `vision/identify.py`, `vision/embedders.py` |
| Look-alike products | Ridge probe per confusable group, used only if cross-validated ≥ 90 % | `vision/fewshot.py` `LookalikeGuard` |
| Flavour variants (colour) | HSV colour histograms; only breaks a near-tie the gallery already short-listed | `vision/cues.py` `ColourCue` |
| Background removal | GrabCut (default option) or SAM 2.1 tiny | `vision/segment.py` |
| Hands, if the detector has no hand class | MediaPipe (optional, not installed, see below) | `vision/hands.py` |
| Odd objects the detector never learned | Any prompt-free open-vocabulary checkpoint (`--fallback-weights`) | `vision/detector.py` `OpenVocabularyFallback` |
| Faces in stored evidence | OpenCV Haar cascades (bundled) | `vision/privacy.py` |

Embedders available through `make_embedder`: `dinov2-small` (default), `dinov2-base`,
`siglip2-base`, `clip-base`, `dinov3-small` (gated on Hugging Face and needs a newer
`transformers`; reported as unavailable otherwise). A gallery records its embedder and its
masking; the identifier refuses a query path that does not match.

### Measured on this shelf (3 products, 132 reference crops, 50 query crops from another session)

`python -m tools.dataset.embedder_bench --train data/shelf-pilot/objects/train --test data/shelf-pilot/objects/val`

| DINOv2-small | 1-shot top-1 | all refs top-1 | known-vs-unknown AUROC | known accepted at 5 % unknown accepted |
|---|---|---|---|---|
| plain crop | 0.76 | 0.96 | 0.93 | 0.82 |
| GrabCut (112 px, 21 ms/crop) | 0.72 | 0.96 | 0.96 | 0.88 |
| SAM 2.1 tiny (box prompt) | 0.56 | 0.94 | 0.94 | 0.82 |

With 50 query crops, differences of a few points are noise. Masking mainly helps reject items
that are not in the gallery, which was the weakest point (unknown items accepted 14–44 % of the
time). SAM with a whole-crop box prompt tends to segment the tray and is not recommended.
SigLIP 2, CLIP and DINOv2-base results are added to `reports/embedder-bench.json` when their
downloads finish; switch embedder only if it wins on this report, then re-enrol.

## One interaction, end to end

```text
tray weight change (grams, tripod position)       -> which recorded units fit       [1: weight]
frames (pre-buffered) -> detector items + hands -> ByteTrack
    MotionProof: hand overlaps item track -> track leaves (pickup) / arrives (return)  [2: motion]
    bracelet markers decoded next to THAT hand -> owner candidate                    [3: owner]
    item crop -> mask -> embedder -> gallery -> look-alike probe -> colour cue      [4: product]
fusion (backend/fusion.py vetoes) -> automatic cart change, or staff review with a suggestion
staff decision -> labelled sample -> threshold recommendation, KPIs, new references
```

Each candidate carries `hand_product`, `trajectory`, `bracelet_hand`, `identity`, `chain_refs`
from the motion proof and `similarity`, `margin`, `top_product`, `second_product`, `via` from
recognition. Each event carries `model_versions` (detector file hash, gallery version,
embedder, mask, thresholds, hand source). Hardware mode stays review-first: fusion always adds
`PHYSICAL_OWNERSHIP_NOT_VALIDATED` until physical validation.

Markers are linked to the hand that touched the item, not to every marker in view. Two shoppers
at the shelf with one picking: only the picker's bracelet is next to the hand that moved the
item. If two different markers sit next to that hand, identity stays unproven. The planned
coloured/shaped bracelet detector would add a fourth signal at the same place
(`vision/hands.py` `MotionProof.observe`, markers next to the hand).

## Learning loop

- **Reference quality gate** (`vision/cues.py reference_quality`): restock and teach crops are
  refused if blurred, glare-blown, very dark, mostly skin (hand covering the product), cut off
  by the tray edge (restock only) or too small. The score is stored with the reference.
- **Help the camera** (active learning): crops that were a near-tie or just under the threshold
  go to staff (at most one per track, six per minute). One tap turns the answer into a
  `STAFF_LABEL` reference that vision loads within ~10 s.
- **Threshold tuning** (Insights page): staff-confirmed products plus labelled crops form a
  sample set; after 30 samples the page recommends the threshold/margin that accepts the most
  with ≤ 1 % wrong. Staff apply it explicitly; vision reads it from `/devices/vision-1/config`.
- **Replay before switching models**:
  `python -m tools.replay_models --db data/integration/store.db --evidence evidence --item-weights <new.pt> --gallery <new.npz>`
  re-runs reviewed events' frames and compares with staff decisions and with the models recorded
  at the time.

## Training the item + hand detector

```text
python -m tools.dataset.cli hands data/shelf-pilot/labelled --output data/shelf-pilot/hand-prelabels --hand-class 3   # needs MediaPipe
# fix/complete the hand boxes in the labelling tool (class 3 = hand), then:
python -m tools.dataset.cli crop data/shelf-pilot/labelled --config data/integration/vision-config.json --output data/shelf-pilot/crops-hands --single-class --hand-class 3
python -m tools.dataset.cli make-yaml data/shelf-pilot/crops-hands --output data/shelf-pilot/item-hand.yaml --single-class --with-hands
python -m tools.dataset.cli model train --model models/yolo26n-pretrained.pt --data data/shelf-pilot/item-hand.yaml --device 0 --epochs 150 --imgsz 640
```

The vision service detects the `hand` class automatically. Hands may be labelled by hand
without MediaPipe; MediaPipe is not installed here because it pulls a second OpenCV build
(opencv-contrib 5.0) that conflicts with the pinned opencv-python 4.11. Install it in a
separate environment for pre-labelling only.

## Staff and customer features

- Staff **Insights**: automatic vs review rate, automatic decisions later challenged, median
  review time, top review reasons, camera agreement, threshold recommendation, scale health
  (scale reading vs recorded items, drift flags, calibration age), shrink (unexplained weight
  drops valued by best-matching product, dismissed pickups, removed and misplaced items).
- **Evidence player** in each review card: frames with item, hand and bracelet boxes, a weight
  timeline with bracelet reads, play/scrub, and a **suggested decision** with its reasons.
- **Live shelf map** on Overview for tripod trays (needs the tray geometry).
- Overview alerts for scale mismatch/drift and unanswered camera questions.
- Customer: product thumbnails, "Added / Put back" notices with a tone and a **Not mine?**
  shortcut into the dispute form, and an exit summary after payment.
- Evidence frames with a detected face are blurred before storage (metadata says which);
  frames without a face stay byte-identical originals. `--no-blur-faces` turns it off.

## Not done

- OCR for look-alike text (rapidocr pulls extra OpenCV/onnxruntime packages; colour cue used instead).
- Coloured/shaped bracelet detector (planned separately).
- The live `vision.run` loop changes are covered by unit tests of each part, not yet by a run
  against the camera: run it once the camera is back on the network and check one pickup in the
  evidence player.
