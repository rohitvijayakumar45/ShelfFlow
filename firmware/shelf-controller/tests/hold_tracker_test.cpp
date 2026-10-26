#include "../include/hold_tracker.h"
#include <assert.h>
#include <stdio.h>

using Tracker = HoldTracker<2>;
static bool near(double a, double b, double e = 0.05) { return fabs(a - b) < e; }

int main() {
  Tracker tracker;
  // The calibrated reading starts 20 g off (stale zero) and drifts 4 g/min on C.
  double drift[2] = {20, 0};
  uint32_t t = 0;
  auto run = [&](double a, double c, uint32_t ms, bool stable = true) {
    bool stepped = false;
    for (uint32_t end = t + ms; t < end; t += 100) {
      drift[1] -= 4.0 / 600;  // 4 g/min
      double m[2] = {a + drift[0], c + drift[1]};
      stepped |= tracker.update(m, stable, t);
    }
    return stepped;
  };
  auto shownTotal = [&](double a, double c) -> double {
    double m[2] = {a + drift[0], c + drift[1]};
    return tracker.shown(0, m[0]) + tracker.shown(1, m[1]);
  };

  assert(!run(0, 0, 1000));                       // first settled reading = empty tray
  assert(tracker.seededState() && near(tracker.heldTotal(), 0));
  assert(!run(0, 0, 60000));                      // a minute of drift: still 0
  assert(near(shownTotal(0, 0), 0, 0.2));

  run(0, 0, 500, false);                          // hand places a 10 g item (5 g per cell)
  assert(run(5, 5, 1000));
  assert(near(tracker.lastStep().pre, 0, .2) && near(tracker.lastStep().post, 10, .2));
  assert(!run(5, 5, 120000));                     // two minutes held: drift absorbed
  assert(near(tracker.heldTotal(), 10, .2) && near(shownTotal(5, 5), 10, .2));

  run(5, 5, 500, false);                          // another 40 g
  assert(run(25, 25, 1000) && near(tracker.lastStep().post, 50, .3));
  run(25, 25, 500, false);                        // 10 g picked up
  assert(run(20, 20, 1000) && near(tracker.lastStep().post, 40, .3) && near(tracker.lastStep().pre, 50, .3));

  run(20, 20, 500, false);                        // item slid along the tray: total unchanged
  assert(!run(35, 5, 3000));
  // Drift during the hand's ~1 s on the tray (4 g/min here, an extreme rate) cannot be told
  // apart from load, so each step may carry a few tenths of a gram.
  assert(near(tracker.heldTotal(), 40, .5) && near(tracker.heldCell(0), 35, .5));

  run(35, 5, 500, false);                        // everything removed: snaps to exactly 0
  assert(run(0.4, 0.3, 1000) && tracker.heldTotal() == 0);
  assert(!run(0, 0, 30000) && near(shownTotal(0, 0), 0, .2));

  // A change below the step size is treated as drift; an unsettled reading never counts.
  assert(!run(1, 1, 3000) && tracker.heldTotal() == 0);
  assert(!run(50, 50, 5000, false) && tracker.heldTotal() == 0);
  tracker.reset();                                // tare with something on: that becomes 0
  assert(!run(40, 40, 1000) && tracker.heldTotal() == 0 && near(shownTotal(40, 40), 0, .2));

  // Steps are held in whole grams: a 12.4 g item reads 12, then 12.6 g more reads 25.
  auto whole = [](double g) { return g == round(g); };
  run(40, 40, 500, false);
  assert(run(46.2, 46.2, 1000) && tracker.lastStep().post == 12 && whole(tracker.heldTotal()));
  run(46.2, 46.2, 500, false);
  assert(run(52.5, 52.5, 1000) && tracker.lastStep().post == 25 && tracker.heldTotal() == 25);
  assert(near(shownTotal(52.5, 52.5), 25, .05));    // the live reading is moved onto the held value
  run(52.5, 52.5, 500, false);                      // slide keeps the whole-gram total
  assert(!run(62.5, 42.5, 3000) && tracker.heldTotal() == 25);

  // Operator tare: exactly 0 at once, no settle wait, and stays 0 through drift.
  double m[2] = {52.5 + drift[0], 52.5 + drift[1]};
  tracker.tare(m, t);
  assert(tracker.seededState() && tracker.heldTotal() == 0);
  assert(tracker.shown(0, m[0]) + tracker.shown(1, m[1]) == 0);
  assert(!run(52.5, 52.5, 30000) && tracker.heldTotal() == 0 && near(shownTotal(52.5, 52.5), 0, .2));

  Tracker::Config unrounded; unrounded.roundG = 0;  // roundG 0 keeps the measured value
  Tracker raw(unrounded); double first[2] = {0, 0}, item[2] = {6.2, 6.2};
  raw.update(first, true, 0); raw.update(first, true, 700);
  assert(raw.update(item, true, 1000) && near(raw.lastStep().post, 12.4, 1e-9));
  double bad[2] = {NAN, 0};
  assert(!tracker.update(bad, true, t));
  puts("Hold tracker passed: auto re-tare, item steps up/down, slide, empty snap, reset, whole grams, tare");
}
