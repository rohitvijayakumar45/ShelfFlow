#include <WiFi.h>
#include <LittleFS.h>
#include <Preferences.h>
#include "../../common/Device.h"
#include <SPI.h>
#include <MFRC522.h>
#include <HX711.h>
#include <Wire.h>
#include <math.h>
#include <limits.h>
#include <stdlib.h>
#include <atomic>
#include <freertos/queue.h>
#include "rfid_tap.h"

// Shelf sensor controller: one tray ("box") on three load cells in a tripod, an RC522,
// a 16x2 I2C LCD and two staff service buttons.
// PROPOSED for a separate ESP32-WROOM-32 DevKit. Never the camera board.
// The buttons are staff-only maintenance controls inside the enclosure; they never
// authorise a shopper. Normal-mode buttons only change the display.

// ---------------- Pin map ----------------
constexpr uint8_t SPI_SCK = 18, SPI_MISO = 19, SPI_MOSI = 23, RC522_RST = 22;  // SPI bus and RST shared
constexpr int NREADER = 2;
const uint8_t READER_SS[NREADER] = {21, 4};          // R1 left of tray front, R2 right (own chip select each)
const char* READER_NAME[NREADER] = {"R1", "R2"};
constexpr uint8_t I2C_SDA = 16, I2C_SCL = 17;      // NOT 21/22: those belong to the RC522
constexpr uint8_t BTN_PAGE = 13, BTN_ACTION = 14;  // to GND, internal pull-ups
constexpr int NCELL = 3;
struct CellPins { char name; uint8_t dt, sck; };
const CellPins CELL_PINS[NCELL] = {{'A', 32, 33}, {'B', 25, 26}, {'C', 34, 27}};  // GPIO34 is input-only: DT only
const char* TRAY_ID = "tray-a";

// ---------------- Tuning (starting values; measure on the real platform) ----------------
constexpr int WINDOW = 10;              // samples per cell (~1 s at 10 SPS)
constexpr float STABLE_NOISE_G = 2.0;   // total noise below this counts as settled
constexpr float EVENT_HYSTERESIS_G = 8.0;
constexpr uint32_t DWELL_MS = 600;
constexpr uint32_t SERVICE_TIMEOUT_MS = 120000;
constexpr float CELL_OVERLOAD_G = 5000;
constexpr long SATURATION_RAW = 8388000;

DeviceBus bus("sensor-1");
MFRC522 readers[NREADER] = {MFRC522(READER_SS[0], RC522_RST), MFRC522(READER_SS[1], RC522_RST)};
struct RfidObservation { uint8_t reader; char uid[21]; uint32_t capturedAt; };
QueueHandle_t rfidObservations = nullptr;
std::atomic<int> rfidOnly{-1};
std::atomic<uint32_t> readerReadCount[NREADER]{};
std::atomic<uint32_t> rfidDropped{0}, rfidMaxScanGap[NREADER]{};
uint8_t readerVersion[NREADER]{};

// RF tuning for range and responsiveness. Only the scan task touches reader registers after
// setup; bench commands only change these atomics and raise the dirty flag.
//   power 0: chip defaults.      1: carrier (CW) drive maximum, modulation untouched.
//   power 2: carrier AND modulation drive maximum (strongest field; measured best on this shelf, see reports/rfid-range-*.json).
// threshold: RxThresholdReg (default 0x84). Lower MinLevel = more sensitive receiver.
// settle_ms: time the field runs before polling, so the tag can power up (ISO 14443 wants >= 5 ms).
// wake_tries: extra WUPA attempts per turn: a marginal tag often answers the second one.
constexpr uint8_t RF_POWER_DEFAULT = 2, RF_THRESHOLD_DEFAULT = 0x84, RF_SETTLE_DEFAULT_MS = 6, RF_WAKE_TRIES_DEFAULT = 2;
std::atomic<uint8_t> rfPower{RF_POWER_DEFAULT}, rfThreshold{RF_THRESHOLD_DEFAULT}, rfSettleMs{RF_SETTLE_DEFAULT_MS}, rfWakeTries{RF_WAKE_TRIES_DEFAULT};
std::atomic<bool> rfDirty{true};
void applyRfTuning(MFRC522& reader) {
  const uint8_t power = rfPower.load();
  reader.PCD_SetAntennaGain(MFRC522::RxGain_max);  // 48 dB receiver
  reader.PCD_WriteRegister(MFRC522::GsNReg,    power == 0 ? 0x88 : power == 1 ? 0xF8 : 0xFF);
  reader.PCD_WriteRegister(MFRC522::CWGsPReg,  power == 0 ? 0x20 : 0x3F);
  reader.PCD_WriteRegister(MFRC522::ModGsPReg, power == 2 ? 0x3F : 0x20);
  reader.PCD_WriteRegister(MFRC522::RxThresholdReg, rfThreshold.load());
  // The library waits 25 ms (TReload 1000 x 25 us) for an answer, which is what an empty poll costs.
  // A tag answers REQA/WUPA/SELECT within ~1 ms, so a 4 ms timeout (160 x 25 us) is safe and makes
  // each empty poll ~6x faster: the shelf notices a presented tag sooner and polls both readers more often.
  reader.PCD_WriteRegister(MFRC522::TReloadRegH, 0x00);
  reader.PCD_WriteRegister(MFRC522::TReloadRegL, 0xA0);
}

