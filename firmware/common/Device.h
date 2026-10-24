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
#include "queue_filename.h"
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
  static constexpr unsigned QUEUE_CAPACITY = 256;
  unsigned queuedFiles=0;
  // Queued event paths ("/<stem>.json") ordered by sequence number. Built once at boot and kept
  // current on persist/ACK, so finding the oldest event never rescans the flash directory
  // (a scan of ~180 files took seconds and stalled the whole controller loop).
  std::deque<String> pending;
  bool storageOK=false, wifiStarted=false;
  uint32_t wifiStartAt=0;
  DeviceBus(const char* device):id(device){}
  void lost(){overflow++;if(overflow==1||(overflow&(overflow-1))==0)diagnostics.putUInt("overflow",overflow);}
  String topic(const char* suffix){return "shelfflow/devices/"+id+"/"+suffix;}
  static long seqOf(const String& path){return path.substring(path.lastIndexOf('-')+1,path.length()-5).toInt();}
  void track(const String& path){
    auto at=pending.end();
    while(at!=pending.begin()&&seqOf(*(at-1))>seqOf(path))--at;  // usually appends: new events have the highest sequence
    pending.insert(at,path);
  }
  void untrack(const String& path){for(auto it=pending.begin();it!=pending.end();++it)if(*it==path){pending.erase(it);return;}}
  bool acknowledgeEvent(const String& event){
    if(!storageOK || !event.startsWith(id+"-") || event.indexOf('/')>=0)return false;
    bool removed=false;
    for(const String& path : {"/"+event.substring(id.length()+1)+".json","/"+event+".json"}){
      if(LittleFS.exists(path.c_str())){
        File f=LittleFS.open(path.c_str(),"r");JsonDocument saved;
        auto error=deserializeJson(saved,f);f.close();
        if(error || saved["event_id"].as<String>()!=event)return false;
        if(!LittleFS.remove(path.c_str()))return false;
        if(queuedFiles)queuedFiles--;removed=true;untrack(path);
      }
    }
    return removed;
  }
#ifdef USB_RUNTIME
  void usbNext(){
    if(!storageOK){Serial.println("SF_ERROR storage_unavailable");return;}
    while(!pending.empty()&&!LittleFS.exists(pending.front().c_str()))pending.pop_front();  // removed elsewhere
    if(pending.empty()){Serial.println("SF_IDLE");return;}
    String path=pending.front();
    File queued=LittleFS.open(path.c_str(),"r");JsonDocument d;auto error=deserializeJson(d,queued);queued.close();
    if(error){Serial.println("SF_ERROR invalid_queued_json");return;}
    d.remove("device_key"); // USB host reads its credential locally; never print it.
    Serial.print("SF_EVENT ");serializeJson(d,Serial);Serial.println();
  }
