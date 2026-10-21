#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <esp_timer.h>
#include <esp_camera.h>
#include <atomic>
#ifdef CAMERA_API_CHECK
#include "api_check.h"
#endif
#if __has_include("secrets.h")
#include "secrets.h"
#else
#include "../../common/Secrets.example.h"
#endif
#if defined(CAMERA_IDENTIFY)
// Hardware identification only. No camera pins configured, no Wi-Fi radio started.
void setup(){Serial.begin(115200);}
void loop(){
  uint8_t mac[6];esp_efuse_mac_get_default(mac);
  Serial.printf("IDENTIFY chip=%s rev=%d cores=%d cpu_mhz=%d flash_bytes=%u psram_bytes=%u heap_free=%u mac=%02X:%02X:%02X:%02X:%02X:%02X\n",
    ESP.getChipModel(),ESP.getChipRevision(),ESP.getChipCores(),ESP.getCpuFreqMHz(),ESP.getFlashChipSize(),
    psramFound()?ESP.getPsramSize():0,ESP.getFreeHeap(),mac[0],mac[1],mac[2],mac[3],mac[4],mac[5]);
  delay(2000);
}
#elif defined(CAMERA_PINMAP_PROBE)
#include "candidates/ai_thinker_layout.h"
// One-shot verification of the CANDIDATE map. Remove the IO0-GND jumper first: GPIO0 is XCLK here.
String result="PROBE_NOT_RUN";
void probe(){
  if(!psramFound()){result="PROBE_FAIL no PSRAM detected (carrier is not the expected ESP32-CAM layout or PSRAM failed)";return;}
  camera_config_t c={};c.ledc_channel=LEDC_CHANNEL_0;c.ledc_timer=LEDC_TIMER_0;
  c.pin_d0=CAM_D0;c.pin_d1=CAM_D1;c.pin_d2=CAM_D2;c.pin_d3=CAM_D3;c.pin_d4=CAM_D4;c.pin_d5=CAM_D5;c.pin_d6=CAM_D6;c.pin_d7=CAM_D7;
  c.pin_xclk=CAM_XCLK;c.pin_pclk=CAM_PCLK;c.pin_vsync=CAM_VSYNC;c.pin_href=CAM_HREF;c.pin_sccb_sda=CAM_SDA;c.pin_sccb_scl=CAM_SCL;c.pin_pwdn=CAM_PWDN;c.pin_reset=CAM_RESET;
  c.xclk_freq_hz=20000000;c.pixel_format=PIXFORMAT_JPEG;c.frame_size=FRAMESIZE_QVGA;c.jpeg_quality=12;c.fb_count=1;c.fb_location=CAMERA_FB_IN_PSRAM;c.grab_mode=CAMERA_GRAB_LATEST;
  esp_err_t err=esp_camera_init(&c);
  if(err!=ESP_OK){result="PROBE_FAIL esp_camera_init error 0x"+String(err,HEX)+" (no sensor answered on candidate SCCB pins, or ribbon not seated)";return;}
  sensor_t* s=esp_camera_sensor_get();uint16_t pid=s?s->id.PID:0;
  if(pid!=OV3660_PID){result="PROBE_FAIL sensor PID 0x"+String(pid,HEX)+" is not OV3660 (0x3660)";esp_camera_deinit();return;}
  camera_fb_t* fb=nullptr;for(int i=0;i<3&&!fb;i++){fb=esp_camera_fb_get();if(!fb)delay(100);}
  if(!fb){result="PROBE_FAIL OV3660 answered but no frame arrived (data/VSYNC/HREF/PCLK pins do not match)";esp_camera_deinit();return;}
  bool jpeg=fb->len>4&&fb->buf[0]==0xFF&&fb->buf[1]==0xD8;
  result=String(jpeg?"PROBE_PASS":"PROBE_FAIL")+" pid=0x"+String(pid,HEX)+" frame="+String(fb->width)+"x"+String(fb->height)+" bytes="+String(fb->len)+(jpeg?" jpeg_soi=ok":" jpeg_soi=missing")+" psram_bytes="+String(ESP.getPsramSize());
  esp_camera_fb_return(fb);esp_camera_deinit();
}
void setup(){Serial.begin(115200);delay(500);probe();}
void loop(){Serial.println(result);delay(2000);}
#else
#if __has_include("board_config.h")
#include "board_config.h"
#endif
#ifndef BOARD_CONFIRMED
void setup(){Serial.begin(115200);Serial.println("UNCONFIGURED OV3660: carrier pins/PSRAM must be verified. No camera pins driven.");}
void loop(){delay(1000);}
#else
#include "frame_cache.h"
#include "stream_writer.h"
#include "wifi_networks.h"
CameraWifi cameraWifi;
FrameCache frames;
WebServer server(80);WiFiServer streamServer(81);std::atomic<uint32_t> streamClients{0};
String boot;bool healthy=false,high=false;
bool auth(){if(server.header("X-Camera-Key")!=CAMERA_KEY){server.send(401,"text/plain","Unauthorized");return false;}return true;}
// MJPEG on port 81 in its own task, so /clock, /health and /mode on port 80 stay responsive.
// One consumer at a time; a new client replaces a stale one. Every part carries the same
// sequence/boot/capture headers as /jpeg so the laptop keeps checking order and frame age.
bool streamAuthorized(WiFiClient& c){
  c.setTimeout(2);bool ok=false;uint32_t start=millis();
  while(c.connected()&&millis()-start<2000){
    String line=c.readStringUntil('\n');line.trim();
    if(line.length()==0)break;
    int colon=line.indexOf(':');
    if(colon>0){String name=line.substring(0,colon);name.toLowerCase();String value=line.substring(colon+1);value.trim();if(name=="x-camera-key"&&value==CAMERA_KEY)ok=true;}
  }
  return ok;
}
void streamTask(void*){
  for(;;){
    WiFiClient c=streamServer.available();
    if(!c){vTaskDelay(pdMS_TO_TICKS(10));continue;}
    if(!streamAuthorized(c)){c.print("HTTP/1.1 401 Unauthorized\r\nContent-Length: 12\r\nConnection: close\r\n\r\nUnauthorized");c.stop();continue;}
    if(!healthy){c.print("HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n");c.stop();continue;}
    c.setNoDelay(true);
    String response="HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace;boundary=sfframe\r\nCache-Control: no-store\r\nX-Boot-Id: "+boot+"\r\n\r\n";
    if(!writeBefore(c,reinterpret_cast<const uint8_t*>(response.c_str()),response.length(),esp_timer_get_time()+500000)){c.stop();continue;}
    streamClients++;
    uint32_t lastSequence=0;bool sentFrame=false;
    while(c.connected()){
      if(streamServer.hasClient())break; // a reconnecting consumer takes over
      auto frame=frames.latest();
      if(!frame||(sentFrame&&frame->sequence==lastSequence)){vTaskDelay(pdMS_TO_TICKS(10));continue;}
      String part="--sfframe\r\nContent-Type: image/jpeg\r\nContent-Length: "+String(frame->size)+"\r\nX-Frame-Sequence: "+String(frame->sequence)+"\r\nX-Boot-Id: "+boot+"\r\nX-Capture-Us: "+String(frame->captureUs)+"\r\nX-Width: "+String(frame->width)+"\r\nX-Height: "+String(frame->height)+"\r\n\r\n";
      const int64_t deadline=esp_timer_get_time()+500000;
      bool ok=writeBefore(c,reinterpret_cast<const uint8_t*>(part.c_str()),part.length(),deadline)&&
        writeBefore(c,frame->data,frame->size,deadline)&&
        writeBefore(c,reinterpret_cast<const uint8_t*>("\r\n"),2,deadline);
      lastSequence=frame->sequence;sentFrame=true;
      if(!ok)break;
      vTaskDelay(1);
    }
    streamClients--;c.stop();
  }
}
void configureCarrier(){
  Serial.begin(115200);boot=String(esp_random(),HEX)+String(esp_random(),HEX);
  camera_config_t config={};config.ledc_channel=LEDC_CHANNEL_0;config.ledc_timer=LEDC_TIMER_0;
  config.pin_d0=CAM_D0;config.pin_d1=CAM_D1;config.pin_d2=CAM_D2;config.pin_d3=CAM_D3;config.pin_d4=CAM_D4;config.pin_d5=CAM_D5;config.pin_d6=CAM_D6;config.pin_d7=CAM_D7;
  config.pin_xclk=CAM_XCLK;config.pin_pclk=CAM_PCLK;config.pin_vsync=CAM_VSYNC;config.pin_href=CAM_HREF;config.pin_sccb_sda=CAM_SDA;config.pin_sccb_scl=CAM_SCL;config.pin_pwdn=CAM_PWDN;config.pin_reset=CAM_RESET;
  config.xclk_freq_hz=8000000;config.pixel_format=PIXFORMAT_JPEG;config.frame_size=psramFound()?FRAMESIZE_QXGA:FRAMESIZE_QVGA;config.jpeg_quality=12;config.fb_count=psramFound()?2:1;config.fb_location=psramFound()?CAMERA_FB_IN_PSRAM:CAMERA_FB_IN_DRAM;config.grab_mode=CAMERA_GRAB_LATEST;
  // Allocate JPEG buffers at the largest supported PSRAM size before lowering
  // the sensor resolution. Runtime increases must fit the initial allocation.
  healthy=esp_camera_init(&config)==ESP_OK;
  if(healthy&&esp_camera_sensor_get()->id.PID!=OV3660_PID){Serial.println("Sensor is not OV3660");healthy=false;esp_camera_deinit();}
  if(healthy&&esp_camera_sensor_get()->set_framesize(esp_camera_sensor_get(),FRAMESIZE_QVGA)!=0){Serial.println("Could not set startup resolution");healthy=false;}
  if(healthy&&!frames.begin()){Serial.println("Camera capture task could not start");healthy=false;}
  cameraWifi.begin();const char* headers[]={"X-Camera-Key"};server.collectHeaders(headers,1);
  server.on("/clock",HTTP_GET,[]{if(!auth())return;server.send(200,"application/json","{\"boot_id\":\""+boot+"\",\"monotonic_us\":"+String((uint64_t)esp_timer_get_time())+"}");});
  server.on("/health",HTTP_GET,[]{
    if(!auth())return;
    auto frame=frames.latest();
    String body=String("{\"sensor\":\"OV3660\",\"healthy\":")+(healthy?"true":"false")+
      ",\"psram_bytes\":"+String(ESP.getPsramSize())+",\"high\":"+(high?"true":"false")+
      ",\"stream_port\":81,\"stream_clients\":"+String((uint32_t)streamClients)+
      ",\"rssi_dbm\":"+String(WiFi.RSSI())+",\"channel\":"+String(WiFi.channel())+
      ",\"uptime_ms\":"+String(millis())+",\"cached_frame_ready\":"+(frame?"true":"false");
    body+=",\"clock_mhz\":"+String(frames.clockMHz.load())+
      ",\"jpeg_quality\":"+String(frames.quality.load())+
      ",\"capture_fps_limit\":"+String(frames.fps.load())+",\"send_deadline_ms\":500";
    if(frame)body+=",\"frame_sequence\":"+String(frame->sequence)+
      ",\"frame_age_ms\":"+String((uint32_t)((esp_timer_get_time()-frame->captureUs)/1000))+
      ",\"width\":"+String(frame->width)+",\"height\":"+String(frame->height);
    server.send(200,"application/json",body+"}");
  });
  server.on("/tune",HTTP_POST,[]{
    if(!auth())return;
    if(!healthy){server.send(503);return;}
    if(!server.hasArg("clock_mhz")||!server.hasArg("quality")||!server.hasArg("fps")){
      server.send(400,"text/plain","clock_mhz, quality and fps are required");return;
    }
    const String clock=server.arg("clock_mhz"),quality=server.arg("quality"),fps=server.arg("fps");
    auto numeric=[](const String& value){if(value.length()==0)return false;for(size_t i=0;i<value.length();i++)if(value[i]<'0'||value[i]>'9')return false;return true;};
    if(!numeric(clock)||!numeric(quality)||!numeric(fps)||
       (clock.toInt()!=8&&clock.toInt()!=16&&clock.toInt()!=20)||quality.toInt()<10||quality.toInt()>30||fps.toInt()<5||fps.toInt()>25){
      server.send(400,"text/plain","Allowed: clock 8/16/20 MHz, quality 10..30, fps 5..25");return;
    }
    int resolution=-1;
    if(server.hasArg("resolution")){
      const String value=server.arg("resolution");
      if(value=="320x240")resolution=FRAMESIZE_QVGA;
      else if(value=="480x320")resolution=FRAMESIZE_HVGA;
      else if(value=="640x480")resolution=FRAMESIZE_VGA;
      else if(value=="800x600")resolution=FRAMESIZE_SVGA;
      else{server.send(400,"text/plain","Resolution must be 320x240, 480x320, 640x480 or 800x600");return;}
      if(resolution!=FRAMESIZE_QVGA&&!psramFound()){server.send(409,"text/plain","Higher resolution requires PSRAM");return;}
    }
    if(!frames.tune(clock.toInt(),quality.toInt(),fps.toInt(),resolution)){server.send(500,"text/plain","Camera tuning failed");return;}
    if(resolution!=-1)high=false;
    server.send(200,"application/json","{\"ok\":true}");
  });
  server.on("/mode",HTTP_POST,[]{if(!auth())return;if(!healthy){server.send(503);return;}bool request=server.arg("high")=="1";if(request!=high){if(request&&!psramFound()){server.send(409,"text/plain","High resolution requires measured board capacity");return;}if(!frames.setResolution(request?FRAMESIZE_QXGA:FRAMESIZE_QVGA)){server.send(500,"text/plain","Resolution change failed");return;}high=request;}server.send(200,"application/json","{\"ok\":true}");});
  server.on("/jpeg",HTTP_GET,[]{
    if(!auth())return;
    if(!healthy){server.send(503);return;}
    auto frame=frames.latest();
    if(!frame){server.send(503,"text/plain","Waiting for a fresh camera frame");return;}
    server.sendHeader("X-Frame-Sequence",String(frame->sequence));
    server.sendHeader("X-Boot-Id",boot);
    server.sendHeader("X-Capture-Us",String(frame->captureUs));
    server.sendHeader("X-Width",String(frame->width));
    server.sendHeader("X-Height",String(frame->height));
    server.sendHeader("Cache-Control","no-store");
    server.setContentLength(frame->size);server.send(200,"image/jpeg","");
    server.client().setTimeout(2);server.client().write(frame->data,frame->size);yield();
  });
  server.begin();streamServer.begin();
  xTaskCreatePinnedToCore(streamTask,"mjpeg",8192,nullptr,1,nullptr,1);
}
void serviceCarrier(){server.handleClient();cameraWifi.service();delay(1);}
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
#endif