// Only this task touches SPI/readers after setup. Flash writes, LCD and network
// reconnects no longer schedule RF polling. Retain brief taps in RAM until
// the main loop can persist them. Fields alternate to avoid mutual interference.
void rfidScanTask(void*) {
  RfidTap taps[NREADER];
  uint32_t lastScan[NREADER]{};
  int next = 0;
  for(;;) {
    const int selected = rfidOnly.load();
    const int r = selected >= 0 ? selected : next;
    next = (next + 1) % NREADER;
    if(rfDirty.exchange(false)) { for(int o=0;o<NREADER;o++) applyRfTuning(readers[o]); }
    uint32_t now = millis();
    if(lastScan[r]) {
      uint32_t gap = now - lastScan[r];
      if(gap > rfidMaxScanGap[r].load()) rfidMaxScanGap[r].store(gap);
    }
    lastScan[r] = now;
    for(int o=0;o<NREADER;o++) if(o!=r) readers[o].PCD_AntennaOff();
    MFRC522& reader = readers[r];
    reader.PCD_AntennaOn();
    vTaskDelay(pdMS_TO_TICKS(rfSettleMs.load()));
    reader.PCD_StopCrypto1();
    reader.PCD_WriteRegister(MFRC522::CommandReg, MFRC522::PCD_Idle);
    reader.PCD_WriteRegister(MFRC522::TxModeReg, 0x00);
    reader.PCD_WriteRegister(MFRC522::RxModeReg, 0x00);
    reader.PCD_WriteRegister(MFRC522::ModWidthReg, 0x26);
    byte atqa[2], size=sizeof(atqa);
    auto wake = reader.PICC_WakeupA(atqa,&size);
    for(uint8_t t=1; t<rfWakeTries.load() && wake!=MFRC522::STATUS_OK && wake!=MFRC522::STATUS_COLLISION; t++) {
      size=sizeof(atqa);  // a tag at the edge of range often answers only the second attempt
      wake = reader.PICC_WakeupA(atqa,&size);
    }
    if((wake==MFRC522::STATUS_OK || wake==MFRC522::STATUS_COLLISION) && reader.PICC_ReadCardSerial()) {
      RfidObservation observation{};
      observation.reader=r;observation.capturedAt=millis();
      for(byte i=0;i<reader.uid.size;i++) snprintf(observation.uid+2*i,3,"%02X",reader.uid.uidByte[i]);
      readerReadCount[r]++;
      if(taps[r].observe(observation.uid,observation.capturedAt) &&
         xQueueSend(rfidObservations,&observation,0)!=pdTRUE) rfidDropped++;
      reader.PICC_HaltA();
    } else if(wake==MFRC522::STATUS_OK || wake==MFRC522::STATUS_COLLISION) {
      taps[r].responded(millis());
    } else taps[r].absent(millis());
    reader.PCD_StopCrypto1();
    reader.PCD_WriteRegister(MFRC522::CommandReg, MFRC522::PCD_Idle);
    if(selected<0) reader.PCD_AntennaOff();
    vTaskDelay(1);
  }
}

#ifdef RFID_DIAGNOSTIC
bool calibrationConsole(const String& command);
void usbState();
void reportExistingCalibration();
char rfidCommand[160] = {};
unsigned rfidCommandLength = 0;
bool rfidCommandOverflow = false;
void rfidConsole() {
  unsigned budget = 64;
  while (budget-- && Serial.available()) {
    char ch = Serial.read();
    if (ch == '\r' || ch == '\n') {
      rfidCommand[rfidCommandLength] = '\0';
      String command(rfidCommand); command.trim(); command.toLowerCase();
      rfidCommandLength = 0;
      if (rfidCommandOverflow) { rfidCommandOverflow = false; Serial.println("RFID command too long"); continue; }
      if (!command.length()) continue;
      if (calibrationConsole(command)) continue;
#ifdef USB_RUNTIME
      if(command=="usbstatus"){usbState();continue;}
      if(command=="next"){bus.usbNext();continue;}
      if(command=="calreport"){reportExistingCalibration();continue;}
      if(command.startsWith("ack ")){
        String event=String(rfidCommand).substring(4); // event IDs are lower-case firmware IDs
        Serial.printf("SF_ACK %s %s\n",event.c_str(),bus.acknowledgeEvent(event)?"removed":"not_found");continue;
      }
#endif
#if defined(DEVICE_OFFLINE) && !defined(USB_RUNTIME)
      if(command=="queuetest") {
        if(!bus.benchStorageTest()) {bus.lost();Serial.println("QUEUE_SELFTEST_FAILED");}
        continue;
      }
      if(command=="bencharchive") {
        if(rfidObservations && uxQueueMessagesWaiting(rfidObservations)) Serial.println("BENCH_ARCHIVE refused: pending RFID taps");
        else if(!bus.archiveBenchQueue()) Serial.println("BENCH_ARCHIVE failed: existing files/history retained");
        continue;
      }
#endif
      if(command.startsWith("rf")) {
        // rfpower 0|1|2, rfthr <hex>, rfsettle <ms>, rftries <n>, rfstatus
        String arg = command.indexOf(' ') > 0 ? command.substring(command.indexOf(' ') + 1) : "";
        arg.trim();
        if(command.startsWith("rfpower ") && (arg == "0" || arg == "1" || arg == "2")) rfPower.store(arg.toInt());
        else if(command.startsWith("rfthr ")) { long v = strtol(arg.c_str(), nullptr, 16); if(v >= 0x10 && v <= 0xFF) rfThreshold.store((uint8_t)v); else Serial.println("RF thr refused: hex 10..FF"); }
        else if(command.startsWith("rfsettle ")) { long v = arg.toInt(); if(v >= 3 && v <= 20) rfSettleMs.store((uint8_t)v); else Serial.println("RF settle refused: 3..20 ms"); }
        else if(command.startsWith("rftries ")) { long v = arg.toInt(); if(v >= 1 && v <= 4) rfWakeTries.store((uint8_t)v); else Serial.println("RF tries refused: 1..4"); }
        else if(command != "rfstatus") { Serial.println("RF commands: rfpower 0|1|2, rfthr <hex>, rfsettle <3-20>, rftries <1-4>, rfstatus"); continue; }
        rfDirty.store(true);
        Serial.printf("RF TUNE power=%u thr=0x%02X settle_ms=%u wake_tries=%u\n", rfPower.load(), rfThreshold.load(), rfSettleMs.load(), rfWakeTries.load());
        continue;
      }
      int selected = command == "r1" ? 0 : command == "r2" ? 1 : -1;
      if (command != "r1" && command != "r2" && command != "both") {
        Serial.println("Bench commands: r1, r2, both, mass <grams>, calstatus (Newline)"); continue;
      }
      rfidOnly.store(selected);
      Serial.printf("RFID MODE %s: both modules wired; %s\n",
                    selected < 0 ? "BOTH" : READER_NAME[selected],
                    selected < 0 ? "alternating RF fields" : "only selected antenna enabled, continuous RF field");
    } else if (rfidCommandLength < sizeof(rfidCommand) - 1) {
      rfidCommand[rfidCommandLength++] = ch;
    } else rfidCommandOverflow = true;
  }
}
#endif
Preferences prefs;

