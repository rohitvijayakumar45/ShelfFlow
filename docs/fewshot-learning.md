# Few-shot products: restock, teach, look-alikes and returns

Status: **implemented and covered by automated tests (153 passing), including the vision workers running against the
real backend API in-process. Few-shot accuracy measured on the real pilot crops (below). The teach and restock flows
have not yet been run end to end on the physical shelf.**

## Employee workflow: a product the store has never seen

1. **Products & recognition → Add a product**: ID, name, price. No photos, no training.
2. **Teach** (optional but recommended for brand-new items): pick the tray and 20 s, press *Teach*, then turn the
   product slowly over the tray (front, back, sides, tilted, in hand). Vision keeps up to 20 views, and only those
   that add a new look (cosine < 0.95 to the views already kept). Progress shows live.
3. **Tray loading** wizard as usual: every unit placed is weighed individually, and its before/after stills are cut
   into a reference crop automatically (`Look learned` column: *learning…* → *✓ learned*). Restocking an existing
   product keeps improving its gallery for free.
4. Check **Recognition status**: look-alike warnings and advice appear there. Reject any reference that shows the
   wrong thing (a hand, a neighbouring item). Rejected references leave the live gallery within ~10 s.

Nothing retrains the item detector. It is single-class ("item"), so it already finds unseen products. New products
only change the **gallery** (frozen DINOv2 features), which updates live in the running vision service.

## How it works

| Piece | Where | What it does |
|---|---|---|
| `change_box` | `vision/fewshot.py` | Before/after stills of the tray → box of the newly placed item (exposure-normalised difference, morphology, merged blobs). Refuses `NO_CHANGE` / `GLOBAL_CHANGE` (camera bumped) |
| `augment` | `vision/fewshot.py` | 7 views per crop: original, flip, tighter crop, ±15 % light, ±8° rotation |
| `EnrollmentWorker` | `vision/references.py` | Polls `enrollment-jobs`, crops, checks size and sharpness, posts `RESTOCK_AUTO` references; failures are recorded, not retried forever |
| `TeachCollector` | `vision/references.py` | During teach mode, keeps the largest item crop when it is a new view |
| `ReferenceSync` | `vision/references.py` | Mirrors ACTIVE/REJECTED references into the live gallery, caches embeddings on disk, refits the look-alike guard, reports status |
| `LookalikeGuard` | `vision/fewshot.py` | Finds products whose prototypes are ≥ 0.85 similar; per group, fits a ridge probe on their features and **uses it only if 5-fold accuracy on held-out photos ≥ 90 %** |
| Registry | `backend/references.py` | `product_references`, `teach_sessions` (migration 0010); device-authenticated upload, staff reject/restore, audit trail |

## Similar-looking products within the ESP-cam's limits

The OV3660 at 800×600 cannot read small print reliably, so ShelfFlow never relies on vision alone:
1. **Per-unit weight**, measured at loading, is the first signal (mixed trays).
2. **Position** from the 3-cell tripod tells where the weight left.
3. **Vision**: the gallery needs similarity ≥ threshold *and* a margin over the next product, and track votes must
   agree across frames. A dedicated look-alike check is used only once it has proven itself.
4. **Loading warnings**: `WEIGHT_TOO_CLOSE`, upgraded to `LOOKALIKE_AND_WEIGHT_TOO_CLOSE` when vision also confuses
   the pair. Advice: put them on different trays.
5. Anything still ambiguous goes to staff review. Nothing is guessed.

## Returns

- Weight increase → matched against units in **that shopper's cart from this tray** → unit back `ON_TRAY`, cart and
  stock updated atomically, and the unit's new position recorded on tripod trays.
- Put back on **another tray**: held as `RETURNED_TO_OTHER_TRAY` with the matched unit. Staff confirm in Evidence
  review and the unit is **moved** to the new tray (audited `UNIT_RELOCATED`).
- Matches nothing (foreign object, or an item never loaded) → `WEIGHT_NO_UNIT_MATCH`, shown under *Needs attention*.

## Measured: how many photos does a product need?

`python -m tools.dataset.fewshot_eval --train data/shelf-pilot/objects/train --test data/shelf-pilot/objects/val`.
Real DINOv2 on the GPU, the three pilot products, references from session 1, scored on 50 session-2 crops, 10 random
draws per row, thresholds 0.68 / 0.06. Single crops, before track voting:

| References per product | Correct (plain) | Correct (augmented) | Wrong product |
|---|---|---|---|
| 1 | 42 % | 55 % | ≤ 1.4 % |
| 3 | 62 % | 71 % | ≈ 3 % |
| 5 | 67 % | 71 % | ≈ 1.6 % |
| 10 | 75 % | 80 % | ≤ 1 % |
| all (~45) | 82 % | 86 % | 0 % |

The rest is `UNKNOWN`, not a guess.

**Weakness, measured:** a product absent from the gallery was accepted as a similar known product in 14–44 % of its
crops (sanitizer vs sunscreen), somewhat more with augmentation. That is why loading *is* enrolment, why vision never
charges alone, and why the gallery should not be trusted for products that were never loaded or taught.

**Caveat:** the 0.68 / 0.06 thresholds were selected on these same validation crops, so the table is optimistic.
Record a fresh session including unfamiliar objects before quoting accuracy.

## Next

- Run teach and restock auto-crops on the physical shelf and review the crops (`Look learned`, reference grid).
- Re-run `fewshot_eval` and `gallery-eval` on a fresh session; tune the threshold and margin there, not on the
  validation set.
- If look-alike products are added, check that the guard reports them and that its held-out accuracy holds.
