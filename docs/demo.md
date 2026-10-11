# Demonstration script

## Simulator-backed vertical slice

1. Start the API and both apps per README. Log into staff with configured password.
2. Open customer app in a fresh tab; enter a demo alias. Open a second tab for shopper B when needed.
3. Customer → Generate entry QR. Gate & bracelets → scan with an available phone/laptop browser camera or paste the manual token. Do not move the shelf OV3660.
4. Validate & reserve bracelet. In **simulation mode only**, press Simulate verified release within 20 seconds. It simulates actual UID, beam and position feedback; the screen must say ASSIGNED. This is not physical proof.
5. Staff Overview → Simulation bench → select active visit/tray → Pickup. Customer confirmed cart changes live with a new version; inventory falls atomically.
6. Press Return. Matching same-tray cart item is removed and stock increases.
7. Press Pickup again. Customer → Finish simulated shop. No real money is collected. View receipt.
8. Staff → Gate & bracelets → confirm physical return/cleaning (simulated/manual demonstration). Bracelet is not reusable while session or pending events remain unresolved.

CLI alternative: `python -m simulator.demo` runs the same HTTP device ingestion/fusion flow, prints snapshots/receipt, and releases its simulated bracelet with an audited fixture-cleanup reason.

## Two shoppers and ambiguous evidence

Assign two different bracelets. Submit tray A/B pickups concurrently via tests or select each customer in the bench. Both carts can be active; there is no one-shopper restriction. The automated same-tray scenario supplies two independent mock ownership chains in a joint event. It tests software association, not whether a camera can see them.

Hidden marker, crossed hands, camera failure and weight fault buttons create pending hypotheses, never confirmed quantities. Open Evidence review; fixture references explicitly say simulated/no physical video. Confirm only from an established chain, or dismiss with a reason. Unknown owner hypotheses appear as pending for active visits and require staff reconciliation.

For disputes, customer selects a confirmed interaction and submits “I did not take this.” Staff can uphold by dismissal or apply an audited correction. Correction separates cart change from physical stock change, and original paid receipt remains unchanged with appended simulated adjustment visible. Late events after checkout require correction; they never silently rewrite a completed receipt.

## Gate fault recovery

Tests and `/staff/simulation/gate` support WRONG_UID, JAM, UNCERTAIN_RELEASE and SUCCESS. Retry the same command ID to verify no second command/reservation. Timeout quarantines the reserved bracelet; no assignment occurs merely because servo moved. Inspect the physical chute and bracelet UID before manual assignment/reset. Never retry actuator motion blindly after reboot or lost acknowledgment.

## First physical demonstration

Do not announce automatic checkout accuracy. Identify camera board/pins, bring up each sensor separately, calibrate and measure RFID wrist read zone. Record 20–30 interactions. If a human cannot reliably see wrist → hand → grasp → product motion, improve placement or narrow supported actions. Display bracelet-hand and occlusion limitations. Run hardware mode with staff-reviewed attribution and assisted simulated checkout. Emergency egress stays open in all states.