// ---------------- Load cells ----------------
struct Cell {
  HX711 hx; char name = '?';
  long win[WINDOW] = {0}; uint8_t n = 0, idx = 0;
  long last = LONG_MIN; uint32_t lastReady = 0, lastChange = 0;
  float zero = 0, cpg = 0;   // raw counts at empty, counts per gram (sign follows wiring)
  float x = 0, y = 0;        // support position, mm
} cells[NCELL];
String calVersion = "UNSET", geomVersion = "";
bool geomSet = false;
float knownMass = 500;

void usbState(){
  JsonDocument d;d["device_id"]="sensor-1";d["boot_id"]=bus.boot;d["monotonic_ms"]=millis();
  d["calibration_version"]=calVersion;d["known_mass_g"]=knownMass;d["queued"]=bus.queuedFiles;d["capacity"]=bus.QUEUE_CAPACITY;
  Serial.print("SF_STATE ");serializeJson(d,Serial);Serial.println();
}
void reportExistingCalibration(){
  JsonDocument d;bus.envelope(d,"CALIBRATION",TRAY_ID,calVersion.c_str());
  d["calibration"]["method"]="COMMAND";d["calibration"]["known_mass_g"]=knownMass;
  auto arr=d["calibration"]["cells"].to<JsonArray>();
  for(auto& c:cells){auto value=arr.add<JsonObject>();value["name"]=String(c.name);value["zero_raw"]=c.zero;value["counts_per_g"]=c.cpg;}
  d["evidence_refs"].add("EXISTING_CALIBRATION_REPORT_NO_NVS_CHANGE");
  if(!bus.persist(d))Serial.println("SF_ERROR calibration_report_not_saved");
}

struct Snap {
  bool ready = false, calibrated = false, fault = false;
  long raw[NCELL] = {0}; float g[NCELL] = {0}; float total = 0, noise = 0;
  String flags;
};
long medianRaw(const Cell& c) {
  long s[WINDOW]; for (int i = 0; i < c.n; i++) s[i] = c.win[i];
  for (int i = 0; i < c.n; i++) for (int j = i + 1; j < c.n; j++) if (s[j] < s[i]) { long t = s[i]; s[i] = s[j]; s[j] = t; }
  return c.n ? s[c.n / 2] : 0;
}
float stdRaw(const Cell& c) {
  if (c.n < 2) return 1e9; double m = 0; for (int i = 0; i < c.n; i++) m += c.win[i]; m /= c.n;
  double v = 0; for (int i = 0; i < c.n; i++) v += (c.win[i] - m) * (c.win[i] - m); return sqrt(v / c.n);
}
float gramsOf(int i, long raw) { return cells[i].cpg ? (raw - cells[i].zero) / cells[i].cpg : 0; }
void addFlag(String& flags, const String& f) { flags += (flags.length() ? "," : "") + f; }

void sampleCells() {
  uint32_t now = millis();
  for (auto& c : cells) {
    if (!c.hx.is_ready()) continue;
    long raw = c.hx.read();
    c.lastReady = now;
    if (raw != c.last) { c.lastChange = now; c.last = raw; }
    c.win[c.idx] = raw; c.idx = (c.idx + 1) % WINDOW; if (c.n < WINDOW) c.n++;
  }
}
Snap snapshot() {
  Snap s; uint32_t now = millis(); s.ready = true; s.calibrated = true; double var = 0;
  for (int i = 0; i < NCELL; i++) {
    Cell& c = cells[i];
    s.raw[i] = medianRaw(c);
    if (c.n < WINDOW) s.ready = false;
    if (!c.cpg) s.calibrated = false;
    if (now - c.lastReady > 1000) { addFlag(s.flags, String("DISCONNECTED_") + c.name); s.fault = true; }
    else if (now - c.lastChange > 10000) { addFlag(s.flags, String("STUCK_") + c.name); s.fault = true; }
    if (labs(c.last) >= SATURATION_RAW) { addFlag(s.flags, String("SATURATED_") + c.name); s.fault = true; }
    if (c.cpg) {
      s.g[i] = gramsOf(i, s.raw[i]); s.total += s.g[i];
      float sd = stdRaw(c) / fabs(c.cpg); var += sd * sd;
      if (fabs(s.g[i]) > CELL_OVERLOAD_G) { addFlag(s.flags, String("OVERLOAD_") + c.name); s.fault = true; }
    }
  }
  if (!s.calibrated) addFlag(s.flags, "UNCALIBRATED");
  s.noise = s.calibrated ? sqrt(var) : 0;
  return s;
}

// ---------------- Settled-change detection (baseline kept as RAW, never auto-tared) ----------------
long baseRaw[NCELL] = {0}; bool baselineSet = false; uint32_t stableSince = 0;
bool afterService = false;
float baseGrams(int i) { return gramsOf(i, baseRaw[i]); }

