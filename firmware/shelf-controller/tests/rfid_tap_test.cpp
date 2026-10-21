#include "../include/rfid_tap.h"
#include "../../common/queue_filename.h"
#include <assert.h>
#include <stdio.h>

int main() {
  char stem[28];
  uint32_t sequences[] = {9u,10u,99u,100u,999u,UINT32_MAX};
  for(uint32_t sequence: sequences) {
    assert(queueStem(stem,sizeof(stem),"1234567890abcdef",sequence));
    assert(strlen(stem)+strlen(".json")<=32);
  }
  assert(!queueStem(stem,sizeof(stem),"1234567890abcdef0",1));
  RfidTap r1, r2;
  assert(r1.observe("43D51829", 0)); // first short tap, no startup lockout
  assert(!r1.observe("43D51829", 60));
  assert(!r1.observe("43D51829", 120));
  assert(r2.observe("43D51829", 130)); // same wrist at other reader
  r1.absent(200); // brief missed poll must not duplicate a held tag
  assert(!r1.observe("43D51829", 240));
  r1.absent(850);
  assert(r1.observe("43D51829", 860)); // remove and re-tap within a second
  assert(r1.observe("ABCDEF01", 880)); // new tag, no global cooldown
  assert(!r1.observe("ABCDEF01", 900));
  r1.responded(1400);
  r1.absent(1600);
  assert(!r1.observe("ABCDEF01",1650)); // failed select must not look like removal
  RfidTap held;
  unsigned heldEvents=0;
  for(uint32_t t=0;t<5000;t+=50) heldEvents += held.observe("43D51829",t);
  assert(heldEvents==1); // a long hold must not fill the event queue
  RfidTap entry, exit;
  unsigned sessionEvents=0;
  for(uint32_t item=0;item<7;item++) {
    uint32_t t=item*1000;
    sessionEvents += entry.observe("43D51829",t);
    assert(!entry.observe("43D51829",t+50));
    sessionEvents += exit.observe("43D51829",t+100);
    entry.absent(t+800);exit.absent(t+800);
  }
  assert(sessionEvents==14); // seven presentations at each reader
  RfidTap wrap;
  assert(wrap.observe("43D51829", UINT32_MAX - 100));
  assert(!wrap.observe("43D51829", 30));
  wrap.absent(700);
  assert(wrap.observe("43D51829", 710));
  puts("RFID tap policy passed: held tag, re-tap, UID change, independent readers, timer wrap");
}
