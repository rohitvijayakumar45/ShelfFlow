#include "../include/cell_layout.h"
#include "../include/scale_solver.h"
#include <assert.h>
#include <string.h>
#include <stdio.h>

int main() {
  assert(CELL_PINS[0].name == 'A');
  assert(CELL_PINS[NCELL - 1].name == 'C');
  assert(nextCaptureStep(CAL_ZERO) == CAL_A);
  assert(nextCaptureStep(CAL_C) == CAL_VERIFY);
  assert(calibrationRow(CAL_ZERO) == -1);
  static_assert(NCELL == 2, "Only A and C are fitted");
  assert(nextCaptureStep(CAL_A) == CAL_C);
  assert(calibrationRow(CAL_A) == 0);
  assert(calibrationRow(CAL_C) == 1);
  // Never reuse a calibration saved for the old cells.
  assert(strcmp(CAL_VERSION_KEY, "cal") != 0 && strcmp(CAL_VERSION_KEY, "ac-cal") != 0);
  for (auto pins : CELL_PINS) assert(pins.name != 'B' && pins.dt != 25 && pins.sck != 26);
  const char* why = "";
  float scales[2];
  // Same 44 g at two positions with opposite-sign C wiring and cross-coupling.
  float responses[2][2] = {{13200, -4400}, {4400, -13200}};
  assert(solveCalibration<2>(responses, 44, scales, why));
  assert(fabs(scales[0] - 400) < .001 && fabs(scales[1] + 400) < .001);
  assert(fabs(8800 / scales[0] - 8800 / scales[1] - 44) < .001); // Independent centre verification.
  float singular[2][2] = {{13200, -4400}, {13200, -4400}};
  assert(!solveCalibration<2>(singular, 44, scales, why));
  float noChange[2][2] = {{0, 0}, {4400, -13200}};
  assert(!solveCalibration<2>(noChange, 44, scales, why));
  float invalid[2][2] = {{NAN, 10}, {10, 20}};
  assert(!solveCalibration<2>(invalid, 44, scales, why));
  assert(!solveCalibration<2>(responses, 0, scales, why));
  float oldScales[3];
  float tripod[3][3] = {{13200, 2200, -2200}, {2200, 13200, -2200}, {2200, 2200, -13200}};
  assert(solveCalibration<3>(tripod, 44, oldScales, why));
  assert(fabs(oldScales[0] - 400) < .001 && fabs(oldScales[2] + 400) < .001);
  puts("Cell layout, A/C calibration sequence, two/three-cell solver passed");
}