#endif
  void begin(){
    boot=String(esp_random(),HEX)+String(esp_random(),HEX);
    diagnostics.begin("sfdiag",false);overflow=diagnostics.getUInt("overflow",0);
    storageOK=LittleFS.begin(false); // Never silently format away unacknowledged events.
    if(!storageOK)Serial.println("LittleFS missing. Initialize explicitly; evidence queue disabled.");
    if(storageOK){File root=LittleFS.open("/");File f=root.openNextFile();while(f){String name=f.name();if(name.endsWith(".json")||name.endsWith(".tmp"))queuedFiles++;if(!f.isDirectory()&&name.endsWith(".json"))track(name.startsWith("/")?name:"/"+name);if(name.endsWith(".tmp")){lost();Serial.println("Incomplete event after reboot: assisted reconciliation required");}f.close();f=root.openNextFile();}root.close();}
#ifdef DEVICE_OFFLINE
    WiFi.mode(WIFI_OFF); // Bench diagnosis only: isolates sensor wiring from Wi-Fi supply peaks.
    Serial.println("DEVICE_OFFLINE build: Wi-Fi/MQTT disabled");
#else
    WiFi.mode(WIFI_STA);
#ifdef DEVICE_WIFI_TX_POWER
    WiFi.setTxPower(DEVICE_WIFI_TX_POWER); // smaller transmit current spikes for weak supplies
#endif
#ifdef DEVICE_WIFI_DELAY_MS
    wifiStartAt=millis()+DEVICE_WIFI_DELAY_MS; // let sensors and display start before the radio's current spikes
#else
    WiFi.begin(WIFI_SSID,WIFI_PASSWORD);wifiStarted=true;
#endif
    configTime(0,0,"pool.ntp.org","time.nist.gov");
#endif
    mqtt.setServer(MQTT_HOST,MQTT_PORT);mqtt.setBufferSize(4096);mqtt.setSocketTimeout(1);
    mqtt.setCallback([this](char* t,byte* payload,unsigned int len){
      JsonDocument doc;if(deserializeJson(doc,payload,len))return;
      if(String(t)==topic("acks")&&doc["durable"].as<bool>()){
        if(doc["command_id"].is<const char*>())acknowledgedCommand=doc["command_id"].as<String>();
        String event=doc["event_id"].as<String>();
        acknowledgeEvent(event);
      }else if(String(t)==topic("commands")&&len<3000){if(incomingCommands.size()<8)incomingCommands.push_back(String((char*)payload,len));else{lost();Serial.println("COMMAND_QUEUE_OVERFLOW");}}
    });
  }
  void tick(){
    uint32_t now=millis();
#ifdef DEVICE_OFFLINE
    return;
#endif
    if(!wifiStarted){
      if((int32_t)(now-wifiStartAt)<0)return;
      WiFi.begin(WIFI_SSID,WIFI_PASSWORD);wifiStarted=true;nextRetry=now+10000;
#ifdef DEVICE_WIFI_TX_POWER
      WiFi.setTxPower(DEVICE_WIFI_TX_POWER);
#endif
      return;
    }
    if(WiFi.status()!=WL_CONNECTED){if((int32_t)(now-nextRetry)>=0){WiFi.reconnect();nextRetry=now+backoff;backoff=min(backoff*2,(uint32_t)30000);}return;}
    if(!mqtt.connected()&&(int32_t)(now-nextRetry)>=0){
      if(mqtt.connect((id+"-"+boot).c_str(),id.c_str(),MQTT_PASSWORD,topic("availability").c_str(),1,true,"offline")){
        mqtt.subscribe(topic("commands").c_str(),1);mqtt.subscribe(topic("acks").c_str(),1);mqtt.publish(topic("availability").c_str(),"online",true);backoff=1000;
      }else{nextRetry=now+backoff;backoff=min(backoff*2,(uint32_t)30000);}
    }
    mqtt.loop();
    if(command.length()==0&&!incomingCommands.empty()){command=incomingCommands.front();incomingCommands.pop_front();}
    if(storageOK&&mqtt.connected()&&now-lastPublish>=500){
      // Send the oldest queued sequence first (from the in-memory index, never a directory scan).
      lastPublish=now;
      while(!pending.empty()&&!LittleFS.exists(pending.front().c_str()))pending.pop_front();
      if(!pending.empty()){File queued=LittleFS.open(pending.front().c_str(),"r");if(queued){String data=queued.readString();queued.close();mqtt.publish(topic("events").c_str(),data.c_str());}}
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
    // Count once at boot, then maintain on writes/ACKs. Never rescan up to
    // hundreds of files on every tap. Reserve space for the filesystem metadata.
    const size_t needed=measureJson(d)+4096;
    if(queuedFiles>=QUEUE_CAPACITY || LittleFS.totalBytes()-LittleFS.usedBytes()<needed){lost();Serial.println("QUEUE_OVERFLOW: assisted reconciliation required");return false;}
    char stem[28];
    if(!queueStem(stem,sizeof(stem),d["boot_id"] | "",d["sequence_number"].as<uint32_t>()) ||
       d["event_id"].as<String>()!=id+"-"+String(stem)) {lost();Serial.println("QUEUE_ID_INVALID");return false;}
    String path="/"+String(stem);
    File out=LittleFS.open((path+".tmp").c_str(),"w");if(!out){lost();return false;}
    queuedFiles++;
    // Serialize once before writing. This gives a stable byte count and avoids
    // hundreds of individual filesystem writes on the time-sensitive path.
    String payload;serializeJson(d,payload);
    const size_t expected=payload.length();
    const size_t written=out.write(reinterpret_cast<const uint8_t*>(payload.c_str()),expected);
    out.flush();out.close();
    const bool renamed=written==expected && LittleFS.rename((path+".tmp").c_str(),(path+".json").c_str());
    if(!renamed){
      lost();Serial.printf("QUEUE_WRITE_FAILED bytes=%u expected=%u rename=%s: evidence retained for review\n",(unsigned)written,(unsigned)expected,written==expected?"failed":"not_attempted");return false;
    }
    track(path+".json");
    return true;
  }
#if defined(DEVICE_OFFLINE) && defined(RFID_DIAGNOSTIC) && !defined(USB_RUNTIME)
  bool benchStorageTest(){
    if(seq<98)seq=98; // exercise the filename boundary that failed before
    for(unsigned i=0;i<14;i++){
      JsonDocument d;envelope(d,"HEALTH","tray-a","BENCH_STORAGE_TEST");
      d["evidence_refs"].add("BENCH_STORAGE_TEST_NOT_A_CUSTOMER_EVENT");
      if(!persist(d))return false;
      char stem[28];if(!queueStem(stem,sizeof(stem),boot.c_str(),d["sequence_number"].as<uint32_t>()))return false;
      String path="/"+String(stem);
      File file=LittleFS.open((path+".json").c_str(),"r");if(!file)return false;
      JsonDocument saved;auto error=deserializeJson(saved,file);file.close();
      if(error || saved["event_id"].as<String>()!=d["event_id"].as<String>() || LittleFS.exists((path+".tmp").c_str()))return false;
    }
    Serial.printf("QUEUE_SELFTEST_PASS events=14 durable=%u/%u (bench HEALTH probes, not RFID scans)\n",queuedFiles,QUEUE_CAPACITY);
    return true;
  }
  // Explicit USB maintenance for retired bench tests only, after a host backup.
  // Keep every file on flash; record the old loss counter before starting fresh.
  bool archiveBenchQueue(){
    if(!storageOK || LittleFS.totalBytes()-LittleFS.usedBytes()<8192)return false;
    if(!LittleFS.exists("/bench-archive")&&!LittleFS.mkdir("/bench-archive"))return false;
    std::deque<String> names;
    File root=LittleFS.open("/");File file=root.openNextFile();
    while(file){String name=file.name();if(!file.isDirectory()&&(name.endsWith(".json")||name.endsWith(".tmp")))names.push_back(name);file.close();file=root.openNextFile();}
    root.close();
    JsonDocument context;context["reason"]="RETIRED_USB_BENCH_TESTS_AFTER_HOST_BACKUP";
    context["previous_overflow"]=overflow;context["files_to_archive"]=names.size();context["boot_id"]=boot;
    File note=LittleFS.open(("/bench-archive/context-"+boot+".json").c_str(),"w");if(!note)return false;
    size_t written=serializeJson(context,note);note.flush();note.close();
    if(written!=measureJson(context))return false;
    for(const auto& name:names){
      String base=name.substring(name.lastIndexOf('/')+1);
      String destination="/bench-archive/"+base;
      String source=name.startsWith("/")?name:"/"+name;
      if(LittleFS.exists(destination)||!LittleFS.rename(source,destination))return false;
      if(queuedFiles)queuedFiles--;
    }
    if(diagnostics.putUInt("overflow",0)!=sizeof(uint32_t))return false;
    overflow=0;
    Serial.printf("BENCH_ARCHIVED files=%u durable_queue=%u/%u previous loss count preserved in archive\n",(unsigned)names.size(),queuedFiles,QUEUE_CAPACITY);
    return true;
  }
#endif
  bool empty(){if(!storageOK)return false;File root=LittleFS.open("/");File file=root.openNextFile();while(file){String name=file.name();if(name.endsWith(".json")||name.endsWith(".tmp"))return false;file=root.openNextFile();}return true;}
};
