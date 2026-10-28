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
#include "cell_layout.h"
#include "scale_solver.h"
#include "manual_tare.h"
#include "weighing_pipeline.h"
#include "drift_check.h"
#include "hold_tracker.h"
#ifdef WEIGHING_SELF_TEST
#include "weighing_self_test.h"
#endif

// Shelf sensor controller: one tray on two 10 kg cells (A left, C right), two RC522 readers,
// a 16x2 I2C LCD and two staff service buttons.
// PROPOSED for a separate ESP32-WROOM-32 DevKit. Never the camera board.
// The buttons are staff-only maintenance controls inside the enclosure; they never
// authorise a shopper. Long normal-mode presses tare/clear the temporary reference.

// ---------------- Pin map ----------------
constexpr uint8_t SPI_SCK = 18, SPI_MISO = 19, SPI_MOSI = 23, RC522_RST = 22;  // SPI bus and RST shared
constexpr int NREADER = 2;
const uint8_t READER_SS[NREADER] = {21, 4};          // R1 left of tray front, R2 right (own chip select each)
const char* READER_NAME[NREADER] = {"R1", "R2"};
constexpr uint8_t I2C_SDA = 16, I2C_SCL = 17;      // NOT 21/22: those belong to the RC522
constexpr uint8_t BTN_PAGE = 13, BTN_ACTION = 14;  // to GND, internal pull-ups
// Active load-cell pins and calibration profile are defined in cell_layout.h.
const char* TRAY_ID = "tray-a";

// ---------------- Tuning (starting values; measure on the real platform) ----------------
// Raw-domain filter and AZT tuning. Each alpha update consumes a NEW A/C pair.
// Defaults are documented in weighing_pipeline.h. The zero of these cells wanders with
// temperature (measured: +-1 g around a trend that reverses minute to minute, up to
// ~4 g/min), so it cannot be predicted. Instead the step-hold tracker (hold_tracker.h)
// re-tares to the last known tray weight every 2 s while settled and moves that weight
// only on a settled step of at least 3 g (an item placed or picked up). The pipeline's
// own empty-tray zero tracking is therefore off: the tracker does it for any load.
WeighingConfig makeScaleConfig() {
  WeighingConfig config;
  config.autoZeroEnabled = false;
  return config;
}
const WeighingConfig SCALE_CONFIG = makeScaleConfig();
constexpr uint32_t SCALE_DEBUG_INTERVAL_MS = 1000;
bool scaleDebugEnabled = true;
bool weighingSelfTestOK = true;
constexpr int WINDOW = 10;              // samples per cell (~1 s at 10 SPS)
constexpr float STABLE_NOISE_G = 2.0;   // total noise below this counts as settled
constexpr float EVENT_HYSTERESIS_G = 3.0;   // items weigh up to ~15 g; noise ~0.5 g
constexpr uint32_t DWELL_MS = 600;
HoldTracker<NCELL>::Config makeHoldConfig() {
  HoldTracker<NCELL>::Config config;
  config.anchorMs = 2000; config.settleMs = DWELL_MS; config.stepG = EVENT_HYSTERESIS_G; config.zeroSnapG = 1.5;
  return config;
}
HoldTracker<NCELL> hold(makeHoldConfig());
constexpr uint32_t SERVICE_TIMEOUT_MS = 120000;
constexpr float CELL_OVERLOAD_G = 9000;  // 90% of the 10 kg cell rating
constexpr long SATURATION_RAW = 8388000;
constexpr long GLITCH_COUNTS = 60000;   // ~240 g on one cell between two 100 ms reads
// Quick calibration before demos: empty, mass over A, over C, at the centre, empty.
constexpr uint32_t QC_WINDOW_MS = 5000, QC_STILL_MS = 1000, QC_STEP_TIMEOUT_MS = 30000;
constexpr double QC_MOVE_COUNTS = 1500;  // ~6 g summed over the cells: the mass was moved

DeviceBus bus("sensor-1");
MFRC522 readers[NREADER] = {MFRC522(READER_SS[0], RC522_RST), MFRC522(READER_SS[1], RC522_RST)};
struct RfidObservation { uint8_t reader; char uid[21]; uint32_t capturedAt; };
QueueHandle_t rfidObservations = nullptr;
std::atomic<int> rfidOnly{-1};
std::atomic<uint32_t> readerReadCount[NREADER]{};
std::atomic<uint32_t> rfidDropped{0}, rfidMaxScanGap[NREADER]{};
uint8_t readerVersion[NREADER]{};
// RFID self-healing (the scan once froze for minutes and only a controller reset brought it back).
//   In the scan task: a reader whose version register stops answering, or that has heard no tag for
//   RF_IDLE_REINIT_MS, gets both readers reset and re-initialised (rfidReinits).
//   In the main loop: the task stamps rfidHeartbeat every pass; silent for RFID_STALL_MS, the task is
//   deleted and started again (it resets the readers first, rfidRestarts). If that does not bring it
//   back within RFID_RESTART_GRACE_MS (e.g. the old task died holding the SPI bus), the controller
//   restarts; queued events survive on flash.
constexpr uint32_t RF_HEALTH_CHECK_MS = 10000, RF_IDLE_REINIT_MS = 120000;
constexpr uint32_t RFID_STALL_MS = 5000, RFID_RESTART_GRACE_MS = 10000;
std::atomic<uint32_t> rfidHeartbeat{0}, rfidRestarts{0}, rfidReinits{0};
TaskHandle_t rfidTask = nullptr;

// RF tuning for range and responsiveness. Only the scan task touches reader registers after
// setup; bench commands only change these atomics and raise the dirty flag.
//   power 0: chip defaults.      1: carrier (CW) drive maximum, modulation untouched.
//   power 2: carrier AND modulation drive maximum (strongest field; measured best on this shelf, see reports/rfid-range-*.json).
// threshold: RxThresholdReg (default 0x84). Lower MinLevel = more sensitive receiver.
// settle_ms: time the field runs before polling, so the tag can power up (ISO 14443 wants >= 5 ms).
//   10 ms: with alternating fields a tag on the weaker reader (longer cable, weaker 3.3 V) did not
//   power up within 6 ms and that reader never read in BOTH mode, while it read fine alone.
// wake_tries: WUPA attempts per turn: a marginal tag often answers the second or third one.
constexpr uint8_t RF_POWER_DEFAULT = 2, RF_THRESHOLD_DEFAULT = 0x84, RF_SETTLE_DEFAULT_MS = 10, RF_WAKE_TRIES_DEFAULT = 3;
// A reader that heard a tag (ATQA) but failed to read its UID keeps its field on and retries
// this many times before the scan moves to the other reader.
constexpr uint8_t RF_READ_RETRIES = 3;
// Per-reader diagnostics: turns that got an answer, successful UID reads, answers whose read failed.
std::atomic<uint32_t> readerAnswered[NREADER]{}, readerReadFailed[NREADER]{};
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

// Hardware reset (one shared RST wire) and full re-initialisation of both readers. Scan task only.
void resetReaders() {
  for (uint8_t ss : READER_SS) digitalWrite(ss, HIGH);
  digitalWrite(RC522_RST, LOW); vTaskDelay(pdMS_TO_TICKS(2)); digitalWrite(RC522_RST, HIGH); vTaskDelay(pdMS_TO_TICKS(50));
  for (int r = 0; r < NREADER; r++) {
    readers[r].PCD_Init(READER_SS[r], MFRC522::UNUSED_PIN);
    applyRfTuning(readers[r]);
    readers[r].PCD_AntennaOff();
    readerVersion[r] = readers[r].PCD_ReadRegister(MFRC522::VersionReg);
  }
  rfidReinits++;
}

