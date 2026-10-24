#pragma once
#include <stdint.h>
#include <math.h>

struct WeighingConfig {
  double emaAlpha = 0.25;                 // Per complete ADC pair, not per loop iteration.
  double referenceGramsPerCount = 0.0025; // Normalisation unit only; never recalibrated by tare/AZT.
  double zeroDeadbandG = 0.5;
  double zeroCaptureG = 0.5;              // AZT may act only while |net| is inside this window (never below the deadband).
  double zeroFreezeG = 0;                 // One raw sample beyond this freezes AZT at once (0: the capture window).
  uint32_t zeroDwellMs = 2000;
  double zeroRateGPerSecond = 0.02;
  double zeroMaximumCorrectionG = 0.5;   // Absolute correction budget since confirmed empty tare.
  double zeroMaxNoiseG = 0.15;
  double settleLagG = 0.5;
  uint32_t staleMs = 1000;
  bool autoZeroEnabled = true;
};

// Pure numerical core: no ADC I/O, Serial, NVS, allocation, or blocking delays.
// ADC counts, fixed calibration, manual reference and AZT are separate state.
template<int N>
class WeighingPipeline {
public:
  struct Calibration { double zeroCounts; double gramsPerCount; };
  struct Reading {
    bool valid = false, zeroTracking = false, deadbandActive = false, loaded = false;
    uint32_t capturedMs = 0;
    int32_t raw[N] = {};
    double filteredRaw[N] = {};
    double cellGrams[N] = {};
    double combinedRawCounts = 0, filteredCombinedCounts = 0;
    double calibratedTotalG = 0, rawReferenceG = 0, rawNetG = 0, netG = 0, displayG = 0;
    double zeroCorrectionG = 0, zeroCorrectionAbsG = 0;
  };
private:
  WeighingConfig config;
  Calibration calibration[N] = {};
  double tareG[N] = {}, zeroCorrection[N] = {};
  bool excluded[N] = {}; // Does not disable ADC acquisition.
  // Linear zero-drift model measured by the quick check: the zero of cell i moves by
  // driftRate[i] counts/s. It is extrapolated for a limited time only, then held.
  double driftRate[N] = {}, driftCounts[N] = {};
  uint32_t driftRemainingMs = 0;
  Reading reading;
  bool calibrated = false, seeded = false, emptyConfirmed = false, dwelling = false;
  uint32_t dwellStarted = 0, previousMs = 0;

  void advanceDrift(uint32_t elapsedMs) {
    const uint32_t step = elapsedMs < driftRemainingMs ? elapsedMs : driftRemainingMs;
    if (!step) return;
    for (int i = 0; i < N; ++i) driftCounts[i] += driftRate[i] * step / 1000.0;
    driftRemainingMs -= step;
  }
  double zeroAt(int i) const { return calibration[i].zeroCounts + driftCounts[i]; }
  double captureG() const { return fmax(config.zeroCaptureG, config.zeroDeadbandG); }
  double freezeG() const { return fmax(config.zeroFreezeG, captureG()); }

  void calculateOutputs() {
    reading.calibratedTotalG = reading.filteredCombinedCounts * config.referenceGramsPerCount;
    reading.netG = reading.rawReferenceG = reading.rawNetG = reading.zeroCorrectionG = reading.zeroCorrectionAbsG = 0;
    for (int i = 0; i < N; ++i) {
      reading.cellGrams[i] = netAt(i, reading.filteredRaw[i]);
      if (excluded[i]) continue;
      reading.netG += reading.cellGrams[i];
      reading.rawNetG += netAt(i, reading.raw[i]);
      reading.rawReferenceG += calibratedAt(i, reading.raw[i]) - tareG[i];
      reading.zeroCorrectionG += zeroCorrection[i];
      reading.zeroCorrectionAbsG += fabs(zeroCorrection[i]);
    }
    reading.displayG = reading.netG;
  }
public:
  explicit WeighingPipeline(const WeighingConfig& settings = WeighingConfig()) : config(settings) {}

