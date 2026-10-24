"""Perfboard migration guide (fast layout): one PDF with to-scale schematics, every connection and the build steps.

The layout data below drives the schematic, the connection tables and the short-circuit check, so they always agree.

  python -m tools.perfboard_guide        ->  docs/perfboard/ShelfFlow-perfboard-guide.pdf (+ .html)

Grid: C1..C55 left to right, R1..R37 top to bottom, viewed from the COMPONENT side. Pitch 2.54 mm.
"""
import html
import shutil
import subprocess
from pathlib import Path

COLS, ROWS, PITCH_MM = 55, 37, 2.54
OUT = Path("docs/perfboard")

NETS = {  # colour, label
    "5V": ("#d62728", "5 V"), "GND": ("#333333", "GND"), "3V3": ("#ff7f0e", "3.3 V"),
    "HX": ("#2ca02c", "HX711 data/clock"), "SPI": ("#1f77b4", "RC522 bus / select"),
    "UI": ("#9467bd", "LCD and buttons"), "CAM": ("#e377c2", "camera"),
}
RAILS = {1: "5V", 2: "GND", 3: "3V3"}
RAIL_FROM = 3

def col(c, top, names):
    return {n: (c, top + i) for i, n in enumerate(names)}

HX_ROWS = ["GND", "DT", "SCK", "VCC"]
RC_ROWS = ["SDA", "SCK", "MOSI", "MISO", "IRQ", "GND", "RST", "3V3"]
UI_PINS = ["GND", "VCC", "SDA", "SCL", "PAGE", "ACTION"]

# part: (title, pins {name: (col,row)}, body (x0,y0,x1,y1) in pad units, kind)
PARTS = {
    "J0": ("5 V in", {"+": (1, 1), "-": (1, 3)}, (0.4, 0.4, 1.6, 3.6), "terminal"),
    "HXA": ("HX711 A", col(15, 8, HX_ROWS), (1.6, 5.4, 15.5, 13.6), "module"),
    "HXB": ("HX711 B", col(29, 8, HX_ROWS), (15.6, 5.4, 29.5, 13.6), "module"),
    "HXC": ("HX711 C", col(43, 8, HX_ROWS), (29.6, 5.4, 43.5, 13.6), "module"),
    "R1": ("RC522 R1", col(20, 22, RC_ROWS), (19.5, 21.5, 20.5, 29.5), "header"),
    "R2": ("RC522 R2", col(21, 22, RC_ROWS), (20.5, 21.5, 21.5, 29.5), "header"),
    "UI": ("UI header", {n: (38 + i, 28) for i, n in enumerate(UI_PINS)}, (37.5, 27.5, 43.5, 28.5), "header"),
    "C1": ("C1 100µF", {"-": (33, 2), "+": (33, 3)}, (32.6, 1.6, 33.4, 3.4), "cap"),
    "C2": ("C2 100µF", {"+": (53, 1), "-": (53, 2)}, (52.6, 0.6, 53.4, 2.4), "cap"),
    "C3": ("C3 10µF", {"-": (22, 27), "+": (22, 29)}, (21.6, 26.6, 22.4, 29.4), "cap"),
}
# Wire landing pads on the rails for the ESP power wires (not parts, just labelled pads).
LANDINGS = {
    "ESP1 VIN": ((32, 1), "5V"), "ESP1 GND": ((32, 2), "GND"), "ESP1 3V3": ((32, 3), "3V3"),
    "CAM 5V": ((52, 1), "5V"), "CAM GND": ((52, 2), "GND"),
}

def p(ref):
    part, pin = ref.split(":")
    return PARTS[part][1][pin]

# (net, a, b, kind, step) kind: bare = tinned wire / solder bridge (solder side); link = insulated wire.
WIRES = [
    ("5V", (1, 1), (RAIL_FROM, 1), "bare", "3.2"),
    ("GND", (1, 3), (RAIL_FROM, 2), "link", "3.2"),
    ("GND", (15, 8), (43, 8), "bare", "3.3"),            # HX711 GND bus along R8
    ("3V3", (15, 11), (43, 11), "bare", "3.4"),          # HX711 VCC bus along R11
    ("GND", (16, 8), (16, 2), "link", "3.5"),            # GND bus -> GND rail (crosses the 3V3 rail: insulated)
    ("3V3", (43, 11), (44, 11), "bare", "3.6"), ("3V3", (44, 11), (44, 3), "bare", "3.6"),  # VCC bus -> 3V3 rail
    *[("SPI" if r != 27 and r != 29 else ("GND" if r == 27 else "3V3"), (20, r), (21, r), "bare", "3.7") for r in (23, 24, 25, 27, 28, 29)],
    ("GND", (21, 27), (22, 27), "bare", "3.8"), ("3V3", (21, 29), (22, 29), "bare", "3.8"),
    ("GND", (20, 27), (18, 2), "link", "3.9"), ("3V3", (20, 29), (19, 3), "link", "3.10"),
    ("GND", (38, 28), (37, 2), "link", "3.11"), ("3V3", (39, 28), (40, 3), "link", "3.12"),
]
# Wires that leave the board to an ESP pin: (pad, net, ESP pin, step, colour hint)
ESP_WIRES = [
    ((32, 1), "5V", "ESP #1 VIN", "4.1", "red"), ((32, 2), "GND", "ESP #1 GND", "4.2", "black"),
    ((32, 3), "3V3", "ESP #1 3V3", "4.3", "orange"),
    ((15, 9), "HX", "ESP #1 D32", "4.4", "green"), ((15, 10), "HX", "ESP #1 D33", "4.5", "white"),
    ((29, 9), "HX", "ESP #1 D25", "4.6", "green"), ((29, 10), "HX", "ESP #1 D26", "4.7", "white"),
    ((43, 9), "HX", "ESP #1 D34", "4.8", "green"), ((43, 10), "HX", "ESP #1 D27", "4.9", "white"),
    ((20, 23), "SPI", "ESP #1 D18", "4.10", "blue"), ((20, 24), "SPI", "ESP #1 D23", "4.11", "blue"),
    ((20, 25), "SPI", "ESP #1 D19", "4.12", "blue"), ((20, 28), "SPI", "ESP #1 D22", "4.13", "grey"),
    ((20, 22), "SPI", "ESP #1 D21", "4.14", "yellow SS1"), ((21, 22), "SPI", "ESP #1 D4", "4.15", "yellow SS2"),
    ((40, 28), "UI", "ESP #1 RX2 (16)", "4.16", "purple"), ((41, 28), "UI", "ESP #1 TX2 (17)", "4.17", "purple"),
    ((42, 28), "UI", "ESP #1 D13", "4.18", "purple"), ((43, 28), "UI", "ESP #1 D14", "4.19", "purple"),
    ((52, 1), "5V", "ESP-CAM 5V", "4.20", "red"), ((52, 2), "GND", "ESP-CAM GND", "4.21", "black"),
]

