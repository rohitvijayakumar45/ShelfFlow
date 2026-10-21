#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

// The installed filesystem may have a 32-byte component limit. Keep the device
// ID inside JSON, not in every filename: 16 boot + '-' + 10 sequence + '.json'.
inline bool queueStem(char* output, size_t capacity, const char* boot, uint32_t sequence) {
  if(!boot || !boot[0] || strlen(boot)>16) return false;
  const int count=snprintf(output,capacity,"%s-%lu",boot,(unsigned long)sequence);
  return count>0 && size_t(count)<capacity;
}
