# Live weighing window and three-cell web calibration

Run the normal USB bridge, then `.venv\Scripts\python.exe -m tools.weight_monitor`.
Open http://127.0.0.1:8767/. The bridge remains the sole COM3 owner.
Use the `devkitc-v4-usb-abc` firmware environment for all three channels.

The page always shows A, B and C panels. `RAW` telemetry reads each HX711's latest
signed integer ADC code independently, before any numerical processing. Per-cell
sample age and validity distinguish missing data from a real zero. A failed or
uncalibrated weighing pipeline does not hide healthy channels. Actual gram values
come from separate `WEIGH` telemetry; unavailable values are shown as dashes.
Raw and calculated data refresh approximately once a second.

## Calibration

1. Empty the tray. Click **Capture empty tray**, then keep hands off. Wait for
   the page to advance to A. This records the three raw empty offsets temporarily.
2. Put the same measured **44 g** reference near A. Release it, click **Capture
   44 g near A**, and wait for confirmation.
3. Move the reference near B; capture B. Move it near C; capture C.
4. Move it to the centre, release it, and click **Verify centre & save**.
   The independent check must be within 2 g of 44 g. On failure, calibration is
   not saved. Investigate drift, mounting, contacts or reference placement.
5. After **Calibration saved**, click **Cancel / leave calibration** to return
   to ordinary weighing. Despite its general label, this does not undo a saved
   calibration. Remove the reference and verify zero; test all positions again.

All three channels are captured at every position. The solver calculates fixed
gains from `sum((raw[i] - empty[i]) * gain[i]) = 44` at the three positions. It
does not assign 44 g separately to each channel. Captures use raw-window medians,
require fresh, nonsaturated readings with raw standard deviation at most 400
counts, and wait for one continuous second of stability (20-second timeout).
This readiness test is independent of an old, incorrect gram calibration.
Singular/poorly separated responses and invalid gains are rejected. Calibration
freezes AZT and ordinary weight event detection. Pending offsets and gains are
applied only after successful centre verification; cancellation before saving
retains the previous calibration. Raw data are never tared or scaled in storage.

The existing three-cell NVS profile is separate from the A/C-only profile. Until
the new sequence is saved, the old three-cell calibration may be displayed;
do not treat those gram values as newly validated. Firmware upload clears the
temporary tare but preserves the saved calibration profiles.

## Zero and drift

**Exclude B from total / Include B in total** switches the controller's runtime
calculation between A+C and A+B+C. B remains electrically active and its raw
ADC samples and drift chart stay visible. The switch is allowed only outside
service calibration and pending tare operations. It clears the temporary tare
and AZT confirmation, resets the event baseline, and retains all saved gains.
Zero the empty tray again after switching. Three-cell web calibration requires
B to be included. The selection is temporary: controller restart includes B.

With B excluded, the total is a partial support sum, not a validated whole-tray
mass. Automatic inventory weight-change events are paused and live health is
flagged `PARTIAL_WEIGHT_B_EXCLUDED`. This prevents the missing contribution or
the calculation-mode step from being treated as a pickup/return. Re-enabling B
starts a new event baseline. Both modes use the same independent raw-cell EMAs;
switching rebuilds their sum immediately instead of blending the two modes.

**Zero empty tray** changes only the volatile reference through the controller's
stable empty-tare command; it is available outside service calibration. It also
arms the tightly bounded empty-zero tracking. **Start drift reference now** only
changes the browser's baseline. Neither action changes raw ADC codes or span
gains. New drift references start from the next fresh sample.

Requests use a local same-origin POST, a strict command allowlist, the current
controller boot ID and a ten-second expiry. The USB bridge consumes each request
before dispatch, so a reconnect cannot replay it. Both page/server and firmware
check calibration order. The page waits for actual controller state, rather than
claiming completion when the request is merely queued.

Software tests verify signed three-cell summation, the three-position solver,
centre prediction, singular-matrix rejection, independent raw logging and
one-shot command delivery. They do not establish physical accuracy or cure
electrical/mechanical drift. Use an unchanged load to measure drift.

## Quick calibration before a demo (44 g, under a minute)

The two 10 kg cells drift by up to about 1 g/min while they settle. Before each demo press
**Start** under **Quick calibration** at the top of the weight monitor (or `qc start` on the serial
console; `qc cancel` / a long PAGE press cancels). Then follow the prompt; every move is detected
automatically, just let go each time:

1. Tray empty (5 s measurement).
2. 44 g right over cell A, left (5 s).
3. Moved right over cell C, right (5 s).
4. Moved to the centre (5 s).
5. Taken off (5 s).

Each window starts once the tray has been still for 1 s; touching the tray restarts that window.
Each cell's samples are fitted by least squares to `zero + rate*t + load[position]`
(`include/drift_check.h`), so the drift is removed before the A and C loads are used to solve the two
gains, and the centre load is an independent check (+-2 g). On success the gains and zeros are saved
as calibration `ac2-<boot>-<n>q` and the measured drift is extrapolated for 15 minutes (then held).
It refuses, changing nothing, if something moved (fit scatter > 1.5 g), the A and C readings are too
similar, the centre check is off, or a cell drifts faster than 6 g/min.

Other weighing safeguards:
- Zero tracking pulls an empty, still tray back to zero from up to 2 g away (3 g/min, 3 g total
  since the last confirmed-empty zero). Any step over 2 g freezes it at once.
- A single HX711 read more than 60 000 counts (~240 g) away from both neighbours is dropped as a
  corrupted transfer; `glitches` in the controller state counts them.
- The event baseline follows the reading while it stays within the 8 g hysteresis, so slow drift
  never turns into a false pickup/return event.
