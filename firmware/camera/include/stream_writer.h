#pragma once
#include <WiFi.h>

// Bounds socket enqueue time, not Wi-Fi delivery/acknowledgement time.
bool writeBefore(WiFiClient& client, const uint8_t* data, size_t size, int64_t deadlineUs);
