# Full shop workflow (hardware mode)

Entry → bracelet → verified pickups/returns → exit bill → bracelet return.

**RFID readers:** there are only two RC522 readers, so they are store registries, not shelf readers:
**R1 = entry** (check-in tap), **R2 = exit** (check-out tap). Set in `backend/config.py`
(`RFID_ENTRY_READER`, `RFID_EXIT_READER`; empty turns a role off). Firmware is unchanged: the controller
still reports every tap with its reader name. Vision ignores taps from these two readers, and the assisted
suggestions no longer use them as "who was at the shelf"; at the shelf, identity comes from the bracelet
pattern on camera only. Every step that is not
proven by both the camera and the shelf scale goes to the staff Review queue; nobody is charged on a guess.

## 1. Entry and bracelet assignment

1. Customer app → **Entry & bracelet** → *Generate entry QR* (single use, 2 minutes).
2. Staff app → **Shoppers & checkout → Check-in & bracelets → Check in a customer**: scan the QR, tap the
   bracelet being handed over on the **entry reader R1**, *Assign and start visit* (`POST /staff/check-in`).
   In hardware mode the backend refuses the assignment unless that tag was tapped on R1 in the last 2 minutes.
3. The backend consumes the QR, sets the bracelet `ASSIGNED`, the visit `ACTIVE`, and writes a
   `BraceletBinding` row: **customer ↔ session ↔ RFID UID ↔ pattern markers** (Stripes = 200, Circles = 201),
   with start/end times. Staff see the mapping in `GET /staff/state` → `visit_bindings`.
4. Refused: unregistered tag, bracelet already bound/not cleaned/awaiting return, bracelet with no pattern,
   a second bracelet for the same visit. A bracelet can never belong to two active customers.
5. The customer app shows the active visit, the bracelet and its pattern; the cart is live.

## 2. Pickup and return (camera + weight)

- The shelf scale reports a WEIGHT event; vision opens a collection window around it and sends one
  INTERACTION event citing that weight event (`SENSOR_EVENT:<id>`).
- Vision tracks each item, the hands, and the bracelet bands (`band-stripe` / `band-circle` from the
  Train products model). The band next to the hand that moved an item names the bracelet.
- Backend fusion (`backend/fusion.py`) checks: bracelet → active session, hand–item–trajectory chain,
  product (vision agrees with weight), weight change vs. the product's unit weight and tolerance (or the
  individually weighed units on a unit-tracked tray), tray, stock, sensor health, calibration version,
  timing (strict auto-add: the weight event arrived during the camera window).
- All checks pass → cart and stock update at once and the customer app shows a toast. Returns (weight
  increase + item arriving under the hand) remove the quantity from the same customer's cart and restore
  stock; a return without a matching cart quantity is refused (`RETURN_WITHOUT_MATCHING_CART`).
- Duplicates: each shelf weight event can be used once (`SensorClaim`), each item trajectory once
  (`ALREADY_CONSUMED_PRODUCT_TRAJECTORY`). A repeated camera report goes to review instead of charging twice.

## 2b. RFID attribution (one shopper at the shelf at a time)

Until the bracelet bands and hand tracking are trained, set `CART_ATTRIBUTION=rfid`
(`backend/shelf_cart.py`). The shelf weight itself drives the cart:

- **Who:** the shopper whose bracelet was **last tapped on either reader (R1 or R2)** after check-in, or
  who checked in most recently, within `SHELF_PRESENCE_S` (default 300 s). Another shopper's tap takes over.
  The check-in tap itself does not count (the bracelet is not bound yet).
- **When:** every settled shelf weight step of 5 g or more. Weight down is a pickup, weight up a return.
  Steps while a tray-loading session is open are stock, not shopping.
- **What:** on a tray with weighed units, the units whose weights add up to the change (the camera breaks
  a tie and must not contradict it). Otherwise the product the camera stopped seeing (pickup) or started
  seeing (return), compared over the time since the tray's previous weight step; checked against the
  product's unit weight when one is set. The backend samples the camera (`CAMERA_URL`) twice a second
  for this (`backend/camera_watch.py`).
- All checks pass and `AUTO_ADD=true`: the cart changes at once and both apps show it. Anything unproven
  (no shopper tapped, camera offline or unsure, weight does not fit, return not in that cart) goes to the
  Review queue with the shopper and product filled in; staff pick the product if needed and press Record.
