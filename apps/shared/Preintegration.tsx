import { useEffect, useState, FormEvent } from "react";
import { call, money } from "./api";
import { AuthImage, EvidencePlayer } from "./Insights";

export function Preintegration({ data, token, action }: {
  data: any; token: string; action: (path: string, body: unknown) => Promise<any>;
}) {
  const [product, setProduct] = useState(data.products[0]?.id || "");
  const [tray, setTray] = useState(data.trays[0]?.id || "");
  const [session, setSession] = useState("");
  const [quantity, setQuantity] = useState(1);
  const [direction, setDirection] = useState("PICKUP");
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [camera, setCamera] = useState<any>(null);
  const [image, setImage] = useState("");
  const [retry, setRetry] = useState<{ signature: string; key: string } | null>(null);
  useEffect(() => {
    let stopped = false;
    const refresh = () => call("/staff/preintegration/camera", token)
      .then(value => { if (!stopped) setCamera(value); })
      .catch(() => { if (!stopped) setCamera({ available: false }); });
    refresh(); const timer = setInterval(refresh, 5000);
    return () => { stopped = true; clearInterval(timer); };
  }, [token]);
  useEffect(() => () => { if (image) URL.revokeObjectURL(image); }, [image]);
  const active = data.sessions.filter((s: any) => s.status === "ACTIVE");
  const picks = data.assisted_stock || [];
  const recent = (data.recent_taps || []).filter((t: any) => Date.now() / 1000 - t.captured < 120);
  async function snapshot() {
    try {
      const response = await fetch("/staff/camera/preview", { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) throw Error("No fresh camera image. Check camera power and hotspot.");
      setImage(URL.createObjectURL(await response.blob()));
    } catch (e) { setError(String(e)); }
  }
  async function submit(kind: "COUNT" | "MOVE", event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setMessage("");
    const values: any = { tray_id: tray, product_id: product, reason, physical_confirmed: confirmed };
    if (kind === "COUNT") values.quantity = quantity;
    else { values.session_id = session; values.quantity_delta = direction === "PICKUP" ? quantity : -quantity; }
    const signature = JSON.stringify({ kind, ...values });
    const key = retry?.signature === signature ? retry.key : crypto.randomUUID();
    setRetry({ signature, key });
    try {
      await action(`/staff/preintegration/${kind === "COUNT" ? "stock" : "move"}`, { ...values, idempotency_key: key });
      setRetry(null); setConfirmed(false);
      setMessage(kind === "COUNT" ? "Physical stock count recorded. No weight was recorded." : "Confirmed item movement recorded. Customer cart updated.");
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  if (data.weights_enabled !== false) return <section className="panel"><h2>Assisted workflow is inactive</h2><p>Weight-based integration is enabled. Use the normal loading and evidence workflow.</p></section>;
  const fields = <>
    <label>Tray<select value={tray} onChange={e => setTray(e.target.value)}>{data.trays.map((t: any) => <option key={t.id}>{t.id}</option>)}</select></label>
    <label>Known product<select value={product} onChange={e => setProduct(e.target.value)}>{data.products.map((p: any) => <option key={p.id} value={p.id}>{p.name} · {money(p.price_minor)}</option>)}</select></label>
    <label>Quantity<input type="number" min={0} max={1000} required value={quantity} onChange={e => setQuantity(Number(e.target.value))} /></label>
    <label>Observation / reason<input value={reason} onChange={e => setReason(e.target.value)} minLength={3} maxLength={500} required placeholder="What did you physically count or observe?" /></label>
    <label><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /> I physically verified this count or movement and the product identity.</label>
  </>;
  return <>
    <section className="notice"><div><b>Assisted demonstration · weights unavailable</b><p>Staff confirm stock and each item movement. RFID taps identify bracelets; camera labels are suggestions. Unknown items stay unpriced until staff identify them. No camera-only automatic billing.</p></div></section>
    {error && <p className="error" role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    <CameraSuggestions data={data} token={token} action={action} />
    <section className="panel"><h2>Camera and readers</h2><p>Camera: <b>{camera?.available ? "fresh frames available" : "unavailable — reconnect camera before visual checks"}</b></p>
      {camera?.available && <p>Current suggestions: {(camera.predictions || []).map((p: any) => p.product || "Unknown").join(", ") || "No products detected"}</p>}
      <button className="secondary" onClick={snapshot} disabled={!camera?.available}>Show fresh detection image</button>
      {image && <div><img src={image} style={{ maxWidth: "100%" }} alt="Captured detector image; still image, not live video" /><p className="muted">Still image captured when requested. Refresh to see changes.</p></div>}
      <p>Recent reader activity (last two minutes): R1 {recent.filter((t: any) => t.reader === "R1").length} · R2 {recent.filter((t: any) => t.reader === "R2").length}. Counts are received reports, not a physical tap-test result.</p>
      {recent.slice(0, 6).map((t: any) => <p key={t.event_id}>{t.reader} · {t.uid} · {new Date(t.captured * 1000).toLocaleTimeString()}</p>)}
      <p className="muted">Use Gate & bracelets for verified handover. R2 scans do not take payment or close a session automatically.</p>
    </section>
    <section className="panel"><h2>Counted stock and confirmed movements</h2><p>This is a separate temporary inventory ledger. Existing measured units and calibration are preserved. Enter the number physically remaining on the shelf before recording pickups.</p>
      <form onSubmit={e => submit("MOVE", e)}>{fields}
        <label>Customer session<select value={session} onChange={e => setSession(e.target.value)}><option value="">Choose an active customer</option>{active.map((s: any) => <option key={s.id} value={s.id}>{s.customer_alias} · {s.id.slice(0, 8)}</option>)}</select></label>
        <label>Movement<select value={direction} onChange={e => setDirection(e.target.value)}><option value="PICKUP">Picked up / add to cart</option><option value="RETURN">Returned / remove from cart</option></select></label>
        <div className="row"><button disabled={busy || !confirmed || !session || quantity < 1 || quantity > 10}>Confirm observed movement</button><button type="button" className="secondary" disabled={busy || !confirmed || reason.trim().length < 3 || quantity < 0 || quantity > 1000} onClick={e => submit("COUNT", e)}>Save physical shelf count</button></div>
      </form>
      <table><thead><tr><th>Product</th><th>Tray</th><th>Counted on shelf</th></tr></thead><tbody>{picks.map((p: any) => <tr key={p.tray_id+":"+p.product_id}><td>{data.products.find((v: any) => v.id === p.product_id)?.name}</td><td>{p.tray_id}</td><td>{p.quantity}</td></tr>)}</tbody></table>
      <p className="muted">Checkout remains staff-assisted with a reconciliation reason and simulated payment, under Sessions & receipts. Check the customer's actual items and any pending evidence first.</p>
    </section>
    <section className="panel"><h2>Recent assisted actions</h2>{(data.assisted_actions || []).slice(0, 10).map((a: any) => <p key={a.id}>{new Date(a.created*1000).toLocaleTimeString()} · {a.kind} · {a.payload.product_id} · {a.payload.quantity_delta ?? a.payload.quantity} · {a.payload.reason}</p>)}<p className="muted">Duplicate request retries do not count twice. No weights are fabricated.</p></section>
  </>;
}

/** Camera interactions as pre-filled movements: staff check the clip, adjust if needed, and record. */
function CameraSuggestions({ data, token, action }: { data: any; token: string; action: (path: string, body: unknown) => Promise<any> }) {
  const [rows, setRows] = useState<any[]>([]);
  const [error, setError] = useState("");
  const load = () => call("/staff/preintegration/suggestions", token).then(setRows).catch(e => setError(String(e)));
  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t); }, [token]);
  return <section className="panel">
    <div className="sectionhead"><h2>Camera suggestions</h2><span className="tag">{rows.length} waiting</span></div>
    <p>The camera saw an item leave or arrive under a hand. Product comes from recognition, the customer from the bracelet
      at that hand or a bracelet read by R1/R2. Check the clip, correct anything, then record. Nothing reaches a cart until you do.</p>
    {error && <p className="error">{error}</p>}
    {!rows.length && <p className="muted">No camera interactions waiting.</p>}
    {rows.map(r => <SuggestionCard key={r.hypothesis_id} s={r} data={data} token={token} action={action} done={load} />)}
  </section>;
}

function SuggestionCard({ s, data, token, action, done }: { s: any; data: any; token: string; action: (path: string, body: unknown) => Promise<any>; done: () => void }) {
  const [product, setProduct] = useState(s.product_id || "");
  const [session, setSession] = useState(s.session_id || "");
  const [direction, setDirection] = useState(s.action);
  const [quantity, setQuantity] = useState(s.quantity || 1);
  const [confirmed, setConfirmed] = useState(false);
  const [reason, setReason] = useState("Checked camera clip");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [key] = useState(() => crypto.randomUUID());
  const [error, setError] = useState("");
  const active = data.sessions.filter((x: any) => x.status === "ACTIVE");
  const name = (id: string) => data.products.find((p: any) => p.id === id)?.name || id;
  async function record() {
    setBusy(true); setError("");
    try {
      await action(`/staff/preintegration/from-evidence/${s.hypothesis_id}`, { product_id: product, session_id: session,
        quantity_delta: direction === "PICKUP" ? quantity : -quantity, physical_confirmed: confirmed, reason, idempotency_key: key });
      done();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function dismiss() {
    const why = prompt("Dismiss this camera event (no item moved / not a product). Reason:");
    if (!why) return;
    try { await action(`/staff/review/${s.hypothesis_id}`, { decision: "DISMISS", session_id: null, quantity: 1, reason: why }); done(); }
    catch (e) { setError(String(e)); }
  }
  return <article className={s.confidence === "strong" ? "review result" : "review result warn"}>
    <div className="row"><b>{new Date(s.captured * 1000).toLocaleTimeString()} · {s.tray_id} · {s.action === "PICKUP" ? "picked up" : "returned"} · {s.product_id ? name(s.product_id) : "unknown item"}</b>
      <span className="tag">{s.confidence}</span></div>
    {s.frames.length > 0 && !open && <AuthImage path={`/staff/evidence-file/${encodeURIComponent(s.frames[Math.floor(s.frames.length / 2)])}`} token={token} alt="Middle frame of the camera clip" className="loadphoto" />}
    <ul className="why">{s.why.map((w: string) => <li key={w}>{w}</li>)}</ul>
    <button className="textbutton" onClick={() => setOpen(!open)}>{open ? "Hide clip" : "Play clip"}</button>
    {open && <EvidencePlayer eventId={s.event_id} token={token} />}
    <div className="setupgrid">
      <label>Product<select value={product} onChange={e => setProduct(e.target.value)}><option value="">Choose from the video</option>
        {data.products.map((p: any) => <option key={p.id} value={p.id}>{p.name} · {money(p.price_minor)}</option>)}</select></label>
      <label>Customer<select value={session} onChange={e => setSession(e.target.value)}><option value="">Choose from the bracelet in the video</option>
        {active.map((x: any) => <option key={x.id} value={x.id}>{x.customer_alias} · {x.id.slice(0, 8)}</option>)}</select></label>
      <label>Movement<select value={direction} onChange={e => setDirection(e.target.value)}><option value="PICKUP">Picked up</option><option value="RETURN">Returned</option></select></label>
      <label>Quantity<input type="number" min={1} max={10} value={quantity} onChange={e => setQuantity(Number(e.target.value))} /></label>
      <label>Reason<input value={reason} onChange={e => setReason(e.target.value)} minLength={3} /></label>
    </div>
    {s.stock === null && product && <p className="muted">Count {name(product)} on {s.tray_id} first (section below).</p>}
    <label><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /> I checked the clip: this item and this customer are right.</label>
    <div className="actions">
      <button disabled={busy || !confirmed || !product || !session || reason.trim().length < 3} onClick={record}>Record movement</button>
      <button className="secondary" onClick={dismiss}>Dismiss</button>
    </div>
    {error && <p className="error">{error}</p>}
  </article>;
}