# ---------------------------------------------------------------- checks
def check():
    used = {}
    for part, (_, pins, _, _) in PARTS.items():
        for pin, xy in pins.items():
            assert 1 <= xy[0] <= COLS and 1 <= xy[1] <= ROWS
            assert xy not in used, f"{xy} used by {used[xy]} and {part}:{pin}"
            if xy[1] in RAILS and xy[0] >= RAIL_FROM and part not in ("C1", "C2"):
                raise AssertionError(f"{part}:{pin} on a rail row")
            used[xy] = f"{part}:{pin}"
    net_of = {}
    for (c, r), net in [(xy, net) for xy, net in ((v[0], v[1]) for v in LANDINGS.values())]:
        net_of[(c, r)] = net
    for r, net in RAILS.items():
        for c in range(RAIL_FROM, COLS + 1):
            net_of.setdefault((c, r), net)
    for net, a, b, kind, _ in WIRES:
        if kind != "bare":
            continue
        (c0, r0), (c1, r1) = a, b
        assert c0 == c1 or r0 == r1, f"diagonal bare wire {a}-{b}"
        cells = [(c, r0) for c in range(min(c0, c1), max(c0, c1) + 1)] if r0 == r1 else [(c0, r) for r in range(min(r0, r1), max(r0, r1) + 1)]
        for xy in cells:
            other = net_of.get(xy)
            want = net if net in ("5V", "GND", "3V3") else None
            if other and want and other != want:
                raise AssertionError(f"bare {net} wire {a}-{b} touches {other} at C{xy[0]}R{xy[1]}")
            if xy in used and xy not in (a, b):
                owner = used[xy]
                if not (owner.startswith(("R1:", "R2:", "C3:")) or owner.split(":")[1] in ("GND", "VCC")):
                    raise AssertionError(f"bare {net} wire {a}-{b} runs over pin {owner}")
            if want:
                net_of[xy] = want
    return used

# ---------------------------------------------------------------- schematic (SVG, pad units)
def esc(s):
    return html.escape(str(s))

