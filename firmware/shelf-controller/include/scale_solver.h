#pragma once
#include <math.h>

// Solve one known-mass equation per active support position. Kept independent
// of Arduino so the two- and three-cell calibration math can be executed on a host.
template<int N>
bool solveCalibration(const float (&response)[N][N], float mass, float (&out)[N], const char*& why) {
  double a[N][N + 1], norm = 1;
  if (!isfinite(mass) || mass <= 0) { why = "BAD MASS"; return false; }
  for (int row = 0; row < N; ++row) {
    double length = 0;
    for (int col = 0; col < N; ++col) {
      if (!isfinite(response[row][col])) { why = "BAD INPUT"; return false; }
      a[row][col] = response[row][col]; length += a[row][col] * a[row][col];
    }
    if (length == 0) { why = "NO CHANGE"; return false; }
    a[row][N] = mass; norm *= sqrt(length);
  }
  double determinant = 1;
  for (int col = 0; col < N; ++col) {
    int pivot = col;
    for (int row = col + 1; row < N; ++row)
      if (fabs(a[row][col]) > fabs(a[pivot][col])) pivot = row;
    if (pivot != col) {
      for (int k = 0; k <= N; ++k) { double t = a[col][k]; a[col][k] = a[pivot][k]; a[pivot][k] = t; }
      determinant = -determinant;
    }
    if (a[col][col] == 0) { why = "SINGULAR"; return false; }
    determinant *= a[col][col];
    for (int row = col + 1; row < N; ++row) {
      double factor = a[row][col] / a[col][col];
      for (int k = col; k <= N; ++k) a[row][k] -= factor * a[col][k];
    }
  }
  if (fabs(determinant) / norm < 0.05) { why = "SPREAD MASSES"; return false; }
  double scale[N];
  for (int row = N - 1; row >= 0; --row) {
    double value = a[row][N];
    for (int col = row + 1; col < N; ++col) value -= a[row][col] * scale[col];
    scale[row] = value / a[row][row];
  }
  for (int i = 0; i < N; ++i) {
    if (!isfinite(scale[i]) || scale[i] == 0) { why = "BAD SCALE"; return false; }
    double counts = 1.0 / scale[i];
    if (fabs(counts) < 10 || fabs(counts) > 100000) { why = "CHECK CELL"; return false; }
    out[i] = counts;
  }
  why = "";
  return true;
}
