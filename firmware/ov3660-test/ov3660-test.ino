#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <esp_camera.h>
#include <esp_mac.h>

// ESP32-S is NOT enough to identify the carrier's camera pins.
// Change 0 to 1 ONLY after checking its schematic / the repository pin-map probe.
#ifndef CAMERA_PINMAP_VERIFIED
#define CAMERA_PINMAP_VERIFIED 0
#endif

const char* AP_NAME = "ESP32CAM-Test";
const char* AP_PASSWORD = "camera123"; // Local bench test password (8+ characters).
WebServer server(80);
bool cameraReady = false;

const char PAGE[] PROGMEM = R"HTML(
<!doctype html><html><head><meta name="viewport" content="width=device-width">
<title>OV3660 test</title></head><body>
<h2>OV3660 camera test</h2><p>JPEG snapshots refresh after each image loads.</p>
<img id="camera" style="max-width:100%;height:auto" alt="Camera image">
<script>
const image = document.getElementById('camera');
function refresh(){ image.src='/capture?t='+Date.now(); }
image.onload=()=>setTimeout(refresh,500);
image.onerror=()=>setTimeout(refresh,1500);
refresh();
</script></body></html>
)HTML";

bool validJpeg(camera_fb_t* frame) {
  return frame && frame->format == PIXFORMAT_JPEG && frame->len >= 4 &&
         frame->buf[0] == 0xff && frame->buf[1] == 0xd8;
}

void capturePhoto() {
  camera_fb_t* frame = esp_camera_fb_get();
  if (!validJpeg(frame)) {
    if (frame) esp_camera_fb_return(frame);
    server.send(503, "text/plain", "Capture failed; check Serial Monitor.");
    Serial.println("CAPTURE FAILED");
    return;
  }
  server.sendHeader("Cache-Control", "no-store");
  server.setContentLength(frame->len);
  server.send(200, "image/jpeg", "");
  WiFiClient client = server.client();
  size_t written = client.write(frame->buf, frame->len);
  Serial.printf("JPEG %ux%u, %u bytes, sent %u bytes\n",
                frame->width, frame->height, (unsigned)frame->len, (unsigned)written);
  esp_camera_fb_return(frame);
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  // Read the factory-derived station MAC before starting the Wi-Fi driver.
  uint8_t mac[6] = {};
  char macText[18] = "UNAVAILABLE";
  if (esp_read_mac(mac, ESP_MAC_WIFI_STA) == ESP_OK) {
    snprintf(macText, sizeof(macText), "%02X:%02X:%02X:%02X:%02X:%02X",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  }
  Serial.printf("\nChip: %s | flash: %u | PSRAM: %u | MAC: %s\n",
                ESP.getChipModel(), ESP.getFlashChipSize(),
                ESP.getPsramSize(), macText);
#if !CAMERA_PINMAP_VERIFIED
  Serial.println("IDENTIFY ONLY: no camera pins driven.");
  Serial.println("Verify carrier camera wiring before setting CAMERA_PINMAP_VERIFIED to 1.");
  return;
#else
  // AI-Thinker ESP32-CAM carrier map ONLY. These are internal ribbon connections.
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = 5;  config.pin_d1 = 18;
  config.pin_d2 = 19; config.pin_d3 = 21;
  config.pin_d4 = 36; config.pin_d5 = 39;
  config.pin_d6 = 34; config.pin_d7 = 35;
  config.pin_xclk = 0;
  config.pin_pclk = 22;
  config.pin_vsync = 25;
  config.pin_href = 23;
  config.pin_sccb_sda = 26;
  config.pin_sccb_scl = 27;
  config.pin_pwdn = 32;
  config.pin_reset = -1;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = psramFound() ? FRAMESIZE_VGA : FRAMESIZE_QVGA;
  config.jpeg_quality = 12;
  config.fb_count = 1;
  config.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

  Serial.printf("Starting camera init: PSRAM=%s, frame=%s\n",
                psramFound() ? "detected" : "not detected",
                psramFound() ? "VGA" : "QVGA");
  Serial.flush();
  esp_err_t error = esp_camera_init(&config);
  Serial.printf("Camera init returned: 0x%x\n", error);
  if (error != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x. Check pin map, ribbon and power.\n", error);
    return;
  }
  sensor_t* sensor = esp_camera_sensor_get();
  Serial.printf("Sensor PID: 0x%04x (OV3660 expected: 0x3660)\n",
                sensor ? sensor->id.PID : 0);
  if (!sensor || sensor->id.PID != OV3660_PID) {
    Serial.println("Sensor is not identified as OV3660. Test stopped.");
    esp_camera_deinit();
    return;
  }
  camera_fb_t* frame = esp_camera_fb_get();
  bool frameOK = validJpeg(frame);
  if (frameOK) Serial.printf("CAMERA PASS: JPEG %ux%u, %u bytes\n",
                             frame->width, frame->height, (unsigned)frame->len);
  if (frame) esp_camera_fb_return(frame);
  if (!frameOK) {
    Serial.println("No valid JPEG; test stopped.");
    esp_camera_deinit();
    return;
  }
  WiFi.mode(WIFI_AP);
  if (!WiFi.softAP(AP_NAME, AP_PASSWORD)) {
    Serial.println("Wi-Fi access point failed.");
    esp_camera_deinit();
    return;
  }
  server.on("/", []() { server.send_P(200, "text/html", PAGE); });
  server.on("/capture", capturePhoto);
  server.begin();
  cameraReady = true;
  Serial.printf("Connect laptop to Wi-Fi: %s | password: %s\n", AP_NAME, AP_PASSWORD);
  Serial.print("Open http://");
  Serial.println(WiFi.softAPIP());
#endif
}

void loop() {
  if (cameraReady) server.handleClient();
  delay(2);
}