def schematic(width_mm, title):
    m_left, m_top, m_right, m_bottom = 2.6, 2.6, 2.6, 4.2
    vb_w, vb_h = COLS + m_left + m_right - 1, ROWS + m_top + m_bottom - 1
    ox, oy = m_left - 1, m_top - 1
    X = lambda c: c + ox
    Y = lambda r: r + oy
    height_mm = width_mm * vb_h / vb_w
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vb_w:.2f} {vb_h:.2f}" width="{width_mm:.1f}mm" height="{height_mm:.1f}mm" font-family="Segoe UI, Arial, sans-serif">',
         f'<title>{esc(title)}</title>',
         f'<rect x="{X(0.5)}" y="{Y(0.5)}" width="{COLS}" height="{ROWS}" fill="#f6efe0" stroke="#8a7a5a" stroke-width="0.12" rx="0.4"/>']
    for c in range(1, COLS + 1):
        for r in range(1, ROWS + 1):
            o.append(f'<circle cx="{X(c)}" cy="{Y(r)}" r="0.2" fill="#c9b48a"/>')
    for c in range(1, COLS + 1):
        if c == 1 or c % 5 == 0:
            o.append(f'<text x="{X(c)}" y="{Y(0.05)}" font-size="0.6" text-anchor="middle" fill="#555">C{c}</text>')
            o.append(f'<text x="{X(c)}" y="{Y(ROWS + 1.25)}" font-size="0.6" text-anchor="middle" fill="#555">C{c}</text>')
    for r in range(1, ROWS + 1):
        o.append(f'<text x="{X(0.25)}" y="{Y(r) + 0.2}" font-size="0.55" text-anchor="end" fill="#555">R{r}</text>')
        o.append(f'<text x="{X(COLS + 0.75)}" y="{Y(r) + 0.2}" font-size="0.55" fill="#555">R{r}</text>')
    # rails
    for r, net in RAILS.items():
        o.append(f'<line x1="{X(RAIL_FROM)}" y1="{Y(r)}" x2="{X(COLS)}" y2="{Y(r)}" stroke="{NETS[net][0]}" stroke-width="0.42" stroke-linecap="round"/>')
        o.append(f'<text x="{X(5.2)}" y="{Y(r) - 0.28}" font-size="0.55" fill="{NETS[net][0]}" font-weight="700">{NETS[net][1]} rail</text>')
    # part bodies
    for part, (title_, pins, (x0, y0, x1, y1), kind) in PARTS.items():
        fill = {"module": "#e8f0e6", "header": "#eef2f8", "cap": "#f8eef2", "terminal": "#eeeeee"}[kind]
        o.append(f'<rect x="{X(x0)}" y="{Y(y0)}" width="{x1 - x0}" height="{y1 - y0}" fill="{fill}" fill-opacity="0.75" stroke="#4a5a6a" stroke-width="0.08" stroke-dasharray="{"0.4 0.25" if kind == "module" else "none"}" rx="0.2"/>')
    # bare wires and bridges (drawn under the pins), then links
    for net, a, b, kind, _ in WIRES:
        colour = NETS[net][0]
        if kind == "bare":
            o.append(f'<line x1="{X(a[0])}" y1="{Y(a[1])}" x2="{X(b[0])}" y2="{Y(b[1])}" stroke="{colour}" stroke-width="0.34" stroke-linecap="round"/>')
    for net, a, b, kind, _ in WIRES:
        if kind == "link":
            colour = NETS[net][0]
            o.append(f'<path d="M{X(a[0])} {Y(a[1])} L{X(b[0])} {Y(b[1])}" stroke="{colour}" stroke-width="0.16" stroke-dasharray="0.5 0.25" fill="none"/>')
            o.append(f'<circle cx="{X(b[0])}" cy="{Y(b[1])}" r="0.22" fill="{colour}"/>')
    # pins and labels
    label_side = {"HXA": "end", "HXB": "end", "HXC": "end", "R1": "end", "R2": "start"}
    for part, (title_, pins, (x0, y0, x1, y1), kind) in PARTS.items():
        for pin, (c, r) in pins.items():
            o.append(f'<rect x="{X(c) - 0.33}" y="{Y(r) - 0.33}" width="0.66" height="0.66" fill="#fff" stroke="#111" stroke-width="0.07"/>')
            if kind == "cap":
                if part != "C3":
                    o.append(f'<text x="{X(c) + 0.45}" y="{Y(r) + 0.2}" font-size="0.6" font-weight="700">{esc(pin)}</text>')
            elif part == "UI":
                o.append(f'<text transform="translate({X(c) + 0.17},{Y(r) - 0.5}) rotate(-90)" font-size="0.48">{esc(pin)}</text>')
            elif part == "R1":
                pass  # R1 and R2 share row names: labelled once, right of the C3 capacitor
            elif part == "J0":
                o.append(f'<text x="{X(c) + 0.5}" y="{Y(r) + 0.2}" font-size="0.6" font-weight="700">{esc(pin)}</text>')
            else:
                side = label_side.get(part, "end")
                dx = -0.45 if side == "end" else (1.7 if part == "R2" else 0.45)
                o.append(f'<text x="{X(c) + dx}" y="{Y(r) + 0.18}" font-size="0.5" text-anchor="{side}">{esc(pin)}</text>')
        tx = (x0 + x1) / 2
        if part in ("R1", "R2"):
            o.append(f'<text x="{X(tx)}" y="{Y(y0) - 0.25}" font-size="0.55" font-weight="700" text-anchor="middle">{part}</text>')
        elif part in ("C1", "C2"):
            o.append(f'<text x="{X(x1) + 0.2}" y="{Y(y0) + 0.1}" font-size="0.5" font-weight="700">{esc(title_)}</text>')
        elif part == "C3":
            o.append(f'<text x="{X(x1) + 4.6}" y="{Y((y0 + y1) / 2) + 0.2}" font-size="0.5" font-weight="700">C3 10µF: − R27, + R29</text>')
        elif part == "J0":
            o.append(f'<text x="{X(0.5)}" y="{Y(4.5)}" font-size="0.5" font-weight="700">J0 5 V in</text>')
        elif part == "UI":
            o.append(f'<text x="{X(x0) - 0.3}" y="{Y(y0) + 0.75}" font-size="0.55" font-weight="700" text-anchor="end">UI header</text>')
        elif kind == "module":
            o.append(f'<text x="{X(tx)}" y="{Y(y0) + 0.9}" font-size="0.75" font-weight="700" text-anchor="middle">{esc(title_)}</text>')
            o.append(f'<text x="{X(tx)}" y="{Y(y0) + 1.8}" font-size="0.5" text-anchor="middle" fill="#2c5f2d">module body (header at right edge)</text>')
            o.append(f'<text x="{X(x0) + 0.3}" y="{Y(y1) - 0.5}" font-size="0.48" fill="#2c5f2d">load cell {title_[-1]}: E+ E− A− A+ at this end</text>')
        else:
            o.append(f'<text x="{X(tx)}" y="{Y(y0) - 0.25}" font-size="0.55" font-weight="700" text-anchor="middle">{esc(title_)}</text>')
    # ESP wire stubs
    stub = {}
    for (c, r), net, target, step, _ in ESP_WIRES:
        colour = NETS[net][0]
        if r <= 3:
            o.append(f'<circle cx="{X(c)}" cy="{Y(r)}" r="0.3" fill="#fff" stroke="#111" stroke-width="0.12"/>')
            continue
        if (c, r) in [(15, 9), (15, 10), (29, 9), (29, 10), (43, 9), (43, 10)]:
            x2, y2, anchor = X(c) + 1.0, Y(r), "start"
        elif c in (20,) and r in (23, 24, 25, 28, 22):
            x2, y2, anchor = X(c) - 3.0, Y(r), "end"
        elif (c, r) == (21, 22):
            x2, y2, anchor = X(c) + 1.4, Y(r) - 1.0, "start"
        else:  # UI header pins: down
            x2, y2, anchor = X(c), Y(r) + 1.8 + (c - 40) * 0.0, "middle"
        o.append(f'<line x1="{X(c)}" y1="{Y(r)}" x2="{x2}" y2="{y2}" stroke="{colour}" stroke-width="0.12"/>')
        o.append(f'<circle cx="{x2}" cy="{y2}" r="0.12" fill="{colour}"/>')
        if anchor == "middle":
            o.append(f'<text transform="translate({x2 + 0.15},{y2 + 0.3}) rotate(90)" font-size="0.42" fill="{colour}" font-weight="700">{esc(step)} → {esc(target)}</text>')
        else:
            off = 0.2 if anchor == "start" else -0.2
            o.append(f'<text x="{x2 + off}" y="{y2 + 0.18}" font-size="0.42" text-anchor="{anchor}" fill="{colour}" font-weight="700">{esc(step)} → {esc(target)}</text>')
    for (cx, txt) in ((31.6, "4.1 VIN · 4.2 GND · 4.3 3V3 → ESP #1 (circled pads, C32)"), (51.6, "4.20 5V · 4.21 GND → ESP-CAM (C52)")):
        o.append(f'<text x="{X(cx)}" y="{Y(4.35)}" font-size="0.5" font-weight="700" text-anchor="end" fill="#111">{esc(txt)}</text>')
    # legend
    ly = Y(ROWS + 2.3)
    lx = X(1)
    for net, (colour, label) in NETS.items():
        o.append(f'<line x1="{lx}" y1="{ly}" x2="{lx + 1.4}" y2="{ly}" stroke="{colour}" stroke-width="0.34"/>')
        o.append(f'<text x="{lx + 1.7}" y="{ly + 0.2}" font-size="0.55">{esc(label)}</text>')
        lx += 7.2
    ly2 = Y(ROWS + 3.4)
    o.append(f'<line x1="{X(1)}" y1="{ly2}" x2="{X(2.4)}" y2="{ly2}" stroke="#333" stroke-width="0.34"/><text x="{X(2.7)}" y="{ly2 + 0.2}" font-size="0.55">bare tinned wire / solder bridge (solder side)</text>')
    o.append(f'<line x1="{X(17)}" y1="{ly2}" x2="{X(18.4)}" y2="{ly2}" stroke="#333" stroke-width="0.16" stroke-dasharray="0.5 0.25"/><text x="{X(18.7)}" y="{ly2 + 0.2}" font-size="0.55">insulated wire (may cross)</text>')
    o.append(f'<text x="{X(29)}" y="{ly2 + 0.2}" font-size="0.55">n.n → ESP pin = wire to an ESP (step number in the guide)</text>')
    o.append("</svg>")
    return "\n".join(o)