  bool included(int i) const { return i >= 0 && i < N && !excluded[i]; }
  bool setIncluded(int i, bool enabled) {
    if (i < 0 || i >= N) return false;
    if (excluded[i] == !enabled) return true;
    excluded[i] = !enabled;
    // Rebuild from per-cell EMAs instead of blending two calculation modes.
    reading.combinedRawCounts = reading.filteredCombinedCounts = 0;
    for (int j = 0; j < N; ++j) if (!excluded[j]) {
      reading.combinedRawCounts += calibratedAt(j, reading.raw[j]) / config.referenceGramsPerCount;
      reading.filteredCombinedCounts += calibratedAt(j, reading.filteredRaw[j]) / config.referenceGramsPerCount;
    }
    clearTare();
    return true;
  }

  bool configure(const Calibration (&values)[N]) {
    calibrated = false; seeded = false; reading = Reading();
    clearTare(); clearDrift();
    if (!isfinite(config.emaAlpha) || config.emaAlpha <= 0 || config.emaAlpha > 1 ||
        !isfinite(config.referenceGramsPerCount) || config.referenceGramsPerCount <= 0 ||
        !isfinite(config.zeroDeadbandG) || config.zeroDeadbandG <= 0 ||
        !isfinite(config.zeroCaptureG) || !isfinite(config.zeroFreezeG) ||
        !isfinite(config.zeroRateGPerSecond) || config.zeroRateGPerSecond < 0 ||
        !isfinite(config.zeroMaximumCorrectionG) || config.zeroMaximumCorrectionG < 0 ||
        !isfinite(config.zeroMaxNoiseG) || config.zeroMaxNoiseG < 0 ||
        !isfinite(config.settleLagG) || config.settleLagG <= 0 || config.staleMs == 0) return false;
    for (int i = 0; i < N; ++i) {
      if (!isfinite(values[i].zeroCounts) || values[i].zeroCounts < -8388608 || values[i].zeroCounts > 8388607 ||
          !isfinite(values[i].gramsPerCount) ||
          fabs(values[i].gramsPerCount) < 0.00001 || fabs(values[i].gramsPerCount) > 0.1) return false;
    }
    for (int i = 0; i < N; ++i) calibration[i] = values[i];
    calibrated = true; return true;
  }

  double calibratedAt(int i, double counts) const {
    return (counts - zeroAt(i)) * calibration[i].gramsPerCount;
  }
  double netAt(int i, double counts) const {
    return calibratedAt(i, counts) - tareG[i] - zeroCorrection[i];
  }
  // Counts with the drift accumulated so far removed. A reference stored in this
  // form keeps its meaning while the modelled zero moves on (event baselines).
  double driftCorrected(int i, double counts) const { return counts - driftCounts[i]; }
  double netAtCorrected(int i, double corrected) const {
    return (corrected - calibration[i].zeroCounts) * calibration[i].gramsPerCount - tareG[i] - zeroCorrection[i];
  }
  // Start extrapolating a measured zero drift (counts per second per cell) for horizonMs.
  bool setDrift(const double (&countsPerSecond)[N], uint32_t horizonMs) {
    for (double rate : countsPerSecond) if (!isfinite(rate) || fabs(rate) > 10000) return false;
    for (int i = 0; i < N; ++i) { driftRate[i] = countsPerSecond[i]; driftCounts[i] = 0; }
    driftRemainingMs = horizonMs;
    if (seeded && calibrated) calculateOutputs();
    return true;
  }
  void clearDrift() {
    for (int i = 0; i < N; ++i) driftRate[i] = driftCounts[i] = 0;
    driftRemainingMs = 0;
  }
  uint32_t driftRemaining() const { return driftRemainingMs; }
  double driftOffsetCounts(int i) const { return driftCounts[i]; }
  double driftRateCounts(int i) const { return driftRate[i]; }
  bool setTare(const double (&referenceG)[N], bool operatorConfirmedEmpty) {
    if (!calibrated) return false;
    for (double value : referenceG) if (!isfinite(value) || fabs(value) > 1677721.6) return false;
    for (int i = 0; i < N; ++i) { tareG[i] = referenceG[i]; zeroCorrection[i] = 0; }
    emptyConfirmed = operatorConfirmedEmpty; dwelling = false;
    reading.zeroTracking = reading.deadbandActive = false;
    if (seeded) calculateOutputs();
    return true;
  }
  void clearTare() {
    for (int i = 0; i < N; ++i) tareG[i] = zeroCorrection[i] = 0;
    emptyConfirmed = dwelling = false;
    reading.zeroTracking = reading.deadbandActive = false;
    if (seeded && calibrated) calculateOutputs();
  }
  void suspendZeroTracking() {
    dwelling = false; reading.zeroTracking = reading.deadbandActive = false;
  }
  bool fresh(uint32_t now) const {
    return reading.valid && uint32_t(now - reading.capturedMs) <= config.staleMs;
  }
  bool settled() const {
    return reading.valid && fabs(reading.rawNetG - reading.netG) <= config.settleLagG;
  }
  bool confirmedEmpty() const { return emptyConfirmed; }
  bool hasCalibration() const { return calibrated; }
  const Reading& value() const { return reading; }

