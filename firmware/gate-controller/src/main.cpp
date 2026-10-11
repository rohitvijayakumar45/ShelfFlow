#include <WiFi.h>
#include <LittleFS.h>
#include <Preferences.h>
#include "../../common/Device.h"
#include <SPI.h>
#include <MFRC522.h>
// Demo mechanism: SG90-class servo; optical release beam + closed-position microswitch.
// Verify actual servo and sensor electrical specifications before power-up.
constexpr int SERVO_PIN=13,RELEASE_PIN=32,POSITION_PIN=33;
DeviceBus bus("gate-1");MFRC522 rfid(21,22);Preferences prefs;
enum State{IDLE,READ_UID,MOVING,VERIFY,RETURNING,FAULT};State state=IDLE;
String commandId,expected,actual,fault;bool released=false,position=false;uint32_t began=0,lastScan=0,beamSince=0,lastAck=0;
String seenCommands="";
String barrierId="";
bool sawOpenPosition=false;

void servo(bool open){ledcWrite(0,(uint32_t)((open?1900:1100)*65535UL/20000));}
bool save(){bool ok=prefs.putString("command",commandId)>0&&prefs.putString("expected",expected)>0;ok=ok&&prefs.putBool("released",released)>0&&prefs.putBool("position",position)>0&&prefs.putBool("active",state!=IDLE)>0;prefs.putString("actual",actual);prefs.putString("fault",fault);return ok;}
void sendAck(){JsonDocument d;d["command_id"]=commandId;d["actual_uid"]=actual;d["released"]=released;d["position_ok"]=position;d["fault"]=fault;d["device_key"]=DEVICE_KEY;String payload;serializeJson(d,payload);if(bus.mqtt.connected())bus.mqtt.publish(bus.topic("gate-ack").c_str(),payload.c_str());lastAck=millis();}
void finish(const char* why){fault=why;state=FAULT;save();servo(false);sendAck();}
void setup(){
  Serial.begin(115200);pinMode(RELEASE_PIN,INPUT_PULLUP);pinMode(POSITION_PIN,INPUT_PULLUP);bus.begin();prefs.begin("gate",false);
  ledcSetup(0,50,16);ledcAttachPin(SERVO_PIN,0);ledcWrite(0,0); // No commanded movement until known state.
  SPI.begin(18,19,23);rfid.PCD_Init();
  commandId=prefs.getString("command","");seenCommands=prefs.getString("seen","");expected=prefs.getString("expected","");actual=prefs.getString("actual","");released=prefs.getBool("released",false);position=prefs.getBool("position",false);fault=prefs.getString("fault","");
  if(prefs.getBool("active",false)){fault="REBOOT_UNCERTAIN_RELEASE";state=FAULT;save();}
}
void loop(){
  bus.tick();uint32_t now=millis();
  if(bus.command.length()){
    JsonDocument d;if(!deserializeJson(d,bus.command)){
      String incoming=d["command_id"].as<String>();
      if(d["action"]=="FLUSH")barrierId=d["barrier_id"].as<String>();
      if(d["action"]=="DISPENSE"&&incoming.length()){
        if(incoming==commandId){if(state==IDLE||state==FAULT)sendAck();} // Never acknowledge uncertain release while motion is in progress.
        else if(seenCommands.indexOf("|"+incoming+"|")>=0){Serial.println("Prior command replay refused");}
        else if(seenCommands.length()>12000){Serial.println("Command ledger full: inspect and rotate device ledger before reuse");}
        else if(state==IDLE&&incoming.indexOf('|')<0&&incoming.length()<=100){commandId=incoming;seenCommands+="|"+incoming+"|";expected=d["expected_uid"].as<String>();actual="";released=false;position=false;fault="";state=READ_UID;began=now;if(prefs.putString("seen",seenCommands)==0||!save())finish("PERSISTENCE_FAILURE");}
      }
      if(d["action"]=="MANUAL_RESET"&&d["physical_inspected"].as<bool>()&&state==FAULT){state=IDLE;prefs.putBool("active",false);ledcWrite(0,0);}
    }bus.command="";
  }
  if(state==READ_UID&&now-lastScan>=100){lastScan=now;if(rfid.PICC_IsNewCardPresent()&&rfid.PICC_ReadCardSerial()){
    actual="";for(byte i=0;i<rfid.uid.size;i++){if(rfid.uid.uidByte[i]<16)actual+="0";actual+=String(rfid.uid.uidByte[i],HEX);}actual.toUpperCase();rfid.PICC_HaltA();rfid.PCD_StopCrypto1();
    if(actual!=expected)finish("WRONG_UID");else if(digitalRead(POSITION_PIN)!=LOW)finish("POSITION_NOT_READY");else if(digitalRead(RELEASE_PIN)==LOW)finish("RELEASE_SENSOR_BLOCKED");else{state=MOVING;began=now;sawOpenPosition=false;beamSince=0;if(!save())finish("PERSISTENCE_FAILURE");else servo(true);}
  }else if(now-began>5000)finish("UID_TIMEOUT");}
  if(state==MOVING){if(digitalRead(POSITION_PIN)==HIGH)sawOpenPosition=true;if(digitalRead(RELEASE_PIN)==LOW&&sawOpenPosition){if(!beamSince)beamSince=now;if(now-beamSince>40){released=true;state=RETURNING;began=now;save();servo(false);}}else beamSince=0;if(now-began>3000)finish("JAM_OR_RELEASE_TIMEOUT");}
  if(state==RETURNING){if(digitalRead(POSITION_PIN)==LOW&&now-began>500){position=true;state=IDLE;save();sendAck();ledcWrite(0,0);}else if(now-began>3000)finish("POSITION_TIMEOUT");}
  if(commandId.length()&&bus.acknowledgedCommand!=commandId&&(state==FAULT||state==IDLE)&&now-lastAck>2000)sendAck();
  if(barrierId.length()&&state==IDLE&&bus.empty()){JsonDocument d;bus.envelope(d,"BARRIER","tray-a","UNSET");d["evidence_refs"].add("BARRIER:"+barrierId);bus.persist(d);barrierId="";}
  delay(1);
}
