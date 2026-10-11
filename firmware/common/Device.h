#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <LittleFS.h>
#include <Preferences.h>
#include <esp_timer.h>
#include <time.h>
#include <deque>
#if __has_include("secrets.h")
#include "secrets.h"
#else
#include "Secrets.example.h"
#endif

class DeviceBus {
 public:
  WiFiClient network;
  PubSubClient mqtt{network};
  Preferences diagnostics;
  String id, boot, command, acknowledgedCommand;
  std::deque<String> incomingCommands;
  uint32_t seq=0, overflow=0, nextRetry=0, backoff=1000, lastPublish=0;
  bool storageOK=false;
  DeviceBus(const char* device):id(device){}
  void lost(){overflow++;if(overflow==1||(overflow&(overflow-1))==0)diagnostics.putUInt("overflow",overflow);}
  String topic(const char* suffix){return "shelfflow/devices/"+id+"/"+suffix;}
  void begin(){
    boot=String(esp_random(),HEX)+String(esp_random(),HEX);
    diagnostics.begin("sfdiag",false);overflow=diagnostics.getUInt("overflow",0);
    storageOK=LittleFS.begin(false); // Never silently format away unacknowledged events.
    if(!storageOK)Serial.println("LittleFS missing. Initialize explicitly; evidence queue disabled.");
    if(storageOK){File root=LittleFS.open("/");File f=root.openNextFile();while(f){if(String(f.name()).endsWith(".tmp")){lost();Serial.println("Incomplete event after reboot: assisted reconciliation required");}f=root.openNextFile();}}
    WiFi.mode(WIFI_STA);WiFi.begin(WIFI_SSID,WIFI_PASSWORD);
    configTime(0,0,"pool.ntp.org","time.nist.gov");
    mqtt.setServer(MQTT_HOST,MQTT_PORT);mqtt.setBufferSize(4096);mqtt.setSocketTimeout(1);
    mqtt.setCallback([this](char* t,byte* payload,unsigned int len){
      JsonDocument doc;if(deserializeJson(doc,payload,len))return;
      if(String(t)==topic("acks")&&doc["durable"].as<bool>()){
        if(doc["command_id"].is<const char*>())acknowledgedCommand=doc["command_id"].as<String>();
        String event=doc["event_id"].as<String>();
        if(event.length()&&event.indexOf('/')<0&&storageOK)LittleFS.remove(("/"+event+".json").c_str());
      }else if(String(t)==topic("commands")&&len<3000){if(incomingCommands.size()<8)incomingCommands.push_back(String((char*)payload,len));else{lost();Serial.println("COMMAND_QUEUE_OVERFLOW");}}
    });
  }
  void tick(){
    uint32_t now=millis();
    if(WiFi.status()!=WL_CONNECTED){if((int32_t)(now-nextRetry)>=0){WiFi.reconnect();nextRetry=now+backoff;backoff=min(backoff*2,(uint32_t)30000);}return;}
    if(!mqtt.connected()&&(int32_t)(now-nextRetry)>=0){
      if(mqtt.connect((id+"-"+boot).c_str(),id.c_str(),MQTT_PASSWORD,topic("availability").c_str(),1,true,"offline")){
        mqtt.subscribe(topic("commands").c_str(),1);mqtt.subscribe(topic("acks").c_str(),1);mqtt.publish(topic("availability").c_str(),"online",true);backoff=1000;
      }else{nextRetry=now+backoff;backoff=min(backoff*2,(uint32_t)30000);}
    }
    mqtt.loop();
    if(command.length()==0&&!incomingCommands.empty()){command=incomingCommands.front();incomingCommands.pop_front();}
    if(storageOK&&mqtt.connected()&&now-lastPublish>=500){
      lastPublish=now;File root=LittleFS.open("/");File f=root.openNextFile();
      while(f){String name=f.name();if(name.endsWith(".json")){String data=f.readString();mqtt.publish(topic("events").c_str(),data.c_str());break;}f=root.openNextFile();}
    }
  }
  void envelope(JsonDocument& d,const char* type,const char* tray,const char* calibration){
    uint32_t s=seq++;double wall=(double)time(nullptr);
    d["schema_version"]=1;d["event_id"]=id+"-"+boot+"-"+String(s);d["event_type"]=type;
    d["device_id"]=id;d["boot_id"]=boot;d["sequence_number"]=s;d["shelf_id"]="shelf-1";d["tray_id"]=tray;
    d["capture_start"]=wall;d["capture_end"]=wall;d["monotonic_ms"]=(uint64_t)(esp_timer_get_time()/1000);
    d["clock_synced"]=false;d["clock_uncertainty_ms"]=1000; // NTP wall seconds alone is insufficient for fusion ordering.
    d["calibration_version"]=calibration;d["model_status"]="MODEL_UNAVAILABLE";
    d["sensor_health"]["ok"]=storageOK&&overflow==0;d["sensor_health"]["queue_overflow"]=overflow;
    d["device_key"]=DEVICE_KEY;
  }
  bool persist(JsonDocument& d){
    if(!storageOK){lost();return false;}
    unsigned count=0;File root=LittleFS.open("/");File file=root.openNextFile();while(file){String name=file.name();if(name.endsWith(".json")||name.endsWith(".tmp"))count++;file=root.openNextFile();}
    if(count>=64){lost();Serial.println("QUEUE_OVERFLOW: assisted reconciliation required");return false;}
    String path="/"+d["event_id"].as<String>();File out=LittleFS.open((path+".tmp").c_str(),"w");if(!out){lost();return false;}
    serializeJson(d,out);out.flush();out.close();return LittleFS.rename((path+".tmp").c_str(),(path+".json").c_str());
  }
  bool empty(){if(!storageOK)return false;File root=LittleFS.open("/");File file=root.openNextFile();while(file){String name=file.name();if(name.endsWith(".json")||name.endsWith(".tmp"))return false;file=root.openNextFile();}return true;}
};
