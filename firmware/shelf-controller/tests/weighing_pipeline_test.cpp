#include "../include/weighing_self_test.h"
#include <stdio.h>
int main() {
  int failure = weighing_self_test::run();
  if (failure) { printf("Weighing self-test failed at line %d\n", failure); return 1; }
  puts("Weighing pipeline passed: scaling, raw EMA, tare, AZT bounds/freeze, stale ADCs, wrap");
  return 0;
}
