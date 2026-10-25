# Manual capture and SAM review

Staff → **Products & recognition** → **Show camera and capture manually**.

1. Select the product for the next photo and click **Capture now**. This saves
   the latest fresh, original frame from the shared camera proxy. No YOLO box,
   teach session, diversity check or image-quality score is required. A broken
   camera connection or invalid image still reports an error instead of saving
   a stale/empty image. The original stays available in the capture queue.
2. Select any saved capture. Draw a box around the entire intended product, or
   enter left/top/right/bottom percentages. Keep hands and adjacent products
   outside the selection. The product label belongs to that saved capture;
   changing the next-capture product picker cannot relabel an earlier image.
3. Click **Segment product with SAM**. A single background worker processes
   queued selections using the local `models/sam2.1_t.pt` model. First use
   includes loading the model; new snapshots remain available during inference.
4. Inspect the tight original crop and SAM cutout. Quality problems are shown
   as warnings rather than silently discarding the snapshot. If the mask is
   wrong, redraw the box and segment again. SAM cannot restore blur, hidden
   product details or parts outside the image.
5. Check the confirmation and click **Approve and add to gallery**. Two labelled
   references are added: the tight original crop (similar to live YOLO crops)
   and the neutral-background SAM view. Existing gallery preprocessing, models
   and thresholds are retained; live inference does not run SAM on every frame.
   ReferenceSync loads these references into vision and detector preview during
   their normal sync cycles (normally around ten seconds).

Capturing/segmenting never changes a cart. Nothing enters the gallery until
approval. Approved references can be rejected using the existing References
controls. Discarding a draft keeps its original image on disk for traceability.
Captures are paged in batches of 50, with **Show older captures** for history.

## Runtime and recovery

Migration **0013** adds `manual_captures`. It preserves existing products,
references, calibration and shopping evidence. Original and derived JPEGs live
in the authenticated catalogue directory under `EVIDENCE_ROOT`.

`python -m tools.local_app backend` starts the capture worker automatically.
For other launchers, set `MANUAL_CAPTURE_WORKER=true`. The worker uses
`CAPTURE_SEGMENT_DEVICE=cuda:0` by default; `cpu` is available if GPU memory is
limited. `RAW_CAMERA_URL` defaults to `http://127.0.0.1:8765`. Use the raw proxy
there, not the annotated detector preview. All captures/crops stay local.

Capture request keys prevent a network retry from taking a second photo.
Segmentation revisions prevent approving an obsolete crop. Approval is atomic
and repeatable without duplicate references. Worker jobs survive restart;
interrupted processing is requeued. A failed segmentation keeps the original
and can be retried. Discarding a job during inference prevents its result from
becoming a ready or approved reference.

The old one-view teach endpoint is retained for compatibility. The manual
capture UI uses the new snapshot queue and no longer waits twelve seconds for
that teach session. Automatic Teach remains separate and retains its quality
and diversity checks.

## Validation

The tests cover immediate capture without detection/quality gating, private
endpoints, retries, empty masks, out-of-bounds selections, crop warnings,
segmentation failures, discard during inference, stale-revision approval and
duplicate approval. Gallery gains must still be measured on held-out views;
adding SAM crops is not by itself evidence of better recognition accuracy.
