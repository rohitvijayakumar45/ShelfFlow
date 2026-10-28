"""Load-cell monitor: raw counts, grams and drift for each cell, live in a browser.

Reads the WEIGH lines the USB bridge already logs (data/integration/usb/rfid-diagnostics.jsonl), so it never
opens the controller's serial port and can run next to the bridge.

  python -m tools.cell_monitor            # http://127.0.0.1:8770
  python -m tools.cell_monitor --lan      # also reachable from phones on the same network
"""
import argparse
import json
import os
import re
import statistics
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
LOG = Path(os.getenv("USB_EVENT_ROOT", ROOT / "data/integration/usb")) / "rfid-diagnostics.jsonl"
CELL_FIELD = re.compile(r"^(raw|ema|zero|gain|net)_([A-Z])$")


def read_tail(path, max_bytes):
    """Last max_bytes of the log as whole lines (the log can be large)."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        data = f.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[1:] if size > max_bytes else lines


def samples(seconds):
    """WEIGH readings from the last `seconds`: [{t, total, shown, cells: {A: {raw, ema, zero, gain, net}}}]."""
    now = time.time()
    out = []
    # About 330 bytes per WEIGH line plus RFID diagnostics: 1.5 kB per second is plenty.
    for line in read_tail(LOG, int(min(seconds, 3600) * 1500) + 200_000):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        text, t = entry.get("line", ""), entry.get("received_s", 0)
        if not text.startswith("WEIGH ") or t < now - seconds:
            continue
        fields = dict(part.split("=", 1) for part in text.split()[1:] if "=" in part)
        cells = {}
        for key, value in fields.items():
            m = CELL_FIELD.match(key)
            if m:
                try:
                    cells.setdefault(m.group(2), {})[m.group(1)] = float(value)
                except ValueError:
                    pass
        try:
            out.append({"t": t, "total": float(fields.get("calibrated_g", "nan")), "shown": float(fields.get("shown_g", "nan")), "cells": cells})
        except ValueError:
            continue
    return out


def slope_per_min(points):
    """Least-squares trend of (t, value) points, in units per minute."""
    if len(points) < 2:
        return 0.0
    t0 = points[0][0]
    xs = [(t - t0) / 60 for t, _ in points]
    ys = [v for _, v in points]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else 0.0


def quantile(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))]


def drift(points, step=None):
    """Noise and drift of one series of (t, value).

    jitter: median |change| between consecutive readings (about one per second), insensitive to steps.
    steps:  changes bigger than `step` (an item placed or lifted, or a glitch); they are not drift.
    drift:  trend per minute since the last step, i.e. while nothing was placed or lifted.
    scale:  1st..99th percentile, so one wild reading cannot flatten the graph.
    """
    if len(points) < 3:
        return None
    values = [v for _, v in points]
    changes = [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
    jitter = statistics.median(changes)
    threshold = step if step is not None else max(10 * jitter, 1.0)
    breaks = [i for i in range(1, len(values)) if abs(values[i] - values[i - 1]) > threshold]
    quiet = points[breaks[-1]:] if breaks else points
    quiet_values = [v for _, v in quiet]
    return {"last": values[-1], "min": min(values), "max": max(values), "range": max(values) - min(values),
            "sd": statistics.pstdev(values), "jitter": jitter, "steps": len(breaks), "step_threshold": threshold,
            "quiet_s": quiet[-1][0] - quiet[0][0], "quiet_sd": statistics.pstdev(quiet_values) if len(quiet_values) > 1 else 0.0,
            "drift_per_min": slope_per_min(quiet) if len(quiet) >= 10 else None,
            "scale": [quantile(values, 0.01), quantile(values, 0.99)]}


def summary(seconds):
    rows = samples(seconds)
    names = sorted({name for r in rows for name in r["cells"]})
    cells = {}
    for name in names:
        series = [(r["t"], r["cells"][name]) for r in rows if name in r["cells"]]
        last = series[-1][1] if series else {}
        gain = abs(last.get("gain") or 0) or None
        cells[name] = {
            "latest": last,
            "net": drift([(t, c["net"]) for t, c in series if "net" in c], step=5.0),
            # The same 5 g step expressed in raw counts for this cell's gain.
            "raw": drift([(t, c["raw"]) for t, c in series if "raw" in c], step=5.0 / gain if gain else None),
            "points": [[round(t, 2), c.get("raw"), round(c.get("net", float("nan")), 2)] for t, c in series],
        }
    total = [(r["t"], r["shown"]) for r in rows if r["shown"] == r["shown"]]
    return {"now": time.time(), "seconds": seconds, "count": len(rows), "age_s": (time.time() - rows[-1]["t"]) if rows else None,
            "cells": cells, "total": {"drift": drift(total, step=5.0), "points": [[round(t, 2), round(v, 2)] for t, v in total]}}


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Load cell monitor</title>
<style>
:root{--paper:#f7f5ef;--surface:#fff;--surface-2:#f1eee6;--ink:#1f2a26;--ink-2:#55625c;--line:#e3ded2;--forest:#0f5c47;--ok:#1e7b4f;--warn:#a86a07;--bad:#b42318;--a:#2453c7;--c:#c2410c;--total:#0f5c47;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--paper:#121715;--surface:#1a211e;--surface-2:#222b27;--ink:#ecf1ee;--ink-2:#b3bfb8;--line:#2c3632;--forest:#4fb892;--ok:#4fb892;--warn:#f2c777;--bad:#f07a6c;--a:#8fb0ff;--c:#ffa26b;--total:#4fb892;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.45 "Segoe UI",system-ui,sans-serif}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;padding:14px 20px;background:var(--surface);border-bottom:1px solid var(--line)}
h1{font-size:20px;margin:0}main{max-width:1200px;margin:0 auto;padding:16px;display:flex;flex-direction:column;gap:16px}
.seg{display:inline-flex;gap:4px;padding:4px;background:var(--surface-2);border-radius:12px}.seg button{border:0;background:transparent;color:var(--ink-2);font:inherit;font-weight:600;padding:6px 12px;border-radius:9px;cursor:pointer;min-height:36px}.seg button.on{background:var(--surface);color:var(--ink);box-shadow:0 1px 3px rgba(0,0,0,.12)}
.status{font-size:13px;color:var(--ink-2)}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;background:var(--ok)}.dot.stale{background:var(--bad)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:16px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:16px;display:flex;flex-direction:column;gap:12px}
.card h2{font-size:17px;margin:0;display:flex;align-items:center;gap:8px}.swatch{width:12px;height:12px;border-radius:3px}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.stat{background:var(--surface-2);border-radius:10px;padding:8px 10px}.stat span{display:block;font-size:12px;color:var(--ink-2);font-weight:600}.stat b{font-size:18px;font-variant-numeric:tabular-nums}
.verdict{font-size:13px;font-weight:600;padding:4px 10px;border-radius:999px;margin-left:auto}.verdict.ok{background:color-mix(in srgb,var(--ok) 15%,transparent);color:var(--ok)}.verdict.warn{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn)}.verdict.bad{background:color-mix(in srgb,var(--bad) 15%,transparent);color:var(--bad)}
canvas{width:100%;height:150px;display:block}.label{font-size:12px;color:var(--ink-2);font-weight:600;margin-bottom:2px}
.raw{font-family:Consolas,ui-monospace,monospace;font-size:12px;color:var(--ink-2);word-break:break-all}
@media (max-width:600px){.stats{grid-template-columns:repeat(2,minmax(0,1fr))}header{padding:12px 16px}}
</style></head><body>
<header><h1>Load cell monitor</h1>
<div class="seg" id="win"><button data-s="60">1 min</button><button data-s="300" class="on">5 min</button><button data-s="900">15 min</button><button data-s="3600">1 h</button></div>
<div class="status" id="status">Connecting…</div></header>
<main><div class="grid" id="cells"></div>
<section class="card"><h2><span class="swatch" style="background:var(--total)"></span>Tray total (as on the shelf screen)<span class="verdict" id="totalVerdict"></span></h2>
<div class="stats" id="totalStats"></div><div><div class="label">Grams</div><canvas id="totalChart"></canvas></div></section>
<p class="status">One reading per second. <b>Jitter</b>: typical change from one reading to the next (under 0.2 g is quiet, over 0.5 g is noisy). <b>Steps</b>: jumps over 5 g, such as an item placed or lifted, or a glitch; they are not counted as drift. <b>Drift (still)</b>: trend per minute since the last step, while nothing moved. <b>Still for</b>: how long since the last step, and the spread over that time. Graphs show the chosen window ending now; red dots are readings outside the scale.</p></main>
<script>
const colors={A:"--a",C:"--c",B:"--warn",D:"--forest"};let seconds=300;
const css=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const fmt=(v,d=1)=>v==null||isNaN(v)?"—":Number(v).toFixed(d);
function verdict(j){return j==null?["","" ]:j<0.2?["ok","Quiet"]:j<0.5?["warn","Some noise"]:["bad","Noisy"]}
function chart(canvas,pts,color,d,now){const r=window.devicePixelRatio||1,w=canvas.clientWidth,h=canvas.clientHeight;canvas.width=w*r;canvas.height=h*r;const g=canvas.getContext("2d");g.scale(r,r);g.clearRect(0,0,w,h);
 g.font="11px system-ui";const ink2=css("--ink-2");
 const good=pts.filter(p=>p[1]!=null&&!isNaN(p[1]));if(good.length<2||!d){g.fillStyle=ink2;g.fillText("Waiting for readings…",8,20);return}
 // Fixed window ending now: the graph scrolls left in real time; a pause shows as empty space on the right.
 const t1=now,t0=now-seconds;let [lo,hi]=d.scale;if(hi-lo<1){const m=(hi+lo)/2;lo=m-0.5;hi=m+0.5}const pad=(hi-lo)*0.12;lo-=pad;hi+=pad;
 const L=58,R=w-8,T=8,B=h-18,x=t=>L+(t-t0)/(t1-t0)*(R-L),y=v=>B-(Math.min(hi,Math.max(lo,v))-lo)/(hi-lo)*(B-T);
 g.strokeStyle=css("--line");g.fillStyle=ink2;g.lineWidth=1;const span=hi-lo,dec=span<5?2:span<50?1:0;
 for(let i=0;i<=3;i++){const v=lo+span*i/3,yy=y(v);g.beginPath();g.moveTo(L,yy);g.lineTo(R,yy);g.stroke();g.fillText(Math.abs(v)>=10000?(v/1000).toFixed(1)+"k":v.toFixed(dec),2,yy+4)}
 for(const f of [0,0.5,1]){const tt=t0+f*(t1-t0),xx=x(tt);g.fillText(f===1?"now":"-"+Math.round((t1-tt))+" s",Math.min(R-26,Math.max(L,xx-10)),h-4)}
 g.strokeStyle=color;g.lineWidth=1.6;g.beginPath();let prev=null,off=0;
 for(const p of good){if(p[0]<t0)continue;const xx=x(p[0]),yy=y(p[1]);if(p[1]<lo||p[1]>hi)off++;
  if(prev&&p[0]-prev<=3)g.lineTo(xx,yy);else g.moveTo(xx,yy);prev=p[0]}g.stroke();
 // Readings outside the scale are pinned to the edge in red, never hidden.
 g.fillStyle=css("--bad");for(const p of good){if(p[0]<t0||(p[1]>=lo&&p[1]<=hi))continue;g.beginPath();g.arc(x(p[0]),y(p[1]),3,0,7);g.fill()}
 if(off){g.fillText(off+" reading"+(off>1?"s":"")+" off scale (red)",L+4,T+10)}}
function stats(el,d,unit){const g=unit==="g",n=v=>fmt(v,g?1:0);
 const drift=d&&d.drift_per_min!=null?(d.drift_per_min>=0?"+":"")+fmt(d.drift_per_min,g?2:0)+" "+unit+"/min":"—";
 el.innerHTML=[["Now",d&&n(d.last)+(g?" g":"")],["Jitter",d&&fmt(d.jitter,g?2:0)+" "+unit],["Drift (still)",drift],
  ["Steps",d&&d.steps+(d.steps===1?" change":" changes")],["Still for",d&&Math.round(d.quiet_s)+" s · ±"+fmt(d.quiet_sd,g?1:0)],["Min / max",d&&n(d.min)+" / "+n(d.max)]]
  .map(([k,v])=>`<div class="stat"><span>${k}</span><b>${v??"—"}</b></div>`).join("")}
function render(data){const box=document.getElementById("cells");const names=Object.keys(data.cells);
 if(box.dataset.names!==names.join()){box.dataset.names=names.join();box.innerHTML=names.map(n=>`<section class="card" id="cell-${n}"><h2><span class="swatch" style="background:var(${colors[n]||"--forest"})"></span>Cell ${n}<span class="verdict"></span></h2><div class="stats g"></div><div><div class="label">Grams (net)</div><canvas class="cg"></canvas></div><div><div class="label">Raw counts</div><canvas class="cr"></canvas></div><div class="stats r"></div><div class="raw"></div></section>`).join("")}
 for(const n of names){const c=data.cells[n],el=document.getElementById("cell-"+n),col=css(colors[n]||"--forest");const [cls,txt]=verdict(c.net&&c.net.jitter);const v=el.querySelector(".verdict");v.className="verdict "+cls;v.textContent=txt;
  stats(el.querySelector(".stats.g"),c.net,"g");stats(el.querySelector(".stats.r"),c.raw,"counts");chart(el.querySelector(".cg"),c.points.map(p=>[p[0],p[2]]),col,c.net,data.now);chart(el.querySelector(".cr"),c.points.map(p=>[p[0],p[1]]),col,c.raw,data.now);
  const l=c.latest;el.querySelector(".raw").textContent=`raw ${fmt(l.raw,0)}  ema ${fmt(l.ema,1)}  zero ${fmt(l.zero,1)}  gain ${fmt(l.gain,6)} g/count`}
 const t=data.total,[tc,tt]=verdict(t.drift&&t.drift.jitter),tv=document.getElementById("totalVerdict");tv.className="verdict "+tc;tv.textContent=tt;stats(document.getElementById("totalStats"),t.drift,"g");chart(document.getElementById("totalChart"),t.points,css("--total"),t.drift,data.now);
 const st=document.getElementById("status");const stale=data.age_s==null||data.age_s>5;st.innerHTML=`<span class="dot ${stale?"stale":""}"></span>${stale?(data.age_s==null?"No readings in this window — is the USB bridge running?":"Last reading "+Math.round(data.age_s)+" s ago — controller or bridge stopped?"):data.count+" readings · live"}`}
async function tick(){try{const r=await fetch("/data?seconds="+seconds,{cache:"no-store"});render(await r.json())}catch(e){document.getElementById("status").textContent="Monitor not reachable"}setTimeout(tick,1000)}
document.querySelectorAll("#win button").forEach(b=>b.onclick=()=>{seconds=+b.dataset.s;document.querySelectorAll("#win button").forEach(x=>x.classList.toggle("on",x===b))});
tick();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            body, kind = PAGE.encode(), "text/html; charset=utf-8"
        elif url.path == "/data":
            try:
                seconds = max(10.0, min(3600.0, float(parse_qs(url.query).get("seconds", ["300"])[0])))
            except ValueError:
                seconds = 300.0
            try:
                body = json.dumps(summary(seconds)).encode()
            except FileNotFoundError:
                body = json.dumps({"now": time.time(), "seconds": seconds, "count": 0, "age_s": None, "cells": {}, "total": {"drift": None, "points": []}}).encode()
            kind = "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--lan", action="store_true", help="listen on all interfaces (phones on the same Wi-Fi)")
    args = parser.parse_args()
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    print(f"Load cell monitor on http://{'<this laptop>' if args.lan else '127.0.0.1'}:{args.port} (reading {LOG})", flush=True)
    ThreadingHTTPServer((host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
