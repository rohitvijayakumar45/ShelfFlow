# Physical acceptance checklist

No item below has been physically verified during implementation.

## Identify hardware

- [ ] Camera carrier front/back photos, exact model/revision and schematic.
- [ ] ESP32 chip family, module number, flash size, PSRAM type/capacity.
- [ ] OV3660 connector pinout, lens, oscillator/clock/power topology.
- [x] RFID reader family: RC522, confirmed by user.
- [ ] Actual RC522 module revision, pin labels and voltage schematic.
- [ ] Sensor/gate DevKitC revisions and regulator/pin availability confirmed.
- [ ] Load-cell bridge diagrams, rated loads, excitation and mounting drawings.
- [ ] HX711 board supply/logic levels and wiring verified.
- [ ] Servo/actuator actual model, stall current/travel, beam/switch output levels.
- [ ] Supply ratings, grounds, USB/external power routing and decoupling inspected.

## Visibility before training

Record 20–30 representative interactions in actual mounting position. Include two shoppers/different trays, same tray simultaneous, crossed hands, reach-past, rotated/hidden marker, opposite hand, handoff, pickup+return and equal-weight cancellation. Label with volunteers' aliases. Ask a human reviewer to identify wrist/hand, grasp, SKU, direction and quantity independently of RFID recency. If chain is invisible, do not train a model to guess it: adjust camera angle, tray lips/spacing/marker placement, or explicitly restrict supported wrist-hand behavior. No guaranteed camera distance.

## Sensing and timing

- [ ] Explicit tare and known-mass calibration each tray.
- [ ] Unit mass distribution from multiple actual SKU samples; report mean/range/spread, tolerance and sample count.
- [ ] Loaded/unloaded noise, drift over time, overload recovery, disconnect/stuck tests.
- [ ] Cross-talk and placement dependence measured; packaging/cables do not bridge trays.
- [ ] RC522 natural wrist-path read zone mapped; one reader does not cover entire shelf.
- [ ] A/B capture benchmarks under motion: switch delay, gaps, age, upload, blur, marker readability and product crop detail.
- [ ] Camera-controller-laptop offset and uncertainty measured across Wi-Fi loss and reboot.
- [ ] Queue overflow, power-loss atomic writes, ACK loss, offline replay and flash endurance tested.
- [ ] Flush completeness and stable baselines verified before exit; assisted reconciliation for offline devices.

## Gate mechanism

- [ ] Bracelets separate one at a time over representative repeated cycles.
- [ ] Wrong actual UID never activates cart.
- [ ] Jam/no beam/no position pulse faults and accessible manual release tested.
- [ ] Duplicate commands, lost acknowledgments, reboot before/during/after release tested physically.
- [ ] Prior command ledger replay cannot repeat actuation; ledger saturation refuses new motion.
- [ ] Return-bin confirmation, cleaning and unresolved-session reuse guard tested.

## Report separately

Record detector SKU precision/recall/mAP from held-out sessions; event quantity accuracy, ownership accuracy, false charges, missed events, pending fraction, identity failures, data loss, p50/p95/p99 latency. Report denominators, scenario breakdown, timing intervals and number of volunteers. Distinguish all simulated figures from physical results. A low false-charge rate achieved by deferring everything is not proof of good coverage; report pending/missed rates too.

Automatic physical attribution stays disabled until the chain extractor, timing, quantity evidence and sensor safeguards are validated. Neither pretrained pose, box co-visibility nor arbitrary confidence thresholds resolve occluded ownership. Emergency egress never depends on payment or review completion.
