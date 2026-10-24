"""Check the three HX711 load-cell channels using the shelf controller firmware already on the ESP.

  python -m tools.loadcell_check --port COM3

1. Idle (10 s, tray untouched): each cell must report, change slightly (live) and not be saturated.
2. Press test: press over cell A, then B, then C when asked. The pressed cell must move the most.
Stop the USB bridge first (it holds the COM port).
"""
import argparse
import re
import statistics
import time
import serial

DIAG = re.compile(r"DIAG raw A=(-?\d+) B=(-?\d+) C=(-?\d+).*?(flags=(\S+))?\s+rc522")
SATURATED = 8_000_000

def read(port, seconds):
    rows, flags = [], set()
    end = time.time() + seconds
    while time.time() < end:
        line = port.readline().decode("ascii", errors="replace")
        m = DIAG.search(line)
        if m:
            rows.append(tuple(int(m.group(i)) for i in (1, 2, 3)))
            if m.group(5):
                flags.update(m.group(5).split(","))
    return rows, flags

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", default="COM3"); p.add_argument("--no-press", action="store_true")
    a = p.parse_args()
    port = serial.Serial(port=None, baudrate=115200, timeout=0.3)
    port.dtr = port.rts = False
    port.port = a.port
    port.open()
    time.sleep(1)
    port.reset_input_buffer()
    print("Idle check: don't touch the tray for 10 s ...", flush=True)
    rows, flags = read(port, 10)
    if len(rows) < 5:
        raise SystemExit("No DIAG lines: is the shelf firmware running and the bridge stopped?")
    ok = True
    idle = {}
    for i, name in enumerate("ABC"):
        values = [r[i] for r in rows]
        mean, sd = statistics.mean(values), statistics.pstdev(values)
        idle[name] = mean
        problems = []
        if any(f.endswith("_" + name) and f.startswith(("DISCONNECTED", "STUCK", "SATURATED")) for f in flags):
            problems.append(",".join(sorted(f for f in flags if f.endswith("_" + name))))
        if abs(mean) >= SATURATED:
            problems.append("saturated (A+/A- or E+/E- open or swapped)")
        if sd == 0:
            problems.append("value never changes (not reading)")
        ok &= not problems
        print(f"  cell {name}: raw {mean:>10.0f}  noise {sd:7.1f} counts  {'OK' if not problems else 'FAIL: ' + '; '.join(problems)}")
    if not a.no_press:
        for name in "ABC":
            input(f"\nPress firmly over cell {name} and keep pressing, then hit Enter ...")
            rows, _ = read(port, 4)
            if not rows:
                print("  no data"); ok = False; continue
            moves = {n: abs(statistics.mean(r[i] for r in rows) - idle[n]) for i, n in enumerate("ABC")}
            best = max(moves, key=moves.get)
            good = best == name and moves[name] > 2000
            ok &= good
            print("  change: " + "  ".join(f"{n}={moves[n]:.0f}" for n in "ABC") +
                  f"  -> {'OK' if good else 'FAIL: cell ' + best + ' moved most' if moves[best] > 2000 else 'FAIL: nothing moved'}")
            input("  Release, then hit Enter ...")
            time.sleep(1)
    port.close()
    print("\nALL THREE CELLS OK" if ok else "\nSOME CHECKS FAILED: see the lines above")

if __name__ == "__main__":
    main()
