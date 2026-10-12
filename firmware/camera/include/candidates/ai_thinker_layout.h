#pragma once
// CANDIDATE ONLY - NOT a confirmed board_config.h.
// Source: AI-Thinker ESP32-CAM reference layout (same 24-pin camera FPC used by OV2640/OV3660 variants),
// as published in Espressif's esp32-camera examples (CAMERA_MODEL_AI_THINKER).
// Used exclusively by the esp32cam-pinmap-probe environment, which accepts this map only if the
// OV3660 product ID is read back over SCCB AND a valid JPEG frame is captured.
// Copy into include/board_config.h (with BOARD_CONFIRMED) only after that probe prints PROBE_PASS.
#define CAM_PWDN 32
#define CAM_RESET -1
#define CAM_XCLK 0
#define CAM_SDA 26
#define CAM_SCL 27
#define CAM_D7 35
#define CAM_D6 34
#define CAM_D5 39
#define CAM_D4 36
#define CAM_D3 21
#define CAM_D2 19
#define CAM_D1 18
#define CAM_D0 5
#define CAM_VSYNC 25
#define CAM_HREF 23
#define CAM_PCLK 22
