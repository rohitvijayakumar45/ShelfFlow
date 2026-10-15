#include <Arduino.h>
#include <HX711.h>
#include <Preferences.h>
#include <math.h>
#include <stdlib.h>

// Separate ESP32 DevKit / WROOM board, NOT an ESP32-CAM.
constexpr uint8_t HX711_DT = 32;
constexpr uint8_t HX711_SCK = 33;
constexpr unsigned AVERAGE_SAMPLES = 10;
constexpr unsigned CALIBRATION_SAMPLES = 20;
constexpr float CAPACITY_G = 3000.0f;

HX711 scale;
Preferences settings;
bool storageOK = false;
bool tared = false;
double zeroRaw = 0;
double countsPerGram = 0;
long readings[AVERAGE_SAMPLES] = {};
unsigned readingIndex = 0, readingCount = 0;
uint32_t lastSample = 0, lastPrint = 0;
bool saturated = false;

// Collect only new ADC samples, with a timeout if the HX711 is unplugged.
char operation = 0;
double operationSum = 0;
unsigned operationCount = 0;
float knownMass = 0;
uint32_t operationStarted = 0;
char command[48] = {};
unsigned commandLength = 0;
bool commandOverflow = false;

void printHelp() {
  Serial.println("Commands (send with Newline):");
  Serial.println("  t       Empty the platform, then tare/zero");
  Serial.println("  c 500   Place a known 500 gram mass, then calibrate");
  Serial.println("  h       Show help");
  Serial.println("Calibration mass is in grams, from 1 to 3000.");
}

void startOperation(char kind, float mass = 0) {
  operation = kind;
  knownMass = mass;
  operationSum = 0;
  operationCount = 0;
  operationStarted = millis();
  Serial.println("Keep the platform still; collecting 20 samples...");
}

void handleCommand() {
  String line(command);
  line.trim();
  if (!line.length()) return;
  if (line == "h") { printHelp(); return; }
  if (operation) { Serial.println("Busy; wait for sampling to finish."); return; }
  if (line == "t") { startOperation('t'); return; }
  if (line.startsWith("c ")) {
    char* end = nullptr;
    const char* input = line.c_str() + 2;
    float mass = strtof(input, &end);
    if (end == input || *end != '\0' || !isfinite(mass) || mass < 1 || mass > CAPACITY_G) {
      Serial.println("Use c followed by a known mass in grams: c 500");
      return;
    }
    if (!tared) { Serial.println("First empty the platform and send t."); return; }
    startOperation('c', mass);
    return;
  }
  Serial.println("Unknown command. Send h for help.");
}

void finishOperation() {
  double average = operationSum / operationCount;
  if (operation == 't') {
    zeroRaw = average;
    tared = true;
    Serial.println("Tare complete: empty platform = 0 grams.");
  } else {
    double difference = average - zeroRaw;
    if (fabs(difference) < 100) {
      Serial.println("Calibration refused: too little change. Check mass and wiring.");
    } else {
      countsPerGram = difference / knownMass; // Negative polarity is valid.
      if (storageOK) {
        settings.putDouble("counts-per-g", countsPerGram);
        Serial.println("Calibration saved. Remove the mass to check zero.");
      } else {
        Serial.println("Calibration active for this boot only: storage unavailable.");
      }
      Serial.printf("Calibration factor: %.6f ADC counts/gram\n", countsPerGram);
    }
  }
  operation = 0;
  readingCount = 0;
  readingIndex = 0;
}

void setup() {
  Serial.begin(115200);
  scale.begin(HX711_DT, HX711_SCK, 128);
  storageOK = settings.begin("hx711-test", false);
  if (storageOK) countsPerGram = settings.getDouble("counts-per-g", 0);
  if (!isfinite(countsPerGram)) countsPerGram = 0;
  Serial.println("\nHX711 + 3 kg load cell standalone test");
  Serial.println("DT = GPIO32, SCK = GPIO33. Serial = 115200 baud.");
  Serial.println("Empty the platform and send t on EVERY restart.");
  if (countsPerGram == 0) Serial.println("Not calibrated: raw readings until t then c <grams>.");
  else Serial.println("Saved calibration loaded; tare is still required.");
  printHelp();
}

void loop() {
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') {
      command[commandLength] = '\0';
      if (commandOverflow) Serial.println("Command too long; discarded.");
      else handleCommand();
      commandLength = 0;
      commandOverflow = false;
    } else if (commandLength < sizeof(command) - 1) {
      command[commandLength++] = ch;
    } else commandOverflow = true;
  }

  uint32_t now = millis();
  if (scale.is_ready()) {
    long raw = scale.read();
    lastSample = now;
    saturated = raw <= -8388608L || raw >= 8388607L;
    readings[readingIndex++ % AVERAGE_SAMPLES] = raw;
    if (readingCount < AVERAGE_SAMPLES) ++readingCount;
    if (operation) {
      if (saturated) {
        Serial.println("Sampling cancelled: ADC saturated. Check load and wiring.");
        operation = 0;
      } else {
        operationSum += raw;
        if (++operationCount == CALIBRATION_SAMPLES) finishOperation();
      }
    }
  }
  if (operation && now - operationStarted > 5000) {
    Serial.println("Sampling timed out: check HX711 power, DT and SCK.");
    operation = 0;
  }
  if (now - lastPrint >= 500) {
    lastPrint = now;
    if (!readingCount || now - lastSample > 1500) {
      Serial.println("Waiting for fresh HX711 data: check power and wiring.");
    } else if (saturated) {
      Serial.println("ADC saturated: check load and wiring.");
    } else if (!operation) {
      double average = 0;
      for (unsigned i = 0; i < readingCount; ++i) average += readings[i];
      average /= readingCount;
      if (!tared || countsPerGram == 0) {
        Serial.printf("Raw ADC: %.0f | %s\n", average,
                      !tared ? "send t with platform empty" : "send c <known grams>");
      } else {
        double grams = (average - zeroRaw) / countsPerGram;
        Serial.printf("Weight: %.1f g | %.3f kg%s\n", grams, grams / 1000.0,
                      fabs(grams) > CAPACITY_G ? " | ABOVE 3 KG RANGE" : "");
      }
    }
  }
  delay(1);
}