# ---------------------------------------------------------------- document
CSS = """
@page { size: A4; margin: 14mm 13mm 14mm 13mm; }
@page land { size: A4 landscape; margin: 8mm; }
* { box-sizing: border-box; }
body { font-family: "Segoe UI", Arial, sans-serif; color: #1d2b24; font-size: 9.6pt; line-height: 1.45; }
h1 { font-size: 21pt; margin: 0 0 2mm; color: #175745; }
h2 { font-size: 14pt; color: #175745; border-bottom: 1.5px solid #175745; padding-bottom: 1mm; margin: 6mm 0 3mm; page-break-after: avoid; }
h3 { font-size: 11pt; margin: 4mm 0 2mm; page-break-after: avoid; }
p { margin: 1.5mm 0; }
table { border-collapse: collapse; width: 100%; margin: 2mm 0 3mm; page-break-inside: auto; }
tr { page-break-inside: avoid; }
th, td { border: 0.6px solid #b9c6bf; padding: 1.1mm 1.6mm; text-align: left; vertical-align: top; }
th { background: #e7efe9; font-weight: 600; }
code { font-family: Consolas, monospace; background: #f1f3f0; padding: 0 1mm; border-radius: 1mm; font-size: 8.8pt; }
.box { border: 1px solid #b9d7c5; background: #eaf4ee; border-radius: 2mm; padding: 2.5mm 4mm; margin: 3mm 0; }
.warn { border-color: #e8c98a; background: #fff6e5; }
.danger { border-color: #e5a0a0; background: #fdeeee; }
.check { border-color: #9fc3e6; background: #eef5fc; }
.land { page: land; page-break-before: always; text-align: center; }
.land h2 { text-align: left; margin-top: 0; }
.land .note { text-align: left; font-size: 8.5pt; }
.tick { width: 7mm; text-align: center; }
.pagebreak { page-break-before: always; }
.small { font-size: 8.4pt; color: #4a5a52; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 4mm; }
.mono { font-family: Consolas, monospace; white-space: pre; font-size: 8.6pt; background: #f6f8f5; border: 0.6px solid #cfd8d2; border-radius: 2mm; padding: 2mm 3mm; line-height: 1.3; }
.sw { display: inline-block; width: 9mm; height: 2.2mm; vertical-align: middle; margin-right: 1.5mm; border-radius: 1mm; }
"""

def table(head, rows, tick=False):
    h = "".join(f"<th>{c}</th>" for c in head) + ("<th class='tick'>S</th><th class='tick'>C</th>" if tick else "")
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + ("<td class='tick'>☐</td><td class='tick'>☐</td>" if tick else "") + "</tr>" for r in rows)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>"

def pad(xy):
    return f"<code>C{xy[0]}R{xy[1]}</code>"

