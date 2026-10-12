#include <WiFi.h>
#include <LittleFS.h>
#include <Preferences.h>
#include "../../common/Device.h"
#include <SPI.h>
#include <MFRC522.h>
#include <HX711.h>
#include <math.h>

// PROPOSED only for separate DevKitC V4 ESP32-WROOM-32. Never camera-board pins.
DeviceBus bus("sensor-1");MFRC522 reader(21,22);Preferences prefs;
struct TrayState {
  HX711 hx;const char* id;float zero=0,scale=0,values[12]={0},baseline=0;String version="UNSET";
  unsigned count=0,index=0;bool baselineSet=false;uint32_t stableSince=0,lastReady=0,lastChange=0;long lastRaw=0;
} trays[2];
uint32_t lastRfid=0,lastHealth=0,lastDiag=0;String lastUid="";
String barrierId="";uint32_t barrierRequested=0;

void sample(TrayState& t){
  if(!t.hx.is_ready())return;
  long raw=t.hx.read();uint32_t now=millis();t.lastReady=now;
  if(raw!=t.lastRaw){t.lastChange=now;t.lastRaw=raw;}
  if(t.scale==0)return;
  float grams=(raw-t.zero)/t.scale;t.values[t.index++%12]=grams;t.count=min(t.count+1,12U);
  float sorted[12],sum=0,variance=0;for(unsigned i=0;i<t.count;i++){sorted[i]=t.values[i];sum+=sorted[i];}
  for(unsigned i=0;i<t.count;i++)for(unsigned j=i+1;j<t.count;j++)if(sorted[j]<sorted[i]){float x=sorted[j];sorted[j]=sorted[i];sorted[i]=x;}
  float median=sorted[t.count/2],mean=sum/t.count;for(unsigned i=0;i<t.count;i++)variance+=powf(t.values[i]-mean,2);float noise=sqrtf(variance/t.count);
  bool fault=abs(grams)>5000||now-t.lastChange>10000;
  bool stable=t.count==12&&noise<2&&!fault;
  if(!stable){t.stableSince=0;return;}
  if(!t.stableSince){t.stableSince=now;return;}
  if(now-t.stableSince<600)return;
  if(!t.baselineSet){t.baseline=median;t.baselineSet=true;return;}
  if(abs(median-t.baseline)<8)return;
  JsonDocument d;bus.envelope(d,"WEIGHT",t.id,t.version.c_str());
  d["weight"]["raw"]=raw;d["weight"]["filtered"]=median;d["weight"]["pre_g"]=t.baseline;d["weight"]["post_g"]=median;d["weight"]["noise_g"]=noise;d["weight"]["settled"]=true;
  bus.persist(d);t.baseline=median;
}
void setup(){
  Serial.begin(115200);bus.begin();prefs.begin("tray-cal",false);
  trays[0].id="tray-a";trays[1].id="tray-b";trays[0].hx.begin(32,33);trays[1].hx.begin(25,26);
  for(unsigned i=0;i<2;i++){String key=String(i);trays[i].zero=prefs.getFloat((key+"zero").c_str(),0);trays[i].scale=prefs.getFloat((key+"scale").c_str(),0);trays[i].version=prefs.getString((key+"ver").c_str(),"UNSET");}
  SPI.begin(18,19,23);reader.PCD_Init();
}
void loop(){
  bus.tick();uint32_t now=millis();for(auto& t:trays)sample(t);
  if(bus.command.length()){
    JsonDocument d;deserializeJson(d,bus.command);bus.command="";
    if(d["action"]=="FLUSH"){barrierId=d["barrier_id"].as<String>();barrierRequested=now;}
    if(d["action"]=="CALIBRATE")for(unsigned i=0;i<2;i++)if(d["tray_id"]==trays[i].id){
      float grams=d["known_mass_g"],zero=d["zero_raw"],loaded=d["mass_raw"];
      if(grams>0&&loaded!=zero){auto& t=trays[i];String requested=d["version"].as<String>();if(requested==t.version){Serial.println("Duplicate calibration version refused");continue;}t.zero=zero;t.scale=(loaded-zero)/grams;t.version=requested;t.baselineSet=false;t.count=0;
        String key=String(i);prefs.putFloat((key+"zero").c_str(),t.zero);prefs.putFloat((key+"scale").c_str(),t.scale);prefs.putString((key+"ver").c_str(),t.version);Serial.println("Explicit calibration saved");}
    }
  }
  if(barrierId.length()&&bus.empty()&&now-barrierRequested>1500){JsonDocument d;bus.envelope(d,"BARRIER","tray-a","UNSET");d["evidence_refs"].add("BARRIER:"+barrierId);bus.persist(d);barrierId="";}
  if(now-lastRfid>=100){lastRfid=now;if(reader.PICC_IsNewCardPresent()&&reader.PICC_ReadCardSerial()){
    String uid="";for(byte i=0;i<reader.uid.size;i++){if(reader.uid.uidByte[i]<16)uid+="0";uid+=String(reader.uid.uidByte[i],HEX);}uid.toUpperCase();
    Serial.println("DIAG RFID uid="+uid);
    JsonDocument d;bus.envelope(d,"RFID","tray-a","UNSET");d["evidence_refs"].add("RFID:"+uid);bus.persist(d);
    // One provisional reader marks a local zone, not the whole shelf. Both trays remain observed visually.
    reader.PICC_HaltA();reader.PCD_StopCrypto1();}}
  // Bench bring-up output: raw counts work before calibration, Wi-Fi or MQTT exist.
  if(now-lastDiag>=1000){lastDiag=now;Serial.printf("DIAG raw tray-a=%ld%s tray-b=%ld%s rc522_version=0x%02X wifi=%s mqtt=%s fs=%s\n",
    trays[0].lastRaw,now-trays[0].lastReady>1000?"(NO_DATA)":"",trays[1].lastRaw,now-trays[1].lastReady>1000?"(NO_DATA)":"",
    reader.PCD_ReadRegister(MFRC522::VersionReg),WiFi.status()==WL_CONNECTED?"up":"down",bus.mqtt.connected()?"up":"down",bus.storageOK?"ok":"MISSING");}
  if(now-lastHealth>=500){lastHealth=now;for(auto& t:trays){JsonDocument d;bus.envelope(d,"HEALTH",t.id,t.version.c_str());
    if(now-t.lastReady>1000){d["sensor_health"]["flags"].add("DISCONNECTED");d["sensor_health"]["ok"]=false;}
    if(!t.scale){d["sensor_health"]["flags"].add("UNCALIBRATED");d["sensor_health"]["ok"]=false;}
    if(t.scale&&now-t.lastChange>10000){d["sensor_health"]["flags"].add("STUCK_READING");d["sensor_health"]["ok"]=false;}
    if(t.scale&&abs((t.lastRaw-t.zero)/t.scale)>5000){d["sensor_health"]["flags"].add("OVERLOAD");d["sensor_health"]["ok"]=false;}
    d["weight"]["raw"]=t.lastRaw;if(t.scale)d["weight"]["filtered"]=(t.lastRaw-t.zero)/t.scale;else d["weight"]["filtered"]=nullptr;d["weight"]["pre_g"]=t.baseline;d["weight"]["post_g"]=t.baseline;d["weight"]["noise_g"]=2;d["weight"]["settled"]=false;
    // Best-effort diagnostics avoid constant flash writes. Interaction deltas are persisted.
    String body;serializeJson(d,body);if(bus.mqtt.connected())bus.mqtt.publish(bus.topic("events").c_str(),body.c_str());}}
  delay(1);
}