- Checkout: the customer presses **Checkout** in the app (`POST /customer/checkout-request`); the app says
  to go to the exit, tap the bracelet on the exit reader R2 and hand it over. That R2 tap bills the visit
  once nothing blocks it (section 4); *Keep shopping* withdraws the request. Without a request, an R2 tap is an
  ordinary tap that marks the shopper at the shelf. A shopper who pressed Checkout is no longer counted as at
  the shelf. Staff can still check out from **Shoppers & checkout**. Camera INTERACTION events are stored as
  evidence only, so nothing is charged twice.
- Two shoppers at the shelf at the same time cannot be told apart this way: the last tap wins. That is
  what `CART_ATTRIBUTION=camera` (bracelet pattern at the hand, section 3) is for.

## 2c. Hybrid (two shoppers)

`CART_ATTRIBUTION=hybrid`: section 2b while one shopper is at the shelf; with two or more (both tapped or
checked in within `SHELF_PRESENCE_S`) the camera confirms a bracelet once its pattern is in 5 frames in a row
(`band_confirm_frames` in vision-config, `vision/hands.py` BandConfirmer) and the weight step goes to the
customer whose bracelet was confirmed nearest in time (one step per confirmation). No confirmation within 35 s:
Review queue, with the shoppers who were at the shelf (`BRACELET_SEEN`, `SHELF_WEIGHT_WAITING_FOR_CAMERA`,
`SHELF_WEIGHT_TO_REVIEW` in the audit log).
Needs the vision service (`tools.local_app vision`) and a model trained with both bands.

## Live staff screens

The USB bridge forwards the controller's live and held tray weight once a second
(`POST /devices/sensor-1/live`), and staff screens hold a WebSocket (`/ws/staff`) that pushes it, plus a
nudge after every committed cart, review or shelf event so the page reloads at once. The tray weight
shows like the shelf LCD: the held whole grams while the tray is still, the live reading while it changes.
Polling every 2 s stays as the fallback. The customer cart already updates through `/ws/cart`.

## 3. Two customers on the same shelf

- When several items move in one window, vision sends **one candidate per moved item**
  (`vision/hands.py` `track_candidates`), each carrying only the band seen at the hand that moved it.
- The shared scale only checks the **total** change. It is never used to decide who took what.
- Charged per customer only when every hand names exactly one bracelet and its product. Otherwise
  (both bands at one hand, unknown product, cancelling pickup + return, weight tie) the event is held
  for staff review (`IDENTITY_UNPROVEN`, `OWNER_PRODUCT_UNRESOLVED`, …).

## 4. Exit and billing

Exit detection is the **exit reader R2**:

1. The customer taps the bracelet on R2. The backend records an exit request for that bracelet's active visit
   (`EXIT_TAP` audit) and bills it at once if nothing blocks it; otherwise a backend worker
   (`EXIT_WORKER=true`, set by `tools.local_app backend`) retries every 3 s and bills as soon as the
   blockers clear. An exit request that still cannot be billed after 2 minutes expires (the customer may
   have gone back to shopping) and staff check out by hand. Taps on R2 with no active visit are ignored.
   Staff app → **Shoppers & checkout → Visits & checkout → Check out at the exit** lists visits waiting at
   the exit and why; *Find by bracelet tap* (on R2) or the visit list opens one for a manual check-out.
2. The screen lists anything blocking the bill (`GET /staff/visits/{id}` → `exit_blockers`):
   pending/disputed events (go to the Review queue), events still processing, or a shelf weight change in
   the last ~25 s that no camera event has used yet (wait).
3. *Check out and generate bill* (`POST /staff/exit`): the cart is frozen into an order, a receipt is
   created, the visit becomes `COMPLETE`. The payment key is `exit-<session>`, so pressing it again or a
   repeated exit request returns the same bill; never a second one.
4. **Payment is simulated**: no payment provider is connected. The receipt says
   `bill_status: FINAL`, `payment_status: SIMULATED_NOT_COLLECTED`, and both apps say no money was collected.
5. The bracelet binding ends at exit (the camera stops mapping that bracelet to the customer) and the
   bracelet becomes `AWAITING_RETURN`. It is released for the next customer only when staff confirm the
   return on the same screen (*Bracelet … is back*), optionally marking it as still needing cleaning.

Still missing: a physical exit gate (R2 registers the exit; it does not stop anyone leaving), shelf RFID
readers (none left over), and a real payment adapter (the `Payment.adapter` field is `SIMULATED` today).

## Switching it on

In `data/integration/runtime.env`: `WEIGHTS_ENABLED=true` and `AUTO_ADD=true` (and `CART_ATTRIBUTION=rfid`
for section 2b), then restart the backend, the USB bridge and vision. Before that: register both bracelets with their pattern, train the two bands on the Train
products page, and load the tray (unit weights) with the tray-loading wizard.
