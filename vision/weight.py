"""Explicit calibration and stable baseline detector; never auto-tares stock."""
from collections import deque
from dataclasses import dataclass
from statistics import median, pstdev

@dataclass
class Calibration:
    zero_raw: float
    counts_per_g: float
    version: str

    @classmethod
    def known_mass(cls, zero, loaded, grams, version):
        if grams <= 0 or loaded == zero:
            raise ValueError("Nonzero calibration span and positive known mass required")
        return cls(zero, (loaded - zero) / grams, version)

    def grams(self, raw):
        return (raw - self.zero_raw) / self.counts_per_g

class WeightProcessor:
    def __init__(self, calibration, window=12, settle_ms=600, stable_noise_g=2, hysteresis_g=8, max_g=5000):
        self.calibration = calibration
        self.samples = deque(maxlen=window)
        self.settle_ms, self.stable_noise_g = settle_ms, stable_noise_g
        self.hysteresis_g, self.max_g = hysteresis_g, max_g
        self.baseline = None
        self.last_time = None
        self.last_raw = None
        self.same_since = None
        self.flags = set()
        self.stable_since = None
        self.drift_origin = None

    def tare(self, raw, empty_verified):
        if not empty_verified:
            raise ValueError("Empty tray must be explicitly verified")
        self.calibration.zero_raw = raw
        self.samples.clear()
        self.baseline = None

    def feed(self, raw, monotonic_ms):
        flags = set()
        if self.last_time is not None and monotonic_ms - self.last_time > 1000:
            flags.add("DISCONNECTED")
        if self.last_time is not None and monotonic_ms <= self.last_time:
            return {"health": ["OUT_OF_ORDER"], "event": None}
        self.last_time = monotonic_ms
        if raw is None:
            self.samples.clear()
            return {"health": ["DISCONNECTED"], "event": None}
        if raw == self.last_raw:
            if self.same_since is not None and monotonic_ms - self.same_since > 10000:
                flags.add("STUCK_READING")
        else:
            self.same_since = monotonic_ms
        self.last_raw = raw
        value = self.calibration.grams(raw)
        if abs(value) > self.max_g:
            flags.add("OVERLOAD")
        self.samples.append(value)
        filtered, noise = median(self.samples), pstdev(self.samples)
        stable = len(self.samples) == self.samples.maxlen and noise <= self.stable_noise_g
        event = None
        if not stable:
            self.stable_since = None
        elif self.stable_since is None:
            self.stable_since = monotonic_ms
        elif monotonic_ms - self.stable_since >= self.settle_ms and not flags:
            if self.baseline is None:
                self.baseline = filtered
                self.drift_origin = (monotonic_ms, filtered)
            elif abs(filtered - self.baseline) >= self.hysteresis_g:
                event = {"pre_g": self.baseline, "post_g": filtered, "noise_g": noise, "settled": True}
                self.baseline = filtered
                self.drift_origin = (monotonic_ms, filtered)
            elif self.drift_origin and monotonic_ms - self.drift_origin[0] >= 60000 and abs(filtered - self.drift_origin[1]) > self.stable_noise_g:
                flags.add("DRIFT")
        return {"raw": raw, "filtered": filtered, "noise_g": noise, "stable": stable, "health": sorted(flags), "event": event}
