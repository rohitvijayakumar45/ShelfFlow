#include "../include/drift_check.h"
#include "../include/weighing_pipeline.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

using Fit = DriftFit<3>;
static double noise() { return (rand() % 2001 - 1000) / 10.0; }  // +-100 counts, like cell A

// Real-tray-like cells: ~249 and ~244 counts/g, C reversed load sharing not needed.
static const double cpg[2] = {248.6, 243.6};
static const double zero[2] = {52890, -455149};
// Fraction of a mass carried by each cell when it is over A, over C, at the centre.
static const double share[3][2] = {{0.85, 0.15}, {0.12, 0.88}, {0.52, 0.48}};

// ~50 s run: 5 s windows at 10 samples/s with 5 s gaps for moving the mass.
static void simulate(Fit (&fit)[2], const double (&rateGMin)[2], double mass, double centreMass) {
  for (auto& f : fit) f.reset();
  const struct { double from; int position; } windows[] = {{0, -1}, {10, 0}, {20, 1}, {30, 2}, {40, -1}};
  for (auto w : windows)
    for (double t = w.from; t < w.from + 5; t += .1)
      for (int i = 0; i < 2; ++i) {
        const double grams = w.position < 0 ? 0 : (w.position == 2 ? centreMass : mass) * share[w.position][i];
        fit[i].add(t, w.position, zero[i] + rateGMin[i] / 60 * cpg[i] * t + grams * cpg[i] + noise());
      }
}

int main() {
  srand(7);
  const double drift[2] = {0.2, -0.7};  // g/min, as measured on the tray
  Fit fit[2];
  simulate(fit, drift, 44, 44);
  for (int i = 0; i < 2; ++i) {
    assert(fit[i].solve());
    assert(fabs(fit[i].rate / cpg[i] * 60 - drift[i]) < 0.15);  // rate to 0.15 g/min from 50 s
    assert(fabs(fit[i].zeroAt(45) - (zero[i] + drift[i] / 60 * cpg[i] * 45)) / cpg[i] < 0.1);
  }
  QuickCalResult<2> result = evaluateQuickCalibration<2>(fit, 44);
  assert(result.ok);
  for (int i = 0; i < 2; ++i) assert(fabs(result.countsPerGram[i] - cpg[i]) / cpg[i] < 0.003);
  assert(fabs(result.verifyG - 44) < 0.2);
  assert(fabs(result.rateGPerMin[1] - drift[1]) < 0.15);

  // Without the model the same drift would bias the gains: the fit removes it.
  const double fast[2] = {3, -3};
  simulate(fit, fast, 44, 44); for (auto& f : fit) assert(f.solve());
  result = evaluateQuickCalibration<2>(fit, 44);
  assert(result.ok && fabs(result.countsPerGram[0] - cpg[0]) / cpg[0] < 0.005);

  // Wrong mass at the centre, mass never placed, a hand on the tray, too fast drift: refused.
  simulate(fit, drift, 44, 50); for (auto& f : fit) f.solve();
  assert(!evaluateQuickCalibration<2>(fit, 44).ok);
  simulate(fit, drift, 0, 0); for (auto& f : fit) f.solve();
  assert(!evaluateQuickCalibration<2>(fit, 44).ok);
  const double crazy[2] = {9, 0};
  simulate(fit, crazy, 44, 44); for (auto& f : fit) f.solve();
  assert(!evaluateQuickCalibration<2>(fit, 44).ok);
  Fit moved[2];
  for (double t = 0; t < 45; t += .1)
    for (int i = 0; i < 2; ++i) {
      const int position = t < 5 ? -1 : t < 15 ? 0 : t < 25 ? 1 : t < 35 ? 2 : -1;
      moved[i].add(t, position, zero[i] + (position < 0 ? 0 : 44 * share[position][i] * cpg[i]) + (int(t) % 3 ? 0 : 3000));
    }
  for (auto& f : moved) assert(f.solve());
  assert(!evaluateQuickCalibration<2>(moved, 44).ok);
  Fit partial; partial.add(0, -1, 1); assert(!partial.solve());

  // Pipeline: the measured drift keeps an empty tray at zero, then stops after the horizon.
  const double gpc[2] = {1 / cpg[0], 1 / cpg[1]};
  const double rate[2] = {drift[0] / 60 * cpg[0], drift[1] / 60 * cpg[1]};
  WeighingConfig direct; direct.emaAlpha = 1;
  WeighingPipeline<2> scale(direct);
  WeighingPipeline<2>::Calibration cal[2] = {{zero[0], gpc[0]}, {zero[1], gpc[1]}};
  assert(scale.configure(cal));
  int32_t raw[2] = {int32_t(zero[0]), int32_t(zero[1])};
  scale.push(raw, 0, true, .01);
  assert(scale.setDrift(rate, 300000));  // 5 min
  double worst = 0;
  for (uint32_t t = 100; t <= 300000; t += 100) {
    for (int i = 0; i < 2; ++i) raw[i] = int32_t(lround(zero[i] + rate[i] * t / 1000.0));
    scale.push(raw, t, true, .01);
    worst = fmax(worst, fabs(scale.value().netG));
  }
  assert(worst < 0.01);
  const double baseline = scale.driftCorrected(1, raw[1]);
  for (uint32_t t = 300100; t <= 360000; t += 100) scale.push(raw, t, true, .01);
  assert(scale.driftRemaining() == 0);
  assert(fabs(scale.value().netG) < 0.01);  // held, not extrapolated further
  assert(fabs(scale.netAtCorrected(1, baseline) - scale.netAt(1, raw[1])) < 1e-9);
  const double nan[2] = {NAN, 0};
  assert(!scale.setDrift(nan, 1000));
  assert(scale.configure(cal) && scale.driftRemaining() == 0);  // new calibration drops the model
  // Tray tuning: zero tracking follows a wandering empty tray but never absorbs a 2 g item.
  WeighingConfig tray; tray.zeroCaptureG = 1.5; tray.zeroFreezeG = 4; tray.zeroRateGPerSecond = .05;
  tray.zeroMaximumCorrectionG = 20;
  WeighingPipeline<2> follow(tray); assert(follow.configure(cal));
  int32_t empty[2] = {int32_t(zero[0]), int32_t(zero[1])};
  follow.push(empty, 0, true, .01); double none2[2] = {0, 0}; assert(follow.setTare(none2, true));
  uint32_t t = 0;
  for (; t < 120000; t += 100) {  // 1 g/min wander on C for 2 min, tracked
    int32_t wander[2] = {empty[0], int32_t(zero[1] + cpg[1] * t / 60000.0)};
    follow.push(wander, t, true, .01);
  }
  assert(fabs(follow.value().netG) < 0.5);
  const double tracked = follow.value().zeroCorrectionG;
  int32_t item[2] = {int32_t(zero[0] + 1.0 * cpg[0]), int32_t(zero[1] + cpg[1] * 2 + 1.0 * cpg[1])};  // +2 g item
  for (uint32_t end = t + 300000; t < end; t += 100) follow.push(item, t, true, .01);
  assert(fabs(follow.value().zeroCorrectionG - tracked) < 0.05 && follow.value().netG > 1.9);
  puts("Quick calibration passed: A/C/centre gains with drift removed, refusals, pipeline extrapolation and horizon");
}
