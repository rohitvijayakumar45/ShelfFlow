import { useEffect, useRef, useState, PointerEvent } from "react";
import { API, call } from "./api";
import { AuthImage } from "./Insights";

type Box = [number, number, number, number];
type Draft = {
  id: string; product_id: string; original_file: string; width: number; height: number;
  status: string; revision: number; box: Box | null; plain_file: string | null;
  crop_file: string | null; created: number; details: any; reference_ids: string[];
};

export function ManualCapture({ token, products, tray, startOpen }: { token: string; products: any[]; tray: string; startOpen?: boolean }) {
  const [show, setShow] = useState(!!startOpen), [product, setProduct] = useState(products[0]?.id || "");
  const [drafts, setDrafts] = useState<Draft[]>([]), [selected, setSelected] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null), [live, setLive] = useState("");
  const [original, setOriginal] = useState(""), [box, setBox] = useState<Box | null>(null);
  const [confirmed, setConfirmed] = useState(false), [busy, setBusy] = useState(false);
  const [capturing, setCapturing] = useState(false), [error, setError] = useState("");
  const [notice, setNotice] = useState(""), [older, setOlder] = useState<number | null>(null);
  const start = useRef<[number, number] | null>(null);
  const retry = useRef<{ product: string; tray: string; key: string } | null>(null);
  const name = (id: string) => products.find(p => p.id === id)?.name || id;
  function remember(row: Draft) {
    setDrafts(rows => [row, ...rows.filter(r => r.id !== row.id)].sort((a, b) => b.created-a.created));
    setDraft(row);
  }
  useEffect(() => {
    if (!show) return;
    let stopped = false;
    call("/staff/manual-captures", token).then(r => { if (!stopped) { setDrafts(r.captures); setOlder(r.next_before); } }).catch(e => setError(String(e)));
    return () => { stopped = true; };
  }, [token, show]);
  useEffect(() => {
    if (!show) return;
    let stopped = false, url = "", timer: ReturnType<typeof setTimeout>;
    async function update() {
      try {
        const r = await fetch(API+"/staff/camera/raw-preview", { headers: { Authorization: `Bearer ${token}` } });
        if (!r.ok) throw Error("Camera unavailable. Saved captures are still available below.");
        const blob = await r.blob();
        if (!stopped) { if (url) URL.revokeObjectURL(url); url = URL.createObjectURL(blob); setLive(url); }
      } catch { if (!stopped) setLive(""); }
      if (!stopped) timer = setTimeout(update, 1000);
    }
    update();
    return () => { stopped = true; clearTimeout(timer); if (url) URL.revokeObjectURL(url); };
  }, [token, show]);
  useEffect(() => {
    if (!selected || !show) return;
    let stopped = false, timer: ReturnType<typeof setTimeout>;
    async function update() {
      try {
        const row = await call(`/staff/manual-captures/${selected}`, token);
        if (!stopped) {
          remember(row);
          if (["QUEUED", "PROCESSING"].includes(row.status)) timer = setTimeout(update, 600);
        }
      } catch (e) { if (!stopped) setError(String(e)); }
    }
    update();
    return () => { stopped = true; clearTimeout(timer); };
  }, [token, selected, show, draft?.status === "QUEUED"]);
  useEffect(() => {
    if (!draft) return;
    setBox(draft.box ? [draft.box[0]/draft.width, draft.box[1]/draft.height, draft.box[2]/draft.width, draft.box[3]/draft.height] : null);
    setConfirmed(false); start.current = null;
  }, [draft?.id, draft?.revision]);
  useEffect(() => {
    setOriginal("");
    if (!draft) return;
    let stopped = false, url = "";
    fetch(API+`/staff/catalogue-file/${draft.original_file}`, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => { if (!r.ok) throw Error("Saved original unavailable"); return r.blob(); })
      .then(blob => { if (!stopped) { url=URL.createObjectURL(blob); setOriginal(url); } })
      .catch(e => { if (!stopped) setError(String(e)); });
    return () => { stopped=true; if (url) URL.revokeObjectURL(url); };
  }, [draft?.id, token]);
  async function capture() {
    setCapturing(true); setError(""); setNotice("");
    if (!retry.current || retry.current.product !== product || retry.current.tray !== tray) retry.current = { product, tray, key: crypto.randomUUID() };
    try {
      const row = await call("/staff/manual-captures", token, { product_id: product, tray_id: tray,
        reason: "Manual original-frame capture", idempotency_key: retry.current!.key });
      remember(row); setSelected(row.id); retry.current=null;
      setNotice(`Snapshot saved (${row.details.capture_ms} ms). Draw a box around ${name(row.product_id)}.`);
    } catch (e) { setError(String(e)); } finally { setCapturing(false); }
  }
  const pending = !!draft && ["QUEUED", "PROCESSING"].includes(draft.status);
  const editable = !!draft && !pending && !["APPROVED", "DISCARDED"].includes(draft.status);
  const dirty = !!draft && !!box && (!draft.box || box.some((v, i) => Math.abs(v-draft.box![i]/(i%2 ? draft.height : draft.width)) > .00001));
  function point(e: PointerEvent<SVGSVGElement>): [number, number] {
    const r=e.currentTarget.getBoundingClientRect();
    return [Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)), Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))];
  }
  function draw(e: PointerEvent<SVGSVGElement>) {
    if (!start.current || !editable) return;
    const p=point(e), s=start.current;
    setBox([Math.min(s[0],p[0]),Math.min(s[1],p[1]),Math.max(s[0],p[0]),Math.max(s[1],p[1])]); setConfirmed(false);
  }
  async function operation(action: "segment" | "approve" | "discard") {
    if (!draft) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const body = action === "segment" ? { box } : action === "approve" ? { revision: draft.revision, physical_confirmed: confirmed } : { reason: "Staff discarded capture" };
      const row = await call(`/staff/manual-captures/${draft.id}/${action}`, token, body);
      remember(row);
      if (action === "approve") setNotice("Approved: product crop and SAM cutout added. Live gallery sync normally follows within about 10 seconds.");
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return <div className="teachcamera">
    <button className="secondary" onClick={() => setShow(!show)}>{show ? "Hide capture workspace" : "Show camera and capture manually"}</button>
    {show && <>
      <h3>Capture now. Crop and approve afterward.</h3>
      <p>No detection box is needed. Every valid camera snapshot is saved, including blurry or hand-covered views. Only approved crops enter the gallery.</p>
      <label>Product for the next capture<select value={product} onChange={e => setProduct(e.target.value)}>{products.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      {live ? <img className="manual-live" src={live} alt="Live original shelf view, without detector annotations" /> : <p className="muted">No fresh camera frame. You can still review saved captures.</p>}
      <button onClick={capture} disabled={capturing || busy || !product || !tray}>{capturing ? "Saving snapshot…" : "Capture now"}</button>
      {error && <p className="error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
      <h3>Saved capture queue</h3>
      <div className="capture-queue">{drafts.map(row => <button disabled={busy || capturing} className={row.id===selected ? "secondary active" : "secondary"} key={row.id}
        onClick={() => { setSelected(row.id); remember(row); setNotice(""); setError(""); }}>
        {name(row.product_id)} · {new Date(row.created*1000).toLocaleTimeString()} · {row.status.toLowerCase()}
      </button>)}</div>
      {older && <button className="textbutton" onClick={async () => { try { const r=await call(`/staff/manual-captures?before=${older}`,token); setDrafts(rows=>[...rows,...r.captures]);setOlder(r.next_before); } catch(e) {setError(String(e));} }}>Show older captures</button>}
      {!drafts.length && <p>No captures saved yet.</p>}
      {draft && <section className="capture-editor">
        <h3>Review: {name(draft.product_id)}</h3>
        <p>Draw a tight box around the whole product. Exclude hands and neighbouring objects. Or enter box coordinates below.</p>
        {original && <div className="capture-box" style={{ aspectRatio: `${draft.width}/${draft.height}` }}>
          <img src={original} alt={`Saved original for ${name(draft.product_id)}`} draggable={false} />
          <svg viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-label="Draw product selection"
            onPointerDown={e => { if (!editable) return; start.current=point(e); e.currentTarget.setPointerCapture(e.pointerId); setConfirmed(false); }}
            onPointerMove={draw} onPointerUp={e => { draw(e); start.current=null; }} onPointerCancel={() => {start.current=null;}}>
            {box && <rect x={box[0]*1000} y={box[1]*1000} width={(box[2]-box[0])*1000} height={(box[3]-box[1])*1000} />}
          </svg>
        </div>}
        <div className="capture-coordinates">{["Left %","Top %","Right %","Bottom %"].map((label,i)=><label key={label}>{label}<input type="number" min={0} max={100} step={1}
          disabled={!editable || busy} value={Math.round((box || [0,0,1,1])[i]*100)} onChange={e=>{const next:Box=box?[...box]:[0,0,1,1];next[i]=Number(e.target.value)/100;setBox(next);setConfirmed(false);}} /></label>)}</div>
        <button className="secondary" disabled={!editable || busy || !box} onClick={()=>operation("segment")}>{pending ? "SAM is processing…" : "Segment product with SAM"}</button>
        {pending && <p role="status">Snapshot saved. SAM is working separately; you can capture more views. First use includes model loading.</p>}
        {draft.status==="FAILED" && <p className="error">{draft.details.error} The original is preserved. Adjust the box and retry.</p>}
        {draft.status==="READY" && <>
          <div className="capture-previews"><figure><AuthImage path={`/staff/catalogue-file/${draft.plain_file}`} token={token} alt="Tight original product crop" /><figcaption>Original product crop</figcaption></figure>
            <figure><AuthImage path={`/staff/catalogue-file/${draft.crop_file}`} token={token} alt="SAM isolated product preview" /><figcaption>SAM background removed</figcaption></figure></div>
          {(draft.details.warnings || []).length>0 && <p className="notice">Check before approving: {draft.details.warnings.map((w:string)=>w.replaceAll("_"," ").toLowerCase()).join("; ")}. These warnings do not discard the capture.</p>}
          {dirty && <p>Selection changed. Run SAM again to preview that box.</p>}
          <label><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)} /> Both previews show the correct product ({name(draft.product_id)}), with enough detail and no unwanted object.</label>
          <button disabled={busy || !confirmed || dirty} onClick={()=>operation("approve")}>Approve and add to gallery</button>
        </>}
        {draft.status==="APPROVED" && <p>Approved. Both references are in the gallery registry. You can reject them in the References section.</p>}
        {draft.status!=="APPROVED" && draft.status!=="DISCARDED" && <button className="textbutton" disabled={busy} onClick={()=>operation("discard")}>Discard this capture</button>}
      </section>}
    </>}
  </div>;
}
