#pragma once
#include <stdint.h>
#include <math.h>
#include "scale_solver.h"

// Quick calibration before demos: empty -> known mass over A -> over C -> at the
// centre -> empty again, a few seconds each. Every ADC sample of one cell is fitted to
//     raw(t) = zero + rate * t + load[k]   while the mass is at position k
// by least squares, so the zero drift is measured across the whole run (the mass did
// not change while it rested in each position). The A and C loads give the gains, the
// centre load is an independent check. Pure numerics: no Arduino, runs on the host.
template<int K>
struct DriftFit {
  static constexpr int P = 2 + K;  // zero, rate, one load per mass position
  double xtx[P][P] = {}, xty[P] = {}, yty = 0, y0 = 0, n = 0, perPosition[K + 1] = {};
  bool started = false;
  double zero = 0, rate = 0, load[K] = {}, residualSd = 0;  // counts, counts/s, counts, counts

  void reset() { *this = DriftFit(); }
  // position: -1 for the empty tray, 0..K-1 for the mass positions.
  void add(double tSeconds, int position, double raw) {
    if (position < -1 || position >= K) return;
    if (!started) { y0 = raw; started = true; }
    double x[P] = {1, tSeconds};
    for (int k = 0; k < K; ++k) x[2 + k] = k == position ? 1 : 0;
    const double y = raw - y0;
    for (int r = 0; r < P; ++r) {
      for (int c = 0; c < P; ++c) xtx[r][c] += x[r] * x[c];
      xty[r] += x[r] * y;
    }
    yty += y * y; n += 1; perPosition[position + 1] += 1;
  }
  // Needs empty samples and samples at every position; Gaussian elimination with pivoting.
  bool solve() {
    if (perPosition[0] < 6 || n < P + 6) return false;
    for (int k = 1; k <= K; ++k) if (perPosition[k] < 3) return false;
    double a[P][P + 1];
    for (int r = 0; r < P; ++r) { for (int c = 0; c < P; ++c) a[r][c] = xtx[r][c]; a[r][P] = xty[r]; }
    for (int col = 0; col < P; ++col) {
      int pivot = col;
      for (int r = col + 1; r < P; ++r) if (fabs(a[r][col]) > fabs(a[pivot][col])) pivot = r;
      if (fabs(a[pivot][col]) < 1e-9) return false;
      if (pivot != col) for (int c = 0; c <= P; ++c) { double t = a[col][c]; a[col][c] = a[pivot][c]; a[pivot][c] = t; }
      for (int r = col + 1; r < P; ++r) {
        const double f = a[r][col] / a[col][col];
        for (int c = col; c <= P; ++c) a[r][c] -= f * a[col][c];
      }
    }
    double beta[P];
    for (int r = P - 1; r >= 0; --r) {
      double v = a[r][P];
      for (int c = r + 1; c < P; ++c) v -= a[r][c] * beta[c];
      beta[r] = v / a[r][r];
    }
    double fitted = 0;
    for (int r = 0; r < P; ++r) fitted += beta[r] * xty[r];
    zero = beta[0] + y0; rate = beta[1];
    for (int k = 0; k < K; ++k) load[k] = beta[2 + k];
    residualSd = sqrt(fmax(0.0, yty - fitted) / (n - P));
    if (!isfinite(zero) || !isfinite(rate) || !isfinite(residualSd)) return false;
    for (double value : load) if (!isfinite(value)) return false;
    return true;
  }
  double zeroAt(double tSeconds) const { return zero + rate * tSeconds; }
};

struct QuickCalLimits {
  double maxRateGPerMin = 6.0;  // per cell
  double maxResidualG = 1.5;    // per cell: anything moving more was not still
  double verifyFraction = 0.03; // centre check: +-3 % of the mass, at least +-2 g
  double verifyMinimumG = 2.0;
};

template<int N>
struct QuickCalResult {
  bool ok = false;
  const char* why = "";
  float countsPerGram[N] = {};
  double verifyG = 0, rateGPerMin[N] = {}, residualG[N] = {};
};

// fit[cell] holds positions 0..N-1 (mass over each support, in cell order) and N (centre).
template<int N>
QuickCalResult<N> evaluateQuickCalibration(const DriftFit<N + 1> (&fit)[N], double knownMassG,
                                           const QuickCalLimits& limits = QuickCalLimits()) {
  QuickCalResult<N> result;
  float response[N][N];
  for (int position = 0; position < N; ++position)
    for (int cell = 0; cell < N; ++cell) response[position][cell] = float(fit[cell].load[position]);
  const char* why = "";
  if (!solveCalibration<N>(response, float(knownMassG), result.countsPerGram, why)) {
    result.why = why[0] ? why : "SOLVE FAILED"; return result;
  }
  for (int i = 0; i < N; ++i) {
    const double gramsPerCount = 1.0 / result.countsPerGram[i];
    result.verifyG += fit[i].load[N] * gramsPerCount;
    result.rateGPerMin[i] = fit[i].rate * gramsPerCount * 60;
    result.residualG[i] = fabs(fit[i].residualSd * gramsPerCount);
    if (result.residualG[i] > limits.maxResidualG) { result.why = "MOVED - REPEAT"; return result; }
    if (fabs(result.rateGPerMin[i]) > limits.maxRateGPerMin) { result.why = "DRIFT TOO FAST"; return result; }
  }
  if (fabs(result.verifyG - knownMassG) > fmax(limits.verifyMinimumG, knownMassG * limits.verifyFraction)) {
    result.why = "CENTRE CHECK"; return result;
  }
  result.ok = true;
  return result;
}