// Where the change happened: weighted average of support positions by each cell's change.
bool changePosition(const float d[NCELL], float& x, float& y) {
  if (!geomSet) return false;
  float sum = 0; for (int i = 0; i < NCELL; i++) sum += d[i];
  if (fabs(sum) < EVENT_HYSTERESIS_G) return false;
  x = 0; y = 0;
  for (int i = 0; i < NCELL; i++) {
    float w = d[i] / sum;
    if (w < -0.15f) return false;  // cells moving in opposite directions: several things changed at once
    x += w * cells[i].x; y += w * cells[i].y;
  }
  return true;
}
void emitWeight(const Snap& s, const String& extraFlags) {
  JsonDocument d; bus.envelope(d, "WEIGHT", TRAY_ID, calVersion.c_str());
  float pre = 0, delta[NCELL]; long rawSum = 0;
  JsonArray arr = d["weight"]["cells"].to<JsonArray>();
  for (int i = 0; i < NCELL; i++) {
    float b = baseGrams(i); pre += b; delta[i] = s.g[i] - b; rawSum += s.raw[i];
    JsonObject c = arr.add<JsonObject>();
    c["name"] = String(cells[i].name); c["raw"] = s.raw[i]; c["pre_g"] = b; c["post_g"] = s.g[i]; c["ok"] = true;
  }
  d["weight"]["raw"] = rawSum; d["weight"]["filtered"] = s.total; d["weight"]["pre_g"] = pre; d["weight"]["post_g"] = s.total;
  d["weight"]["noise_g"] = s.noise; d["weight"]["settled"] = true;
  float x, y;
  if (changePosition(delta, x, y)) { d["weight"]["position_mm"]["x_mm"] = x; d["weight"]["position_mm"]["y_mm"] = y; d["weight"]["geometry_version"] = geomVersion; }
  if (extraFlags.length()) { d["sensor_health"]["flags"].add(extraFlags); d["sensor_health"]["ok"] = false; }
  bus.persist(d);
  Serial.printf("DIAG WEIGHT pre=%.1f post=%.1f delta=%.1f noise=%.2f\n", pre, s.total, s.total - pre, s.noise);
}
void trackWeight(const Snap& s) {
  uint32_t now = millis();
  if (!s.ready || !s.calibrated || s.fault || s.noise >= STABLE_NOISE_G) { stableSince = 0; return; }
  if (!stableSince) { stableSince = now; return; }
  if (now - stableSince < DWELL_MS) return;
  if (!baselineSet) { for (int i = 0; i < NCELL; i++) baseRaw[i] = s.raw[i]; baselineSet = true; return; }
  float pre = 0; for (int i = 0; i < NCELL; i++) pre += baseGrams(i);
  if (fabs(s.total - pre) < EVENT_HYSTERESIS_G) { afterService = false; return; }
  emitWeight(s, afterService ? "AFTER_SERVICE" : "");
  afterService = false;
  for (int i = 0; i < NCELL; i++) baseRaw[i] = s.raw[i];
}

// ---------------- Persistent configuration ----------------
void loadConfig() {
  for (auto& c : cells) {
    String k(c.name);
    c.zero = prefs.getFloat((k + "z").c_str(), 0); c.cpg = prefs.getFloat((k + "k").c_str(), 0);
    c.x = prefs.getFloat((k + "x").c_str(), 0); c.y = prefs.getFloat((k + "y").c_str(), 0);
  }
  calVersion = prefs.getString("cal", "UNSET"); geomVersion = prefs.getString("geo", ""); geomSet = geomVersion.length() > 0;
  knownMass = prefs.getFloat("mass", 500);
}
void saveCalibration(const String& version) {
  for (auto& c : cells) { String k(c.name); prefs.putFloat((k + "z").c_str(), c.zero); prefs.putFloat((k + "k").c_str(), c.cpg); }
  prefs.putString("cal", version); calVersion = version;
}

// ---------------- 16x2 LCD on a PCF8574 backpack (minimal driver, our I2C pins only) ----------------
// Backpack bit map (common modules): P0 RS, P1 RW, P2 EN, P3 backlight, P4..P7 D4..D7.
uint8_t lcdAddr = 0; bool lcdOn = true; String lcdShown[2];
void lcdWrite4(uint8_t nibbleBits, bool rs) {
  uint8_t b = nibbleBits | (rs ? 0x01 : 0) | (lcdOn ? 0x08 : 0);
  Wire.beginTransmission(lcdAddr); Wire.write(b | 0x04); Wire.write(b); Wire.endTransmission();
  delayMicroseconds(50);
}
void lcdSend(uint8_t v, bool rs) { lcdWrite4(v & 0xF0, rs); lcdWrite4((v << 4) & 0xF0, rs); }
bool lcdBegin() {
  Wire.begin(I2C_SDA, I2C_SCL, 100000);
  for (uint8_t a : {0x27, 0x3F}) { Wire.beginTransmission(a); if (Wire.endTransmission() == 0) { lcdAddr = a; break; } }
  if (!lcdAddr) return false;
  delay(50);
  for (int i = 0; i < 3; i++) { lcdWrite4(0x30, false); delay(5); }
  lcdWrite4(0x20, false);                       // 4-bit mode
  lcdSend(0x28, false); lcdSend(0x0C, false);   // 2 lines, display on
  lcdSend(0x01, false); delay(2); lcdSend(0x06, false);
  return true;
}
void lcdLine(int row, String text) {
  if (!lcdAddr) return;
  while (text.length() < 16) text += ' ';
  text = text.substring(0, 16);
  if (text == lcdShown[row]) return;  // only rewrite changed lines: I2C writes are slow
  lcdShown[row] = text;
  lcdSend(row ? 0xC0 : 0x80, false);
  for (char ch : text) lcdSend((uint8_t)ch, true);
}

// ---------------- Buttons (debounced, short / long / both-held) ----------------
enum Press { P_NONE, P_SHORT, P_LONG };
struct Button {
  uint8_t pin; bool down = false, raw = false, longFired = false; uint32_t changed = 0, pressedAt = 0;
  explicit Button(uint8_t p) : pin(p) {}
  Press poll(uint32_t now) {
    bool r = digitalRead(pin) == LOW;
    if (r != raw) { raw = r; changed = now; }
    if (now - changed < 30 || r == down) {
      if (down && !longFired && now - pressedAt >= 1500) { longFired = true; return P_LONG; }
      return P_NONE;
    }
    down = r;
    if (down) { pressedAt = now; longFired = false; return P_NONE; }
    return (!longFired && now - pressedAt < 800) ? P_SHORT : P_NONE;
  }
} btnPage{BTN_PAGE}, btnAction{BTN_ACTION};

// ---------------- Service mode: on-shelf 3-position calibration ----------------
enum SvcStep { SVC_OFF, SVC_HOME, CAL_ZERO, CAL_A, CAL_B, CAL_C, CAL_VERIFY, CAL_DONE };
SvcStep svc = SVC_OFF; uint32_t svcActivity = 0, bothSince = 0; bool bothLatched = false;
Press pendingCalibration = P_NONE;
SvcStep pendingCalibrationStep = SVC_OFF;
uint32_t calibrationRequestedAt = 0, calibrationStillSince = 0;
float newZero[NCELL], capture[NCELL][NCELL], newCpg[NCELL]; String svcMessage;
int page = 0; constexpr int PAGES = 6;
String lastUid = "", lastReader = ""; uint32_t lastUidAt = 0;

