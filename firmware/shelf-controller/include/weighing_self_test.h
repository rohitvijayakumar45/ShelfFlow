#pragma once
#include "weighing_pipeline.h"
#include "scale_solver.h"

#define SCALE_CHECK(condition) do { if (!(condition)) return __LINE__; } while (false)
namespace weighing_self_test {
using Scale = WeighingPipeline<2>;
static bool near(double a, double b, double epsilon = 1e-9) { return fabs(a - b) < epsilon; }
static bool configure(Scale& scale) {
  Scale::Calibration calibration[2] = {{400000, .0025}, {200000, -.002}};
  return scale.configure(calibration);
}

inline int run() {
  Scale scale; SCALE_CHECK(configure(scale));
  int32_t raw[2] = {401000, 199500};
  SCALE_CHECK(scale.push(raw, 0, true, .01));
  SCALE_CHECK(near(scale.value().netG, 3.5));
  SCALE_CHECK(near(scale.value().filteredCombinedCounts * .0025, 3.5));
  SCALE_CHECK(near(scale.value().cellGrams[0] + scale.value().cellGrams[1], scale.value().netG));
  raw[0] = 402000; raw[1] = 199000;
  scale.push(raw, 100, true, .01);
  SCALE_CHECK(near(scale.value().netG, 3.5 + .25 * (7 - 3.5))); // Raw-domain EMA, one application.
  SCALE_CHECK(near(scale.value().calibratedTotalG, scale.value().netG));
  double reference[2] = {scale.calibratedAt(0, scale.value().filteredRaw[0]),
                         scale.calibratedAt(1, scale.value().filteredRaw[1])};
  for (int n = 0; n < 10000; ++n) {
    SCALE_CHECK(scale.setTare(reference, false));
    SCALE_CHECK(near(scale.value().netG, 0)); // Re-tare replaces an absolute reference, no accumulation.
  }
  SCALE_CHECK(near(scale.calibratedAt(0, 401000), 2.5)); // Gain and calibration zero never change.
  scale.clearTare(); SCALE_CHECK(near(scale.value().netG, 4.375));

  WeighingConfig config; config.emaAlpha = 1;
  Scale azt(config); SCALE_CHECK(configure(azt));
  int32_t zero[2] = {400000, 200000};
  azt.push(zero, 0, true, .01);
  double empty[2] = {0, 0}; SCALE_CHECK(azt.setTare(empty, true));
  int32_t slightDrift[2] = {400100, 200000}; // +0.25 g.
  for (uint32_t t = 100; t <= 2000; t += 100) azt.push(slightDrift, t, true, .01);
  SCALE_CHECK(near(azt.value().zeroCorrectionG, 0)); // Must qualify continuously for a full 2 s.
  azt.push(slightDrift, 2100, true, .01);
  SCALE_CHECK(azt.value().zeroTracking && azt.value().deadbandActive);
  SCALE_CHECK(near(azt.value().displayG, 0) && azt.value().netG > 0);
  SCALE_CHECK(azt.value().zeroCorrectionG > 0 && azt.value().zeroCorrectionG <= .002001);
  const double beforeLoad = azt.value().zeroCorrectionG;
  int32_t load[2] = {417600, 200000}; // +44 g.
  azt.push(load, 2200, true, .01);
  SCALE_CHECK(azt.value().loaded && !azt.value().zeroTracking && !azt.value().deadbandActive);
  SCALE_CHECK(near(azt.value().zeroCorrectionG, beforeLoad));
  SCALE_CHECK(azt.value().netG > 43);
  for (uint32_t t = 2300; t < 6000; t += 100) azt.push(load, t, true, .01);
  SCALE_CHECK(near(azt.value().zeroCorrectionG, beforeLoad)); // Never track a real held weight away.

  // An arbitrary 44 g reference tared to zero must NOT be mistaken for known empty.
  double loadedReference[2] = {44, 0};
  SCALE_CHECK(azt.setTare(loadedReference, false));
  for (uint32_t t = 6000; t < 12000; t += 100) azt.push(load, t, true, .01);
  SCALE_CHECK(!azt.value().zeroTracking && near(azt.value().zeroCorrectionG, 0));

  // Immediate freeze must use raw data, not the lagging EMA / displayed zero.
  WeighingConfig slow; slow.emaAlpha = .01;
  Scale lag(slow); SCALE_CHECK(configure(lag)); lag.push(zero, 0, true, .01); lag.setTare(empty, true);
  for (uint32_t t = 100; t <= 2200; t += 100) lag.push(zero, t, true, .01);
  SCALE_CHECK(lag.value().deadbandActive);
  lag.push(load, 2300, true, .01);
  SCALE_CHECK(lag.value().netG < .5 && lag.value().rawNetG > 43);
  SCALE_CHECK(!lag.value().zeroTracking && !lag.value().deadbandActive);

  // Continuous small drift can never walk the zero beyond the configured budget.
  WeighingConfig budgetConfig = config; budgetConfig.zeroDeadbandG = 1;
  Scale bounded(budgetConfig); SCALE_CHECK(configure(bounded)); bounded.push(zero, 0, true, .01); bounded.setTare(empty, true);
  for (uint32_t t = 100; t < 120000; t += 100) {
    const int32_t delta = int32_t((bounded.value().zeroCorrectionG + .25) / .0025);
    int32_t drift[2] = {400000 + delta, 200000};
    bounded.push(drift, t, true, .01);
    SCALE_CHECK(bounded.value().zeroCorrectionAbsG <= .50000001);
  }
  SCALE_CHECK(bounded.value().zeroCorrectionAbsG > .49);

  // Noise, unhealthy ADCs, service mode and stale data reset zero qualification.
  Scale guarded(config); SCALE_CHECK(configure(guarded)); guarded.push(zero, 0, true, .01); guarded.setTare(empty, true);
  for (uint32_t t = 100; t < 4000; t += 100) guarded.push(slightDrift, t, true, 1);
  SCALE_CHECK(!guarded.value().zeroTracking && near(guarded.value().zeroCorrectionG, 0));
  guarded.push(slightDrift, 4000, false, .01); SCALE_CHECK(!guarded.value().valid);
  guarded.push(slightDrift, 4100, true, .01, false); SCALE_CHECK(!guarded.value().zeroTracking);
  SCALE_CHECK(!guarded.fresh(6000));
  guarded.push(load, 10000, true, .01); // Re-seed after a gap: no stale blend or giant AZT step.
  SCALE_CHECK(guarded.value().netG > 43 && !guarded.value().zeroTracking);
  int32_t bad[2] = {8388608, 0};
  SCALE_CHECK(!guarded.push(bad, 10100, true, .01));
  SCALE_CHECK(!guarded.value().valid);
  Scale invalid;
  Scale::Calibration broken[2] = {{0, 0}, {0, .002}};
  SCALE_CHECK(!invalid.configure(broken));
  SCALE_CHECK(!invalid.push(zero, 0, true, .01));
  Scale::Calibration invalidZero[2] = {{1e100, .0025}, {0, -.002}};
  SCALE_CHECK(!invalid.configure(invalidZero));
  double invalidTare[2] = {1e100, 0};
  SCALE_CHECK(!scale.setTare(invalidTare, true));

  // Millis rollover must neither skip the two-second dwell nor reset calibration.
  Scale wrap(config); SCALE_CHECK(configure(wrap)); wrap.push(zero, UINT32_MAX - 1500, true, .01); wrap.setTare(empty, true);
  for (uint32_t dt = 100; dt < 2400; dt += 100)
    wrap.push(slightDrift, uint32_t(UINT32_MAX - 1500 + dt), true, .01);
  SCALE_CHECK(wrap.value().zeroTracking);
  // Full tripod: every capture includes all supports, with reversed C polarity.
  float response3[3][3] = {{13200, 2200, -2200}, {2200, 13200, -2200}, {2200, 2200, -13200}};
  float cpg3[3]; const char* why = "";
  SCALE_CHECK(solveCalibration<3>(response3, 44, cpg3, why));
  WeighingConfig direct; direct.emaAlpha = 1;
  WeighingPipeline<3> tripod(direct);
  WeighingPipeline<3>::Calibration cal3[3] = {{400000, 1.0/cpg3[0]}, {300000, 1.0/cpg3[1]}, {200000, 1.0/cpg3[2]}};
  SCALE_CHECK(tripod.configure(cal3));
  for (int row = 0; row < 3; ++row) {
    int32_t adc[3] = {400000+int32_t(response3[row][0]), 300000+int32_t(response3[row][1]), 200000+int32_t(response3[row][2])};
    SCALE_CHECK(tripod.push(adc, row*100, true, .01));
    SCALE_CHECK(near(tripod.value().netG, 44, .0001));
  }
  int32_t centre3[3] = {406000, 306000, 194400};
  SCALE_CHECK(tripod.push(centre3, 400, true, .01));
  SCALE_CHECK(near(tripod.value().netG, 44, .0001));
  SCALE_CHECK(tripod.setIncluded(1, false));
  SCALE_CHECK(near(tripod.value().netG, 29, .0001));
  centre3[1] += 40000; // B remains sampled/filtered, but cannot alter the selected total.
  SCALE_CHECK(tripod.push(centre3, 500, true, .01));
  SCALE_CHECK(near(tripod.value().netG, 29, .0001));
  SCALE_CHECK(tripod.value().raw[1] == centre3[1]);
  SCALE_CHECK(near(tripod.value().filteredCombinedCounts * direct.referenceGramsPerCount, 29, .0001));
  SCALE_CHECK(tripod.setIncluded(1, true));
  SCALE_CHECK(near(tripod.value().netG, 144, .0001));
  SCALE_CHECK(!tripod.confirmedEmpty());
  float singular3[3][3] = {{13200, 2200, -2200}, {13200, 2200, -2200}, {2200, 2200, -13200}};
  SCALE_CHECK(!solveCalibration<3>(singular3, 44, cpg3, why));
  return 0;
}

} // namespace weighing_self_test
#undef SCALE_CHECK
