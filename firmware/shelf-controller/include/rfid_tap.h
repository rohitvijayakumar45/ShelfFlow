#pragma once
#include <stdint.h>
#include <string.h>

// One event per presentation. A different UID is always reported immediately;
// the same UID is rearmed after a measured absence, not a one-second cooldown.
class RfidTap {
  char uid[21] = {};
  uint32_t lastSeen = 0;
  bool present = false;
public:
  static constexpr uint32_t releaseMs = 600;
  bool observe(const char* seenUid, uint32_t now) {
    const bool emit = !present || strcmp(uid, seenUid) != 0 ||
                      uint32_t(now - lastSeen) >= releaseMs;
    strncpy(uid, seenUid, sizeof(uid) - 1);
    uid[sizeof(uid) - 1] = '\0';
    present = true;
    lastSeen = now;
    return emit;
  }
  void responded(uint32_t now) {
    if(present) lastSeen = now; // WUPA response still proves RF presence if selection fails
  }
  void absent(uint32_t now) {
    if(present && uint32_t(now - lastSeen) >= releaseMs) present = false;
  }
};
