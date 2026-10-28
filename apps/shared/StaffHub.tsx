import { ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { PhotoButton } from "./PhotoButton";
import { ArrowRight, CheckCircle, GraduationCap, Scales, Users, VideoCamera, WarningCircle } from "@phosphor-icons/react";
import { API, call, money } from "./api";
import { MjpegView } from "./Live";
import { TrainProducts } from "./Training";
import { AuthImage, EvidencePlayer, HelpTheCamera, ShelfMap, Suggestion } from "./Insights";
import { Notice, OverviewAlerts, ProductsRecognition, ReviewCard, Simulation } from "./App";

type Action = (path: string, body: unknown) => Promise<any>;
export type Go = (page: string, tab?: string, product?: string) => void;

// ---------- shared status ----------

/** Polled once by the staff shell; drives the header chips and Home/Live camera. */
export function useCameraStatus(token: string, everyMs = 3000) {
  const [camera, setCamera] = useState<any>(null);
  useEffect(() => {
    let stopped = false;
    const load = () =>
      call("/staff/preintegration/camera", token)
        .then((v) => !stopped && setCamera(v))
        .catch(() => !stopped && setCamera({ available: false, predictions: [] }));
    load();
    const t = setInterval(load, everyMs);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [token, everyMs]);
  return camera;
}

/** Staff push channel (/ws/staff): the live tray weight about once a second, and a nudge after every committed
 *  cart, review or shelf event so screens reload at once. Polling stays as the fallback. */
export const STAFF_CHANGED = "shelfflow:staff-changed";
export function useStaffSocket(token: string, onChanged: () => void) {
  const [live, setLive] = useState<{ trays: Record<string, any>; at: number }>({ trays: {}, at: 0 });
  const changed = useRef(onChanged);
  changed.current = onChanged;
  useEffect(() => {
    if (!token) return;
    let stop = false,
      ws: WebSocket,
      timer: ReturnType<typeof setTimeout>;
    const open = () => {
      ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/staff`);
      ws.onopen = () => (stop ? ws.close() : ws.send(JSON.stringify({ token })));
      ws.onmessage = (e) => {
        if (stop) return;
        const m = JSON.parse(e.data);
        if (m.type === "live") setLive({ trays: m.trays, at: Date.now() });
        else if (m.type === "changed") {
          changed.current();
          window.dispatchEvent(new Event(STAFF_CHANGED));
        }
      };
      ws.onclose = () => {
        if (!stop) timer = setTimeout(open, 2000);
      };
    };
    open();
    return () => {
      stop = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, [token]);
  return live;
}

/** Tray weight on staff screens: the pushed live reading while it keeps arriving, else the last shelf report.
 *  `shown` follows the shelf LCD (held whole grams unless the tray is changing); `live` is the raw reading. */
export function trayWeight(data: any, trayId?: string): { live: number; shown: number } | null {
  const tray = data.trays.find((t: any) => t.id === trayId) ?? data.trays[0];
  const pushed = data.live_weight?.[tray?.id];
  if (pushed) return { live: pushed.live_g, shown: pushed.display_g };
  const w = tray?.latest_sample?.weight;
  return w ? { live: w.filtered ?? w.post_g ?? 0, shown: w.post_g ?? w.filtered ?? 0 } : null;
}
export const grams = (g: number) => `${Number.isInteger(g) ? g : g.toFixed(1)} g`;

export const pendingOf = (data: any) => (data?.hypotheses || []).filter((h: any) => ["PENDING_REVIEW", "DISPUTED"].includes(h.status));
export function controllerOnline(data: any) {
  const sensor = (data?.devices || []).find((d: any) => d.id.startsWith("sensor"));
  return !!sensor && Date.now() / 1000 - sensor.last_seen < 30;
}
const when = (ts?: number | null) => {
  if (!ts) return "time unknown";
  const d = new Date(ts * 1000);
  const today = d.toDateString() === new Date().toDateString();
  return (today ? "" : d.toLocaleDateString(undefined, { day: "numeric", month: "short" }) + " ") + d.toLocaleTimeString();
};
import { reasonText as human } from "./reasons";

export function StatusChips({ data, camera, go }: { data: any; camera: any; go: Go }) {
  const pending = pendingOf(data).length;
  return (
    <div className="statuschips">
      <button className={camera?.available ? "chip ok" : "chip bad"} onClick={() => go("Live camera")}>
        <span className="dot" /> Camera {camera?.available ? "live" : "offline"}
      </button>
      <button className={controllerOnline(data) ? "chip ok" : "chip bad"} onClick={() => go("Shelf setup")}>
        <span className="dot" /> Shelf {controllerOnline(data) ? "connected" : "no signal"}
      </button>
      <button className={pending ? "chip warn" : "chip ok"} onClick={() => go("Review queue")}>
        {pending ? `${pending} to review` : "Nothing to review"}
      </button>
    </div>
  );
}

export function Tabs({ tabs, value, onChange }: { tabs: [string, string][]; value: string; onChange: (v: string) => void }) {
  return (
    <div className="pagetabs" role="tablist">
      {tabs.map(([id, label]) => (
        <button key={id} role="tab" aria-selected={value === id} className={value === id ? "selected" : ""} onClick={() => onChange(id)}>
          {label}
        </button>
      ))}
    </div>
  );
}

// ---------- Home ----------

export function Home({ data, token, action, camera, go }: { data: any; token: string; action: Action; camera: any; go: Go }) {
  const pending = pendingOf(data);
  const shoppers = data.sessions.filter((s: any) => s.status === "ACTIVE").length;
  const weight = trayWeight(data);
  const tiles: [string, string, string, boolean, string][] = [
    ["Camera", camera?.available ? "Live" : "Offline", camera?.available ? "Watch the shelf" : "Check power and hotspot", !!camera?.available, "Live camera"],
    ["Shelf controller", controllerOnline(data) ? "Connected" : "No signal", controllerOnline(data) ? "Weights and RFID arriving" : "Check USB and the bridge", controllerOnline(data), "Shelf setup"],
    ["To review", String(pending.length), pending.length ? "Open the review queue" : "All caught up", pending.length === 0, "Review queue"],
    ["Shoppers in store", String(shoppers), "Check-in and checkout", true, "Shoppers & checkout"],
  ];
  return (
    <>
      <div className="tiles">
        {tiles.map(([label, value, hint, ok, page]) => (
          <button key={label} className={ok ? "tile" : "tile attention"} onClick={() => go(page)}>
            <span>{label}</span>
            <strong>{value}</strong>
            <small>{hint} <ArrowRight size={14} /></small>
          </button>
        ))}
      </div>
      <div className="homegrid">
        <section className="panel">
          <div className="sectionhead">
            <h2>Shelf right now</h2>
            <span><button className="textbutton" onClick={() => go("Live camera")}>Full screen view</button></span>
          </div>
          <MjpegView url={API + "/staff/camera/stream"} token={token} empty="Camera offline — see Live camera for what to check" />
          <p className="muted">
            {weight ? `Tray weight: ${grams(weight.shown)} · ` : ""}
            Sees: {(camera?.predictions || []).map((p: any) => p.product || "unknown item").join(", ") || "no products"}
          </p>
        </section>
        <div>
          <section className="panel">
            <h2>Quick actions</h2>
            <div className="quickactions">
              <button onClick={() => go("Review queue")} disabled={!pending.length}><CheckCircle size={20} /> Review {pending.length || ""} pending</button>
              <button className="secondary" onClick={() => go("Products & training", "train")}><GraduationCap size={20} /> Teach a product</button>
              <button className="secondary" onClick={() => go("Shoppers & checkout")}><Users size={20} /> Check a shopper in or out</button>
              <button className="secondary" onClick={() => go("Stock")}><Scales size={20} /> Count stock</button>
            </div>
          </section>
          <OverviewAlerts data={data} pending={pending} token={token} />
          <section className="panel">
            <h2>Recent bracelet taps</h2>
            {!data.recent_taps?.length && <p className="muted">No taps yet. Tap a registered tag on R1 or R2.</p>}
            {data.recent_taps?.slice(0, 5).map((tap: any) => (
              <div className="row" key={tap.event_id}>
                <span>{tap.reader || "Reader"} · {tap.uid}</span>
                <span className="muted">{when(tap.captured)}</span>
              </div>
            ))}
          </section>
        </div>
      </div>
      {data.trays.filter((t: any) => t.unit_tracking).map((t: any) => (
        <section className="panel" key={t.id}>
          <h2>Where items sit on {t.id}</h2>
          <ShelfMap tray={t} units={data.tray_units} products={data.products} />
        </section>
      ))}
      {data.mode === "simulation" && (
        <section className="panel">
          <h2>Simulation bench</h2>
          <p>Demo controls that generate labelled evidence through the normal device path.</p>
          <Simulation data={data} action={action} />
        </section>
      )}
    </>
  );
}

// ---------- Live camera ----------

export function LiveCamera({ data, token, go }: { data: any; token: string; go: Go }) {
  const [view, setView] = useState<"detections" | "raw">("detections");
  const [fps, setFps] = useState(0);
  const camera = useCameraStatus(token, 1000);
  const weight = trayWeight(data);
  return (
    <div className="livegrid">
      <section className="panel">
        <div className="sectionhead">
          <Tabs tabs={[["detections", "With detections"], ["raw", "Original"]]} value={view} onChange={(v) => setView(v as any)} />
          <span className="tag">{fps ? `${fps} fps` : "no video"}</span>
        </div>
        <MjpegView key={view} url={`${API}/staff/camera/stream?view=${view}`} token={token} onFps={setFps}
          empty={<CameraHelp />} />
        <p className="muted">
          {view === "detections"
            ? "Boxes are what the recognition model sees right now. Nothing here changes a cart."
            : "Untouched camera frames, exactly what training and evidence use."}
        </p>
      </section>
      <div>
        <section className="panel">
          <h2>What the camera sees</h2>
          {!camera?.available ? (
            <p className="muted">No fresh frames.</p>
          ) : (camera.predictions || []).length === 0 ? (
            <p>No products detected.</p>
          ) : (
            camera.predictions.map((p: any, i: number) => (
              <div className="row" key={i}>
                <b>{data.products.find((x: any) => x.id === p.product)?.name || p.product || "Unknown item"}</b>
                {p.score !== undefined && <span className="muted">{Math.round(p.score * 100)}%</span>}
              </div>
            ))
          )}
          <button className="secondary" onClick={() => go("Products & training", "train")}><GraduationCap size={18} /> Teach it a product</button>
        </section>
        <section className="panel">
          <h2>Tray weight</h2>
          {weight ? (
            <p className="bignumber">{grams(weight.shown)}</p>
          ) : (
            <p className="muted">No reading yet.</p>
          )}
          <p className="muted">{controllerOnline(data) ? "Shelf controller connected" : "Shelf controller not sending"}</p>
        </section>
        <section className="panel">
          <h2>Bracelet taps</h2>
          {!data.recent_taps?.length && <p className="muted">None yet.</p>}
          {data.recent_taps?.slice(0, 5).map((tap: any) => (
            <div className="row" key={tap.event_id}>
              <span>{tap.reader} · {tap.uid}</span>
              <span className="muted">{when(tap.captured)}</span>
            </div>
          ))}
        </section>
      </div>
    </div>
  );
}

function CameraHelp() {
  return (
    <div className="camerahelp">
      <WarningCircle size={28} />
      <b>No camera video</b>
      <ol>
        <li>Camera powered, and the phone hotspot <b>X200T</b> on (laptop on it too).</li>
        <li><code>tools.camera_preview</code> and <code>tools.detector_preview</code> running.</li>
        <li>Still nothing after a minute? The camera may be stuck at start-up — press its RST button.</li>
      </ol>
    </div>
  );
}

// ---------- Review queue ----------

export function ReviewQueue({ data, token, action, reload }: { data: any; token: string; action: Action; reload: () => Promise<void> }) {
  const [suggestions, setSuggestions] = useState<any[]>([]);
  const [filter, setFilter] = useState("all");
  const [selectedId, setSelectedId] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [bulk, setBulk] = useState<{ done: number; total: number } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let stopped = false;
    const load = () => call("/staff/preintegration/suggestions", token).then((r) => !stopped && setSuggestions(r)).catch(() => {});
    load();
    const t = setInterval(load, 4000);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [token]);
  const byHypothesis = useMemo(() => Object.fromEntries(suggestions.map((s) => [s.hypothesis_id, s])), [suggestions]);
  const items = pendingOf(data)
    .map((h: any) => ({ h, s: byHypothesis[h.id] }))
    .sort((a: any, b: any) => (b.s?.captured || b.h.captured || 0) - (a.s?.captured || a.h.captured || 0));
  const shown = items.filter((i: any) => filter === "all" || (filter === "camera" ? !!i.s : !i.s));
  const index = Math.max(0, shown.findIndex((i: any) => i.h.id === selectedId));
  const current = shown[index];
  const name = (id?: string) => (id && id !== "UNKNOWN" ? data.products.find((p: any) => p.id === id)?.name || id : "unknown item");
  async function dismissPicked() {
    const reason = prompt(`Dismiss ${picked.size} item(s): nothing to charge. Reason (kept in the audit log):`, "Test interactions, no real shopper");
    if (!reason || reason.trim().length < 3) return;
    const ids = [...picked];
    setBulk({ done: 0, total: ids.length });
    setError("");
    for (let k = 0; k < ids.length; k++) {
      try {
        await call(`/staff/review/${ids[k]}`, token, { decision: "DISMISS", session_id: null, quantity: 1, reason });
      } catch (e) {
        setError(String(e));
      }
      setBulk({ done: k + 1, total: ids.length });
    }
    setPicked(new Set());
    setBulk(null);
    await reload();
  }
  if (!items.length)
    return (
      <section className="panel">
        <div className="empty">
          <CheckCircle size={36} />
          <h3>Nothing to review.</h3>
          <p>When the shelf sees something it cannot settle on its own, it appears here.</p>
        </div>
      </section>
    );
  return (
    <div className="queue">
      <section className="panel queuelist">
        <div className="queuetools">
          <Tabs tabs={[["all", `All ${items.length}`], ["camera", `Camera ${items.filter((i: any) => i.s).length}`], ["other", "Weight & other"]]}
            value={filter} onChange={(v) => { setFilter(v); setSelectedId(""); }} />
          <label className="inline">
            <input type="checkbox" checked={shown.length > 0 && shown.every((i: any) => picked.has(i.h.id))}
              onChange={(e) => setPicked(e.target.checked ? new Set(shown.map((i: any) => i.h.id)) : new Set())} />
            Select all
          </label>
          {picked.size > 0 && (
            <button className="secondary" disabled={!!bulk} onClick={dismissPicked}>
              {bulk ? `Dismissing ${bulk.done}/${bulk.total}…` : `Dismiss ${picked.size} selected`}
            </button>
          )}
        </div>
        {error && <p className="error">{error}</p>}
        <ul>
          {shown.map((i: any) => {
            const act = i.s?.action || i.h.proposal?.action;
            const product = i.s?.product_id || i.h.proposal?.product_id;
            return (
              <li key={i.h.id} className={i === current ? "current" : ""}>
                <input type="checkbox" aria-label="Select" checked={picked.has(i.h.id)}
                  onChange={(e) => {
                    const next = new Set(picked);
                    e.target.checked ? next.add(i.h.id) : next.delete(i.h.id);
                    setPicked(next);
                  }} />
                <button className="queuerow" onClick={() => setSelectedId(i.h.id)}>
                  {i.h.thumb ? (
                    <AuthImage path={`/staff/evidence-file/${encodeURIComponent(i.h.thumb)}`} token={token} alt="" className="queuethumb" />
                  ) : (
                    <span className="queuethumb nothumb">no video</span>
                  )}
                  <span>
                  <b>{act === "PICKUP" ? "Picked up" : act === "RETURN" ? "Put back" : "Unclear movement"}{product ? ` · ${name(product)}` : ""}</b>
                  <small>{when(i.s?.captured || i.h.captured)} · {i.h.tray_id}{i.s ? ` · camera ${i.s.confidence}` : ""}</small>
                  <small className="muted">{(i.h.reason || []).slice(0, 2).map(human).join(" · ")}</small>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>
      <section className="panel queuedetail">
        {current ? (
          <>
            <div className="sectionhead">
              <h2>Item {index + 1} of {shown.length}</h2>
              <span>
                <button className="textbutton" disabled={index === 0} onClick={() => setSelectedId(shown[index - 1].h.id)}>Previous</button>{" "}
                <button className="textbutton" disabled={index >= shown.length - 1} onClick={() => setSelectedId(shown[index + 1].h.id)}>Next</button>
              </span>
            </div>
            <QuickReview key={current.h.id} item={current} data={data} token={token} action={action}
              onPrev={() => index > 0 && setSelectedId(shown[index - 1].h.id)}
              onNext={() => index < shown.length - 1 && setSelectedId(shown[index + 1].h.id)}
              onDone={async () => {
                const next = shown[index + 1] || shown[index - 1];
                setSelectedId(next ? next.h.id : "");
                await reload();
                call("/staff/preintegration/suggestions", token).then(setSuggestions).catch(() => {});
              }} />
          </>
        ) : (
          <p className="muted">Choose an item.</p>
        )}
      </section>
    </div>
  );
}

/** One review item: the clip plays at once; product and shopper are one tap each; Enter records, D dismisses. */
function QuickReview({ item, data, token, action, onDone, onPrev, onNext }: {
  item: { h: any; s?: any }; data: any; token: string; action: Action;
  onDone: () => Promise<void>; onPrev: () => void; onNext: () => void;
}) {
  const { h, s } = item;
  const [direction, setDirection] = useState<string>(s?.action || h.proposal?.action || "PICKUP");
  const [product, setProduct] = useState<string>(s?.product_id || h.proposal?.product_id || "");
  const [session, setSession] = useState<string>(s?.session_id || h.session_id || "");
  const [note, setNote] = useState("");
  const [more, setMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const key = useMemo(() => crypto.randomUUID(), [h.id]);
  const active = data.sessions.filter((x: any) => x.status === "ACTIVE");
  const guess = s?.product_id || h.proposal?.product_id;
  // Shelf weight step attributed to the shopper at the shelf (rfid attribution): confirm straight into the cart.
  const shelf = !s && h.proposal?.source === "SHELF_WEIGHT";
  const canRecord = (!!s || shelf) && !!product && !!session && !busy;
  async function run(fn: () => Promise<any>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await onDone();
    } catch (e) {
      setError(String((e as Error).message || e));
      setBusy(false);
    }
  }
  const record = () =>
    canRecord &&
    run(() =>
      shelf
        ? call(`/staff/review/${h.id}`, token, {
            decision: "CONFIRM",
            session_id: session,
            product_id: product,
            quantity: h.proposal.quantity || 1,
            reason: note.trim() || "Checked the shelf weight and camera",
          })
        : call(`/staff/preintegration/from-evidence/${h.id}`, token, {
        product_id: product,
        session_id: session,
        quantity_delta: (direction === "PICKUP" ? 1 : -1) * (s.quantity || 1),
        physical_confirmed: true,
        reason: note.trim() || "Checked the camera clip",
        idempotency_key: key,
      }),
    );
  const dismiss = () =>
    !busy &&
    run(() =>
      call(`/staff/review/${h.id}`, token, {
        decision: "DISMISS",
        session_id: null,
        quantity: 1,
        reason: note.trim() || "No real product movement in the clip",
      }),
    );
  // A shelf pickup nobody is charged for (staff took it, a shopper who wasn't tapped in): the scale proved it
  // left, so its weighed units leave the tray stock too. "Not a real movement" keeps the stock as it was.
  const leftTray = () =>
    !busy &&
    run(() =>
      call(`/staff/review/${h.id}`, token, {
        decision: "LEFT_TRAY",
        session_id: null,
        quantity: 1,
        reason: note.trim() || "Left the tray, nobody charged",
      }),
    );
  // An item put on the tray that no cart holds (staff restocking, an item already billed): new stock, no cart change.
  const restock = () =>
    !busy &&
    !!product &&
    run(() =>
      call(`/staff/review/${h.id}`, token, {
        decision: "RESTOCK",
        session_id: null,
        product_id: product,
        quantity: 1,
        reason: note.trim() || "Put on the tray as stock",
      }),
    );
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (["INPUT", "SELECT", "TEXTAREA"].includes(tag) || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "Enter") record();
      else if (e.key.toLowerCase() === "d") dismiss();
      else if (e.key === "ArrowRight") onNext();
      else if (e.key === "ArrowLeft") onPrev();
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  const name = (id: string) => data.products.find((p: any) => p.id === id)?.name || id;
  const defaultNote = s ? "Checked the camera clip" : shelf ? "Checked the shelf weight and camera" : "No real product movement in the clip";
  const why: string[] = s?.why || [
    ...(shelf
      ? [
          `Shelf weight ${h.proposal.weight_delta_g > 0 ? "+" : ""}${h.proposal.weight_delta_g} g.`,
          h.proposal.customer_source ? `Shopper from ${h.proposal.customer_source}.` : "No shopper was at the shelf: choose one.",
        ]
      : []),
    ...(h.reason || []).map(human),
  ];
  return (
    <div className="quickreview">
      <p className="quickwhen">
        {when(s?.captured || h.captured)} · {h.tray_id}
        {s ? ` · camera ${s.confidence}` : ""}
      </p>
      {h.frames > 0 || s ? <EvidencePlayer eventId={h.event_id} token={token} autoPlay /> : <p className="muted">No video stored for this item.</p>}
      <ul className="why">
        {why.map((w: string) => (
          <li key={w}>{w}</li>
        ))}
      </ul>
      {s || shelf ? (
        <>
          <div className="pickrow">
            <span>Movement</span>
            {/* A shelf weight step's direction is the weight's: down is a pickup, up a return. */}
            <button className={direction === "PICKUP" ? "pick selected" : "pick"} disabled={shelf} onClick={() => setDirection("PICKUP")}>Picked up</button>
            <button className={direction === "RETURN" ? "pick selected" : "pick"} disabled={shelf} onClick={() => setDirection("RETURN")}>Put back</button>
          </div>
          <div className="pickrow">
            <span>Product</span>
            <div className="productpicks">
              {data.products.map((p: any) => (
                <button key={p.id} className={product === p.id ? "productpick selected" : "productpick"} onClick={() => setProduct(p.id)}>
                  <AuthImage path={`/staff/product-image/${encodeURIComponent(p.id)}`} token={token} alt="" className="pickimg" />
                  <span>{p.name}</span>
                  {p.id === guess && <small>{shelf ? "suggested" : "camera guess"}</small>}
                </button>
              ))}
            </div>
          </div>
          <div className="pickrow">
            <span>Shopper</span>
            {active.length === 0 && (
              <small className="muted">No shopper is checked in, so this can only be dismissed. Check shoppers in under Shoppers & checkout.</small>
            )}
            {active.map((x: any) => (
              <button key={x.id} className={session === x.id ? "pick selected" : "pick"} onClick={() => setSession(x.id)}>
                {x.customer_alias || "Shopper"} · {x.id.slice(0, 4)}
              </button>
            ))}
          </div>
        </>
      ) : (
        <Suggestion hypothesisId={h.id} token={token} onUse={() => setMore(true)} />
      )}
      <input className="quicknote" value={note} onChange={(e) => setNote(e.target.value)} placeholder={`Note (optional). Default: "${defaultNote}"`} />
      <div className="quickbuttons">
        {(s || shelf) && (
          <button disabled={!canRecord} onClick={record}>
            Record: {direction === "PICKUP" ? "picked up" : "put back"} {product ? name(product) : "…"} <kbd>Enter</kbd>
          </button>
        )}
        {shelf && h.proposal.action === "RETURN" && (
          <button className="secondary" disabled={busy || !product} onClick={restock}>
            Put on the tray as stock {product ? `· ${name(product)}` : ""}
          </button>
        )}
        {shelf && h.proposal.action === "PICKUP" && (
          <button className="secondary" disabled={busy} onClick={leftTray}>
            Left the tray, nobody charged
          </button>
        )}
        <button className="danger" disabled={busy} onClick={dismiss}>
          Not a real movement <kbd>D</kbd>
        </button>
        <button className="secondary" onClick={onNext}>
          Skip <kbd>→</kbd>
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {!s && !shelf && (
        <details open={more} onToggle={(e) => setMore((e.target as HTMLDetailsElement).open)}>
          <summary>More options (confirm into a cart, corrections)</summary>
          {more && <ReviewCard h={h} data={data} token={token} action={action} />}
        </details>
      )}
    </div>
  );
}

// ---------- Products & training ----------

export function ProductsHub({ data, token, action, tab, product, go }: {
  data: any; token: string; action: Action; tab: string; product?: string; go: Go;
}) {
  const setTab = (t: string, p?: string) => go("Products & training", t, p);
  return (
    <>
      <Tabs tabs={[["catalogue", "Catalogue"], ["train", "Train with video"], ["photos", "Photo references"], ["questions", "Camera questions"]]}
        value={tab} onChange={(t) => setTab(t)} />
      {tab === "catalogue" && <Catalogue data={data} token={token} action={action} openTab={setTab} />}
      {tab === "train" && <TrainProducts key={product || "train"} data={data} token={token} initialProduct={product} />}
      {tab === "photos" && <ProductsRecognition data={data} action={action} token={token} compact openProduct={product} />}
      {tab === "questions" && <HelpTheCamera data={data} token={token} />}
    </>
  );
}

function Catalogue({ data, token, action, openTab }: { data: any; token: string; action: Action; openTab: (t: string, p?: string) => void }) {
  const [refs, setRefs] = useState<any>(null);
  const [trained, setTrained] = useState<string[] | null>(null);
  const [form, setForm] = useState({ product_id: "", name: "", price: "" });
  const [error, setError] = useState("");
  const [photoVersion, setPhotoVersion] = useState<Record<string, number>>({});
  useEffect(() => {
    call("/staff/references", token).then(setRefs).catch(() => {});
    fetch("/trainer/status", { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => (r.ok ? r.json() : null))
      .then((s) => setTrained(s?.active_model?.classes || []))
      .catch(() => setTrained(null));
  }, [token]);
  const photos = (pid: string) => (refs?.references || []).filter((r: any) => r.product_id === pid && r.status === "ACTIVE").length + (refs?.gallery_status?.products?.[pid] || 0);
  const stock = (pid: string) =>
    data.weights_enabled === false
      ? (data.assisted_stock || []).filter((s: any) => s.product_id === pid).reduce((n: number, s: any) => n + s.quantity, 0)
      : data.tray_units.filter((u: any) => u.product_id === pid && u.status === "ON_TRAY").length;
  return (
    <>
      <section className="panel">
        <h2>Products</h2>
        <table>
          <thead>
            <tr><th>Product</th><th>Price</th><th>On shelf</th><th>Video model</th><th>Photo references</th><th></th></tr>
          </thead>
          <tbody>
            {data.products.map((p: any) => (
              <tr key={p.id}>
                <td className="productcell">
                  <AuthImage path={`/staff/product-image/${encodeURIComponent(p.id)}?v=${photoVersion[p.id] || 0}`} token={token} alt="" className="pickimg" />
                  <span>{p.name}<br /><small className="muted">{p.id}</small></span>
                </td>
                <td>{money(p.price_minor)}</td>
                <td>{stock(p.id)}</td>
                <td>{trained === null ? <span className="muted">trainer off</span> : trained.includes(p.id) ? "✓ trained" : <span className="lowstock">not trained</span>}</td>
                <td>{photos(p.id) || <span className="lowstock">none</span>}</td>
                <td className="rowactions">
                  <button className="secondary" onClick={() => openTab("train", p.id)}>Train</button>
                  <PhotoButton productId={p.id} token={token} hasPhoto={!!photoVersion[p.id]} onDone={() => setPhotoVersion((v) => ({ ...v, [p.id]: Date.now() }))} />
                  <button className="textbutton" onClick={() => openTab("photos", p.id)}>Recognition photos</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="panel">
        <h2>Add a product</h2>
        <div className="setupgrid">
          <label>Name<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value, product_id: form.product_id || "" })} placeholder="Hand cream" /></label>
          <label>Short ID (lowercase)<input value={form.product_id} placeholder={form.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "hand-cream"}
            onChange={(e) => setForm({ ...form, product_id: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-") })} /></label>
          <label>Price (₹)<input type="number" min={0} step="0.01" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} /></label>
        </div>
        <button disabled={!form.name || form.price === ""}
          onClick={async () => {
            const id = form.product_id || form.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
            setError("");
            try {
              await action("/staff/catalogue", { product_id: id, name: form.name, price_minor: Math.round(Number(form.price) * 100) });
              setForm({ product_id: "", name: "", price: "" });
              openTab("train", id);
            } catch (e) {
              setError(String(e));
            }
          }}>
          Add and teach it
        </button>
        {error && <p className="error">{error}</p>}
      </section>
    </>
  );
}

export function PageIntro({ children }: { children: ReactNode }) {
  return <p className="lede">{children}</p>;
}
export { VideoCamera };