const char* calibrationStepName() {
  const char* names[] = {"OFF", "HOME", "EMPTY", "A", "B", "C", "VERIFY", "SAVED"};
  return names[svc];
}
#ifdef RFID_DIAGNOSTIC
bool calibrationConsole(const String& command) {
  if (command == "calstatus") {
    Serial.printf("CAL step=%s mass_g=%.3f version=%s raw_sd=%.1f/%.1f/%.1f\n",
                  calibrationStepName(), knownMass, calVersion.c_str(),
                  stdRaw(cells[0]), stdRaw(cells[1]), stdRaw(cells[2]));
    return true;
  }
  if (!command.startsWith("mass ")) return false;
  if (svc != SVC_OFF && svc != SVC_HOME) {
    Serial.println("CAL mass refused: cancel active calibration with PAGE first"); return true;
  }
  const char* input = command.c_str() + 5; char* end = nullptr;
  float mass = strtof(input, &end);
  if (end == input || *end || !isfinite(mass) || mass < 10 || mass > 3000) {
    Serial.println("CAL mass refused: use mass <grams>, 10 to 3000"); return true;
  }
  if (prefs.putFloat("mass", mass) != sizeof(float)) {
    Serial.println("CAL mass save failed; previous mass retained"); return true;
  }
  knownMass = mass;
  Serial.printf("CAL known mass saved: %.3f g\n", knownMass);
  return true;
}
#endif

// Solve sum_i R[j][i] * k[i] = M for k = grams per count (Gaussian elimination, partial pivoting).
bool solveScales(const float R[NCELL][NCELL], float M, float out[NCELL], String& why) {
  double a[NCELL][NCELL + 1]; double norm = 1;
  for (int j = 0; j < NCELL; j++) {
    double rn = 0; for (int i = 0; i < NCELL; i++) { a[j][i] = R[j][i]; rn += R[j][i] * R[j][i]; }
    a[j][NCELL] = M; norm *= sqrt(rn);
    if (rn == 0) { why = "NO CHANGE"; return false; }
  }
  double det = 1;
  for (int c = 0; c < NCELL; c++) {
    int p = c; for (int r = c + 1; r < NCELL; r++) if (fabs(a[r][c]) > fabs(a[p][c])) p = r;
    if (p != c) { for (int k = 0; k <= NCELL; k++) { double t = a[c][k]; a[c][k] = a[p][k]; a[p][k] = t; } det = -det; }
    if (a[c][c] == 0) { why = "SINGULAR"; return false; }
    det *= a[c][c];
    for (int r = c + 1; r < NCELL; r++) { double f = a[r][c] / a[c][c]; for (int k = c; k <= NCELL; k++) a[r][k] -= f * a[c][k]; }
  }
  if (fabs(det) / norm < 0.05) { why = "SPREAD MASSES"; return false; }  // positions too similar
  double k[NCELL];
  for (int r = NCELL - 1; r >= 0; r--) { double s = a[r][NCELL]; for (int c = r + 1; c < NCELL; c++) s -= a[r][c] * k[c]; k[r] = s / a[r][r]; }
  for (int i = 0; i < NCELL; i++) {
    if (!isfinite(k[i]) || k[i] == 0) { why = "BAD CELL " + String(cells[i].name); return false; }
    out[i] = 1.0 / k[i];
    if (fabs(out[i]) < 10 || fabs(out[i]) > 100000) { why = "CHECK CELL " + String(cells[i].name); return false; }
  }
  return true;
}
bool stillEnough(const Snap& s) {
  if (!s.ready || s.fault) return false;
  for (auto& c : cells) if (stdRaw(c) > 400) return false;  // ~1 g on a typical 3 kg cell; tune per cell
  return true;
}
void reportCalibration(float verify) {
  JsonDocument d; bus.envelope(d, "CALIBRATION", TRAY_ID, calVersion.c_str());
  d["calibration"]["method"] = "SERVICE_MODE_3_POSITION"; d["calibration"]["known_mass_g"] = knownMass;
  d["calibration"]["verify_total_g"] = verify;
  JsonArray arr = d["calibration"]["cells"].to<JsonArray>();
  for (auto& c : cells) { JsonObject o = arr.add<JsonObject>(); o["name"] = String(c.name); o["zero_raw"] = c.zero; o["counts_per_g"] = c.cpg; }
  bus.persist(d);
}
void applyServiceAction(Press p, const Snap& s) {
  if (svc == SVC_HOME && p == P_SHORT) { svc = CAL_ZERO; return; }
  if (svc == CAL_DONE && p == P_SHORT) { svc = SVC_HOME; return; }
  if (svc >= CAL_ZERO && svc <= CAL_C && p == P_SHORT) {
    if (!stillEnough(s)) { svcMessage = "NOT STILL - WAIT"; Serial.println("CAL capture refused: wait for stable healthy readings"); return; }
    Serial.printf("CAL captured %s mass_g=%.3f raw=%ld/%ld/%ld\n",
                  calibrationStepName(), knownMass, s.raw[0], s.raw[1], s.raw[2]);
    if (svc == CAL_ZERO) for (int i = 0; i < NCELL; i++) newZero[i] = s.raw[i];
    else { int j = svc - CAL_A; for (int i = 0; i < NCELL; i++) capture[j][i] = s.raw[i] - newZero[i]; }
    svcMessage = "";
    if (svc == CAL_C) {
      String why;
      if (solveScales(capture, knownMass, newCpg, why)) svc = CAL_VERIFY; else { svcMessage = "FAIL " + why; svc = CAL_ZERO; }
    } else svc = SvcStep(svc + 1);
    return;
  }
  if (svc == CAL_VERIFY && p == P_LONG) {
    if (!stillEnough(s)) { svcMessage = "NOT STILL - WAIT"; Serial.println("CAL save refused: readings not stable/healthy"); return; }
    float verify = 0; for (int i = 0; i < NCELL; i++) verify += (s.raw[i] - newZero[i]) / newCpg[i];
    if (!isfinite(verify) || fabs(verify - knownMass) > fmaxf(2.0f, knownMass * 0.03f)) {
      svcMessage = "VERIFY MASS";
      Serial.printf("CAL save refused: measured %.3f g, expected %.3f g; keep known mass on tray\n", verify, knownMass);
      return;
    }
    for (int i = 0; i < NCELL; i++) { cells[i].zero = newZero[i]; cells[i].cpg = newCpg[i]; }
    uint32_t count = prefs.getUInt("calcount", 0) + 1; prefs.putUInt("calcount", count);
    saveCalibration("dev-" + bus.boot.substring(0, 6) + "-" + String(count));
    reportCalibration(verify);
    Serial.printf("CAL SAVED version=%s mass_g=%.3f verify_g=%.3f counts_per_g=%.6f/%.6f/%.6f\n",
                  calVersion.c_str(), knownMass, verify, cells[0].cpg, cells[1].cpg, cells[2].cpg);
    svcMessage = calVersion; svc = CAL_DONE;
  }
}
void serviceAction(Press p, const Snap& s) {
  bool capture = svc >= CAL_ZERO && svc <= CAL_C && p == P_SHORT;
  bool save = svc == CAL_VERIFY && p == P_LONG;
  if (capture || save) {
    pendingCalibration = p; pendingCalibrationStep = svc;
    calibrationRequestedAt = millis(); calibrationStillSince = 0;
    svcMessage = "WAIT HANDS OFF";
    Serial.printf("CAL %s requested at step=%s: release buttons; waiting for 1 s stable readings\n",
                  save ? "save" : "capture", calibrationStepName());
    return;
  }
  applyServiceAction(p, s);
}
void tickCalibrationCapture(const Snap& s, uint32_t now) {
  if (pendingCalibration == P_NONE) return;
  if (svc != pendingCalibrationStep) { pendingCalibration = P_NONE; return; }
  if (now - calibrationRequestedAt > 20000) {
    pendingCalibration = P_NONE; svcMessage = "UNSTABLE - RETRY";
    Serial.println("CAL request timed out: no stable hands-off window; press ACTION to retry");
    return;
  }
  if (btnPage.down || btnAction.down || !stillEnough(s)) { calibrationStillSince = 0; return; }
  if (!calibrationStillSince) { calibrationStillSince = now; return; }
  if (now - calibrationStillSince < 1000) return;
  Press action = pendingCalibration; pendingCalibration = P_NONE;
  svcMessage = ""; svcActivity = now;
  applyServiceAction(action, s);
}
void enterService() { svc = SVC_HOME; svcActivity = millis(); svcMessage = ""; Serial.println("SERVICE MODE ON"); }
void exitService() {
  pendingCalibration = P_NONE;
  svc = SVC_OFF; afterService = true; stableSince = 0;  // a change across service is reported, flagged, never hidden
  Serial.println("SERVICE MODE OFF");
}
void handleButtons(const Snap& s) {
  uint32_t now = millis();
  Press pp = btnPage.poll(now), pa = btnAction.poll(now);
  if (btnPage.down && btnAction.down) {
    if (!bothSince) bothSince = now;
    if (!bothLatched && now - bothSince >= 3000) { bothLatched = true; svc == SVC_OFF ? enterService() : exitService(); }
    return;
  }
  bothSince = 0;
  if (bothLatched) { if (!btnPage.down && !btnAction.down) bothLatched = false; return; }  // ignore the release
  if (svc == SVC_OFF) {
    if (pp == P_SHORT) page = (page + 1) % PAGES;
    if (pa == P_SHORT) { lcdOn = !lcdOn; lcdShown[0] = lcdShown[1] = ""; }  // backlight toggle only
    return;
  }
  if (pp != P_NONE || pa != P_NONE) svcActivity = now;
  if (pp == P_SHORT) { svc = SVC_HOME; svcMessage = ""; }  // cancel back to service home
  if (pa != P_NONE) serviceAction(pa, s);
  if (now - svcActivity > SERVICE_TIMEOUT_MS) exitService();
}

