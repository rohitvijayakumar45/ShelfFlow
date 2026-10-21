#pragma once

// Additional credentials belong in the ignored local secrets.h.
#ifndef WIFI_SSID_2
#define WIFI_SSID_2 ""
#define WIFI_PASSWORD_2 ""
#endif
#ifndef WIFI_SSID_3
#define WIFI_SSID_3 ""
#define WIFI_PASSWORD_3 ""
#endif

struct CameraNetwork { const char* ssid; const char* password; };
static const CameraNetwork cameraNetworks[] = {
  {WIFI_SSID, WIFI_PASSWORD},
  {WIFI_SSID_2, WIFI_PASSWORD_2},
  {WIFI_SSID_3, WIFI_PASSWORD_3},
};

// Connection attempts run asynchronously; no scans or waits in the video loop.
class CameraWifi {
  size_t next = 0;
  uint32_t attemptStarted = 0;
  bool wasConnected = false;

  void attempt(uint32_t now) {
    const size_t count = sizeof(cameraNetworks) / sizeof(cameraNetworks[0]);
    for(size_t i = 0; i < count; ++i) {
      const auto& network = cameraNetworks[next];
      next = (next + 1) % count;
      if(!network.ssid[0]) continue;
      WiFi.disconnect(false, false);
      WiFi.begin(network.ssid, network.password);
      attemptStarted = now;
      Serial.printf("WIFI_TRY ssid=%s\n", network.ssid);
      return;
    }
    attemptStarted = now;
  }

public:
  void begin() {
    WiFi.mode(WIFI_STA);
    WiFi.persistent(false);
    WiFi.setAutoReconnect(false);
    WiFi.setSleep(false);
    attempt(millis());
  }

  void service() {
    const uint32_t now = millis();
    if(WiFi.status() == WL_CONNECTED) {
      if(!wasConnected) Serial.println("WIFI_CONNECTED ssid=" + WiFi.SSID() + " ip=" + WiFi.localIP().toString());
      wasConnected = true;
      return;
    }
    if(wasConnected) {
      wasConnected = false;
      Serial.println("WIFI_LOST trying saved networks");
      attempt(now);
    } else if(uint32_t(now - attemptStarted) >= 15000) {
      attempt(now);
    }
  }
};
