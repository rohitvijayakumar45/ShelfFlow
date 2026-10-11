#include <Arduino.h>
// Proposed UART2 mapping for verified WROOM-32 DevKitC V4, NOT camera pins.
// Override with compiler definitions only after checking the actual programmer board.
#ifndef BRIDGE_RX_PIN
#define BRIDGE_RX_PIN 16
#endif
#ifndef BRIDGE_TX_PIN
#define BRIDGE_TX_PIN 17
#endif
constexpr uint32_t BAUD=115200;
void setup(){
  Serial.setRxBufferSize(2048);Serial.begin(BAUD);
  Serial2.setRxBufferSize(2048);Serial2.begin(BAUD,SERIAL_8N1,BRIDGE_RX_PIN,BRIDGE_TX_PIN);
  // No startup prints: esptool requires a clean binary channel.
}
void loop(){
  unsigned budget=512;
  while(budget--&&Serial.available()&&Serial2.availableForWrite())Serial2.write(Serial.read());
  budget=512;
  while(budget--&&Serial2.available()&&Serial.availableForWrite())Serial.write(Serial2.read());
  delay(1);
}
