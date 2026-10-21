"""Compare RFID RF settings at a fixed tag distance (read rate per setting, per reader).

Hold ONE tag flat and still at a fixed distance above one reader (use a stack of cards as a spacer),
ideally the distance where reads were unreliable. For each reader the script runs every preset in
turn with that reader's field continuously on, and counts successful reads per second.

  python -m tools.rfid_range_test --port COM3 --readers r1,r2 --seconds 6 --pause 8

Stop the USB bridge first (it owns the port). Frame-level read counts only; the result depends on
the tag, the distance, and what is near the antenna, so repeat at 2-3 distances.
"""
import argparse
import json
import re
import time
import serial

PRESETS = [
    ("chip defaults, 1 try, 10 ms", ["rfpower 0", "rfthr 84", "rfsettle 10", "rftries 1"]),
    ("carrier max, 2 tries, 6 ms", ["rfpower 1", "rfthr 84", "rfsettle 6", "rftries 2"]),
    ("carrier+mod max, 2 tries, 6 ms", ["rfpower 2", "rfthr 84", "rfsettle 6", "rftries 2"]),
    ("carrier+mod max, thr 0x64", ["rfpower 2", "rfthr 64", "rfsettle 6", "rftries 2"]),
    ("carrier+mod max, thr 0x44", ["rfpower 2", "rfthr 44", "rfsettle 6", "rftries 2"]),
]
EXTENDED = [
    ("max drive, 4 tries, 6 ms", ["rfpower 2", "rfthr 84", "rfsettle 6", "rftries 4"]),
    ("max drive, 4 tries, 12 ms", ["rfpower 2", "rfthr 84", "rfsettle 12", "rftries 4"]),
    ("max drive, 4 tries, 20 ms", ["rfpower 2", "rfthr 84", "rfsettle 20", "rftries 4"]),
    ("max drive, 4 tries, 12 ms, thr 0x74", ["rfpower 2", "rfthr 74", "rfsettle 12", "rftries 4"]),
]
SCANS = re.compile(r"scans=(\d+)/(\d+)")

def send(port, text, wait=0.35):
    port.write((text + "\n").encode())
    time.sleep(wait)

def counts(port):
    last = None
    for line in port.read(8192).decode("ascii", errors="replace").splitlines():
        m = SCANS.search(line)
        if m:
            last = (int(m.group(1)), int(m.group(2)))
    return last

def measure(port, index, seconds):
    port.reset_input_buffer()
    first = None
    end = time.time() + seconds + 1.2
    last = None
    while time.time() < end:
        got = counts(port)
        if got:
            last = got
            first = first or got
        time.sleep(0.2)
    return (last[index] - first[index]) / max(1.0, seconds) if first and last else None

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", default="COM3"); p.add_argument("--readers", default="r1,r2")
    p.add_argument("--seconds", type=float, default=6); p.add_argument("--pause", type=float, default=8)
    p.add_argument("--start-delay", type=float, default=0, help="seconds to wait before the first measurement"); p.add_argument("--extended", action="store_true", help="also try longer settle and more wake tries"); p.add_argument("--label", default=""); p.add_argument("--output", default="reports/rfid-range-test.json")
    a = p.parse_args()
    results = []
    with serial.Serial(a.port, 115200, timeout=0.2) as port:
        time.sleep(0.5)
        if a.start_delay:
            print(f"--- place the tag over {a.readers.split(',')[0].upper()}: measuring starts in {a.start_delay:.0f} s ---", flush=True)
            time.sleep(a.start_delay)
        for n, reader in enumerate(a.readers.split(",")):
            if n:
                print(f"--- move the tag to {reader.upper()} now ({a.pause:.0f} s) ---", flush=True)
                time.sleep(a.pause)
            send(port, reader)
            for label, commands in PRESETS + (EXTENDED if a.extended else []):
                for command in commands:
                    send(port, command, 0.15)
                time.sleep(0.4)
                rate = measure(port, 0 if reader == "r1" else 1, a.seconds)
                results.append({"reader": reader, "preset": label, "reads_per_s": None if rate is None else round(rate, 1)})
                print(f"{reader.upper()}  {label:34s} {results[-1]['reads_per_s']} reads/s", flush=True)
        send(port, "rfpower 1"); send(port, "rfthr 84"); send(port, "rfsettle 6"); send(port, "rftries 2"); send(port, "both")
    with open(a.output, "w") as f:
        json.dump({"label": a.label, "seconds_per_preset": a.seconds, "results": results,
                   "note": "Successful reads per second with a fixed tag; frame-level, not accuracy."}, f, indent=1)

if __name__ == "__main__":
    main()
