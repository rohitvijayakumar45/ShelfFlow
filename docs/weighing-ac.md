# A/C weighing pipeline

The complete controller is `firmware/shelf-controller/src/main.cpp`. The reusable numerical core is `include/weighing_pipeline.h`; it is independent of Arduino and hardware I/O. `include/scale_solver.h` performs known-mass calibration. `include/weighing_self_test.h` contains the numerical regression checks used by the optional ESP32 startup self-test and the host test wrapper.

## Acquisition and fixed calibration

`sampleCells()` only reads new signed integer ADC counts and records freshness/noise windows. `processWeighingSample()` waits until every active module has supplied a new sample, then advances the filter once. Polling the main loop or refreshing the LCD never advances the EMA. B is not part of the active-cell array and remains in HX711 power-down in the A/C build.

For each cell:

```cpp
calibratedGrams = (double(rawCounts) - zeroCounts) * gramsPerCount;
```

The previous NVS and backend representation, `counts_per_g`, is preserved for compatibility. Its reciprocal is calculated once on calibration load/save:

```cpp
gramsPerCount = 1.0 / countsPerGram;
```

Tare and AZT never change these coefficients, the calibration zero, or ADC samples. Negative factors are valid when a cell's output polarity is reversed. Factors must be finite, nonzero and within the configured supported range.

The service calibration still captures EMPTY, the same known mass near A, the same mass near C, then an independent centre verification. The fixed coefficients solve these two equations:

```text
knownMass = deltaA_at_A * gainA + deltaC_at_A * gainC
knownMass = deltaA_at_C * gainA + deltaC_at_C * gainC
```

Do not assign the entire known mass separately to both cells from one shared tray reading: that double-counts the load. Singular or nearly identical calibration positions are rejected. Calibration captures use a separate median of the raw window, so EMA lag and display deadband cannot alter the span calculation.

## Raw-domain filtering and summation

With different gains or opposite signs, `rawA + rawC` is not a meaningful common scale. The implementation combines gain-normalised ADC counts before its combined EMA:

```text
combinedCounts = sum((raw[i] - zero[i]) * (gain[i] / referenceGain))
filteredCounts += alpha * (combinedCounts - filteredCounts)
calibratedTotalGrams = filteredCounts * referenceGain
```

`referenceGain` is a fixed unit conversion, not a second span calibration. Parallel per-cell raw EMAs use the same alpha and sample pairs. By linearity their calibrated gram contributions sum to the same combined result. The final net total is the sum of the two cell contributions after independent tare and zero-tracking corrections.

ADC values stay integers; filtering, reference subtraction and arithmetic use double precision. The event baseline stores filtered raw counts, matching the filtered measurement. A settled event requires the unfiltered/filtered gram difference to be within 0.5 g as well as the existing noise/dwell checks, avoiding a false opposite event while EMA catches up.

## Configuration

Edit `WeighingConfig` in `include/weighing_pipeline.h`. The controller instantiates it as `SCALE_CONFIG` near the top of `main.cpp`.

| Setting | Default | Meaning |
|---|---:|---|
| `emaAlpha` | 0.25 | Once per fresh complete ADC pair; smaller is smoother and slower |
| `referenceGramsPerCount` | 0.0025 | Fixed common-count normalisation; individual calibration remains separate |
| `zeroDeadbandG` | 0.5 | Both unfiltered and filtered signals must remain inside ±0.5 g |
| `zeroDwellMs` | 2000 | Continuous qualifying time before tracking starts |
| `zeroRateGPerSecond` | 0.02 | Maximum sum of absolute cell corrections per second |
| `zeroMaximumCorrectionG` | 0.5 | Absolute correction budget relative to the confirmed empty reference |
| `zeroMaxNoiseG` | 0.15 | Maximum measured raw-window noise for AZT |
| `settleLagG` | 0.5 | Maximum unfiltered/filtered difference before normal settled weight events |
| `staleMs` | 1000 | Missing-pair deadline; gaps reseed the EMA rather than blending stale samples |
| `autoZeroEnabled` | true | Still requires explicit operator confirmation of an empty tray |

## Safe zero tracking

