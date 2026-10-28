import { useState, useEffect, useRef } from "react";
import { Play, Pause, ShoppingBag } from "@phosphor-icons/react";
import { call, money, API } from "./api";

type Action = (path: string, body: unknown) => Promise<any>;

/** Image from an authenticated endpoint (staff or customer token), as an object URL. */
export function AuthImage({ path, token, alt, className }: { path: string; token: string; alt: string; className?: string }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    let stopped = false,
      made = "";
    fetch(API + path, { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => (r.ok ? r.blob() : Promise.reject()))
      .then((blob) => {
        if (stopped) return;
        made = URL.createObjectURL(blob);
        setUrl(made);
      })
      .catch(() => setUrl(""));
    return () => {
      stopped = true;
      if (made) URL.revokeObjectURL(made);
    };
  }, [path, token]);
  return url ? <img className={className} src={url} alt={alt} /> : <div className={(className || "") + " empty-photo"}>No image</div>;
}

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);
import { reasonText } from "./reasons";

/** Store-level numbers, recognition tuning, scale drift and shrink, all from staff decisions and sensor data. */
export function Insights({ data, action, token }: { data: any; action: Action; token: string }) {
  const [kpis, setKpis] = useState<any>(null),
    [tuning, setTuning] = useState<any>(null),
    [drift, setDrift] = useState<any[]>([]),
    [shrink, setShrink] = useState<any>(null),
    [error, setError] = useState("");
  const load = async () => {
    try {
      const [k, t, d, s] = await Promise.all([
        call("/staff/kpis", token),
        call("/staff/recognition/tuning", token),
        call("/staff/reports/drift", token),
        call("/staff/reports/shrink", token),
      ]);
      setKpis(k);
      setTuning(t);
      setDrift(d);
      setShrink(s);
      setError("");
    } catch (e) {
      setError(String(e));
    }
  };
  useEffect(() => {
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [token]);
  const product = (id: string | null) => data.products.find((p: any) => p.id === id)?.name || id || "unknown item";
  return (
    <>
      {error && <p role="alert" className="error">{error}</p>}
      <section className="panel">
        <div className="sectionhead">
          <h2>How the shelf is doing</h2>
          <span className="tag">{kpis ? `${kpis.interactions} interactions` : "loading"}</span>
        </div>
        {kpis && (
          <>
            <div className="metrics">
              <div><span>Decided automatically</span><strong>{pct(kpis.auto_rate)}</strong></div>
              <div><span>Sent to review</span><strong>{pct(kpis.review_rate)}</strong></div>
              <div><span>Automatic, not challenged</span><strong>{pct(kpis.auto_precision)}</strong></div>
              <div><span>Median time to review</span><strong>{kpis.median_review_seconds === null ? "—" : `${Math.round(kpis.median_review_seconds)} s`}</strong></div>
            </div>
            <p className="muted">
              Camera agreed with staff on {pct(kpis.vision_agreement)} of reviewed items ({kpis.vision_agreement_samples} samples) ·{" "}
              {kpis.disputes} dispute{kpis.disputes === 1 ? "" : "s"} · {kpis.pending} waiting now. {kpis.note}
            </p>
            {kpis.top_reasons.length > 0 && (
              <>
                <h3>Why events needed a person</h3>
                {kpis.top_reasons.map(([r, n]: [string, number]) => (
                  <div className="row" key={r}><span>{reasonText(r)}</span><b>{n}</b></div>
                ))}
              </>
            )}
          </>
        )}
      </section>
      <section className="panel">
        <h2>Recognition thresholds</h2>
        <p>
          Every product a staff member confirms is a labelled example. Once there are enough, this suggests the similarity
          threshold that lets the camera decide the most items with almost no wrong product. Nothing changes until you apply it.
        </p>
        {tuning && (
          <>
            <div className="row"><span>Labelled samples</span><b>{tuning.samples} / {tuning.min_samples} needed</b></div>
            <div className="row"><span>Camera's first guess was right</span><b>{tuning.samples ? pct(tuning.vision_right / tuning.samples) : "—"}</b></div>
            <div className="row">
              <span>In use</span>
              <b>{tuning.current ? `threshold ${tuning.current.threshold} · margin ${tuning.current.margin}` : "start-up values from vision config"}</b>
            </div>
            {tuning.recommended ? (
              <div className="result">
                Recommended: threshold <b>{tuning.recommended.threshold}</b>, margin <b>{tuning.recommended.margin}</b>: decides{" "}
                {pct(tuning.recommended.auto_rate)} of these items with {pct(tuning.recommended.wrong_rate)} wrong
                {tuning.current_auto_rate !== undefined && ` (now ${pct(tuning.current_auto_rate)} with ${pct(tuning.current_wrong_rate)} wrong)`}.
                <p>
                  <button
                    onClick={() => {
                      const reason = prompt("Apply these thresholds to the live camera. Reason:");
                      if (reason) action("/staff/recognition/settings", { ...tuning.recommended, reason }).then(load).catch(() => {});
                    }}
                  >
                    Apply to camera
                  </button>
                </p>
                <p className="muted">{tuning.advice}</p>
              </div>
            ) : (
              <p className="muted">{tuning.advice}</p>
            )}
          </>
        )}
      </section>
      <section className="panel">
        <h2>Scale health</h2>
        {drift.map((t) => (
          <div key={t.tray_id} className={t.status === "OK" ? "result" : "result warn"}>
            <b>{t.tray_id}</b> · {t.status.replaceAll("_", " ").toLowerCase()}
            {t.scale_g !== undefined && (
              <p>
                Scale reads {t.scale_g} g, records say {t.records_g} g ({t.difference_g > 0 ? "+" : ""}
                {t.difference_g} g, allowed ±{t.allowance_g} g).
              </p>
            )}
            <p className="muted">
              Calibration {t.calibration_version}
              {t.calibration_age_days !== null && `, ${t.calibration_age_days} days old`} · drift flags in 24 h: {t.drift_flags_24h}
            </p>
            {t.advice && <p>{t.advice}</p>}
          </div>
        ))}
      </section>
      <section className="panel">
        <h2>Shrink and misplaced items</h2>
        {shrink && (
          <>
            <div className="metrics">
              <div><span>Unexplained weight drops</span><strong>{shrink.unexplained_weight_drops.length}</strong></div>
              <div><span>Estimated value</span><strong>{money(shrink.unexplained_value_minor)}</strong></div>
              <div><span>Dismissed pickups</span><strong>{shrink.dismissed_pickups.length}</strong></div>
              <div><span>Put back on wrong tray</span><strong>{shrink.relocated_units.length + shrink.wrong_tray_pending.length}</strong></div>
            </div>
            {shrink.unexplained_weight_drops.slice(0, 12).map((d: any) => (
              <div className="row" key={d.event_id}>
                <span>
                  {new Date(d.at * 1000).toLocaleString()} · {d.tray_id} · {d.delta_g} g
                </span>
                <span>{d.likely_product ? `${product(d.likely_product)} (${money(d.estimated_value_minor)})` : "no matching product"}</span>
              </div>
            ))}
            {shrink.dismissed_pickups.slice(0, 8).map((d: any) => (
              <div className="row" key={d.hypothesis_id}>
                <span>Dismissed pickup · {d.tray_id}</span>
                <span>{d.product_id ? `${product(d.product_id)} × ${d.quantity}` : "unknown item"}</span>
              </div>
            ))}
            {shrink.removed_units.length > 0 && <p className="muted">{shrink.removed_units.length} item(s) removed by staff.</p>}
            <p className="muted">{shrink.note}</p>
          </>
        )}
      </section>
    </>
  );
}

/** Active learning: staff name the crops the camera could not decide; each answer becomes a reference. */
export function HelpTheCamera({ data, token }: { data: any; token: string }) {
  const [rows, setRows] = useState<any[]>([]),
    [other, setOther] = useState<Record<string, string>>({}),
    [error, setError] = useState("");
  const load = () =>
    call("/staff/uncertain", token)
      .then((r) => {
        setRows(r);
        setError("");
      })
      .catch((e) => setError(String(e)));
  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [token]);
  const answer = (id: string, product_id: string | null) =>
    call(`/staff/uncertain/${id}/label`, token, { product_id })
      .then(load)
      .catch((e) => setError(String(e)));
  const name = (id: string | null) => data.products.find((p: any) => p.id === id)?.name || id;
  return (
    <section className="panel">
      <div className="sectionhead">
        <h2>Help the camera</h2>
        <span className="tag">{rows.length} waiting</span>
      </div>
      <p>
        The camera was unsure about these items. One tap tells it what each one is; the answer becomes a new reference right
        away, so the same doubt does not come back.
      </p>
      {error && <p role="alert" className="error">{error}</p>}
      {!rows.length && <p className="muted">Nothing to answer.</p>}
      <div className="refgrid">
        {rows.map((r) => (
          <figure key={r.id} className="askcard">
            <AuthImage path={`/staff/catalogue-file/${encodeURIComponent(r.file)}`} token={token} alt="Item the camera was unsure about" className="loadphoto" />
            <figcaption className="askactions">
              <small className="muted">
                {r.tray_id || "tray"} · similarity {r.similarity.toFixed(2)}
              </small>
              {[r.top_product, r.second_product].filter(Boolean).map((p: string) => (
                <button key={p} className="secondary" onClick={() => answer(r.id, p)}>
                  {name(p)}
                </button>
              ))}
              <select value={other[r.id] || ""} onChange={(e) => setOther({ ...other, [r.id]: e.target.value })}>
                <option value="">Something else…</option>
                {data.products.map((p: any) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
              {other[r.id] && <button onClick={() => answer(r.id, other[r.id])}>Save as {name(other[r.id])}</button>}
              <button className="textbutton" onClick={() => answer(r.id, null)}>Not a product / unclear</button>
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  );
}

const PALETTE = ["#175745", "#b5651d", "#3d5a99", "#8a3b6e", "#5b7d1a", "#a33a3a", "#2d7f86", "#6b5aa6"];
const colourOf = (id: string, ids: string[]) => PALETTE[Math.max(0, ids.indexOf(id)) % PALETTE.length];

/** Top view of a tripod tray: load-cell supports and where each loaded item's weight landed. */
export function ShelfMap({ tray, units, products }: { tray: any; units: any[]; products: any[] }) {
  const cells: any[] = tray.geometry?.cells || [];
  const onTray = units.filter((u) => u.tray_id === tray.id && u.status === "ON_TRAY");
  const placed = onTray.filter((u) => u.x_mm !== null && u.y_mm !== null);
  if (!cells.length) return <p className="muted">{tray.id}: send the load-cell positions (geometry) to see a map.</p>;
  const xs = [...cells.map((c) => c.x_mm), ...placed.map((u) => u.x_mm)],
    ys = [...cells.map((c) => c.y_mm), ...placed.map((u) => u.y_mm)];
  const pad = 25,
    minX = Math.min(...xs) - pad,
    maxX = Math.max(...xs) + pad,
    minY = Math.min(...ys) - pad,
    maxY = Math.max(...ys) + pad;
  const ids = [...new Set(onTray.map((u) => u.product_id))];
  const name = (id: string) => products.find((p) => p.id === id)?.name || id;
  return (
    <figure className="shelfmap">
      <svg viewBox={`${minX} ${minY} ${maxX - minX} ${maxY - minY}`} role="img" aria-label={`Map of ${tray.id}`}>
        <polygon points={cells.map((c) => `${c.x_mm},${c.y_mm}`).join(" ")} className="mapplate" />
        {cells.map((c) => (
          <g key={c.name}>
            <circle cx={c.x_mm} cy={c.y_mm} r={6} className="mapcell" />
            <text x={c.x_mm} y={c.y_mm - 10} className="maplabel">{c.name}</text>
          </g>
        ))}
        {placed.map((u) => (
          <g key={u.id}>
            <circle cx={u.x_mm} cy={u.y_mm} r={9} fill={colourOf(u.product_id, ids)} className="mapunit">
              <title>{`${name(u.product_id)} · ${u.weight_g.toFixed(1)} g`}</title>
            </circle>
          </g>
        ))}
      </svg>
      <figcaption>
        {ids.map((id) => (
          <span key={id} className="legend">
            <i style={{ background: colourOf(id, ids) }} /> {name(id)} × {onTray.filter((u) => u.product_id === id).length}
          </span>
        ))}
        {onTray.length > placed.length && <span className="muted">{onTray.length - placed.length} item(s) without a recorded place</span>}
      </figcaption>
    </figure>
  );
}

/** Frames with the boxes vision saw, the tray weight and bracelet taps on one timeline. */
export function EvidencePlayer({ eventId, token, autoPlay }: { eventId: string; token: string; autoPlay?: boolean }) {
  const [timeline, setTimeline] = useState<any>(null),
    [images, setImages] = useState<string[]>([]),
    [index, setIndex] = useState(0),
    [size, setSize] = useState<[number, number]>([0, 0]),
    [playing, setPlaying] = useState(!!autoPlay),
    [ready, setReady] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    let stopped = false;
    const urls: string[] = [];
    (async () => {
      try {
        const t = await call(`/staff/evidence/${eventId}/timeline`, token);
        if (stopped) return;
        setTimeline(t);
        // Middle frame first (shown at once), then the rest 8 at a time.
        const frames = t.frames.slice(0, 80);
        const order = [Math.floor(frames.length / 2), ...frames.map((_: any, i: number) => i)];
        const fetched = new Array(frames.length).fill("");
        const load = async (i: number) => {
          if (fetched[i]) return;
          const r = await fetch(`${API}/staff/evidence-file/${encodeURIComponent(frames[i].ref)}`, { headers: { Authorization: `Bearer ${token}` } });
          fetched[i] = r.ok ? URL.createObjectURL(await r.blob()) : "";
          if (fetched[i]) urls.push(fetched[i]);
        };
        if (frames.length) {
          await load(order[0]);
          if (stopped) return;
          setImages([...fetched]);
          setIndex(order[0]);
        }
        for (let k = 1; k < order.length && !stopped; k += 8) {
          await Promise.all(order.slice(k, k + 8).map(load));
          if (!stopped) setImages([...fetched]);
        }
        if (!stopped) setReady(true);
      } catch (e) {
        if (!stopped) setError(String(e));
      }
    })();
    return () => {
      stopped = true;
      urls.forEach((u) => u && URL.revokeObjectURL(u));
    };
  }, [eventId, token]);
  useEffect(() => {
    if (!playing || !ready || !images.length) return; // play once every frame is in
    const t = setInterval(() => setIndex((i) => (i + 1) % images.length), 125);
    return () => clearInterval(t);
  }, [playing, ready, images.length]);
  if (error) return <p className="error">{error}</p>;
  if (!timeline) return <p className="muted">Loading evidence…</p>;
  const frame = timeline.frames[index];
  const ann = frame?.annotations;
  const trayAnn = ann?.trays?.[timeline.tray_id];
  // One time axis for frames, weight events and taps.
  const times = [
    ...timeline.frames.map((f: any) => f.t).filter((t: any) => t),
    ...timeline.weights.map((w: any) => w.t),
    ...timeline.taps.map((t: any) => t.t),
    ...timeline.window,
  ];
  const t0 = Math.min(...times) - 0.5,
    t1 = Math.max(...times) + 0.5;
  const X = (t: number) => ((t - t0) / Math.max(0.001, t1 - t0)) * 600;
  const levels = timeline.weights.flatMap((w: any) => [w.pre_g, w.post_g]);
  const g0 = Math.min(...levels, 0),
    g1 = Math.max(...levels, 1);
  const Y = (g: number) => 100 - ((g - g0) / Math.max(1, g1 - g0)) * 84;
  let path = "";
  timeline.weights.forEach((w: any, i: number) => {
    path += `${i ? "L" : "M"}${X(w.t)},${Y(w.pre_g)} L${X(w.t)},${Y(w.post_g)} `;
    const next = timeline.weights[i + 1];
    path += `L${next ? X(next.t) : 600},${Y(w.post_g)} `;
  });
  return (
    <div className="evidenceplayer">
      {images.length ? (
        <div className="framebox">
          <img src={images[index]} alt={`Evidence frame ${index + 1} of ${images.length}`} onLoad={(e) => setSize([e.currentTarget.naturalWidth, e.currentTarget.naturalHeight])} />
          {size[0] > 0 && ann && (
            <svg viewBox={`0 0 ${size[0]} ${size[1]}`} className="overlay" aria-hidden="true">
              {trayAnn && <rect x={trayAnn.roi[0]} y={trayAnn.roi[1]} width={trayAnn.roi[2] - trayAnn.roi[0]} height={trayAnn.roi[3] - trayAnn.roi[1]} className="ovroi" />}
              {trayAnn?.items.map((it: any, i: number) => (
                <g key={"i" + i}>
                  <rect x={it.box[0]} y={it.box[1]} width={it.box[2] - it.box[0]} height={it.box[3] - it.box[1]} className="ovitem" />
                  <text x={it.box[0] + 3} y={it.box[1] + 14} className="ovtext">
                    {it.sku === "UNKNOWN" ? "item" : it.sku}
                    {it.similarity !== null && ` ${it.similarity}`} #{it.track}
                  </text>
                </g>
              ))}
              {trayAnn?.hands.map((h: number[], i: number) => (
                <rect key={"h" + i} x={h[0]} y={h[1]} width={h[2] - h[0]} height={h[3] - h[1]} className="ovhand" />
              ))}
              {ann.markers.map((m: any) => {
                const cx = m.corners.reduce((s: number, p: number[]) => s + p[0], 0) / 4,
                  cy = m.corners.reduce((s: number, p: number[]) => s + p[1], 0) / 4;
                return (
                  <g key={"m" + m.id}>
                    <polygon points={m.corners.map((p: number[]) => p.join(",")).join(" ")} className="ovmarker" />
                    <text x={cx + 8} y={cy} className="ovtext">bracelet {m.id}</text>
                  </g>
                );
              })}
            </svg>
          )}
        </div>
      ) : (
        <p className="muted">No original frames stored for this event (simulated or expired).</p>
      )}
      <div className="playerbar">
        <button className="iconbutton" aria-label={playing ? "Pause" : "Play"} onClick={() => setPlaying(!playing)} disabled={!images.length}>
          {playing ? <Pause /> : <Play />}
        </button>
        <input type="range" min={0} max={Math.max(0, images.length - 1)} value={index} onChange={(e) => setIndex(Number(e.target.value))} aria-label="Frame" />
        <small className="muted">
          {images.length ? `${index + 1}/${images.length}` : ""}
          {frame?.faces_redacted ? " · face blurred" : ""}
        </small>
      </div>
      <svg viewBox="0 0 600 120" className="weightchart" role="img" aria-label="Tray weight and bracelet taps over time">
        <rect x={X(timeline.window[0])} y={0} width={Math.max(2, X(timeline.window[1]) - X(timeline.window[0]))} height={104} className="chartwindow" />
        {path && <path d={path} className="chartline" />}
        {timeline.weights.map((w: any) => (
          <text key={w.event_id} x={Math.min(560, X(w.t) + 4)} y={Math.max(12, Y(w.post_g) - 4)} className="charttext">
            {(w.post_g - w.pre_g > 0 ? "+" : "") + (w.post_g - w.pre_g).toFixed(1)} g
          </text>
        ))}
        {timeline.taps.map((t: any, i: number) => (
          <g key={"t" + i}>
            <line x1={X(t.t)} x2={X(t.t)} y1={104} y2={118} className="charttap" />
            <text x={X(t.t) + 3} y={117} className="charttext">{t.reader || "tap"}</text>
          </g>
        ))}
        {frame?.t && <line x1={X(frame.t)} x2={X(frame.t)} y1={0} y2={120} className="chartcursor" />}
      </svg>
      <p className="muted">
        Shaded: the interaction window. Line: tray weight. Ticks: bracelet reads. Boxes: green items, orange hands, blue bracelet
        markers.
        {timeline.model_versions?.gallery && ` Models: ${Object.entries(timeline.model_versions).map(([k, v]) => `${k} ${v}`).join(" · ")}.`}
      </p>
    </div>
  );
}

/** Suggested decision for a review card; the parent decides whether to use it. */
export function Suggestion({ hypothesisId, token, onUse }: { hypothesisId: string; token: string; onUse: (s: any) => void }) {
  const [s, setS] = useState<any>(null);
  useEffect(() => {
    call(`/staff/review/${hypothesisId}/suggestion`, token).then(setS).catch(() => setS(null));
  }, [hypothesisId, token]);
  if (!s) return null;
  return (
    <div className={s.confidence === "strong" ? "result" : "result warn"}>
      <b>Suggested: {s.decision === "CONFIRM" ? "confirm" : "dismiss"}</b> · {s.confidence} evidence
      <ul className="why">
        {s.why.map((w: string) => (
          <li key={w}>{w}</li>
        ))}
      </ul>
      <button className="secondary" onClick={() => onUse(s)}>Use suggestion</button>
    </div>
  );
}

/** Cart / receipt thumbnail: the product's clearest learned reference, or an icon. */
export function ProductThumb({ productId, token, hasImage }: { productId: string; token: string; hasImage?: boolean }) {
  if (!hasImage)
    return (
      <div className="producticon">
        <ShoppingBag size={30} />
      </div>
    );
  return <AuthImage path={`/customer/product-image/${encodeURIComponent(productId)}`} token={token} alt="" className="productthumb" />;
}

/** Brief notices when the cart changes, each with a way to say "this was not me". */
export function useCartToasts(events: any[] | undefined) {
  const seen = useRef<Set<string> | null>(null),
    [toasts, setToasts] = useState<any[]>([]);
  useEffect(() => {
    if (!events) return;
    if (seen.current === null) {
      seen.current = new Set(events.map((e) => e.id)); // no toasts for what was already in the cart
      return;
    }
    const fresh = events.filter((e) => !seen.current!.has(e.id));
    fresh.forEach((e) => seen.current!.add(e.id));
    if (!fresh.length) return;
    setToasts((t) => [...t, ...fresh]);
    try {
      const ctx = new AudioContext(),
        osc = ctx.createOscillator(),
        gain = ctx.createGain();
      osc.frequency.value = fresh[0].action === "RETURN" ? 520 : 880;
      gain.gain.setValueAtTime(0.08, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.25);
      osc.connect(gain).connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.25);
    } catch {
      /* sound is optional */
    }
    const ids = fresh.map((e) => e.id);
    setTimeout(() => setToasts((t) => t.filter((x) => !ids.includes(x.id))), 8000);
  }, [events]);
  return [toasts, (id: string) => setToasts((t) => t.filter((x) => x.id !== id))] as const;
}