// ---------------- Display ----------------
String fmtG(float g) { char b[12]; snprintf(b, sizeof b, "%.1f", g); return b; }
void render(const Snap& s) {
  if (svc != SVC_OFF) {
    // A rejected capture is retried by ACTION; its old warning must not hide
    // the live readiness prompt after the load cells have settled again.
    if (svcMessage == "NOT STILL - WAIT" && stillEnough(s)) svcMessage = "";
    const char* titles[] = {"", "SERVICE ACT:cal", "EMPTY BOX  ACT", "MASS ON A  ACT", "MASS ON B  ACT", "MASS ON C  ACT", "VERIFY HOLD ACT", "SAVED ACT:back"};
    lcdLine(0, titles[svc]);
    if (svcMessage.length()) lcdLine(1, svcMessage);
    else if (svc == CAL_VERIFY) { float v = 0; for (int i = 0; i < NCELL; i++) v += (s.raw[i] - newZero[i]) / newCpg[i]; lcdLine(1, fmtG(v) + " g of " + fmtG(knownMass)); }
    else if (svc == SVC_HOME) lcdLine(1, "mass " + fmtG(knownMass) + " g");
    else lcdLine(1, stillEnough(s) ? "still - press" : "settling...");
    return;
  }
  switch (page) {
    case 0:
      lcdLine(0, s.calibrated ? String(TRAY_ID) + " " + fmtG(s.total) + "g" : String(TRAY_ID) + " UNCALIB");
      lcdLine(1, s.fault ? s.flags : (stableSince ? "STABLE +-" + fmtG(s.noise) : "SETTLING +-" + fmtG(s.noise)));
      break;
    case 1:
      lcdLine(0, "A" + fmtG(s.g[0]) + " B" + fmtG(s.g[1]));
      lcdLine(1, "C" + fmtG(s.g[2]) + (s.calibrated ? " g" : " UNCAL"));
      break;
    case 2:
      lcdLine(0, "A" + String(s.raw[0] / 1000) + "k B" + String(s.raw[1] / 1000) + "k");
      lcdLine(1, "C" + String(s.raw[2] / 1000) + "k raw");
      break;
    case 3:
      lcdLine(0, WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString() : "WiFi down");
      lcdLine(1, String("MQTT ") + (bus.mqtt.connected() ? "up" : "down") + (bus.storageOK ? "" : " NOFS"));
      break;
    case 4:
      lcdLine(0, "Last RFID tag");
      lcdLine(1, lastUid.length() ? lastReader + " " + lastUid + " " + String((millis() - lastUidAt) / 1000) + "s" : "none");
      break;
    default:
      lcdLine(0, "cal " + calVersion);
      lcdLine(1, geomSet ? "geo " + geomVersion : "geo NOT SET");
  }
}

