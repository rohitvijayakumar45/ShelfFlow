#pragma once
#include <stdint.h>
#include <math.h>

// Volatile zero reference: calibration and raw readings stay unchanged.
// Request on long press, capture only after release and a stable hands-off interval.
template<int N>
class ManualTare {
  float offsets[N] = {};
  bool requested = false, timingStill = false, enabled = false;
  uint32_t requestedAt = 0, stillSince = 0, appliedCount = 0;
public:
  enum Result { WAITING, READY, TIMED_OUT };
  void request(uint32_t now) { requested = true; requestedAt = now; timingStill = false; }
  void cancel() { requested = false; timingStill = false; }
  bool pending() const { return requested; }
  bool active() const { return enabled; }
  uint32_t count() const { return appliedCount; }
  float offset(int i) const { return offsets[i]; }
  float totalOffset() const { float sum = 0; for (float value : offsets) sum += value; return sum; }
  float grams(int i, float calibratedGrams) const { return calibratedGrams - offsets[i]; }
  Result poll(uint32_t now, bool buttonsDown, bool stable) {
    if (!requested) return WAITING;
    if (uint32_t(now - requestedAt) >= 20000) { cancel(); return TIMED_OUT; }
    if (buttonsDown || !stable) { timingStill = false; return WAITING; }
    if (!timingStill) { timingStill = true; stillSince = now; return WAITING; }
    return uint32_t(now - stillSince) >= 1000 ? READY : WAITING;
  }
  bool apply(const float (&calibratedGrams)[N]) {
    for (float value : calibratedGrams) if (!isfinite(value)) return false;
    for (int i = 0; i < N; ++i) offsets[i] = calibratedGrams[i];
    enabled = true; ++appliedCount; cancel(); return true;
  }
  void clear() {
    for (float& value : offsets) value = 0;
    enabled = false; cancel();
  }
};
