#pragma once
#include <stdint.h>

// Two 10 kg load cells, one under each side of the tray: A (left) and C (right).
// Cell B was removed from the hardware; GPIO 25/26 are unused.
struct CellPins { char name; uint8_t dt, sck; };
constexpr int NCELL = 2;
constexpr CellPins CELL_PINS[NCELL] = {{'A', 32, 33}, {'C', 34, 27}};
// New NVS keys for the 10 kg cells: calibrations saved for the old cells (3-cell "cal",
// A/C "ac-cal") are never loaded, so the tray reads UNCALIBRATED until recalibrated.
constexpr const char* CAL_KEY_PREFIX = "ac2";
constexpr const char* CAL_VERSION_KEY = "ac2-cal";
constexpr const char* CAL_VERSION_PREFIX = "ac2-";
constexpr const char* CAL_METHOD = "SERVICE_MODE_2_POSITION";

enum SvcStep { SVC_OFF, SVC_HOME, CAL_ZERO, CAL_A, CAL_C, CAL_VERIFY, CAL_DONE };

constexpr int calibrationRow(SvcStep step) {
  return step == CAL_A ? 0 : step == CAL_C ? 1 : -1;
}

constexpr SvcStep nextCaptureStep(SvcStep step) {
  return step == CAL_ZERO ? CAL_A : step == CAL_A ? CAL_C : CAL_VERIFY;
}
