#include "../include/manual_tare.h"
#include <assert.h>
#include <stdio.h>

int main() {
  ManualTare<2> tare;
  assert(!tare.active() && !tare.pending());
  assert(tare.grams(0, 30) == 30);
  tare.request(100);
  assert(tare.poll(1600, true, true) == ManualTare<2>::WAITING); // Held button never samples.
  assert(tare.poll(1700, false, true) == ManualTare<2>::WAITING);
  assert(tare.poll(2500, false, false) == ManualTare<2>::WAITING); // Movement resets settling.
  assert(tare.poll(2600, false, true) == ManualTare<2>::WAITING);
  assert(tare.poll(3599, false, true) == ManualTare<2>::WAITING);
  assert(tare.poll(3600, false, true) == ManualTare<2>::READY);
  float fortyFour[2] = {25, 19};
  assert(tare.apply(fortyFour));
  assert(tare.active() && !tare.pending() && tare.totalOffset() == 44);
  assert(tare.grams(0, 25) + tare.grams(1, 19) == 0);
  assert(tare.grams(0, 0) + tare.grams(1, 0) == -44); // Removing reference is negative, not silently re-zeroed.
  // Real weight differences must survive the change of display zero.
  assert(tare.grams(0, 35) - tare.grams(0, 25) == 10);
  tare.request(4000);
  assert(tare.poll(24000, false, false) == ManualTare<2>::TIMED_OUT);
  assert(tare.active() && tare.totalOffset() == 44); // Failed re-tare retains previous reference.
  float drifted[2] = {26, 20};
  assert(tare.apply(drifted));
  assert(tare.totalOffset() == 46 && tare.grams(0, 26) + tare.grams(1, 20) == 0); // Replace, never accumulate.
  float invalid[2] = {NAN, 0};
  assert(!tare.apply(invalid) && tare.totalOffset() == 46);
  tare.request(25000); tare.cancel();
  assert(!tare.pending() && tare.active()); // Service entry cancels request only.
  tare.clear();
  assert(!tare.active() && tare.grams(0, 26) == 26);
  ManualTare<2> rebooted;
  assert(!rebooted.active() && rebooted.totalOffset() == 0);
  tare.request(UINT32_MAX - 500);
  assert(tare.poll(UINT32_MAX - 100, false, true) == ManualTare<2>::WAITING);
  assert(tare.poll(1000, false, true) == ManualTare<2>::READY);
  puts("Manual tare passed: release/settle, timeout, repeat, delta preservation, clear, reboot, wrap");
}