// ---------------- Commands from the backend ----------------
void handleCommand(const String& body, uint32_t now, String& barrierId, uint32_t& barrierRequested) {
  JsonDocument d; if (deserializeJson(d, body)) return;
  String action = d["action"].as<String>();
  if (action == "FLUSH") { barrierId = d["barrier_id"].as<String>(); barrierRequested = now; return; }
  if (d["tray_id"].as<String>() != TRAY_ID) return;
  if (action == "CALIBRATE") {
    JsonArray list = d["cells"].as<JsonArray>();
    String version = d["version"].as<String>();
    if (list.isNull() || list.size() != NCELL) { Serial.println("CALIBRATE refused: 3-cell box needs per-cell zero_raw/counts_per_g"); return; }
    if (version == calVersion || !version.length()) { Serial.println("Duplicate or empty calibration version refused"); return; }
    float z[NCELL], k[NCELL]; bool seen[NCELL] = {false};
    for (JsonObject o : list) for (int i = 0; i < NCELL; i++) if (o["name"].as<String>() == String(cells[i].name)) {
      z[i] = o["zero_raw"]; k[i] = o["counts_per_g"]; seen[i] = k[i] != 0; }
    for (bool ok : seen) if (!ok) { Serial.println("CALIBRATE refused: every cell A/B/C needs a non-zero scale"); return; }
    for (int i = 0; i < NCELL; i++) { cells[i].zero = z[i]; cells[i].cpg = k[i]; }
    saveCalibration(version); Serial.println("Calibration " + version + " saved");
  } else if (action == "GEOMETRY") {
    JsonArray list = d["cells"].as<JsonArray>(); int found = 0;
    for (JsonObject o : list) for (auto& c : cells) if (o["name"].as<String>() == String(c.name)) { c.x = o["x_mm"]; c.y = o["y_mm"]; found++; }
    if (found != NCELL) { Serial.println("GEOMETRY refused: need A, B and C"); loadConfig(); return; }
    geomVersion = d["version"].as<String>(); geomSet = geomVersion.length() > 0;
    for (auto& c : cells) { String k(c.name); prefs.putFloat((k + "x").c_str(), c.x); prefs.putFloat((k + "y").c_str(), c.y); }
    prefs.putString("geo", geomVersion); Serial.println("Geometry " + geomVersion + " saved");
  } else if (action == "CONFIG") {
    float m = d["known_mass_g"] | 0.0f;
    if (m >= 10 && m <= 10000) { knownMass = m; prefs.putFloat("mass", m); Serial.println("Known mass set"); }
  }
}

// ---------------- Main ----------------
uint32_t lastHealth = 0, lastDiag = 0, lastTick = 0, lastRender = 0;
String barrierId = ""; uint32_t barrierRequested = 0;

void setup() {
  Serial.setTxBufferSize(4096);
  Serial.begin(115200);
#ifdef RFID_DIAGNOSTIC
  Serial.println("BENCH: r1, r2, both, mass <grams>, calstatus at 115200 with Newline");
#endif
  bus.begin();
  for (int i = 0; i < NCELL; i++) {
    cells[i].name = CELL_PINS[i].name;
    cells[i].hx.begin(CELL_PINS[i].dt, CELL_PINS[i].sck);
    pinMode(CELL_PINS[i].dt, INPUT);  // HX711 drives DT push-pull; input-only GPIO34 has no pull-up anyway
  }
  prefs.begin("box", false);
  loadConfig();
  pinMode(BTN_PAGE, INPUT_PULLUP); pinMode(BTN_ACTION, INPUT_PULLUP);
  // Deselect every reader before any SPI traffic, so an uninitialised one never answers on the shared bus.
  for (uint8_t ss : READER_SS) { pinMode(ss, OUTPUT); digitalWrite(ss, HIGH); }
  SPI.begin(SPI_SCK, SPI_MISO, SPI_MOSI);
  // One physical reset wire serves BOTH readers. Keep it actively HIGH after
  // resetting them together; PCD_Init must not switch this shared wire to INPUT.
  digitalWrite(RC522_RST, LOW); pinMode(RC522_RST, OUTPUT);
  delay(2); digitalWrite(RC522_RST, HIGH); delay(50);
  for (int r = 0; r < NREADER; r++) {
    readers[r].PCD_Init(READER_SS[r], MFRC522::UNUSED_PIN);
    applyRfTuning(readers[r]);                           // 48 dB receiver, driver strength, threshold
    readers[r].PCD_AntennaOff();                          // fields are switched on one reader at a time
    readerVersion[r] = readers[r].PCD_ReadRegister(MFRC522::VersionReg);
    Serial.printf("DIAG RC522 %s ss=%u version=0x%02X\n", READER_NAME[r], READER_SS[r], readers[r].PCD_ReadRegister(MFRC522::VersionReg));
  }
  // Distinct writable values verify MOSI/MISO and that chip selects are independent.
  // Restore the configured ISO14443 CRC preset, even if a bad bus read would
  // otherwise give us a corrupt "original" value to write back.
  readers[0].PCD_WriteRegister(MFRC522::ModeReg, 0x3D);
  readers[1].PCD_WriteRegister(MFRC522::ModeReg, 0x3F);
  byte echo1 = readers[0].PCD_ReadRegister(MFRC522::ModeReg);
  byte echo2 = readers[1].PCD_ReadRegister(MFRC522::ModeReg);
  Serial.printf("DIAG RFID SPI_HZ=%u reset=%d R1_mode=0x%02X(expected 0x3D) R2_mode=0x%02X(expected 0x3F) bus=%s\n",
                (unsigned)MFRC522_SPICLOCK, digitalRead(RC522_RST), echo1, echo2,
                echo1 == 0x3D && echo2 == 0x3F ? "PASS" : "FAIL");
  for (int r = 0; r < NREADER; r++) readers[r].PCD_WriteRegister(MFRC522::ModeReg, 0x3D);
  if (lcdBegin()) { lcdLine(0, "ShelfFlow sensor"); lcdLine(1, "starting..."); }
  else Serial.println("LCD not found at 0x27/0x3F: continuing without display");
  rfidObservations=xQueueCreate(64,sizeof(RfidObservation));
  if(!rfidObservations || xTaskCreatePinnedToCore(rfidScanTask,"rfid-scan",4096,nullptr,2,nullptr,0)!=pdPASS) {
    bus.lost();Serial.println("RFID_TASK_FAILED: no RFID capture available");
  }
  Serial.printf("RFID tap mode: 600ms absence to rearm; RAM=64 events; durable_queue=%u/%u\n",bus.queuedFiles,bus.QUEUE_CAPACITY);
}