def document():
    used = check()
    hx = lambda n, x: f"HX711 {n}: GND {pad((x, 8))} · DT {pad((x, 9))} · SCK {pad((x, 10))} · VCC {pad((x, 11))}"
    parts_rows = [
        ["J0", "2-pin screw terminal, 5 V in", f"+ {pad((1, 1))}, − {pad((1, 3))}"],
        ["HX711 A", "soldered by its 4-pin header (header on the module's right edge)", hx("A", 15).split(": ")[1]],
        ["HX711 B", "same", hx("B", 29).split(": ")[1]],
        ["HX711 C", "same", hx("C", 43).split(": ")[1]],
        ["R1 header", "8-pin male header, reader cable plugs in", f"column C20, SDA {pad((20, 22))} … 3V3 {pad((20, 29))}"],
        ["R2 header", "8-pin male header right next to R1", f"column C21, SDA {pad((21, 22))} … 3V3 {pad((21, 29))}"],
        ["UI header", "6-pin male header: GND, VCC, SDA, SCL, PAGE, ACTION", f"row R28, {pad((38, 28))} … {pad((43, 28))}"],
        ["C1", "100 µF, near the ESP #1 power wires", f"− {pad((33, 2))} (GND rail), + {pad((33, 3))} (3V3 rail)"],
        ["C2", "100 µF, near the camera power wires", f"+ {pad((53, 1))} (5V rail), − {pad((53, 2))} (GND rail)"],
        ["C3", "10 µF, at the RC522 block", f"− {pad((22, 27))}, + {pad((22, 29))}"],
    ]
    wire_rows = []
    for net, a, b, kind, step in WIRES:
        wire_rows.append([step, NETS[net][1], pad(a), pad(b), "bare (solder side)" if kind == "bare" else "insulated wire"])
    esp_rows = [[step, pad(xy), target, colour] for xy, net, target, step, colour in ESP_WIRES]

    svg_true = schematic(PITCH_MM * (COLS + 4.2), "Perfboard layout, true scale")
    svg_big = schematic(238, "Perfboard layout, enlarged")
    swatches = "".join(f"<span style='white-space:nowrap;margin-right:4mm'><span class='sw' style='background:{c}'></span>{l}</span>" for c, l in NETS.values())

    pinout = table(["#", "Left pin", "ESP #1 use", "Right pin", "ESP #1 use"], [
        ["1", "EN", "(ESP #2: to GND while flashing)", "D23", "<b>RC522 MOSI</b>"],
        ["2", "VP 36", "–", "D22", "<b>RC522 RST</b>"],
        ["3", "VN 39", "–", "TX0", "(ESP #2: to camera U0T)"],
        ["4", "<b>D34</b>", "<b>HX711 C DT</b>", "RX0", "(ESP #2: to camera U0R)"],
        ["5", "D35", "–", "<b>D21</b>", "<b>RC522 R1 SDA (select)</b>"],
        ["6", "<b>D32</b>", "<b>HX711 A DT</b>", "<b>D19</b>", "<b>RC522 MISO</b>"],
        ["7", "<b>D33</b>", "<b>HX711 A SCK</b>", "<b>D18</b>", "<b>RC522 SCK</b>"],
        ["8", "<b>D25</b>", "<b>HX711 B DT</b>", "D5", "–"],
        ["9", "<b>D26</b>", "<b>HX711 B SCK</b>", "<b>TX2 (17)</b>", "<b>LCD SCL</b>"],
        ["10", "<b>D27</b>", "<b>HX711 C SCK</b>", "<b>RX2 (16)</b>", "<b>LCD SDA</b>"],
        ["11", "<b>D14</b>", "<b>ACTION button</b>", "<b>D4</b>", "<b>RC522 R2 SDA (select)</b>"],
        ["12", "D12", "– (boot pin: never use)", "D2", "–"],
        ["13", "<b>D13</b>", "<b>PAGE button</b>", "D15", "–"],
        ["14", "<b>GND</b>", "<b>GND (one wire)</b>", "GND", "– (joined inside)"],
        ["15", "<b>VIN</b>", "<b>5 V in</b>", "<b>3V3</b>", "<b>3.3 V out → 3V3 rail</b>"],
    ])
    cam = table(["Row 1", "Use", "Row 2", "Use"], [
        ["<b>5V</b>", "<b>5V rail</b>", "3V3", "never connect"], ["<b>GND</b>", "<b>GND rail</b>", "IO16", "–"],
        ["IO12", "–", "<b>IO0</b>", "jumper to the GND pin below it, <b>only while flashing</b>"],
        ["IO13", "–", "GND", "(the jumper's other end)"], ["IO15", "–", "VCC", "never connect"],
        ["IO14", "–", "U0R", "ESP #2 RX0 (flashing only)"], ["IO2", "–", "U0T", "ESP #2 TX0 (flashing only)"],
        ["IO4", "–", "GND", "–"]])

    body = f"""
<h1>ShelfFlow perfboard build guide</h1>
<p class="small">Shelf controller (ESP32 #1), three HX711 load-cell amplifiers, two RC522 readers, LCD and buttons,
ESP32-CAM and its programmer (ESP32 #2). Board 55 × 37 pads, 2.54 mm pitch. Pins match the running firmware
(<code>firmware/shelf-controller</code>, build <code>devkitc-v4-usb-integration</code>).</p>

<div class="box"><b>How the layout saves work.</b> Pins that share a connection are placed next to each other, so one
straight bare wire (a <i>bus</i>) or a solder bridge joins them:
<ul>
<li><b>Three rails in neighbouring rows</b> (R1 5V, R2 GND, R3 3V3). Capacitors sit straight across two rails: no wires.</li>
<li><b>HX711s side by side</b> with their headers on the same rows: one bus powers all three GND pins (R8), one all three VCC pins (R11).</li>
<li><b>RC522 headers back to back</b> (C20 | C21): the six shared pins are joined by solder bridges.</li>
<li><b>One 6-pin UI header</b> for the LCD and both buttons, with a single GND pin.</li>
<li><b>ESPs are not on the board.</b> Female dupont ends go on the ESP pins; the other end is soldered to the pad shown.</li>
<li><b>Camera flashing never touches the board:</b> those links run ESP to ESP, only while flashing.</li>
</ul></div>

<div class="grid2">
<div><h3>Contents</h3>
<ol><li>Schematic, true scale (print at 100 %)</li><li>Schematic, enlarged</li><li>Parts and pad positions</li>
<li>Pinouts</li><li>All connections</li><li>Before you start</li><li>Build order with checks</li>
<li>Inspection before power</li><li>Power-up and tests</li><li>Recalibration</li><li>Troubleshooting</li><li>Tick list</li></ol></div>
<div><h3>Key</h3><p>{swatches}</p>
<p><b>Bare</b> = tinned copper wire or solder bridge on the solder side (thick line).<br>
<b>Insulated</b> = hook-up wire, allowed to cross other wires and rails (dashed line).<br>
<code>C20R23</code> = column 20, row 23, counted from the top-left corner, <b>component side up</b>.<br>
<b>n.n → ESP pin</b> = a wire to an ESP; n.n is its step number in this guide.</p></div>
</div>

<div class="box danger"><b>Never:</b> 5 V on an RC522, HX711 VCC or the 3V3 rail · power on while soldering ·
connect ESP-CAM <code>3V3</code>/<code>VCC</code> or ESP #2 <code>VIN</code> · bridge RC522 pin 1 (SDA) or pin 5 (IRQ)
between R1 and R2 · run bare wire along the HX711 DT (R9) or SCK (R10) rows.</div>

<section class="land"><h2>1. Schematic, true scale: print at 100 % ("Actual size"), lay it on the board</h2>
{svg_true}
<p class="note">Component side up. Rails, buses and bridges are on the solder side. If your print isn't exactly 2.54 mm
between dots, reprint with scaling switched off. The HX711 boards sit raised on their headers above the rail and bus
rows; the bare wires underneath stay clear.</p></section>

<section class="land"><h2>2. Schematic, enlarged for reading</h2>
{svg_big}</section>

<h2 class="pagebreak">3. Parts and pad positions</h2>
<p>All positions are on the component side. The HX711 header is on the module's short edge; its body extends to the
<b>left</b> of the header column, and its load-cell pads (E+, E−, A−, A+) are at the far left end.</p>
{table(["Part", "What", "Pads"], parts_rows)}
<div class="box warn"><b>Check your HX711 header order.</b> This layout assumes the silkscreen reads
<code>GND, DT, SCK, VCC</code> from top to bottom. If yours reads differently (for example <code>VCC, SCK, DT, GND</code>),
rotate all three modules the same way so GND lands on R8 and VCC on R11, or move the two buses to the rows where GND
and VCC actually are. The DT and SCK pads in the tables move with them.</div>
<div class="box warn"><b>RC522 cable order.</b> Both headers are pin 1 (SDA) at the top (R22) and 3V3 at the bottom
(R29), the same order as the pins on the reader module. Plug each reader cable so that the reader's SDA wire lands on
R22.</div>

<h2>4. Pinouts</h2>
<h3>ESP32 DEVKIT V1, 30-pin (ESP #1 shelf controller and ESP #2 programmer)</h3>
<p class="small">Component side up, antenna at the top, USB at the bottom; pins counted from the top. Some boards print
RX2/TX2 as D16/D17. Clone boards vary, so go by the printed labels. D34 is input-only, so it must be HX711 C DT.</p>
{pinout}
<h3>ESP32-CAM</h3>
<p class="small">Lens up, pins at the bottom.</p>
{cam}
<div class="grid2">
<div><h3>HX711</h3><p>Logic: <code>GND · DT · SCK · VCC</code> (read your silkscreen).<br>Cell side: <code>E+ · E− · A− · A+</code>; B−/B+ unused.</p></div>
<div><h3>RC522</h3><p><code>SDA · SCK · MOSI · MISO · IRQ · GND · RST · 3.3V</code><br><b>3.3 V only.</b></p></div>
</div>
<div class="grid2">
<div><h3>UI header (R28, left → right)</h3><p><code>GND · VCC · SDA · SCL · PAGE · ACTION</code><br>Both buttons' second legs join the GND wire in the cable.</p></div>
<div><h3>LCD I2C backpack</h3><p><code>GND · VCC · SDA · SCL</code>. VCC on 3V3, as on the breadboard now. Use 5 V only if you
measured that the backpack's SDA/SCL don't sit at 5 V.</p></div>
</div>

<h2 class="pagebreak">5. All connections</h2>
<h3>On the board (rails, buses, bridges, links)</h3>
{table(["Step", "Net", "From", "To", "Type"], wire_rows)}
<p class="small">Step 3.7 = the six R1↔R2 bridges on rows R23, R24, R25, R27, R28 and R29. Rails: bare wire along R1, R2
and R3 from C3 to C55.</p>
<h3>Wires to the ESPs (female dupont on the ESP end)</h3>
{table(["Step", "Board pad", "ESP pin", "Colour"], esp_rows)}
<h3>Load cells (to each HX711's far end, copy the breadboard exactly)</h3>
{table(["Cell wire (typical colour)", "HX711 pad"], [["red", "E+"], ["black", "E−"], ["white", "A−"], ["green", "A+"]])}
<p class="small">Cell A = left support, B = middle, C = right. A cell must stay on its own HX711: the calibration depends on it.</p>
<h3>Camera flashing only (off the board, female–female duponts)</h3>
{table(["From", "To"], [["ESP-CAM IO0", "ESP-CAM GND (the neighbouring pin)"], ["ESP #2 EN", "ESP #2 GND"],
                        ["ESP #2 TX0", "ESP-CAM U0T"], ["ESP #2 RX0", "ESP-CAM U0R"], ["ESP #2 GND", "ESP-CAM GND"]])}
<p>Remove all of them after flashing. Never connect ESP #2 VIN.</p>

<h2 class="pagebreak">6. Before you start (30 min)</h2>
<ol>
<li><b>Baseline on the breadboard.</b> Stop the USB bridge, then run <code>.\\.venv\\Scripts\\pio.exe device monitor -p COM3 -b 115200 --dtr 0 --rts 0</code>.
Save 10 <code>DIAG raw …</code> lines; expect <code>rc522=0xB2/0xB2</code> and <code>lcd=ok</code>. Type <code>calstatus</code> and note the version.</li>
<li><b>Photograph</b> the breadboard, especially the load-cell wires on each HX711.</li>
<li><b>Label with tape:</b> load-cell cables A/B/C, HX711s A/B/C, readers R1 (left of the tray front) and R2 (right).</li>
<li><b>Print</b> page 1 of the schematic at 100 % and check one 10-pad span measures 25.4 mm.</li>
<li><b>Power off:</b> USB and the 5 V supply unplugged.</li>
<li><b>Kit:</b> iron at 320–350 °C with a fine tip, 0.6–0.8 mm rosin-core solder, flux, multimeter with beep, cutters,
stripper, <b>tinned bare wire</b> (rails and buses), insulated solid-core 22–24 AWG, an 8-pin male header ×2, a 6-pin male
header, a 2-pin screw terminal, 100 µF ×2, 10 µF ×1, about 25 female-ended dupont wires, tape or cable ties.</li>
</ol>

<h2>7. Build order</h2>
<div class="box"><b>Soldering rules.</b>
<ul>
<li>Heat pad and pin together for 2–3 s, then feed solder. You want a shiny cone.</li>
<li>Modules and headers: solder one corner pin, check the part sits flat, then the rest.</li>
<li><b>Bus:</b> lay bare wire flat on the solder side along the row, and solder it to every pin pad on the row plus every 3–4 empty pads.</li>
<li><b>Bridge:</b> tin both pads, then add solder until the blobs merge (or lay a cut-off lead across both).</li>
<li>After every joint, look for an <b>unwanted</b> bridge to the next pad. Clear it with wick, or by dragging a clean tip between the pads.</li>
</ul></div>

<h3>Phase 1: place and mark (no solder) · 10 min</h3>
<p>Lay the printed schematic next to the board. Mark the rail rows (R1 <b>5V</b>, R2 <b>GND</b>, R3 <b>3V3</b>) and every
part outline with a marker on the component side. Dry-fit each part on its pads.</p>
<div class="box check">✔ All three HX711 headers line up on R8–R11 at C15, C29 and C43. The R1/R2 headers sit in C20/C21, R22–R29.</div>

<h3>Phase 2: solder the parts (not the ESPs) · 30 min</h3>
{table(["Step", "Part", "Pads"], [["2.1", "HX711 A", "C15 R8–R11"], ["2.2", "HX711 B", "C29 R8–R11"], ["2.3", "HX711 C", "C43 R8–R11"],
    ["2.4", "R1 and R2 8-pin headers", "C20 and C21, R22–R29"], ["2.5", "UI 6-pin header", "C38–C43, R28"],
    ["2.6", "Screw terminal J0", "C1 R1 (+), C1 R3 (−)"]], tick=True)}
<div class="box check">✔ Beep test: no two neighbouring pins of any part beep together.</div>

<h3>Phase 3: rails, buses, bridges, capacitors · 30 min</h3>
{table(["Step", "Do this", "Pads"], [
    ["3.1", "Three rails: bare wire along R1 (5V), R2 (GND), R3 (3V3)", "C3 → C55"],
    ["3.2", "J0 + to the 5V rail (bare); J0 − to the GND rail (insulated link)", "C1R1→C3R1; C1R3→C3R2"],
    ["3.3", "HX711 GND bus (bare)", "R8, C15 → C43"],
    ["3.4", "HX711 VCC bus (bare)", "R11, C15 → C43"],
    ["3.5", "GND bus → GND rail (<b>insulated</b>: it crosses the 3V3 rail)", "C16R8 → C16R2"],
    ["3.6", "VCC bus → 3V3 rail (bare)", "C43R11 → C44R11 → up C44 to C44R3"],
    ["3.7", "Six R1↔R2 bridges: SCK, MOSI, MISO, GND, RST, 3V3. <b>Not</b> R22 (SDA) or R26 (IRQ)", "C20↔C21 on R23, R24, R25, R27, R28, R29"],
    ["3.8", "C3 10 µF (− on R27, + on R29), bridged to C21", "C22R27, C22R29"],
    ["3.9", "RC522 GND → GND rail (insulated)", "C20R27 → C18R2"],
    ["3.10", "RC522 3V3 → 3V3 rail (insulated)", "C20R29 → C19R3"],
    ["3.11", "UI GND → GND rail (insulated)", "C38R28 → C37R2"],
    ["3.12", "UI VCC → 3V3 rail (insulated)", "C39R28 → C40R3"],
    ["3.13", "C1 100 µF straddling GND (−) and 3V3 (+)", "C33R2, C33R3"],
    ["3.14", "C2 100 µF straddling 5V (+) and GND (−)", "C53R1, C53R2"]], tick=True)}
<div class="box check">✔ <b>Beep checks:</b>
<ul>
<li>5V↔GND, 3V3↔GND and 5V↔3V3: <b>silent</b>.</li>
<li>Every HX711 GND → GND rail, and every HX711 VCC and R1/R2 3V3 → 3V3 rail: <b>beep</b>.</li>
<li>R1 pin ↔ R2 pin: <b>beep</b> on R23/R24/R25/R27/R28/R29, <b>silent</b> on R22 and R26.</li>
<li>Nothing on R9 or R10 (DT/SCK) beeps to anything yet.</li>
<li>Capacitor stripe (−) faces the GND rail.</li>
</ul></div>

<h3>Phase 4: wires to the ESPs · 40 min</h3>
<p>Solder one end to the pad listed, and put a female dupont on the other end. Label each one (e.g. <code>D32</code>).
Cut them to length so the ESP #1 bundle stays under 20 cm.</p>
{table(["Step", "Board pad", "ESP pin", "Colour"], esp_rows, tick=True)}
<div class="box check">✔ Each labelled end beeps to its pad and to nothing else on its neighbour list (section 8).
Once everything is plugged, tape across each row of dupont ends so they can't work loose.</div>

<h3>Phase 5: cables and load cells · 25 min</h3>
{table(["Step", "What"], [
    ["5.1", "Load cell A → HX711 A cell pads (E+ red, E− black, A− white, A+ green, or as in your photo). Strain-relieve the cable with tape or a tie."],
    ["5.2", "Load cell B → HX711 B"], ["5.3", "Load cell C → HX711 C"],
    ["5.4", "R1 and R2 reader cables, 8 wires each, reader SDA on R22. Keep under 20 cm, GND wire alongside."],
    ["5.5", "UI cable: LCD GND/VCC/SDA/SCL to pins 1–4; PAGE to pin 5, ACTION to pin 6; both buttons' other legs to the GND wire."]], tick=True)}
<div class="box check">✔ Each load cell: E+↔E− and A+↔A− each read about 350–1000 Ω. Open = broken wire or joint.</div>

<h2 class="pagebreak">8. Inspection before power</h2>
<ul><li>Joints are shiny cones; there are no balls or gaps.</li><li>There are <b>no bridges except the R1↔R2 ones and C3's two</b>. Look at the board against a light.</li>
<li>No wire clippings are on the board (shake it upside down).</li><li>Capacitor stripes (−) are on GND.</li></ul>
{table(["Must be SILENT (a beep = short)", "Why"], [
    ["5V ↔ GND · 3V3 ↔ GND · 5V ↔ 3V3", "supply or regulator short"],
    ["C20R22 ↔ C21R22 (the two SDA)", "both readers would answer at once"],
    ["any two of R23, R24, R25, R28 (SCK, MOSI, MISO, RST)", "SPI lines shorted"],
    ["each HX711: R9 ↔ R10, and R9/R10 ↔ R8 or R11", "DT/SCK shorted or tied to power"],
    ["UI pins 3↔4 (SDA/SCL), 5↔6 (PAGE/ACTION)", "LCD or buttons merged"]])}

<h2>9. Power-up and tests</h2>
<ol>
<li><b>5 V supply only, no ESPs plugged.</b> 5V rail ≈ 5.0 V; 3V3 rail ≈ 0 V (ESP #1 feeds it). Nothing gets warm. Disconnect.</li>
<li><b>Plug ESP #1, USB only.</b> The 3V3 rail reads ≈ 3.3 V. Open the serial monitor (command in section 6).
<ul><li>The LCD shows <code>ShelfFlow sensor</code>, then readings.</li>
<li>You see <code>DIAG raw A=… B=… C=… … rc522=0xB2/0xB2 … lcd=ok</code>, with no <code>DISCONNECTED_</code>, <code>STUCK_</code> or <code>SATURATED_</code> flags.</li>
<li>Press over each cell: only that cell's raw value jumps.</li>
<li>Tap a tag on R1, then R2: the <code>scans=</code> counters rise. The <code>r1</code>, <code>r2</code> and <code>both</code> commands test each reader alone.</li>
<li>PAGE changes the LCD page; ACTION toggles the backlight.</li></ul></li>
<li><b>Add the 5 V supply</b> (USB stays). Same results.</li>
<li><b>Plug the ESP-CAM</b> (IO0 jumper off). It joins "Shaun" within about 60 s. The DIAG values must not jump when its Wi-Fi starts.</li>
<li><b>Start the services</b> (<code>docs/local-hardware-integration.md</code>). In the staff app, Device health shows <code>sensor-1</code> recently seen.</li>
</ol>

<h2>10. Recalibration</h2>
<p>The cells were re-soldered, so recalibrate:</p>
<ol>
<li>Leave everything powered for 20 minutes to warm up.</li>
<li>Hold PAGE + ACTION for 3 s, then follow the LCD: empty, weight over A, B and C, verify, hold ACTION to save. The known mass is 44 g (set another with <code>mass &lt;g&gt;</code>).</li>
<li>Empty tray reads ≈ 0 g; the weight reads within ±2 g in the centre and at each corner, and holds for a minute.</li>
<li>Measure the support positions in mm and set the tray geometry in the staff app.</li>
</ol>

<h2>11. Troubleshooting</h2>
{table(["Symptom", "Likely cause"], [
    ["ESP not on COM3 / reboot loop", "Short on a rail (section 8), or C1 missing"],
    ["<code>DISCONNECTED_A/B/C</code>", "That HX711's DT/SCK wire (A: D32/D33, B: D25/D26, C: D34/D27), or its bus joint on R8/R11"],
    ["<code>STUCK_x</code> / <code>SATURATED_x</code>", "Cell wires: E± or A± open or swapped; compare with your photo"],
    ["Weight goes the wrong way", "A+/A− swapped. Harmless after recalibration"],
    ["One reader <code>0x00</code> / <code>0xFF</code>", "Its own SDA wire (D21 or D4), or its cable"],
    ["Both readers bad", "A shared bridge or wire: D18/D23/D19/D22, or the block's GND/3V3"],
    ["One reader picks up the other's tags", "R22 (SDA) accidentally bridged"],
    ["<code>lcd=none</code>", "SDA/SCL swapped, no VCC, or the contrast trimmer"],
    ["A button does nothing", "Its second leg isn't on the GND wire, or the wrong pair of switch legs (beep while pressing)"],
    ["Camera unreachable after power-up", "IO0 jumper still on: remove it and press RST"],
    ["Camera reboots when Wi-Fi starts", "C2 missing, or thin 5V/GND wires"],
    ["Grams drift", "Warm-up (20 min), or cell A's mounting"]])}

<h2 class="pagebreak">12. Tick list</h2>
{table(["Step", "Joint"], [[s, d] for s, d in [
    ("3.1", "Rails R1 5V, R2 GND, R3 3V3"), ("3.2", "J0 to the rails"), ("3.3", "HX711 GND bus R8"), ("3.4", "HX711 VCC bus R11"),
    ("3.5", "GND bus → GND rail"), ("3.6", "VCC bus → 3V3 rail"), ("3.7", "Six R1↔R2 bridges (not R22, R26)"), ("3.8", "C3"),
    ("3.9", "RC522 GND → rail"), ("3.10", "RC522 3V3 → rail"), ("3.11", "UI GND → rail"), ("3.12", "UI VCC → rail"),
    ("3.13", "C1"), ("3.14", "C2")]] + [[s, f"{t} ({c})"] for _, _, t, s, c in ESP_WIRES] +
    [["5.1–5.3", "Load cells A, B, C"], ["5.4", "Reader cables"], ["5.5", "UI cable"]], tick=True)}
<p class="small">Generated by <code>tools/perfboard_guide.py</code> from the same data as the schematic. {len(used)} pads are used by parts; every
bare wire was checked so it never touches a pad of another net.</p>
"""
    return f"<!doctype html><html><head><meta charset='utf-8'><title>ShelfFlow perfboard guide</title><style>{CSS}</style></head><body>{body}</body></html>"

def browser():
    for path in (r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"):
        if Path(path).is_file():
            return path
    return shutil.which("chrome") or shutil.which("msedge")

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    html_path = OUT / "ShelfFlow-perfboard-guide.html"
    html_path.write_text(document(), encoding="utf-8")
    pdf = OUT / "ShelfFlow-perfboard-guide.pdf"
    exe = browser()
    if not exe:
        raise SystemExit("Chrome/Edge not found: open the HTML file and print it to PDF")
    subprocess.run([exe, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={pdf.resolve()}",
                    html_path.resolve().as_uri()], check=True, capture_output=True, timeout=120)
    print("wrote", pdf)