// Only this task touches SPI/readers after setup. Flash writes, LCD and network
// reconnects no longer schedule RF polling. Retain brief taps in RAM until
// the main loop can persist them. Fields alternate to avoid mutual interference.
void rfidScanTask(void*) {
  RfidTap taps[NREADER];
  uint32_t lastScan[NREADER]{};
  int next = 0;
  if (rfidRestarts.load()) resetReaders();  // restarted by the watchdog: start from clean readers
  uint32_t lastHeard[NREADER], lastHealth = millis();
  for (auto& t : lastHeard) t = millis();
  for(;;) {
    rfidHeartbeat.store(millis());
    if (millis() - lastHealth >= RF_HEALTH_CHECK_MS) {
      lastHealth = millis();
      bool reset = false;
      for (int o = 0; o < NREADER; o++) {
        const uint8_t v = readers[o].PCD_ReadRegister(MFRC522::VersionReg);
        reset |= v == 0x00 || v == 0xFF || millis() - lastHeard[o] >= RF_IDLE_REINIT_MS;
      }
      if (reset) { resetReaders(); for (auto& t : lastHeard) t = millis(); }
    }
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
    bool answered = wake==MFRC522::STATUS_OK || wake==MFRC522::STATUS_COLLISION;
    bool read = answered && reader.PICC_ReadCardSerial();
    if(answered) { readerAnswered[r]++; lastHeard[r] = millis(); }
    for(uint8_t t=0; answered && !read && t<RF_READ_RETRIES; t++) {
      // The tag is in the field but SELECT failed (edge of range, noise): retry while it is powered.
      readerReadFailed[r]++;
      reader.PCD_StopCrypto1();
      size=sizeof(atqa);
      wake = reader.PICC_WakeupA(atqa,&size);
      answered = wake==MFRC522::STATUS_OK || wake==MFRC522::STATUS_COLLISION;
      read = answered && reader.PICC_ReadCardSerial();
    }
    if(read) {
      RfidObservation observation{};
      observation.reader=r;observation.capturedAt=millis();
      for(byte i=0;i<reader.uid.size;i++) snprintf(observation.uid+2*i,3,"%02X",reader.uid.uidByte[i]);
      readerReadCount[r]++;
      if(taps[r].observe(observation.uid,observation.capturedAt) &&
         xQueueSend(rfidObservations,&observation,0)!=pdTRUE) rfidDropped++;
      reader.PICC_HaltA();
    } else if(answered) {
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
void requestManualTare();
void clearManualTare();
void requestEmptyTare();
bool weighingConsole(const String& command);
// Guidance from the laptop (tray loading): two lines shown on the LCD until guideUntil.
// '#' in a line is replaced by the live tray weight, so "on tray #" stays real time.
String guideLine[2];
uint32_t guideUntil = 0;
void showGuide(String raw) {
  // "lcd <seconds> <line 1>|<line 2>" or "lcd off". Text keeps its case; only printable ASCII.
  raw.trim();
  String rest = raw.substring(3); rest.trim();
  if (rest.equalsIgnoreCase("off")) { guideUntil = 0; Serial.println("LCD GUIDE off"); return; }
  int space = rest.indexOf(' ');
  long seconds = space > 0 ? rest.substring(0, space).toInt() : 0;
  if (seconds < 1 || seconds > 600) { Serial.println("LCD GUIDE refused: lcd <1-600 s> <line1>|<line2>"); return; }
  String text = rest.substring(space + 1);
  int bar = text.indexOf('|');
  String lines[2] = {bar >= 0 ? text.substring(0, bar) : text, bar >= 0 ? text.substring(bar + 1) : ""};
  for (int r = 0; r < 2; r++) {
    String clean;
    for (char ch : lines[r]) if (ch >= 0x20 && ch <= 0x7E && clean.length() < 16) clean += ch;
    guideLine[r] = clean;
  }
  guideUntil = millis() + (uint32_t)seconds * 1000;
  if (!guideUntil) guideUntil = 1;
  Serial.printf("LCD GUIDE %lds\n", seconds);
}
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
      if (calibrationConsole(command) || weighingConsole(command)) continue;
#ifdef USB_RUNTIME
      if(command=="usbstatus"){usbState();continue;}
      if(command=="next"){bus.usbNext();continue;}
      if(command=="calreport"){reportExistingCalibration();continue;}
      if(command=="tare"){requestManualTare();continue;}
      if(command=="tareempty"){requestEmptyTare();continue;}
      if(command=="untare"){clearManualTare();continue;}
      if(command.startsWith("lcd")){showGuide(String(rfidCommand));continue;}
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
  double gramsPerCount = 0; bool newRaw = false;
  long pending = LONG_MIN; uint32_t glitches = 0;  // outlier held until the next read confirms it
  float zero = 0, cpg = 0;   // raw counts at empty, counts per gram (sign follows wiring)
  float x = 0, y = 0;        // support position, mm
} cells[NCELL];
String calVersion = "UNSET", geomVersion = "";
bool geomSet = false;
float knownMass = 500;
ManualTare<NCELL> manualTare;
WeighingPipeline<NCELL> weighing(SCALE_CONFIG);
bool tareEmptyRequested = false;
String tareMessage;
uint32_t tareMessageUntil = 0;
void tareFeedback(const String& message) { tareMessage = message; tareMessageUntil = millis() + 2500; }


void appendCalibrationState(JsonDocument& d);
void appendQuickCheckState(JsonDocument& d);
bool qcActive();
void qcStart(); void qcNext(); void qcCancel(const char* why); void qcReset();
void usbState(){
  JsonDocument d;d["device_id"]="sensor-1";d["boot_id"]=bus.boot;d["monotonic_ms"]=millis();
  d["calibration_version"]=calVersion;d["known_mass_g"]=knownMass;d["queued"]=bus.queuedFiles;d["capacity"]=bus.QUEUE_CAPACITY;
  for (const auto& pins : CELL_PINS) d["active_cells"].add(String(pins.name));
  for (int i = 0; i < NCELL; ++i) if (weighing.included(i)) d["calculation_cells"].add(String(cells[i].name));
  for (const auto& c : cells) d["glitches"][String(c.name)] = c.glitches;
  appendQuickCheckState(d);
  d["tare_active"] = manualTare.active(); d["tare_pending"] = manualTare.pending();
  d["tare_offset_g"] = manualTare.totalOffset(); d["tare_count"] = manualTare.count();
  d["azt_empty_confirmed"] = weighing.confirmedEmpty();
  d["azt_tracking"] = weighing.value().zeroTracking;
  d["azt_correction_g"] = weighing.value().zeroCorrectionG;
  d["azt_correction_abs_g"] = weighing.value().zeroCorrectionAbsG;
  d["ema_alpha"] = SCALE_CONFIG.emaAlpha;
  d["held_g"] = hold.heldTotal();
  appendCalibrationState(d);
  Serial.print("SF_STATE ");serializeJson(d,Serial);Serial.println();
}
void reportExistingCalibration(){
  if (calVersion == "UNSET") { Serial.println("SF_ERROR calibration_required"); return; }
  JsonDocument d;bus.envelope(d,"CALIBRATION",TRAY_ID,calVersion.c_str());
  d["calibration"]["method"]="COMMAND";d["calibration"]["known_mass_g"]=knownMass;
  auto arr=d["calibration"]["cells"].to<JsonArray>();
  for(auto& c:cells){auto value=arr.add<JsonObject>();value["name"]=String(c.name);value["zero_raw"]=c.zero;value["counts_per_g"]=c.cpg;}
  d["evidence_refs"].add("EXISTING_CALIBRATION_REPORT_NO_NVS_CHANGE");
  if(!bus.persist(d))Serial.println("SF_ERROR calibration_report_not_saved");
}

struct Snap {
  bool ready = false, calibrated = false, fault = false;
  long raw[NCELL] = {0}, calibrationRaw[NCELL] = {0};
  // measured: fixed-calibration grams (drift included); g/total: drift-corrected by the hold tracker.
  double filteredRaw[NCELL] = {0}, measured[NCELL] = {0}, g[NCELL] = {0}, total = 0, displayTotal = 0;
  float noise = 0;
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
String noiseDiagnostic() {
  String text;
  for (auto& c : cells) { if (text.length()) text += " "; text += String(c.name) + "=" + String(stdRaw(c), 1); }
  return text;
}
String rawDiagnostic(const Snap& s) {
  String text;
  for (int i = 0; i < NCELL; ++i) { if (i) text += " "; text += String(cells[i].name) + "=" + String(s.raw[i]); }
  return text;
}
String scaleDiagnostic() {
  String text;
  for (auto& c : cells) { if (text.length()) text += " "; text += String(c.name) + "=" + String(c.cpg, 6); }
  return text;
}
// Stable multiplier. cpg is retained only for backwards-compatible NVS/wire reports.
// Its reciprocal is calculated once when calibration is loaded/saved, never by tare/AZT.
double absoluteGramsOf(int i, double raw) { return (raw - cells[i].zero) * cells[i].gramsPerCount; }
double gramsOf(int i, double raw) { return weighing.netAt(i, raw); }
void refreshWeighingCalibration() {
  WeighingPipeline<NCELL>::Calibration values[NCELL];
  manualTare.clear();
  for (int i = 0; i < NCELL; ++i) {
    cells[i].gramsPerCount = isfinite(cells[i].cpg) && cells[i].cpg != 0 ? 1.0 / double(cells[i].cpg) : 0;
    values[i] = {cells[i].zero, cells[i].gramsPerCount};
    cells[i].newRaw = false;
  }
  if (!weighingSelfTestOK) for (auto& value : values) value.gramsPerCount = 0;
  weighing.configure(values);
  hold.reset();  // a new calibration starts from the empty tray
}
bool weighingConsole(const String& command) {
  if (command == "qc start") { qcStart(); return true; }
  if (command == "qc next") { qcNext(); return true; }
  if (command == "qc cancel") { qcCancel("Cancelled"); return true; }
#ifdef WEIGHING_SELF_TEST
  if (command == "scale selftest") {
    const int failure = weighing_self_test::run();
    Serial.printf("WEIGH_SELFTEST %s line=%d\n", failure == 0 ? "PASS" : "FAIL", failure);
    return true;
  }
#endif
  if (command == "scale debug on") { scaleDebugEnabled = true; Serial.println("SCALE_DEBUG enabled"); return true; }
  if (command == "scale debug off") { scaleDebugEnabled = false; Serial.println("SCALE_DEBUG disabled"); return true; }
  return false;
}
void addFlag(String& flags, const String& f) { flags += (flags.length() ? "," : "") + f; }

void sampleCells() {
  uint32_t now = millis();
  for (auto& c : cells) {
    if (!c.hx.is_ready()) continue;
    long raw = c.hx.read();
    c.lastReady = now;
    // A read far from the last good one is held back: if the next read is back near the
    // old value it was a corrupted HX711 transfer (all-0/all-1 bits), not load, and is
    // dropped. A real load step is confirmed by the next read and accepted 100 ms late.
    if (c.last != LONG_MIN && labs(raw - c.last) > GLITCH_COUNTS &&
        (c.pending == LONG_MIN || labs(raw - c.pending) > GLITCH_COUNTS)) {
      if (c.pending != LONG_MIN) c.glitches++;
      c.pending = raw; continue;
    }
    if (c.pending != LONG_MIN && c.last != LONG_MIN && labs(raw - c.last) <= GLITCH_COUNTS) c.glitches++;
    c.pending = LONG_MIN; c.newRaw = true;
    if (raw != c.last) { c.lastChange = now; c.last = raw; }
    c.win[c.idx] = raw; c.idx = (c.idx + 1) % WINDOW; if (c.n < WINDOW) c.n++;
  }
}
// Acquisition above only reads ADC integers. This stage consumes a fresh complete
// pair once, combines/filters raw counts, then converts to grams using fixed gains.
void qcSample(const int32_t (&raw)[NCELL], uint32_t now);
void processWeighingSample(bool allowTracking) {
  for (int i = 0; i < NCELL; ++i) if (weighing.included(i) && !cells[i].newRaw) return;
  int32_t raw[NCELL]; bool healthy = true; double noiseVariance = 0;
  for (int i = 0; i < NCELL; ++i) {
    auto& c = cells[i]; raw[i] = c.last == LONG_MIN ? 0 : c.last; c.newRaw = false;
    if (!weighing.included(i)) continue;
    healthy = healthy && c.n >= WINDOW && millis() - c.lastReady <= SCALE_CONFIG.staleMs &&
              labs(c.last) < SATURATION_RAW;
    double sd = stdRaw(c) * fabs(c.gramsPerCount); noiseVariance += sd * sd;
  }
  weighing.push(raw, millis(), healthy, sqrt(noiseVariance), allowTracking && !manualTare.pending() && !qcActive());
  qcSample(raw, millis());
}
Snap snapshot() {
  Snap s; uint32_t now = millis(); s.ready = true; s.calibrated = true; double var = 0;
  for (int i = 0; i < NCELL; i++) {
    Cell& c = cells[i];
    s.raw[i] = c.last == LONG_MIN ? 0 : c.last; // Actual ADC sample, never a scaled/reused value.
    s.calibrationRaw[i] = medianRaw(c);          // Separate robust capture for calibration only.
    s.filteredRaw[i] = weighing.value().filteredRaw[i];
    bool included = weighing.included(i);
    if (included && c.n < WINDOW) s.ready = false;
    if (!weighing.hasCalibration()) s.calibrated = false;
    if (included && now - c.lastReady > 1000) { addFlag(s.flags, String("DISCONNECTED_") + c.name); s.fault = true; }
    // Identical ADC codes can be valid. Fresh DOUT conversions, not noise, prove liveness.
    if (included && labs(c.last) >= SATURATION_RAW) { addFlag(s.flags, String("SATURATED_") + c.name); s.fault = true; }
    if (c.cpg && included) {
      s.measured[i] = gramsOf(i, s.filteredRaw[i]);
      s.g[i] = hold.shown(i, s.measured[i]); s.total += s.g[i];
      float sd = stdRaw(c) * fabs(c.gramsPerCount); var += sd * sd;
      if (fabs(absoluteGramsOf(i, s.raw[i])) > CELL_OVERLOAD_G) { addFlag(s.flags, String("OVERLOAD_") + c.name); s.fault = true; }
    }
  }
  if (s.calibrated && !weighing.fresh(now)) s.ready = false;
  // Within a step of the held weight the reading is drift or jitter: show the held whole
  // grams. A bigger difference is a hand or item on the move: show it live.
  s.displayTotal = hold.seededState() && fabs(s.total - hold.heldTotal()) < EVENT_HYSTERESIS_G ? hold.heldTotal() : s.total;
  if (!s.calibrated) addFlag(s.flags, "UNCALIBRATED");
  if (!s.ready && s.calibrated) addFlag(s.flags, "NOT_READY");
  s.noise = s.calibrated ? sqrt(var) : 0;
  return s;
}

// ---------------- Settled-change detection: step-hold weighing ----------------
// Events come from the hold tracker: one WEIGHT event per settled step (item placed or
// picked up), with the held weight before and after. Drift between steps is re-tared away.
uint32_t stableSince = 0;
bool afterService = false;

// Where the change happened: weighted average of support positions by each cell's change.
bool changePosition(const float d[NCELL], float& x, float& y) {
  if (NCELL < 3 || !geomSet) return false;
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
void appendTareEvidence(JsonDocument& d) {
  if (weighing.value().zeroCorrectionAbsG > 0) {
    d["evidence_refs"].add("AZT_CORRECTION_G:" + String(weighing.value().zeroCorrectionG, 6));
    d["evidence_refs"].add("AZT_CORRECTION_ABS_G:" + String(weighing.value().zeroCorrectionAbsG, 6));
  }
  if (!manualTare.active()) return;
  d["evidence_refs"].add("TARE_REF:" + bus.boot + ":" + String(manualTare.count()));
  d["evidence_refs"].add("TARE_OFFSET_G:" + String(manualTare.totalOffset(), 6));
}
void emitWeight(const Snap& s, const HoldTracker<NCELL>::Step& step, const String& extraFlags) {
  JsonDocument d; bus.envelope(d, "WEIGHT", TRAY_ID, calVersion.c_str());
  appendTareEvidence(d);
  d["evidence_refs"].add("STEP_HOLD:retare_2s");
  float delta[NCELL]; long rawSum = 0;
  JsonArray arr = d["weight"]["cells"].to<JsonArray>();
  for (int i = 0; i < NCELL; i++) {
    delta[i] = step.postCell[i] - step.preCell[i]; rawSum += s.raw[i];
    JsonObject c = arr.add<JsonObject>();
    c["name"] = String(cells[i].name); c["raw"] = s.raw[i]; c["pre_g"] = step.preCell[i]; c["post_g"] = step.postCell[i]; c["ok"] = true;
  }
  const double pre = step.pre;
  d["weight"]["raw"] = rawSum; d["weight"]["filtered"] = step.post; d["weight"]["pre_g"] = pre; d["weight"]["post_g"] = step.post;
  d["weight"]["noise_g"] = s.noise; d["weight"]["settled"] = true;
  float x, y;
  if (changePosition(delta, x, y)) { d["weight"]["position_mm"]["x_mm"] = x; d["weight"]["position_mm"]["y_mm"] = y; d["weight"]["geometry_version"] = geomVersion; }
  if (extraFlags.length()) { d["sensor_health"]["flags"].add(extraFlags); d["sensor_health"]["ok"] = false; }
  bus.persist(d);
  Serial.printf("DIAG WEIGHT pre=%.1f post=%.1f delta=%.1f noise=%.2f\n", pre, step.post, step.post - pre, s.noise);
}
void trackWeight(const Snap& s) {
  const uint32_t now = millis();
  const bool settled = !manualTare.pending() && s.ready && s.calibrated && !s.fault &&
                       weighing.settled() && s.noise < STABLE_NOISE_G;
  stableSince = settled ? (stableSince ? stableSince : now) : 0;  // LCD "STABLE" indicator
  if (!hold.update(s.measured, settled, now)) return;
  emitWeight(s, hold.lastStep(), afterService ? "AFTER_SERVICE" : "");
  afterService = false;
}

// ---------------- Persistent configuration ----------------
void loadConfig() {
  manualTare.clear();
  for (auto& c : cells) {
    String k = String(CAL_KEY_PREFIX) + c.name;
    c.zero = prefs.getFloat((k + "z").c_str(), 0); c.cpg = prefs.getFloat((k + "k").c_str(), 0);
    String geometryKey(c.name);
    c.x = prefs.getFloat((geometryKey + "x").c_str(), 0); c.y = prefs.getFloat((geometryKey + "y").c_str(), 0);
  }
  calVersion = prefs.getString(CAL_VERSION_KEY, "UNSET"); geomVersion = prefs.getString("geo", ""); geomSet = NCELL == 3 && geomVersion.length() > 0;
  knownMass = prefs.getFloat("mass", 500);
  refreshWeighingCalibration();
}
void saveCalibration(const String& version) {
  for (auto& c : cells) { String k = String(CAL_KEY_PREFIX) + c.name; prefs.putFloat((k + "z").c_str(), c.zero); prefs.putFloat((k + "k").c_str(), c.cpg); }
  prefs.putString(CAL_VERSION_KEY, version); calVersion = version;
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

// ---------------- Service mode: on-shelf 2-position calibration ----------------
SvcStep svc = SVC_OFF; uint32_t svcActivity = 0, bothSince = 0; bool bothLatched = false;
Press pendingCalibration = P_NONE;
SvcStep pendingCalibrationStep = SVC_OFF;
uint32_t calibrationRequestedAt = 0, calibrationStillSince = 0;
float newZero[NCELL], capture[NCELL][NCELL], newCpg[NCELL];
double newGramsPerCount[NCELL] = {}; String svcMessage;
int page = 0; constexpr int PAGES = 6;
String lastUid = "", lastReader = ""; uint32_t lastUidAt = 0;

const char* calibrationStepName() {
  const char* names[] = {"OFF", "HOME", "EMPTY", "A", "C", "VERIFY", "SAVED"};
  return names[svc];
}
// Calibration readiness is based on ADC health, never an obsolete gram scale.
bool calibrationStillEnough() {
  for (const auto& c : cells)
    if (c.n < WINDOW || millis() - c.lastReady > 1000 ||
        labs(c.last) >= SATURATION_RAW || stdRaw(c) > 400) return false;
  return true;
}
void appendCalibrationState(JsonDocument& d) {
  auto state = d["calibration_ui"].to<JsonObject>();
  state["step"] = calibrationStepName();
  state["pending"] = pendingCalibration != P_NONE;
  state["message"] = svcMessage;
  state["still"] = calibrationStillEnough();
  if (svc == CAL_VERIFY) {
    double total = 0;
    for (int i = 0; i < NCELL; ++i)
      total += (medianRaw(cells[i]) - newZero[i]) * newGramsPerCount[i];
    state["verify_g"] = total;
    state["verify_tolerance_g"] = fmaxf(2.0f, knownMass * 0.03f);
  }
}
void enterService();
void exitService();
void qcTick(uint32_t now);
void serviceAction(Press p, const Snap& s);
#ifdef RFID_DIAGNOSTIC
bool calibrationConsole(const String& command) {
  if (command.startsWith("cal ")) {
    if (command == "cal cancel") { exitService(); svcMessage = "Cancelled; saved calibration kept"; return true; }
    if (pendingCalibration != P_NONE || manualTare.pending()) {
      Serial.println("CAL refused: a capture is already pending"); return true;
    }
    if (command == "cal empty") {
      if (svc != SVC_OFF && svc != SVC_HOME && svc != CAL_DONE) {
        Serial.println("CAL refused: cancel previous calibration first"); return true;
      }
      if (prefs.putFloat("mass", 44.0f) != sizeof(float)) {
        Serial.println("CAL refused: cannot save known mass"); return true;
      }
      knownMass = 44.0f; enterService(); svc = CAL_ZERO;
      serviceAction(P_SHORT, snapshot()); return true;
    }
    bool captureRequested = (command == "cal a" && svc == CAL_A) ||
                            (command == "cal c" && svc == CAL_C);
    if (captureRequested || (command == "cal save" && svc == CAL_VERIFY)) {
      svcActivity = millis();
      serviceAction(captureRequested ? P_SHORT : P_LONG, snapshot()); return true;
    }
    Serial.println("CAL refused: wrong step (empty, a, c, save, cancel)"); return true;
  }
  if (command == "calstatus") {
    Serial.printf("CAL step=%s mass_g=%.3f version=%s raw_sd=%s\n",
                  calibrationStepName(), knownMass, calVersion.c_str(),
                  noiseDiagnostic().c_str());
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

// Calibrate only the active supports; each captured position is one matrix row.
bool solveScales(const float (&R)[NCELL][NCELL], float M, float (&out)[NCELL], String& why) {
  const char* message = "";
  bool ok = solveCalibration<NCELL>(R, M, out, message);
  why = message;
  return ok;
}
bool stillEnough(const Snap& s) {
  if (!s.ready || s.fault) return false;
  for (int i = 0; i < NCELL; ++i) if (weighing.included(i) && stdRaw(cells[i]) > 400) return false;
  return true;
}
void reportCalibration(float verify) {
  JsonDocument d; bus.envelope(d, "CALIBRATION", TRAY_ID, calVersion.c_str());
  d["calibration"]["method"] = CAL_METHOD; d["calibration"]["known_mass_g"] = knownMass;
  d["calibration"]["verify_total_g"] = verify;
  JsonArray arr = d["calibration"]["cells"].to<JsonArray>();
  for (auto& c : cells) { JsonObject o = arr.add<JsonObject>(); o["name"] = String(c.name); o["zero_raw"] = c.zero; o["counts_per_g"] = c.cpg; }
  bus.persist(d);
}
void applyServiceAction(Press p, const Snap& s) {
  if (svc == SVC_HOME && p == P_SHORT) { svc = CAL_ZERO; return; }
  if (svc == CAL_DONE && p == P_SHORT) { svc = SVC_HOME; return; }
  if (svc >= CAL_ZERO && svc <= CAL_C && p == P_SHORT) {
    if (!calibrationStillEnough()) { svcMessage = "NOT STILL - WAIT"; Serial.println("CAL capture refused: wait for stable healthy readings"); return; }
    String captured;
    for (int i = 0; i < NCELL; ++i) captured += String(cells[i].name) + "=" + String(s.calibrationRaw[i]) + " ";
    Serial.printf("CAL captured %s mass_g=%.3f median_counts=%s\n",
                  calibrationStepName(), knownMass, captured.c_str());
    if (svc == CAL_ZERO) for (int i = 0; i < NCELL; i++) newZero[i] = s.calibrationRaw[i];
    else { int j = calibrationRow(svc); if (j < 0 || j >= NCELL) { svc = CAL_ZERO; svcMessage = "BAD CAL STEP"; return; }
      for (int i = 0; i < NCELL; i++) capture[j][i] = s.calibrationRaw[i] - newZero[i]; }
    svcMessage = "";
    if (svc == CAL_C) {
      String why;
      if (solveScales(capture, knownMass, newCpg, why)) {
        for (int i = 0; i < NCELL; ++i) newGramsPerCount[i] = 1.0 / double(newCpg[i]);
        svc = CAL_VERIFY;
      } else { svcMessage = "FAIL " + why; svc = CAL_ZERO; }
    } else svc = nextCaptureStep(svc);
    return;
  }
  if (svc == CAL_VERIFY && p == P_LONG) {
    if (!calibrationStillEnough()) { svcMessage = "NOT STILL - WAIT"; Serial.println("CAL save refused: readings not stable/healthy"); return; }
    float verify = 0; for (int i = 0; i < NCELL; i++) verify += (s.calibrationRaw[i] - newZero[i]) * newGramsPerCount[i];
    if (!isfinite(verify) || fabs(verify - knownMass) > fmaxf(2.0f, knownMass * 0.03f)) {
      svcMessage = "VERIFY MASS";
      Serial.printf("CAL save refused: measured %.3f g, expected %.3f g; keep known mass on tray\n", verify, knownMass);
      return;
    }
    manualTare.clear();
    for (int i = 0; i < NCELL; i++) { cells[i].zero = newZero[i]; cells[i].cpg = newCpg[i]; }
    uint32_t count = prefs.getUInt("calcount", 0) + 1; prefs.putUInt("calcount", count);
    saveCalibration(String(CAL_VERSION_PREFIX) + bus.boot.substring(0, 6) + "-" + String(count));
    qcReset();
    refreshWeighingCalibration();
    reportCalibration(verify);
    Serial.printf("CAL SAVED version=%s mass_g=%.3f verify_g=%.3f counts_per_g=%s\n",
                  calVersion.c_str(), knownMass, verify, scaleDiagnostic().c_str());
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
    usbState();
    return;
  }
  if (btnPage.down || btnAction.down || !calibrationStillEnough()) { calibrationStillSince = 0; return; }
  if (!calibrationStillSince) { calibrationStillSince = now; return; }
  if (now - calibrationStillSince < 1000) return;
  Press action = pendingCalibration; pendingCalibration = P_NONE;
  svcMessage = ""; svcActivity = now;
  applyServiceAction(action, s);
  usbState();
}
// Persist an audit observation before changing the volatile zero reference.
// This is HEALTH, not a pickup/return: raw samples and calibration remain intact.
bool recordTareChange(const char* action, float offset) {
  JsonDocument d; bus.envelope(d, "HEALTH", TRAY_ID, calVersion.c_str());
  d["evidence_refs"].add(String("MANUAL_TARE:") + action);
  if (String(action) == "APPLY") d["evidence_refs"].add(tareEmptyRequested ? "TARE_EMPTY_CONFIRMED:1" : "TARE_EMPTY_CONFIRMED:0");
  d["evidence_refs"].add("TARE_OFFSET_G:" + String(offset, 6));
  d["evidence_refs"].add("TARE_REF:" + bus.boot + ":" + String(manualTare.count() + (String(action) == "APPLY" ? 1 : 0)));
  return bus.persist(d);
}
void startManualTare(bool emptyConfirmed) {
  if (svc != SVC_OFF) { tareFeedback("EXIT SERVICE"); Serial.println("TARE_REFUSED service_mode"); return; }
  if (calVersion == "UNSET") { tareFeedback("CALIBRATE FIRST"); Serial.println("TARE_REFUSED uncalibrated"); return; }
  tareEmptyRequested = emptyConfirmed;
  weighing.suspendZeroTracking();
  manualTare.request(millis());
  tareMessage = "";
  Serial.println("TARE_REQUESTED release buttons; waiting for 1 s stable readings");
}
void requestManualTare() { startManualTare(false); }
void requestEmptyTare() { startManualTare(true); }
void clearManualTare() {
  if (svc != SVC_OFF) { Serial.println("TARE_REFUSED service_mode"); return; }
  if (!manualTare.active()) { manualTare.cancel(); tareFeedback("NO TARE ACTIVE"); return; }
  if (!recordTareChange("CLEAR", manualTare.totalOffset())) {
    manualTare.cancel(); tareFeedback("NO SPACE - RETRY"); Serial.println("TARE_REFUSED audit_not_saved"); return;
  }
  manualTare.clear(); weighing.clearTare(); hold.reset(); stableSince = 0; page = 0;
  tareFeedback("TARE CLEARED"); Serial.println("TARE_CLEARED calibration unchanged");
}
void tickManualTare(Snap& s, uint32_t now) {
  if (svc != SVC_OFF) { manualTare.cancel(); return; }
  if (!manualTare.pending()) return;
  auto result = manualTare.poll(now, btnPage.down || btnAction.down,
                                s.calibrated && stillEnough(s) && weighing.settled() && s.noise < STABLE_NOISE_G);
  if (result == ManualTare<NCELL>::TIMED_OUT) {
    tareFeedback("UNSTABLE - RETRY"); Serial.println("TARE_REFUSED unstable_timeout"); return;
  }
  if (result != ManualTare<NCELL>::READY) return;
  float reference[NCELL], sum = 0;
  for (int i = 0; i < NCELL; ++i) { reference[i] = weighing.included(i) ? weighing.calibratedAt(i, s.filteredRaw[i]) : 0; sum += reference[i]; }
  if (!isfinite(sum) || !recordTareChange("APPLY", sum)) {
    manualTare.cancel(); tareFeedback("NO SPACE - RETRY"); Serial.println("TARE_REFUSED audit_not_saved"); return;
  }
  if (!manualTare.apply(reference)) { manualTare.cancel(); tareFeedback("TARE FAILED"); return; }
  double tareReference[NCELL];
  for (int i = 0; i < NCELL; ++i) tareReference[i] = reference[i];
  weighing.setTare(tareReference, tareEmptyRequested);
  s = snapshot();
  hold.tare(s.measured, now);  // readings were stable for the tare: they are exactly 0 now
  Serial.printf("AZT_EMPTY_CONFIRMED %s\n", tareEmptyRequested ? "yes (operator tareempty)" : "no (loaded-reference tare)");
  // The raw event baseline is retained: subtracting the same offset from pre/post
  // cannot invent a weight change or erase a genuine pending load change.
  stableSince = 0; page = 0; s = snapshot();
  tareFeedback("ZERO SET");
  Serial.printf("TARE_APPLIED count=%lu offset_g=%.6f total_g=%.3f calibration=%s (not saved to NVS)\n",
                (unsigned long)manualTare.count(), sum, s.total, calVersion.c_str());
}
void enterService() { if (qcActive()) qcCancel("Service mode"); manualTare.cancel(); svc = SVC_HOME; svcActivity = millis(); svcMessage = ""; Serial.println("SERVICE MODE ON"); }
void exitService() {
  pendingCalibration = P_NONE;
  svc = SVC_OFF; afterService = true; stableSince = 0;  // a change across service is reported, flagged, never hidden
  Serial.println("SERVICE MODE OFF");
}

// ---------------- Quick calibration before demos ----------------
// Under a minute with one known mass and no buttons after Start: empty tray -> mass over
// A -> moved over C -> moved to the centre -> removed. Each move is detected from the
// ADC; then the tray must be still for QC_STILL_MS and is measured for QC_WINDOW_MS. All
// samples are fitted per cell to zero + rate*t + load[position] (drift_check.h): A and C
// give the gains with the drift during the run removed, and the centre is the independent
// check. The drift is NOT extrapolated afterwards: it reverses with temperature from
// minute to minute, so a 50 s trend predicts nothing. The tray is re-zeroed at the fitted
// zero and confirmed empty, so zero tracking follows the wander from then on. Gains and
// zeros are saved like a service calibration.
enum QcStep { QC_OFF, QC_EMPTY1, QC_ON_A, QC_ON_C, QC_CENTRE, QC_EMPTY2, QC_DONE, QC_FAIL };
const char* QC_STEP_NAMES[] = {"OFF", "EMPTY1", "ON_A", "ON_C", "CENTRE", "EMPTY2", "DONE", "FAIL"};
enum QcPhase { QC_WAIT_MOVE, QC_WAIT_STILL, QC_MEASURE };
const char* QC_PHASE_NAMES[] = {"WAIT_MOVE", "WAIT_STILL", "MEASURE"};
QcStep qcStep = QC_OFF;
QcPhase qcPhase = QC_WAIT_STILL;
uint32_t qcStartMs = 0, qcStepAt = 0, qcStillSince = 0, qcCollectAt = 0, qcDoneAt = 0;
DriftFit<NCELL + 1> qcFit[NCELL];
// One window is buffered and only committed to the fit if the tray stayed still throughout.
constexpr int QC_BUFFER = 80;
struct QcSampleRow { double t; int32_t raw[NCELL]; } qcBuffer[QC_BUFFER];
int qcBuffered = 0;
double qcLevel[NCELL] = {}, qcEmptyLevel[NCELL] = {};  // mean counts of the last / first window
String qcMessage;
double qcVerifyG = 0, qcRateGMin[NCELL] = {}, qcResidualG[NCELL] = {};
float qcCountsPerGram[NCELL] = {};

bool qcActive() { return qcStep != QC_OFF && qcStep != QC_DONE && qcStep != QC_FAIL; }
int qcPosition(QcStep step) { return step == QC_ON_A ? 0 : step == QC_ON_C ? 1 : step == QC_CENTRE ? NCELL : -1; }
void qcEnter(QcStep step) {
  qcStep = step; qcStepAt = millis(); qcStillSince = 0; qcBuffered = 0;
  qcPhase = step == QC_EMPTY1 ? QC_WAIT_STILL : QC_WAIT_MOVE;
  Serial.printf("QC step=%s\n", QC_STEP_NAMES[step]);
  usbState();
}
void qcReset() { qcStep = QC_OFF; qcMessage = ""; qcDoneAt = 0; }
void qcFail(const char* why) {
  qcMessage = why; qcEnter(QC_FAIL);
  afterService = true; stableSince = 0;
  Serial.printf("QC FAIL %s (calibration unchanged)\n", why);
}
void qcCancel(const char* why) {
  if (!qcActive()) { Serial.println("QC cancel: no check running"); return; }
  qcFail(why);
}
void qcStart() {
  if (qcActive()) { Serial.println("QC refused: a check is already running"); return; }
  if (svc != SVC_OFF || manualTare.pending()) { Serial.println("QC refused: leave calibration/tare first"); return; }
  for (auto& fit : qcFit) fit.reset();
  qcMessage = ""; qcStartMs = millis();
  qcEnter(QC_EMPTY1);
}
// Manual override: the move was made but not detected (e.g. a very light mass).
void qcNext() {
  if (qcActive() && qcPhase == QC_WAIT_MOVE) { qcPhase = QC_WAIT_STILL; qcStillSince = 0; usbState(); }
  else Serial.println("QC next refused: not waiting for the mass to be moved");
}
void qcSample(const int32_t (&raw)[NCELL], uint32_t now) {
  if (!qcActive() || qcPhase != QC_MEASURE || qcBuffered >= QC_BUFFER) return;
  QcSampleRow& row = qcBuffer[qcBuffered++];
  row.t = uint32_t(now - qcStartMs) / 1000.0;
  for (int i = 0; i < NCELL; ++i) row.raw[i] = raw[i];
}
double qcDistance(const double (&level)[NCELL]) {
  double sum = 0;
  for (int i = 0; i < NCELL; ++i) sum += fabs(medianRaw(cells[i]) - level[i]);
  return sum;
}
// The mass must really rest on the tray (or really be off it) before a window counts:
// a mass still held in the hand leaves a steady but empty-looking tray.
bool qcInExpectedState() {
  if (qcStep == QC_EMPTY1) return true;
  if (qcStep == QC_EMPTY2) return qcDistance(qcEmptyLevel) < qcDistance(qcLevel);
  return qcDistance(qcEmptyLevel) > QC_MOVE_COUNTS;
}
void qcFinish(uint32_t now) {
  for (auto& fit : qcFit) if (!fit.solve()) { qcFail("FIT FAILED"); return; }
  QuickCalResult<NCELL> result = evaluateQuickCalibration<NCELL>(qcFit, knownMass);
  qcVerifyG = result.verifyG;
  for (int i = 0; i < NCELL; ++i) {
    qcRateGMin[i] = result.rateGPerMin[i]; qcResidualG[i] = result.residualG[i];
    qcCountsPerGram[i] = result.countsPerGram[i];
  }
  Serial.printf("QC fit mass_g=%.3f centre_g=%.3f counts_per_g A=%.3f C=%.3f drift_g_min A=%.3f C=%.3f residual_g A=%.3f C=%.3f %s\n",
                knownMass, qcVerifyG, qcCountsPerGram[0], qcCountsPerGram[1], qcRateGMin[0], qcRateGMin[1],
                qcResidualG[0], qcResidualG[1], result.why);
  if (!result.ok) { qcFail(result.why); return; }
  const double t = uint32_t(now - qcStartMs) / 1000.0;
  for (int i = 0; i < NCELL; ++i) {
    cells[i].zero = qcFit[i].zeroAt(t);
    cells[i].cpg = result.countsPerGram[i];
  }
  uint32_t count = prefs.getUInt("calcount", 0) + 1; prefs.putUInt("calcount", count);
  saveCalibration(String(CAL_VERSION_PREFIX) + bus.boot.substring(0, 6) + "-" + String(count) + "q");
  refreshWeighingCalibration();
  double empty[NCELL] = {};
  if (!weighing.setTare(empty, true)) { qcFail("APPLY FAILED"); return; }
  reportCalibration(qcVerifyG);
  JsonDocument audit; bus.envelope(audit, "HEALTH", TRAY_ID, calVersion.c_str());
  audit["evidence_refs"].add("QUICK_CALIBRATION:" + calVersion);
  for (int i = 0; i < NCELL; ++i) {
    audit["evidence_refs"].add("QC_DRIFT_G_PER_MIN_" + String(cells[i].name) + ":" + String(qcRateGMin[i], 4));
    audit["evidence_refs"].add("QC_RESIDUAL_G_" + String(cells[i].name) + ":" + String(qcResidualG[i], 4));
  }
  bus.persist(audit);
  afterService = true; stableSince = 0;
  qcDoneAt = now; qcMessage = "";
  qcEnter(QC_DONE);
  Serial.printf("QC DONE version=%s centre_g=%.3f; empty tray zeroed, zero tracking on\n", calVersion.c_str(), qcVerifyG);
}
void qcTick(uint32_t now) {
  if (!qcActive()) return;
  if (now - qcStepAt > QC_STEP_TIMEOUT_MS) { qcFail(qcPhase == QC_WAIT_MOVE ? "TIMED OUT" : "NOT STILL"); return; }
  if (qcPhase == QC_WAIT_MOVE) {
    if (qcDistance(qcLevel) > QC_MOVE_COUNTS) { qcPhase = QC_WAIT_STILL; qcStillSince = 0; usbState(); }
    return;
  }
  const bool still = !btnPage.down && !btnAction.down && calibrationStillEnough() && qcInExpectedState();
  if (qcPhase == QC_WAIT_STILL) {
    if (!still) { qcStillSince = 0; return; }
    if (!qcStillSince) { qcStillSince = now; return; }
    if (now - qcStillSince < QC_STILL_MS) return;
    qcPhase = QC_MEASURE; qcCollectAt = now; qcBuffered = 0;
    usbState();
    return;
  }
  if (!still) { qcPhase = QC_WAIT_STILL; qcStillSince = 0; qcBuffered = 0; usbState(); return; }  // touched: redo window
  if (now - qcCollectAt < QC_WINDOW_MS) return;
  const int position = qcPosition(qcStep);
  double sum[NCELL] = {};
  for (int r = 0; r < qcBuffered; ++r)
    for (int i = 0; i < NCELL; ++i) { qcFit[i].add(qcBuffer[r].t, position, qcBuffer[r].raw[i]); sum[i] += qcBuffer[r].raw[i]; }
  for (int i = 0; i < NCELL; ++i) qcLevel[i] = qcBuffered ? sum[i] / qcBuffered : medianRaw(cells[i]);
  if (qcStep == QC_EMPTY1) for (int i = 0; i < NCELL; ++i) qcEmptyLevel[i] = qcLevel[i];
  Serial.printf("QC measured step=%s samples=%d\n", QC_STEP_NAMES[qcStep], qcBuffered);
  if (qcStep == QC_EMPTY2) qcFinish(now);
  else qcEnter(QcStep(qcStep + 1));
}
void appendQuickCheckState(JsonDocument& d) {
  auto qc = d["quick_check"].to<JsonObject>();
  const uint32_t now = millis();
  qc["step"] = QC_STEP_NAMES[qcStep];
  qc["phase"] = QC_PHASE_NAMES[qcPhase];
  qc["seconds_left"] = qcPhase == QC_MEASURE ? (QC_WINDOW_MS - min(QC_WINDOW_MS, uint32_t(now - qcCollectAt))) / 1000.0 : 0;
  qc["elapsed_s"] = qcActive() ? uint32_t(now - qcStartMs) / 1000 : 0;
  qc["message"] = qcMessage;
  qc["known_mass_g"] = knownMass;
  if (qcDoneAt) {
    qc["age_s"] = uint32_t(now - qcDoneAt) / 1000;
    qc["centre_g"] = qcVerifyG;
    for (int i = 0; i < NCELL; ++i) {
      qc["counts_per_g"][String(cells[i].name)] = qcCountsPerGram[i];
      qc["drift_g_per_min"][String(cells[i].name)] = qcRateGMin[i];
      qc["residual_g"][String(cells[i].name)] = qcResidualG[i];
    }
  }
}
void handleButtons(const Snap& s) {
  uint32_t now = millis();
  Press pp = btnPage.poll(now), pa = btnAction.poll(now);
  if (btnPage.down && btnAction.down) {
    manualTare.cancel();
    if (!bothSince) bothSince = now;
    if (!bothLatched && now - bothSince >= 3000) { bothLatched = true; svc == SVC_OFF ? enterService() : exitService(); }
    return;
  }
  bothSince = 0;
  if (bothLatched) { if (!btnPage.down && !btnAction.down) bothLatched = false; return; }  // ignore the release
  if (qcActive()) {  // ACTION: "I moved it" if a move was not detected; long PAGE cancels
    if (pa == P_SHORT) qcNext();
    if (pp == P_LONG) qcCancel("Cancelled");
    return;
  }
  if (svc == SVC_OFF) {
    if (pp == P_SHORT) page = (page + 1) % PAGES;
    if (pp == P_LONG) clearManualTare();
    if (pa == P_LONG) requestManualTare();
    if (pa == P_SHORT) { lcdOn = !lcdOn; lcdShown[0] = lcdShown[1] = ""; }  // backlight toggle only
    return;
  }
  if (pp != P_NONE || pa != P_NONE) svcActivity = now;
  if (pp == P_SHORT) { svc = SVC_HOME; svcMessage = ""; }  // cancel back to service home
  if (pa != P_NONE) serviceAction(pa, s);
  if (now - svcActivity > SERVICE_TIMEOUT_MS) exitService();
}

// ---------------- Display ----------------
// Whole grams (held weights, an exact tare) print without a decimal; "-0" prints as "0".
String fmtG(float g) {
  char b[12];
  if (fabsf(g - rintf(g)) < 0.005f) snprintf(b, sizeof b, "%.0f", rintf(g) == 0 ? 0.0f : rintf(g));
  else snprintf(b, sizeof b, "%.1f", g);
  return b;
}
bool renderQuickCheck();
void render(const Snap& s) {
  if (svc != SVC_OFF) {
    // A rejected capture is retried by ACTION; its old warning must not hide
    // the live readiness prompt after the load cells have settled again.
    if (svcMessage == "NOT STILL - WAIT" && stillEnough(s)) svcMessage = "";
    const char* titles[] = {"", "SERVICE ACT:cal", "EMPTY BOX  ACT", "MASS ON A  ACT", "MASS ON C  ACT", "VERIFY HOLD ACT", "SAVED ACT:back"};
    lcdLine(0, titles[svc]);
    if (svcMessage.length()) lcdLine(1, svcMessage);
    else if (svc == CAL_VERIFY) { float v = 0; for (int i = 0; i < NCELL; i++) v += (s.calibrationRaw[i] - newZero[i]) * newGramsPerCount[i]; lcdLine(1, fmtG(v) + " g of " + fmtG(knownMass)); }
    else if (svc == SVC_HOME) lcdLine(1, "mass " + fmtG(knownMass) + " g");
    else lcdLine(1, stillEnough(s) ? "still - press" : "settling...");
    return;
  }
  if (renderQuickCheck()) return;
  if (manualTare.pending()) {
    lcdLine(0, "TARE WAIT..."); lcdLine(1, "RELEASE HANDS"); return;
  }
  if (tareMessage.length() && (int32_t)(tareMessageUntil - millis()) > 0) {
    lcdLine(0, tareMessage); lcdLine(1, fmtG(s.displayTotal) + " g"); return;
  }
  if (guideUntil && (int32_t)(guideUntil - millis()) > 0) {
    for (int r = 0; r < 2; r++) {
      String line = guideLine[r];
      line.replace("#", s.calibrated ? fmtG(s.displayTotal) + "g" : String("UNCAL"));
      lcdLine(r, line);
    }
    return;
  }
  switch (page) {
    case 0:
      lcdLine(0, s.calibrated ? String(manualTare.active() ? "T " : "") + TRAY_ID + " " + fmtG(s.displayTotal) + "g" : String(TRAY_ID) + " UNCALIB");
      lcdLine(1, s.fault ? s.flags : (stableSince ? "STABLE +-" + fmtG(s.noise) : "SETTLING +-" + fmtG(s.noise)));
      break;
    case 1:
      lcdLine(0, "A" + fmtG(s.g[0]) + " C" + fmtG(s.g[1]));
      lcdLine(1, s.calibrated ? "grams per cell" : "UNCALIBRATED");
      break;
    case 2:
      lcdLine(0, "A" + String(s.raw[0] / 1000) + "k C" + String(s.raw[1] / 1000) + "k");
      lcdLine(1, "raw counts");
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

bool renderQuickCheck() {
  const uint32_t now = millis();
  if (!qcActive() && !(qcStep != QC_OFF && now - qcStepAt < 8000)) return false;
  const String mass = fmtG(knownMass) + "g";
  const char* titles[] = {"", "QC: TRAY EMPTY", "PUT MASS OVER A", "MOVE IT OVER C", "MOVE TO CENTRE", "TAKE MASS OFF"};
  if (qcActive()) {
    lcdLine(0, titles[qcStep]);
    if (qcPhase == QC_MEASURE)
      lcdLine(1, "HANDS OFF " + String((QC_WINDOW_MS - min(QC_WINDOW_MS, uint32_t(now - qcCollectAt)) + 999) / 1000) + "s");
    else lcdLine(1, qcPhase == QC_WAIT_MOVE ? mass + " waiting" : "let go, still...");
  } else if (qcStep == QC_DONE) { lcdLine(0, "QC OK centre"); lcdLine(1, fmtG(qcVerifyG) + "g of " + mass); }
  else { lcdLine(0, "QC FAILED"); lcdLine(1, qcMessage); }
  return true;
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
    if (list.isNull() || list.size() != NCELL) { Serial.println("CALIBRATE refused: active cells need zero_raw/counts_per_g"); return; }
    if (version == calVersion || !version.length()) { Serial.println("Duplicate or empty calibration version refused"); return; }
    float z[NCELL], k[NCELL]; bool seen[NCELL] = {false};
    for (JsonObject o : list) for (int i = 0; i < NCELL; i++) if (o["name"].as<String>() == String(cells[i].name)) {
      z[i] = o["zero_raw"]; k[i] = o["counts_per_g"];
      seen[i] = isfinite(z[i]) && z[i] >= -8388608 && z[i] <= 8388607 &&
                isfinite(k[i]) && fabs(k[i]) >= 10 && fabs(k[i]) <= 100000; }
    for (bool ok : seen) if (!ok) { Serial.println("CALIBRATE refused: every active cell needs a non-zero scale"); return; }
    manualTare.clear();
    for (int i = 0; i < NCELL; i++) { cells[i].zero = z[i]; cells[i].cpg = k[i]; }
    saveCalibration(version); refreshWeighingCalibration(); qcReset(); Serial.println("Calibration " + version + " saved");
  } else if (action == "GEOMETRY") {
    if (NCELL < 3) { Serial.println("GEOMETRY refused: A/C mode cannot locate 2D loads"); return; }
    JsonArray list = d["cells"].as<JsonArray>(); int found = 0;
    for (JsonObject o : list) for (auto& c : cells) if (o["name"].as<String>() == String(c.name)) { c.x = o["x_mm"]; c.y = o["y_mm"]; found++; }
    if (found != NCELL) { Serial.println("GEOMETRY refused: need every cell"); loadConfig(); return; }
    geomVersion = d["version"].as<String>(); geomSet = geomVersion.length() > 0;
    for (auto& c : cells) { String k(c.name); prefs.putFloat((k + "x").c_str(), c.x); prefs.putFloat((k + "y").c_str(), c.y); }
    prefs.putString("geo", geomVersion); Serial.println("Geometry " + geomVersion + " saved");
  } else if (action == "CONFIG") {
    float m = d["known_mass_g"] | 0.0f;
    if (m >= 10 && m <= 10000) { knownMass = m; prefs.putFloat("mass", m); Serial.println("Known mass set"); }
  }
}

void debugWeighing(const Snap& s) {
  static uint32_t last = 0;
  if (!scaleDebugEnabled || millis() - last < SCALE_DEBUG_INTERVAL_MS) return;
  last = millis(); const auto& value = weighing.value();
  // Independent per-channel acquisition trace, even if another cell is missing
  // or no valid calibration exists. Never substitute filtered data or zero.
  String rawLine = "RAW sample_ms=" + String(last);
  for (const auto& c : cells) {
    rawLine += " raw_" + String(c.name) + "=" + String(c.last);
    rawLine += " age_" + String(c.name) + "=" + String(last - c.lastReady);
    rawLine += " valid_" + String(c.name) + "=" + String(c.n > 0);
  }
  if (Serial.availableForWrite() >= int(rawLine.length() + 2)) Serial.println(rawLine);
  if (!weighing.fresh(millis())) return;
  String line = "WEIGH pair_ms=" + String(value.capturedMs);
  for (int i = 0; i < NCELL; ++i) {
    line += " raw_" + String(cells[i].name) + "=" + String(value.raw[i]);
    line += " ema_" + String(cells[i].name) + "=" + String(value.filteredRaw[i], 3);
    line += " zero_" + String(cells[i].name) + "=" + String(cells[i].zero + weighing.driftOffsetCounts(i), 3);
    line += " gain_" + String(cells[i].name) + "=" + String(cells[i].gramsPerCount, 9);
    line += " net_" + String(cells[i].name) + "=" + String(value.cellGrams[i], 4);
  }
  line += " sum_raw_eq=" + String(value.combinedRawCounts, 3);
  line += " sum_ema_eq=" + String(value.filteredCombinedCounts, 3);
  line += " calibrated_g=" + String(value.calibratedTotalG, 4);
  line += " raw_net_g=" + String(value.rawNetG, 4);
  line += " net_g=" + String(value.netG, 4) + " display_g=" + String(value.displayG, 4);
  line += " azt_g=" + String(value.zeroCorrectionG, 4) + " tracking=" + String(value.zeroTracking);
  line += " shown_g=" + String(s.total, 4) + " held_g=" + String(hold.heldTotal(), 4);
  if (Serial.availableForWrite() >= int(line.length() + 2)) Serial.println(line);
}

// ---------------- Main ----------------
uint32_t lastHealth = 0, lastDiag = 0, lastTick = 0, lastRender = 0;
String barrierId = ""; uint32_t barrierRequested = 0;

void setup() {
  Serial.setTxBufferSize(4096);
  Serial.begin(115200);
#ifdef WEIGHING_SELF_TEST
  const int weighingTestFailure = weighing_self_test::run();
  weighingSelfTestOK = weighingTestFailure == 0;
  Serial.printf("WEIGH_SELFTEST %s line=%d\n", weighingSelfTestOK ? "PASS" : "FAIL", weighingTestFailure);
#endif
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
  if(!rfidObservations || xTaskCreatePinnedToCore(rfidScanTask,"rfid-scan",4096,nullptr,2,&rfidTask,0)!=pdPASS) {
    bus.lost();Serial.println("RFID_TASK_FAILED: no RFID capture available");
  }
  Serial.printf("RFID tap mode: 600ms absence to rearm; RAM=64 events; durable_queue=%u/%u\n",bus.queuedFiles,bus.QUEUE_CAPACITY);
}

// Main-loop half of the RFID self-healing (see rfidHeartbeat). Serial output stays in this task so the
// scan task never interleaves text with the USB protocol lines.
void rfidWatchdog() {
  static uint32_t restartedAt = 0, reported = 0;
  if (!rfidObservations) return;
  const uint32_t now = millis(), beat = rfidHeartbeat.load();
  if (rfidReinits.load() != reported) {
    reported = rfidReinits.load();
    Serial.printf("RFID_REINIT count=%lu versions=0x%02X/0x%02X\n", (unsigned long)reported, readerVersion[0], readerVersion[1]);
  }
  if (!beat || (int32_t)(now - beat) < (int32_t)RFID_STALL_MS) { restartedAt = 0; return; }
  if (restartedAt) {
    if (now - restartedAt < RFID_RESTART_GRACE_MS) return;
    Serial.println("RFID_WATCHDOG restarted scan did not recover: restarting the controller");
    delay(100);
    ESP.restart();
  }
  Serial.printf("RFID_WATCHDOG scan silent %lu ms: restarting the RFID task\n", (unsigned long)(now - beat));
  if (rfidTask) vTaskDelete(rfidTask);
  rfidTask = nullptr;
  rfidRestarts++;
  rfidHeartbeat.store(now);
  restartedAt = now;
  if (xTaskCreatePinnedToCore(rfidScanTask, "rfid-scan", 4096, nullptr, 2, &rfidTask, 0) != pdPASS)
    Serial.println("RFID_TASK_FAILED: watchdog could not restart RFID capture");
}

void loop() {
#ifdef RFID_DIAGNOSTIC
  rfidConsole();
#endif
  rfidWatchdog();
  bus.tick();
  uint32_t now = millis();
  sampleCells();
  processWeighingSample(svc == SVC_OFF && !btnPage.down && !btnAction.down);
  qcTick(millis());
  static uint32_t lastQcState = 0;  // the monitor shows the check's prompts and countdown live
  if (qcActive() && now - lastQcState >= 1000) { lastQcState = now; usbState(); }
  if (bus.command.length()) { handleCommand(bus.command, now, barrierId, barrierRequested); bus.command = ""; }
  static Snap s; static uint32_t lastSnap = 0;
  if (now - lastSnap >= 50) { lastSnap = now; s = snapshot(); }
  handleButtons(s);
  tickCalibrationCapture(s, millis());
  tickManualTare(s, millis());
  if (now - lastTick >= 100) { lastTick = now; if (svc == SVC_OFF && !qcActive()) trackWeight(s); }
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
    Serial.printf("DIAG raw %s total=%s noise=%.2f%s rc522=0x%02X/0x%02X scans=%lu/%lu lcd=%s svc=%d wifi=%s mqtt=%s fs=%s\n",
      rawDiagnostic(s).c_str(), s.calibrated ? fmtG(s.total).c_str() : "UNCAL", s.noise, s.flags.length() ? (" flags=" + s.flags).c_str() : "",
      readerVersion[0], readerVersion[1],
      (unsigned long)readerReadCount[0].load(), (unsigned long)readerReadCount[1].load(), lcdAddr ? "ok" : "none", (int)svc,
      WiFi.status() == WL_CONNECTED ? "up" : "down", bus.mqtt.connected() ? "up" : "down", bus.storageOK ? "ok" : "MISSING");
    Serial.printf("DIAG RFID_QUEUE durable=%u/%u ram=%u scan_gap_max_ms=%lu/%lu historical_overflow=%lu beat_ms=%lu reinits=%lu restarts=%lu\n",
                  bus.queuedFiles,bus.QUEUE_CAPACITY,rfidObservations?(unsigned)uxQueueMessagesWaiting(rfidObservations):0,
                  (unsigned long)rfidMaxScanGap[0].load(),(unsigned long)rfidMaxScanGap[1].load(),(unsigned long)bus.overflow,
                  (unsigned long)(millis()-rfidHeartbeat.load()),(unsigned long)rfidReinits.load(),(unsigned long)rfidRestarts.load());
    // answered = a tag replied to wake-up; read = UID read; failed = replied but the read failed.
    Serial.printf("DIAG RFID_READERS R1 answered=%lu read=%lu failed=%lu | R2 answered=%lu read=%lu failed=%lu\n",
                  (unsigned long)readerAnswered[0].load(),(unsigned long)readerReadCount[0].load(),(unsigned long)readerReadFailed[0].load(),
                  (unsigned long)readerAnswered[1].load(),(unsigned long)readerReadCount[1].load(),(unsigned long)readerReadFailed[1].load());
    if (svc != SVC_OFF) {
      float verify = 0;
      if (svc == CAL_VERIFY) for (int i = 0; i < NCELL; i++) verify += (s.calibrationRaw[i] - newZero[i]) * newGramsPerCount[i];
      Serial.printf("CAL step=%s mass_g=%.3f still=%s verify_g=%.3f raw_sd=%s\n",
                    calibrationStepName(), knownMass, stillEnough(s) ? "yes" : "no", verify,
                    noiseDiagnostic().c_str());
    }
  }
  if (now - lastHealth >= 2000) {  // best-effort live health (not flash-queued); 2 s keeps database growth modest
    lastHealth = now;
    JsonDocument d; bus.envelope(d, "HEALTH", TRAY_ID, calVersion.c_str());
    appendTareEvidence(d);
    String flags = s.flags; if (manualTare.pending()) addFlag(flags, "TARE_PENDING");
    if (svc != SVC_OFF) addFlag(flags, "SERVICE_MODE");
    if (qcActive()) addFlag(flags, "QUICK_CHECK");
    if (flags.length()) { d["sensor_health"]["flags"].add(flags); d["sensor_health"]["ok"] = false; }
    long rawSum = 0; JsonArray arr = d["weight"]["cells"].to<JsonArray>();
    for (int i = 0; i < NCELL; i++) { rawSum += s.raw[i]; JsonObject c = arr.add<JsonObject>(); c["name"] = String(cells[i].name); c["raw"] = s.raw[i]; if (s.calibrated) c["post_g"] = s.g[i]; c["ok"] = millis() - cells[i].lastReady <= 1000; }
    const double pre = hold.heldTotal();
    d["weight"]["raw"] = rawSum; if (s.calibrated) d["weight"]["filtered"] = s.total; else d["weight"]["filtered"] = nullptr;
    d["weight"]["pre_g"] = pre; d["weight"]["post_g"] = pre; d["weight"]["noise_g"] = s.noise; d["weight"]["settled"] = false;
    String body; serializeJson(d, body); if (bus.mqtt.connected()) bus.mqtt.publish(bus.topic("events").c_str(), body.c_str());
#ifdef USB_RUNTIME
    d.remove("device_key");Serial.print("SF_HEALTH ");serializeJson(d,Serial);Serial.println();
#endif
  }
  debugWeighing(s);
  delay(1);
}
