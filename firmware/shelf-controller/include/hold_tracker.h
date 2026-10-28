#pragma once
#include <stdint.h>
#include <math.h>

// Step-hold weighing: the tray's weight only changes in steps (an item placed or picked
// up), while the load-cell zero wanders slowly with temperature. So the tracker holds the
// last known weight and, every anchorMs while the reading is settled, re-tares each cell
// so it reads exactly the held value again: slow drift is absorbed before it can grow.
// A settled change of at least stepG is a real item: the held weight moves to the new
// value (that is the pickup/return event) and anchoring continues from there. A tray that
// settles within zeroSnapG of empty is held at exactly 0. Each new held weight is rounded
// to the nearest roundG (whole grams), and an operator tare holds exactly 0 at once.
//
// Input is per-cell net grams from the fixed calibration (drift included). Pure numerics:
// no Arduino, runs on the host in tests.
template<int N>
class HoldTracker {
public:
  struct Config {
    uint32_t anchorMs = 2000;   // re-tare interval while settled
    uint32_t settleMs = 600;    // reading must be settled this long before it counts
    double stepG = 3.0;         // smallest change treated as an item (items are >= ~5 g)
    double zeroSnapG = 1.5;     // a settled total this close to 0 is the empty tray
    double roundG = 1.0;        // held totals snap to this resolution; 0 keeps them unrounded
  };
  struct Step { double pre = 0, post = 0, preCell[N] = {}, postCell[N] = {}; };

  explicit HoldTracker(const Config& settings = Config()) : config(settings) {}

  // Treat the current reading as the empty tray (boot, tare, calibration).
  void reset() { seeded = false; stableSince = 0; }
  // Operator tare on a reading already confirmed stable: this reading is exactly 0 now,
  // without waiting for the tracker's own settle time.
  void tare(const double (&measured)[N], uint32_t now) {
    for (double value : measured) if (!isfinite(value)) return;
    zeroAll(measured, now); seeded = true; stableSince = now ? now : 1;
  }
  bool seededState() const { return seeded; }

  // Returns true when a step (item placed/removed) was registered; see lastStep().
  bool update(const double (&measured)[N], bool stable, uint32_t now) {
    for (double value : measured) if (!isfinite(value)) return false;
    if (!stable) { stableSince = 0; return false; }
    if (!stableSince) { stableSince = now ? now : 1; return false; }
    if (uint32_t(now - stableSince) < config.settleMs) return false;
    if (!seeded) { zeroAll(measured, now); seeded = true; return false; }
    double shownTotal = 0, heldTotal = 0;
    for (int i = 0; i < N; ++i) { shownTotal += measured[i] - offset[i]; heldTotal += held[i]; }
    if (fabs(shownTotal - heldTotal) >= config.stepG) {
      step.pre = heldTotal;
      for (int i = 0; i < N; ++i) { step.preCell[i] = held[i]; held[i] = measured[i] - offset[i]; }
      if (fabs(shownTotal) < config.zeroSnapG) zeroAll(measured, now);  // everything taken off
      else holdTotal(rounded(shownTotal), measured, step.preCell);
      step.post = 0;
      for (int i = 0; i < N; ++i) { step.postCell[i] = held[i]; step.post += held[i]; }
      lastAnchor = now;
      return true;
    }
    if (uint32_t(now - lastAnchor) >= config.anchorMs) {
      // Re-tare: the residual is drift. A large per-cell shift with an unchanged total is
      // the item being moved on the tray: keep it as the new split instead of absorbing it.
      bool slid = false; double before[N];
      for (int i = 0; i < N; ++i) before[i] = held[i];
      for (int i = 0; i < N; ++i) {
        const double shown = measured[i] - offset[i];
        if (fabs(shown - held[i]) < config.stepG) offset[i] = measured[i] - held[i];
        else { held[i] = shown; slid = true; }
      }
      if (fabs(heldTotal) < config.zeroSnapG) zeroAll(measured, now);
      else if (slid) holdTotal(heldTotal, measured, before);  // a slide moves weight, never changes it
      lastAnchor = now;
    }
    return false;
  }
  // Drift-corrected live reading of one cell (equals the held value right after a re-tare).
  double shown(int i, double measured) const { return seeded ? measured - offset[i] : measured; }
  double heldCell(int i) const { return held[i]; }
  double heldTotal() const { double total = 0; for (double value : held) total += value; return total; }
  const Step& lastStep() const { return step; }

private:
  double rounded(double grams) const { return config.roundG > 0 ? round(grams / config.roundG) * config.roundG : grams; }
  // Make the held cells add up to total: the cell that changed most absorbs the remainder,
  // and its offset follows so the live reading equals the held one.
  void holdTotal(double total, const double (&measured)[N], const double (&before)[N]) {
    int largest = 0; double sum = 0;
    for (int i = 0; i < N; ++i) {
      sum += held[i];
      if (fabs(held[i] - before[i]) > fabs(held[largest] - before[largest])) largest = i;
    }
    held[largest] += total - sum;
    offset[largest] = measured[largest] - held[largest];
  }
  void zeroAll(const double (&measured)[N], uint32_t now) {
    for (int i = 0; i < N; ++i) { offset[i] = measured[i]; held[i] = 0; }
    lastAnchor = now;
  }
  Config config;
  double offset[N] = {}, held[N] = {};
  Step step;
  bool seeded = false;
  uint32_t stableSince = 0, lastAnchor = 0;
};