void loop() {
#ifdef RFID_DIAGNOSTIC
  rfidConsole();
#endif
  bus.tick();
  uint32_t now = millis();
  sampleCells();
  if (bus.command.length()) { handleCommand(bus.command, now, barrierId, barrierRequested); bus.command = ""; }
  static Snap s; static uint32_t lastSnap = 0;
  if (now - lastSnap >= 50) { lastSnap = now; s = snapshot(); }
  handleButtons(s);
  tickCalibrationCapture(s, millis());
  if (now - lastTick >= 100) { lastTick = now; if (svc == SVC_OFF) trackWeight(s); }
  if (now - lastRender >= 250) { lastRender = now; render(s); }

  if (barrierId.length() && bus.empty() && now - barrierRequested > 1500) {
    JsonDocument d; bus.envelope(d, "BARRIER", TRAY_ID, calVersion.c_str()); d["evidence_refs"].add("BARRIER:" + barrierId); bus.persist(d); barrierId = "";
  }
  // Drain a bounded batch so weights, buttons and LCD also receive time.
  RfidObservation observation;
  for(unsigned n=0;rfidObservations && n<4 && xQueueReceive(rfidObservations,&observation,0)==pdTRUE;n++) {
    const int r=observation.reader;
    String uid(observation.uid);
    lastUid=uid;lastReader=READER_NAME[r];lastUidAt=observation.capturedAt;
    Serial.printf("DIAG RFID reader=%s uid=%s captured_ms=%lu dispatch_ms=%lu\n",
                  READER_NAME[r],observation.uid,(unsigned long)observation.capturedAt,
                  (unsigned long)(millis()-observation.capturedAt));
    JsonDocument d;bus.envelope(d,"RFID",TRAY_ID,calVersion.c_str());
    d["monotonic_ms"]=observation.capturedAt;
    d["evidence_refs"].add("RFID:"+uid);
    d["evidence_refs"].add(String("RFID_READER:")+READER_NAME[r]);
    if(!bus.persist(d)) Serial.println("RFID_PERSIST_FAILED: tap observed but not durable");
  }
  uint32_t dropped=rfidDropped.exchange(0);
  if(dropped) { for(uint32_t i=0;i<dropped;i++) bus.lost();Serial.printf("RFID_BUFFER_OVERFLOW count=%lu\n",(unsigned long)dropped); }
  if (now - lastDiag >= 1000) {
    lastDiag = now;
    Serial.printf("DIAG raw A=%ld B=%ld C=%ld total=%s noise=%.2f%s rc522=0x%02X/0x%02X scans=%lu/%lu lcd=%s svc=%d wifi=%s mqtt=%s fs=%s\n",
      s.raw[0], s.raw[1], s.raw[2], s.calibrated ? fmtG(s.total).c_str() : "UNCAL", s.noise, s.flags.length() ? (" flags=" + s.flags).c_str() : "",
      readerVersion[0], readerVersion[1],
      (unsigned long)readerReadCount[0].load(), (unsigned long)readerReadCount[1].load(), lcdAddr ? "ok" : "none", (int)svc,
      WiFi.status() == WL_CONNECTED ? "up" : "down", bus.mqtt.connected() ? "up" : "down", bus.storageOK ? "ok" : "MISSING");
    Serial.printf("DIAG RFID_QUEUE durable=%u/%u ram=%u scan_gap_max_ms=%lu/%lu historical_overflow=%lu\n",
                  bus.queuedFiles,bus.QUEUE_CAPACITY,rfidObservations?(unsigned)uxQueueMessagesWaiting(rfidObservations):0,
                  (unsigned long)rfidMaxScanGap[0].load(),(unsigned long)rfidMaxScanGap[1].load(),(unsigned long)bus.overflow);
    if (svc != SVC_OFF) {
      float verify = 0;
      if (svc == CAL_VERIFY) for (int i = 0; i < NCELL; i++) verify += (s.raw[i] - newZero[i]) / newCpg[i];
      Serial.printf("CAL step=%s mass_g=%.3f still=%s verify_g=%.3f raw_sd=%.1f/%.1f/%.1f\n",
                    calibrationStepName(), knownMass, stillEnough(s) ? "yes" : "no", verify,
                    stdRaw(cells[0]), stdRaw(cells[1]), stdRaw(cells[2]));
    }
  }
  if (now - lastHealth >= 2000) {  // best-effort live health (not flash-queued); 2 s keeps database growth modest
    lastHealth = now;
    JsonDocument d; bus.envelope(d, "HEALTH", TRAY_ID, calVersion.c_str());
    String flags = s.flags; if (svc != SVC_OFF) addFlag(flags, "SERVICE_MODE");
    if (flags.length()) { d["sensor_health"]["flags"].add(flags); d["sensor_health"]["ok"] = false; }
    long rawSum = 0; JsonArray arr = d["weight"]["cells"].to<JsonArray>();
    for (int i = 0; i < NCELL; i++) { rawSum += s.raw[i]; JsonObject c = arr.add<JsonObject>(); c["name"] = String(cells[i].name); c["raw"] = s.raw[i]; if (s.calibrated) c["post_g"] = s.g[i]; c["ok"] = millis() - cells[i].lastReady <= 1000; }
    float pre = 0; for (int i = 0; i < NCELL; i++) pre += baseGrams(i);
    d["weight"]["raw"] = rawSum; if (s.calibrated) d["weight"]["filtered"] = s.total; else d["weight"]["filtered"] = nullptr;
    d["weight"]["pre_g"] = pre; d["weight"]["post_g"] = pre; d["weight"]["noise_g"] = s.noise; d["weight"]["settled"] = false;
    String body; serializeJson(d, body); if (bus.mqtt.connected()) bus.mqtt.publish(bus.topic("events").c_str(), body.c_str());
#ifdef USB_RUNTIME
    d.remove("device_key");Serial.print("SF_HEALTH ");serializeJson(d,Serial);Serial.println();
#endif
  }
  delay(1);
}
