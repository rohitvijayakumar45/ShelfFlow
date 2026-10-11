#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <esp_timer.h>
#include <esp_camera.h>
#ifdef CAMERA_API_CHECK
#include "api_check.h"
#endif
#if __has_include("secrets.h")
#include "secrets.h"
#else
#include "../../common/Secrets.example.h"
#endif
#if __has_include("board_config.h")
#include "board_config.h"
#endif
#ifndef BOARD_CONFIRMED
void setup(){Serial.begin(115200);Serial.println("UNCONFIGURED OV3660: carrier pins/PSRAM must be verified. No camera pins driven.");}
void loop(){delay(1000);}
#else
WebServer server(80);String boot;uint32_t sequence=0,nextRetry=0,backoff=1000;bool healthy=false,high=false;
bool auth(){if(server.header("X-Camera-Key")!=CAMERA_KEY){server.send(401,"text/plain","Unauthorized");return false;}return true;}
void configureCarrier(){
  Serial.begin(115200);boot=String(esp_random(),HEX)+String(esp_random(),HEX);
  camera_config_t config={};config.ledc_channel=LEDC_CHANNEL_0;config.ledc_timer=LEDC_TIMER_0;
  config.pin_d0=CAM_D0;config.pin_d1=CAM_D1;config.pin_d2=CAM_D2;config.pin_d3=CAM_D3;config.pin_d4=CAM_D4;config.pin_d5=CAM_D5;config.pin_d6=CAM_D6;config.pin_d7=CAM_D7;
  config.pin_xclk=CAM_XCLK;config.pin_pclk=CAM_PCLK;config.pin_vsync=CAM_VSYNC;config.pin_href=CAM_HREF;config.pin_sccb_sda=CAM_SDA;config.pin_sccb_scl=CAM_SCL;config.pin_pwdn=CAM_PWDN;config.pin_reset=CAM_RESET;
  config.xclk_freq_hz=20000000;config.pixel_format=PIXFORMAT_JPEG;config.frame_size=FRAMESIZE_SVGA;config.jpeg_quality=12;config.fb_count=psramFound()?2:1;config.fb_location=psramFound()?CAMERA_FB_IN_PSRAM:CAMERA_FB_IN_DRAM;config.grab_mode=CAMERA_GRAB_LATEST;
  healthy=esp_camera_init(&config)==ESP_OK;
  if(healthy&&esp_camera_sensor_get()->id.PID!=OV3660_PID){Serial.println("Sensor is not OV3660");healthy=false;esp_camera_deinit();}
  WiFi.mode(WIFI_STA);WiFi.begin(WIFI_SSID,WIFI_PASSWORD);const char* headers[]={"X-Camera-Key"};server.collectHeaders(headers,1);
  server.on("/clock",HTTP_GET,[]{if(!auth())return;server.send(200,"application/json","{\"boot_id\":\""+boot+"\",\"monotonic_us\":"+String((uint64_t)esp_timer_get_time())+"}");});
  server.on("/health",HTTP_GET,[]{if(!auth())return;server.send(200,"application/json",String("{\"sensor\":\"OV3660\",\"healthy\":")+(healthy?"true":"false")+",\"psram_bytes\":"+String(ESP.getPsramSize())+",\"high\":"+(high?"true":"false")+"}");});
  server.on("/mode",HTTP_POST,[]{if(!auth())return;if(!healthy){server.send(503);return;}bool request=server.arg("high")=="1";if(request!=high){if(request&&!psramFound()){server.send(409,"text/plain","High resolution requires measured board capacity");return;}esp_camera_sensor_get()->set_framesize(esp_camera_sensor_get(),request?FRAMESIZE_QXGA:FRAMESIZE_SVGA);high=request;}server.send(200,"application/json","{\"ok\":true}");});
  server.on("/jpeg",HTTP_GET,[]{if(!auth())return;if(!healthy){server.send(503);return;}camera_fb_t* fb=esp_camera_fb_get();if(!fb){server.send(503);return;}
    uint64_t capture=(uint64_t)fb->timestamp.tv_sec*1000000+fb->timestamp.tv_usec;
    server.sendHeader("X-Frame-Sequence",String(sequence++));server.sendHeader("X-Boot-Id",boot);server.sendHeader("X-Capture-Us",String(capture));server.sendHeader("X-Width",String(fb->width));server.sendHeader("X-Height",String(fb->height));server.sendHeader("Cache-Control","no-store");
    server.setContentLength(fb->len);server.send(200,"image/jpeg","");server.client().setTimeout(2);server.client().write(fb->buf,fb->len);esp_camera_fb_return(fb);yield();});
  server.begin();
}
void serviceCarrier(){server.handleClient();uint32_t now=millis();if(WiFi.status()!=WL_CONNECTED&&(int32_t)(now-nextRetry)>=0){WiFi.reconnect();nextRetry=now+backoff;backoff=min(backoff*2,(uint32_t)30000);}else if(WiFi.status()==WL_CONNECTED)backoff=1000;delay(1);}
void setup(){
#ifdef CAMERA_API_CHECK
  Serial.begin(115200);Serial.println("Camera API compile check only. No pins assigned or driven.");
#else
  configureCarrier();
#endif
}
void loop(){
#ifdef CAMERA_API_CHECK
  delay(1000);
#else
  serviceCarrier();
#endif
}
#endif
