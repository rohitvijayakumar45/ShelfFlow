# Mixed-product trays (unit tracking)

Status: **backend implemented and covered by automated tests (simulation data). Not yet exercised on the physical shelf.** The staff loading wizard and the vision placement/emptied-spot capture are the next steps.

The original first deployment assumed one product type per tray. This is a deliberate extension: a tray (about 20 cm, ~3 items) may hold different products. A tray switches into this mode only while it is empty, and single-product trays keep working unchanged.

## Loading (staff only, audited)

1. `POST /staff/trays/{tray}/unit-tracking` `{enabled: true, reason}` — tray must be empty.
2. `POST /staff/trays/{tray}/loads` `{reason}` opens a loading session.
3. For each item: place **one** item, wait until the shelf controller reports a settled weight change, then
   `GET /staff/trays/{tray}/weight-events?since=<load start>` and
   `POST /staff/loads/{load}/units` `{product_id, weight_event_id, spot, crop_refs}`.
4. `POST /staff/loads/{load}/close`.

The unit weight is the **difference between two stable readings** (post − pre) of a real `WEIGHT` event. The scale is never auto-tared. The event must be settled, healthy, from this tray, recorded after the session opened, and on the tray's current calibration. It is then claimed, so it can never also count as a pickup. Each loaded unit rebuilds the product's measured weight distribution. A `WEIGHT_TOO_CLOSE` warning is returned when a different product on the same tray could be confused by weight alone.

## Pickup and return fusion (`backend/fusion.py: evaluate_units`)

Signal 1 is weight: the change is matched against units **on the tray** (pickup) or units **in the candidate shopper's cart from this tray** (return), up to 3 units per event, with tolerance `3·√(event noise² + Σ unit noise²) + 1 g`. A match is accepted only if exactly **one product combination** fits. Interchangeable units of the same product are not an ambiguity; the closest-weight unit is moved. Signal 2 is the detector/gallery verdict (`Candidate.product_id`, `sku`) and the emptied spot (`Candidate.spot`).

| Situation | Veto (goes to staff review) |
|---|---|
| No combination fits | `WEIGHT_NO_UNIT_MATCH` |
| Several product combinations fit and vision does not name every unit | `WEIGHT_AMBIGUOUS` |
| Vision names a product outside the weight match | `VISION_WEIGHT_CONFLICT` |
| Hand count ≠ matched unit count | `QUANTITY_MISMATCH` |
| Emptied spot holds none of the matched units | `SPOT_CONFLICT` |
| Several shoppers and several products, a hand without a named product | `OWNER_PRODUCT_UNRESOLVED` |
| Weight change within noise while hands moved | `ZERO_NET_REQUIRES_REVIEW` |
| Weight direction contradicts pickup/return | `ACTION_WEIGHT_CONFLICT` |

All existing identity, ownership-chain, sensor, clock, camera, late-event and evidence-reuse vetoes still apply. In hardware mode everything stays review-first. Vision `UNKNOWN` with a unique weight match may confirm in simulation. Confirmed units move `ON_TRAY → IN_CART` (or back on return) in the same transaction as the cart, inventory and outbox. Tray stock always equals the number of `ON_TRAY` units.

Staff resolve ambiguity with `POST /staff/review/{id}` and `unit_ids`, and corrections name the physical units (`unit_ids`) so unit state, cart and stock stay consistent.

## Known limits

- Products closer in weight than the tolerance cannot be separated by weight; vision or review decides.
- A returned unit's spot is unknown until re-observed, so spot checks on it are conservative.
- More than 3 units changing in one event is not matched automatically.
- Physical tolerance depends on measured noise after calibration on the real platform.
