#pragma once
// Copy to each project's include/secrets.h. Never commit real credentials.
#define WIFI_SSID "configure-me"
#define WIFI_PASSWORD "configure-me"
// Optional camera fallback networks (empty entries are skipped).
#define WIFI_SSID_2 ""
#define WIFI_PASSWORD_2 ""
#define WIFI_SSID_3 ""
#define WIFI_PASSWORD_3 ""
#define MQTT_HOST "192.168.1.10"
#define MQTT_PORT 1883
#define MQTT_PASSWORD "configure-me"
#define DEVICE_KEY "configure-me"
#define CAMERA_KEY "configure-me"
// Local isolated LAN only: use TLS firmware adapter and broker CA before deployment.