  // Call once only after every active cell has supplied a NEW integer ADC sample.
  // Different signs/gains are normalised before combining, so opposing wiring
  // cannot cancel the load as it would in a naive rawA + rawC sum.
  bool push(const int32_t (&raw)[N], uint32_t now, bool healthy,
            double measuredNoiseG, bool allowZeroTracking = true) {
    if (!calibrated) { reading.valid = false; suspendZeroTracking(); return false; }
    uint32_t elapsed = seeded ? uint32_t(now - previousMs) : 0;
    advanceDrift(elapsed);
    double combined = 0;
    for (int i = 0; i < N; ++i) {
      if (raw[i] < -8388608 || raw[i] > 8388607) { reading.valid = false; suspendZeroTracking(); return false; }
      if (!excluded[i]) combined += (double(raw[i]) - zeroAt(i)) *
                  (calibration[i].gramsPerCount / config.referenceGramsPerCount);
    }
    if (!isfinite(combined)) { reading.valid = false; suspendZeroTracking(); return false; }
    const bool reseed = !seeded || elapsed > config.staleMs;
    if (reseed) suspendZeroTracking();
    for (int i = 0; i < N; ++i) {
      reading.raw[i] = raw[i];
      reading.filteredRaw[i] = reseed ? raw[i] :
        reading.filteredRaw[i] + config.emaAlpha * (double(raw[i]) - reading.filteredRaw[i]);
    }
    reading.combinedRawCounts = combined;
    reading.filteredCombinedCounts = reseed ? combined :
      reading.filteredCombinedCounts + config.emaAlpha * (combined - reading.filteredCombinedCounts);
    reading.capturedMs = previousMs = now; seeded = true; reading.valid = healthy;
    calculateOutputs();
    reading.loaded = fabs(reading.rawNetG) > freezeG() ||
                     fabs(reading.rawReferenceG) > freezeG();
    // Use the UNFILTERED net signal as well as EMA: a real load freezes AZT
    // immediately on its ADC sample, even if the filtered reading still looks zero.
    const bool eligible = healthy && allowZeroTracking && config.autoZeroEnabled && emptyConfirmed &&
      isfinite(measuredNoiseG) && measuredNoiseG <= config.zeroMaxNoiseG &&
      !reading.loaded && fabs(reading.netG) <= captureG();
    reading.zeroTracking = reading.deadbandActive = false;
    if (!eligible) { dwelling = false; return reading.valid; }
    if (!dwelling) { dwelling = true; dwellStarted = now; return reading.valid; }
    if (uint32_t(now - dwellStarted) < config.zeroDwellMs) return reading.valid;
    reading.zeroTracking = true;
    // Limit the SUM of absolute per-cell adjustments: opposing offsets cannot
    // grow without bound while their total appears close to zero.
    double residualAbs = 0;
    for (int i = 0; i < N; ++i) if (!excluded[i]) residualAbs += fabs(reading.cellGrams[i]);
    double remaining = fmax(0.0, config.zeroMaximumCorrectionG - reading.zeroCorrectionAbsG);
    double stepBudget = fmin(remaining, config.zeroRateGPerSecond * fmin(elapsed, uint32_t(250)) / 1000.0);
    const double fraction = residualAbs > 0 ? fmin(1.0, stepBudget / residualAbs) : 0;
    for (int i = 0; i < N; ++i) if (!excluded[i]) zeroCorrection[i] += reading.cellGrams[i] * fraction;
    calculateOutputs();
    // Deadband is PRESENTATION ONLY. Logged/fused netG retains the residual.
    reading.deadbandActive = fabs(reading.netG) <= config.zeroDeadbandG;
    if (reading.deadbandActive) reading.displayG = 0;
    return reading.valid;
  }
};