An arbitrary loaded tare is not evidence that the tray is empty. Normal long-ACTION tare therefore leaves AZT unarmed. With the tray actually empty, the explicit Serial command `tareempty` captures a stable temporary reference and confirms empty. `tare` captures any reference load without arming AZT. `untare` clears the temporary reference and AZT correction. The button controls remain ACTION-long for ordinary tare, PAGE-long to clear, and both held for service mode.

Once empty is confirmed, both the raw net signal and the filtered signal must stay inside the deadband continuously for two seconds. Noise, held buttons, service mode, unhealthy/stale ADCs or a signal outside the band reset this dwell. The unfiltered signal freezes tracking immediately on a new ADC sample that exceeds the band, even while the EMA remains close to zero. A second guard compares raw data against the original empty reference, so accumulated corrections cannot walk the acceptance window indefinitely.

The correction is rate-limited and bounded independently of the saved calibration. Opposing per-cell corrections consume an absolute budget rather than cancelling into an apparently harmless total. Corrections are volatile and never written to NVS. Large drift is intentionally left visible instead of being tracked away.

After qualification, display deadband shows exactly `0.0`. Logged/fused net grams retain the residual; the clamp is presentation-only. A sub-deadband load cannot reliably be distinguished from drift using weight measurements alone. A 44 g object that was tared to zero must not be marked empty.

## Serial diagnostics

At 115200 baud, debug traces default to one line per second. `scale debug on` and `scale debug off` control them. The bridge also records these lines locally in `data/integration/usb/rfid-diagnostics.jsonl`; it remains the sole COM3 owner. If using a separate Serial Monitor to send commands, stop the bridge first and restart it afterward.

Each `WEIGH` line includes the paired ADC timestamp, signed integer raw A/C counts, raw EMA values, fixed calibration zero/gain for each channel, per-cell net grams, combined equivalent raw/EMA counts, untared calibrated total, unfiltered net total, final net grams, displayed grams and AZT correction/status. Trace output is skipped if the UART TX buffer lacks room, rather than blocking acquisition.

If raw counts move while the zero and gain stay constant, the signal is changing at acquisition. If raw counts and factors stay constant while computed grams change, investigate the numerical/state path. EMA reduces jitter but does not remove sustained physical creep or an incorrect mechanical load path.

## Build and regression checks

```powershell
.\.venv\Scripts\python.exe -m platformio run -d firmware/shelf-controller -e devkitc-v4-usb-ac
.\.venv\Scripts\python.exe -m pytest -q
```

The A/C environment includes an optional numeric startup check (`WEIGHING_SELF_TEST`). It reports `WEIGH_SELFTEST PASS line=0`; a failed boot test prevents valid scale configuration. The diagnostic command `scale selftest` reruns the same pure numerical tests without changing the physical reference. These checks cover signed scaling, raw-domain EMA, repeated tare without accumulation, preservation of real deltas, AZT dwell/rate/budget/load guards, arbitrary loaded references, noise/stale/invalid ADCs and timer rollover. Native host execution may be blocked by Windows Application Control; compiler success alone is not a runtime pass.

Validation on 24 October: both firmware configurations compiled; the A/C image was flashed and hash-verified on main controller `00:4B:12:EE:81:F4`. The numerical checks ran on that ESP32 and returned `WEIGH_SELFTEST PASS line=0`, including invalid calibration-zero/tare bounds. The Python integration suite passed 183 tests. Ten live debug traces agreed with independently recomputed raw-to-gram arithmetic and per-cell sums within 0.0001 g. The saved `ac-aeda1b-6` calibration was preserved, and B remained disabled. Evidence is in ignored `data/hardware-backups/weighing-pipeline-final-20261024.json` and `weighing-pipeline-postflash-20261024.json`.

These checks verify implementation behavior, not physical accuracy: the live C raw count changed from −52,447 to −53,949 in the first trace capture while its gain stayed fixed. The previous temporary tare was cleared by restart, so the untared reading is visible. No empty-tray confirmation was invented and AZT remains unarmed until the operator runs `tareempty` with an actually empty tray.

The previous three-cell mounting limitation remains: an unmeasured B support can carry part of the physical load. The new math does not establish whole-tray accuracy under that mechanical arrangement. Validate known masses at several positions before relying on demo measurements.
