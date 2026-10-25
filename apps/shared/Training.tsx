import { useEffect, useMemo, useRef, useState } from "react";
import { MjpegView } from "./Live";
import { CheckCircle, Crosshair, Play, Record, Stop, Trash, WarningCircle } from "@phosphor-icons/react";

// Train products: record a clip -> mark the product once (SAM 2.1 boxes every frame)
// -> review blurred/odd frames -> fine-tune YOLO26n -> test live. Talks to tools/trainer.py via /trainer.
const T = "/trainer";

async function tcall(path: string, token: string, body?: unknown) {
  const r = await fetch(T + path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await r.text();
  let data: any = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw Object.assign(new Error("Training service not reachable. Start it with: python -m tools.trainer"), { status: r.status });
  }
  if (!r.ok) throw Object.assign(new Error(typeof data.detail === "string" ? data.detail : `HTTP ${r.status}`), { status: r.status });
  return data;
}

// Same rule as vision/training.py `included`.
const BOX_FLAGS = ["no_object", "tiny", "huge", "jump", "skipped"];
function isIncluded(f: any, threshold: number, kind: string) {
  if (f.manual !== null && f.manual !== undefined) return !!f.manual;
  if ((f.sharp || 0) < threshold) return false;
  if (kind === "background") return true;
  return !(f.flags || []).some((x: string) => BOX_FLAGS.includes(x));
}
const FLAG_TEXT: Record<string, string> = {
  no_object: "product not found",
  tiny: "box too small",
  huge: "box covers almost everything",
  jump: "box jumped",
  skipped: "not labelled",
};
const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/** Image behind staff auth, fetched only when scrolled into view. */
function AuthedImg({ src, token, alt, className }: { src: string; token: string; alt: string; className?: string }) {
  const [url, setUrl] = useState("");
  const holder = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let made = "",
      stopped = false;
    const load = () =>
      fetch(T + src, { headers: { Authorization: `Bearer ${token}` } })
        .then((r) => (r.ok ? r.blob() : Promise.reject()))
        .then((b) => {
          if (stopped) return;
          made = URL.createObjectURL(b);
          setUrl(made);
        })
        .catch(() => {});
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        io.disconnect();
        load();
      }
    }, { rootMargin: "300px" });
    if (holder.current) io.observe(holder.current);
    return () => {
      stopped = true;
      io.disconnect();
      if (made) URL.revokeObjectURL(made);
    };
  }, [src, token]);
  return <div ref={holder} className={className}>{url ? <img src={url} alt={alt} draggable={false} /> : <div className="imgwait" />}</div>;
}

function Bar({ value, label }: { value: number; label?: string }) {
  return (
    <div className="progress" role="progressbar" aria-valuenow={Math.round(value * 100)} aria-valuemin={0} aria-valuemax={100}>
      <span style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }} />
      {label && <b>{label}</b>}
    </div>
  );
}

export function TrainProducts({ data, token, initialProduct }: { data: any; token: string; initialProduct?: string }) {
  const [status, setStatus] = useState<any>(null),
    [sessions, setSessions] = useState<any[]>([]),
    [runs, setRuns] = useState<any[]>([]),
    [error, setError] = useState(""),
    [offline, setOffline] = useState(""),
    [product, setProduct] = useState(initialProduct || data.products[0]?.id || ""),
    [open, setOpen] = useState<{ id: string; mode: "mark" | "review" } | null>(null),
    [epochs, setEpochs] = useState(50),
    [busy, setBusy] = useState(false);
  const refreshLists = async () => {
    const [s, r] = await Promise.all([tcall("/sessions", token), tcall("/runs", token)]);
    setSessions(s);
    setRuns(r);
  };
  useEffect(() => {
    let stopped = false,
      timer: any,
      lastJob = "";
    async function poll() {
      try {
        const s = await tcall("/status", token);
        if (stopped) return;
        setStatus(s);
        setOffline("");
        const jobKey = s.job ? `${s.job.id}:${s.job.phase}` : "";
        if (jobKey !== lastJob) {
          lastJob = jobKey;
          refreshLists().catch(() => {});
        }
      } catch (e) {
        if (!stopped) setOffline(String((e as Error).message || e));
      }
      if (!stopped) timer = setTimeout(poll, 600);
    }
    poll();
    refreshLists().catch(() => {});
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [token]);
  async function run(fn: () => Promise<any>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await refreshLists();
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  }
  const rec = status?.recording;
  const job = status?.job;
  const jobRunning = job && !["done", "failed", "cancelled"].includes(job.phase);
  const nameOf = (pid: string) => data.products.find((p: any) => p.id === pid)?.name || pid;
  const perProduct = useMemo(() => {
    const out: Record<string, { frames: number; sessions: number; unlabelled: number }> = {};
    for (const s of sessions) {
      const key = s.kind === "background" ? "background" : s.product_id;
      out[key] = out[key] || { frames: 0, sessions: 0, unlabelled: 0 };
      if (s.kind === "product" && s.status !== "LABELLED") out[key].unlabelled++;
      else {
        out[key].frames += s.included;
        out[key].sessions++;
      }
    }
    return out;
  }, [sessions]);
  const trainable = Object.keys(perProduct).filter((k) => k !== "background" && perProduct[k].frames > 0);

  if (offline && !status)
    return (
      <section className="panel">
        <h2>Training service is not running</h2>
        <p>Training uses the laptop GPU, so it runs as a separate local service. Start it in a terminal from the ShelfFlow folder:</p>
        <pre>.\.venv\Scripts\python.exe -m tools.local_app trainer</pre>
        <p className="muted">
          Hardware setup (uses the shared camera preview on port 8765). To try the flow with the laptop webcam instead:{" "}
          <code>.\.venv\Scripts\python.exe -m tools.trainer --source webcam:0</code>
        </p>
        <p className="error">{offline}</p>
      </section>
    );

  return (
    <>
      <section className="panel">
        <h2>How it works</h2>
        <ol className="steps trainsteps">
          <li><b>Record</b> a short clip while you turn the product slowly in front of the camera.</li>
          <li><b>Mark</b> the product once — one click (or a box) on any frame. SAM 2.1 then finds it in every frame.</li>
          <li><b>Review</b>: blurred frames and odd boxes are left out automatically. Click any frame to include or exclude it.</li>
          <li><b>Train</b> YOLO26n on all products, then <b>test it live</b> and switch it on.</li>
        </ol>
        <div className="chips">
          <span className={status?.camera.connected ? "chip ok" : "chip bad"}>
            Camera: {status?.camera.connected ? `live · ${status.camera.fps} fps` : "no frames"}
          </span>
          <span className={status?.gpu.available ? "chip ok" : "chip warn"}>
            {status?.gpu.available ? `GPU: ${status.gpu.name}` : "CPU only — labelling and training will be slow"}
          </span>
          {status &&
            Object.entries(status.weights).map(([name, ok]) => (
              <span key={name} className={ok ? "chip ok" : "chip"}>
                {name.replace(".pt", "")}: {ok ? "ready" : "downloads on first use"}
              </span>
            ))}
          {status?.active_model && <span className="chip ok">Live model: run {status.active_model.run}</span>}
        </div>
        {offline && <p className="error">{offline}</p>}
        {error && <p role="alert" className="error">{error}</p>}
      </section>

      <section className="panel">
        <div className="sectionhead">
          <h2>1. Record</h2>
          <span className="tag">every camera frame is kept · max {status?.max_seconds || 120} s</span>
        </div>
        <div className="recordgrid">
          <div>
            <MjpegView url={T + "/stream"} token={token}>
              {rec && (
                <div className="recbadge">
                  <span className="recdot" /> REC {clock(rec.seconds)} · {rec.frames} frames · {rec.sharp} sharp
                </div>
              )}
              {rec?.recent_blurry && <div className="recwarn">Frames are blurry — turn the product more slowly</div>}
            </MjpegView>
          </div>
          <div className="recordside">
            <label>
              What are you recording?
              <select value={rec ? (rec.kind === "background" ? "__bg" : rec.product_id) : product} disabled={!!rec}
                onChange={(e) => setProduct(e.target.value)}>
                {data.products.map((p: any) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
                <option value="__bg">Empty tray / background (no product)</option>
              </select>
            </label>
            {!rec ? (
              <button className="recbutton" disabled={busy || !status?.camera.connected || !product}
                onClick={() => run(() => tcall("/recordings", token, product === "__bg"
                  ? { kind: "background" }
                  : { kind: "product", product_id: product, product_name: nameOf(product) }))}>
                <Record size={22} weight="fill" /> Start recording
              </button>
            ) : (
              <button className="recbutton stop" disabled={busy}
                onClick={() => run(async () => {
                  const s = await tcall("/recordings/stop", token, {});
                  if (s.kind === "product") setOpen({ id: s.id, mode: "mark" });
                })}>
                <Stop size={22} weight="fill" /> Stop · save {rec.frames} frames
              </button>
            )}
            <ul className="tips">
              <li>Hold the product over the tray, fully in view. Turn it slowly: front, back, sides, top, tilted, in your hand.</li>
              <li>30–60 s per product is plenty. Record again later in different light to make it more reliable.</li>
              <li>Record the <b>empty tray</b> once too, so the model learns what "no product" looks like.</li>
            </ul>
          </div>
        </div>
      </section>

      <section className="panel">
        <h2>2. Recordings</h2>
        {sessions.length === 0 ? (
          <p className="muted">No recordings yet.</p>
        ) : (
          <table>
            <thead>
              <tr><th>What</th><th>Recorded</th><th>Frames</th><th>Status</th><th></th></tr>
            </thead>
            <tbody>
              {sessions.map((s) => {
                const labelling = jobRunning && job.kind === "label" && job.session === s.id;
                return (
                  <tr key={s.id} className={open?.id === s.id ? "selectedrow" : ""}>
                    <td>{s.kind === "background" ? "Empty tray" : nameOf(s.product_id)}</td>
                    <td>{new Date(s.created * 1000).toLocaleString()}<br /><small className="muted">{s.duration?.toFixed(0)} s at {s.fps} fps</small></td>
                    <td>{s.frame_count}<br /><small className="muted">{s.blurry} blurry</small></td>
                    <td>
                      {labelling ? (
                        <Bar value={job.progress} label={`${Math.round(job.progress * 100)}%`} />
                      ) : s.kind === "product" && s.status !== "LABELLED" ? (
                        <span className="lowstock">needs marking</span>
                      ) : (
                        <span><CheckCircle size={16} /> {s.included} frames used</span>
                      )}
                    </td>
                    <td className="rowactions">
                      {s.kind === "product" && (
                        <button className="secondary" disabled={!!jobRunning} onClick={() => setOpen({ id: s.id, mode: "mark" })}>
                          <Crosshair size={18} /> {s.status === "LABELLED" ? "Re-mark" : "Mark"}
                        </button>
                      )}
                      {(s.kind === "background" || s.status === "LABELLED") && (
                        <button className="secondary" onClick={() => setOpen({ id: s.id, mode: "review" })}>Review</button>
                      )}
                      <button className="iconbutton" aria-label="Delete recording" disabled={labelling}
                        onClick={() => confirm("Delete this recording and its frames?") &&
                          run(async () => {
                            await tcall(`/sessions/${s.id}/delete`, token, {});
                            if (open?.id === s.id) setOpen(null);
                          })}>
                        <Trash size={18} />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {job?.kind === "label" && (
          <div className={job.phase === "failed" ? "result warn" : "result"} role="status">
            {job.message}
            {jobRunning && (
              <button className="textbutton" onClick={() => run(() => tcall("/jobs/cancel", token, {}))}>Cancel</button>
            )}
          </div>
        )}
      </section>

      {open && (
        <SessionEditor key={open.id + open.mode} id={open.id} mode={open.mode} token={token} job={job}
          onMode={(mode) => setOpen({ id: open.id, mode })} onClose={() => setOpen(null)} onChanged={refreshLists}
          onError={setError} nameOf={nameOf} />
      )}

      <section className="panel">
        <h2>3. Train YOLO26n</h2>
        {trainable.length === 0 ? (
          <p className="muted">Record and mark at least one product first.</p>
        ) : (
          <>
            <table>
              <thead>
                <tr><th>Product</th><th>Frames used</th><th>Recordings</th><th></th></tr>
              </thead>
              <tbody>
                {Object.entries(perProduct).map(([pid, v]) => (
                  <tr key={pid}>
                    <td>{pid === "background" ? "Empty tray (negatives)" : nameOf(pid)}</td>
                    <td>{v.frames}</td>
                    <td>{v.sessions}{v.unlabelled > 0 && <small className="lowstock"> + {v.unlabelled} not marked</small>}</td>
                    <td>
                      {pid !== "background" && v.frames < 80 && <small className="lowstock">few frames — record a bit more</small>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!perProduct.background && (
              <p className="muted"><WarningCircle size={16} /> No empty-tray recording yet: add one to reduce false detections.</p>
            )}
            <div className="actions">
              <label className="inline">
                Training length
                <select value={epochs} onChange={(e) => setEpochs(Number(e.target.value))} disabled={!!jobRunning}>
                  <option value={25}>Quick (25 epochs)</option>
                  <option value={50}>Standard (50 epochs)</option>
                  <option value={100}>Thorough (100 epochs)</option>
                </select>
              </label>
              <button disabled={busy || !!jobRunning || !!rec}
                onClick={() => run(() => tcall("/train", token, { epochs, imgsz: 640 }))}>
                <Play size={20} weight="fill" /> Train on {trainable.length} product{trainable.length === 1 ? "" : "s"}
              </button>
            </div>
          </>
        )}
        {job?.kind === "train" && <TrainProgress job={job} onStop={(force) => run(() => tcall(`/jobs/cancel${force ? "?force=true" : ""}`, token, {}))} />}
      </section>

      <Models runs={runs} token={token} status={status} busy={busy || !!jobRunning} run={run} nameOf={nameOf} />
    </>
  );
}

function TrainProgress({ job, onStop }: { job: any; onStop: (force: boolean) => void }) {
  const p = job.train || {};
  const running = !["done", "failed", "cancelled"].includes(job.phase);
  const elapsed = Date.now() / 1000 - job.started;
  const eta = job.progress > 0.03 && running ? elapsed / job.progress - elapsed : null;
  const last = p.metrics || {};
  return (
    <div className={job.phase === "failed" ? "result warn" : "result"} role="status">
      <div className="sectionhead">
        <b>
          {job.phase === "done" ? "Training finished" : job.phase === "failed" ? "Training failed" : job.phase === "cancelled" ? "Training stopped" : job.message || "Starting"}
        </b>
        {running && (
          <span>
            <button className="secondary" onClick={() => onStop(job.phase === "stopping")}>
              <Stop size={18} /> {job.phase === "stopping" ? "Stop now" : "Stop"}
            </button>
          </span>
        )}
      </div>
      <Bar value={job.progress} label={`${Math.round(job.progress * 100)}%`} />
      {p.batches > 0 && running && <Bar value={(p.batch || 0) / p.batches} label={`epoch ${p.epoch}/${p.epochs} · batch ${p.batch}/${p.batches}`} />}
      <p>
        {clock(elapsed)} elapsed{eta !== null && ` · about ${clock(eta)} left`}
        {p.device && ` · ${p.device}`}
        {last.map50 !== undefined && ` · mAP50 ${(last.map50 * 100).toFixed(1)}% · precision ${(last.precision * 100).toFixed(0)}% · recall ${(last.recall * 100).toFixed(0)}%`}
      </p>
      {job.phase === "failed" && <p className="error">{job.message}</p>}
      {(p.history || []).length > 1 && (
        <svg className="trainchart" viewBox="0 0 300 60" preserveAspectRatio="none" aria-label="Validation mAP50 per epoch">
          <polyline fill="none" strokeWidth="2"
            points={p.history.map((h: any, i: number) => `${(i / Math.max(1, p.history.length - 1)) * 300},${58 - (h.map50 || 0) * 56}`).join(" ")} />
        </svg>
      )}
    </div>
  );
}

function Models({ runs, token, status, busy, run, nameOf }: { runs: any[]; token: string; status: any; busy: boolean; run: (fn: () => Promise<any>) => void; nameOf: (p: string) => string }) {
  const test = status?.test;
  if (runs.length === 0) return null;
  return (
    <section className="panel">
      <h2>4. Trained models</h2>
      <table>
        <thead>
          <tr><th>Trained</th><th>Products</th><th>Accuracy (validation)</th><th></th></tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id}>
              <td>{new Date(r.created * 1000).toLocaleString()}<br /><small className="muted">{r.epochs} epochs · {r.device}</small></td>
              <td>{(r.classes || []).map(nameOf).join(", ")}</td>
              <td>
                {r.result ? (
                  <>
                    mAP50 <b>{(r.result.metrics.map50 * 100).toFixed(1)}%</b> · P {(r.result.metrics.precision * 100).toFixed(0)}% · R {(r.result.metrics.recall * 100).toFixed(0)}%
                    <br />
                    <small className="muted">
                      {Object.entries(r.result.per_class || {}).map(([k, v]: any) => `${nameOf(k)} ${(v.map50 * 100).toFixed(0)}%`).join(" · ")}
                    </small>
                  </>
                ) : (
                  <span className="muted">{r.has_weights ? "stopped early" : "no result"}</span>
                )}
              </td>
              <td className="rowactions">
                {r.active ? (
                  <span className="tag">in use</span>
                ) : (
                  r.has_weights && (
                    <button className="secondary" disabled={busy}
                      onClick={() => run(async () => {
                        const out = await tcall(`/runs/${r.id}/activate`, token, {});
                        alert(out.note);
                      })}>
                      Use this model
                    </button>
                  )
                )}
                {r.has_weights && (
                  <button className="secondary" disabled={busy}
                    onClick={() => run(() => (test?.run === r.id ? tcall("/test/stop", token, {}) : tcall("/test/start", token, { run: r.id })))}>
                    {test?.run === r.id ? "Stop test" : "Test live"}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {test && (
        <div className="recordgrid">
          <MjpegView url={T + "/test/stream"} token={token} />
          <div>
            <h3>Live test</h3>
            <p className="muted">Hold each product in front of the camera. Nothing here affects carts.</p>
            <p>{test.ms !== null && `${test.ms} ms per frame`}</p>
            {(test.detections || []).length === 0 ? <p>No product detected.</p> : test.detections.map((d: any, i: number) => (
              <p key={i}><b>{nameOf(d.product)}</b> {(d.confidence * 100).toFixed(0)}%</p>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function SessionEditor({ id, mode, token, job, onMode, onClose, onChanged, onError, nameOf }: {
  id: string; mode: "mark" | "review"; token: string; job: any; onMode: (m: "mark" | "review") => void;
  onClose: () => void; onChanged: () => Promise<void>; onError: (e: string) => void; nameOf: (p: string) => string;
}) {
  const [s, setS] = useState<any>(null);
  const load = () => tcall(`/sessions/${id}`, token).then(setS).catch((e) => onError(String(e.message || e)));
  useEffect(() => {
    load();
  }, [id]);
  const finished = job?.kind === "label" && job.session === id && job.phase === "done";
  useEffect(() => {
    if (finished) {
      load();
      onMode("review");
    }
  }, [finished]);
  if (!s) return <section className="panel"><p className="muted">Loading recording…</p></section>;
  const title = s.kind === "background" ? "Empty tray" : nameOf(s.product_id);
  return (
    <section className="panel">
      <div className="sectionhead">
        <h2>{mode === "mark" ? `Mark ${title}` : `Review ${title}`}</h2>
        <span><button className="textbutton" onClick={onClose}>Close</button></span>
      </div>
      {mode === "mark" ? (
        <Marker s={s} token={token} job={job} onError={onError} onStarted={onChanged} />
      ) : (
        <Review s={s} token={token} onSaved={(next) => { setS(next); onChanged(); }} onError={onError} />
      )}
    </section>
  );
}

function Marker({ s, token, job, onError, onStarted }: { s: any; token: string; job: any; onError: (e: string) => void; onStarted: () => Promise<void> }) {
  // Start at the sharpest frame: the product is usually steady and in view there.
  const sharpest = useMemo(() => s.frames.reduce((b: any, f: any) => (f.sharp > b.sharp ? f : b), s.frames[0]).i, [s.id]);
  const [frame, setFrame] = useState<number>(s.prompt?.frame ?? sharpest),
    [mark, setMark] = useState<any>(s.prompt?.box ? { box: s.prompt.box } : s.prompt?.point ? { point: s.prompt.point } : null),
    [drag, setDrag] = useState<number[] | null>(null),
    [quality, setQuality] = useState("fast");
  const stage = useRef<HTMLDivElement>(null);
  const running = job && job.kind === "label" && !["done", "failed", "cancelled"].includes(job.phase);
  const toImage = (e: React.PointerEvent) => {
    const r = stage.current!.getBoundingClientRect();
    return [((e.clientX - r.left) / r.width) * s.width, ((e.clientY - r.top) / r.height) * s.height];
  };
  const pct = (x: number, total: number) => `${(x / total) * 100}%`;
  const shown = drag ? { box: [Math.min(drag[0], drag[2]), Math.min(drag[1], drag[3]), Math.max(drag[0], drag[2]), Math.max(drag[1], drag[3])] } : mark;
  return (
    <div className="recordgrid">
      <div>
        <div ref={stage} className="markstage"
          onPointerDown={(e) => {
            if (running) return;
            const [x, y] = toImage(e);
            (e.target as HTMLElement).setPointerCapture(e.pointerId);
            setDrag([x, y, x, y]);
          }}
          onPointerMove={(e) => drag && setDrag([drag[0], drag[1], ...toImage(e)])}
          onPointerUp={() => {
            if (!drag) return;
            const w = Math.abs(drag[2] - drag[0]),
              h = Math.abs(drag[3] - drag[1]);
            setMark(w > 12 && h > 12 ? { box: shown!.box.map((v: number) => Math.round(v)) } : { point: [Math.round(drag[0]), Math.round(drag[1])] });
            setDrag(null);
          }}>
          <AuthedImg key={frame} src={`/sessions/${s.id}/frames/${frame}`} token={token} alt={`Frame ${frame}`} className="markimg" />
          {shown?.box && (
            <div className="markbox" style={{ left: pct(shown.box[0], s.width), top: pct(shown.box[1], s.height), width: pct(shown.box[2] - shown.box[0], s.width), height: pct(shown.box[3] - shown.box[1], s.height) }} />
          )}
          {shown?.point && <div className="markpoint" style={{ left: pct(shown.point[0], s.width), top: pct(shown.point[1], s.height) }} />}
        </div>
        <label className="framescrub">
          Frame {frame + 1} of {s.frames.length}
          <input type="range" min={0} max={s.frames.length - 1} value={frame} disabled={running}
            onChange={(e) => {
              setFrame(Number(e.target.value));
              setMark(null);
            }} />
        </label>
      </div>
      <div className="recordside">
        <p>
          Find a frame where the product is clear (use the slider), then <b>click on the product</b> — or drag a box
          around it for tricky shapes. SAM 2.1 follows it through the whole clip, forwards and backwards.
        </p>
        <label>
          Labelling
          <select value={quality} onChange={(e) => setQuality(e.target.value)} disabled={running}>
            <option value="fast">Fast (SAM 2.1 tiny)</option>
            <option value="precise">Precise (SAM 2.1 base, slower)</option>
          </select>
        </label>
        <button disabled={!mark || running}
          onClick={() =>
            tcall(`/sessions/${s.id}/label`, token, { frame, quality, ...mark })
              .then(onStarted)
              .catch((e) => onError(String(e.message || e)))
          }>
          <Crosshair size={20} /> Find the product in all {s.frames.length} frames
        </button>
        {running && job.session === s.id && <Bar value={job.progress} label={job.message} />}
        {!mark && <p className="muted">No mark yet.</p>}
      </div>
    </div>
  );
}

const PAGE = 48;
function Review({ s, token, onSaved, onError }: { s: any; token: string; onSaved: (s: any) => void; onError: (e: string) => void }) {
  const [threshold, setThreshold] = useState<number>(s.threshold),
    [filter, setFilter] = useState("all"),
    [page, setPage] = useState(0),
    [frames, setFrames] = useState<any[]>(s.frames);
  const kind = s.kind;
  const counts = useMemo(() => {
    const inc = frames.filter((f) => isIncluded(f, threshold, kind)).length;
    return {
      inc,
      blurry: frames.filter((f) => f.sharp < threshold).length,
      flagged: frames.filter((f) => (f.flags || []).some((x: string) => BOX_FLAGS.includes(x))).length,
      manual: frames.filter((f) => f.manual !== null && f.manual !== undefined).length,
    };
  }, [frames, threshold]);
  const visible = frames.filter((f) => {
    const inc = isIncluded(f, threshold, kind);
    if (filter === "included") return inc;
    if (filter === "excluded") return !inc;
    if (filter === "blurry") return f.sharp < threshold;
    if (filter === "flagged") return (f.flags || []).some((x: string) => BOX_FLAGS.includes(x));
    return true;
  });
  const pages = Math.max(1, Math.ceil(visible.length / PAGE));
  const save = (body: any) =>
    tcall(`/sessions/${s.id}/review`, token, body)
      .then(() => tcall(`/sessions/${s.id}`, token))
      .then((next) => {
        setFrames(next.frames);
        onSaved(next);
      })
      .catch((e) => onError(String(e.message || e)));
  function toggle(f: any) {
    const next = !isIncluded(f, threshold, kind);
    setFrames(frames.map((x) => (x.i === f.i ? { ...x, manual: next } : x)));
    save({ manual: { [f.i]: next } });
  }
  const sharpMax = Math.max(1, ...frames.map((f) => f.sharp));
  return (
    <>
      <div className="metrics reviewmetrics">
        <div><span>Frames used</span><strong>{counts.inc}</strong></div>
        <div><span>Blurry</span><strong>{counts.blurry}</strong></div>
        <div><span>Odd boxes</span><strong>{counts.flagged}</strong></div>
        <div><span>Your choices</span><strong>{counts.manual}</strong></div>
      </div>
      <label className="framescrub">
        Blur cut-off {threshold.toFixed(0)} {s.blur_threshold === null && <small className="muted">(automatic)</small>}
        <input type="range" min={0} max={Math.round(sharpMax)} value={threshold}
          onChange={(e) => setThreshold(Number(e.target.value))}
          onPointerUp={() => save({ blur_threshold: threshold })}
          onKeyUp={() => save({ blur_threshold: threshold })} />
      </label>
      <div className="actions">
        <button className="secondary" onClick={() => { setThreshold(s.auto_threshold); save({ auto_threshold: true }); }}>Automatic cut-off</button>
        <button className="secondary" disabled={!counts.manual} onClick={() => save({ reset_manual: true })}>Undo my choices</button>
        <span className="tabs reviewtabs">
          {["all", "included", "excluded", "blurry", "flagged"].map((f) => (
            <button key={f} className={filter === f ? "selected" : ""} onClick={() => { setFilter(f); setPage(0); }}>{f}</button>
          ))}
        </span>
      </div>
      <p className="muted">Click a frame to include or exclude it. Green = used for training. Boxes come from SAM 2.1.</p>
      <div className="framegrid">
        {visible.slice(page * PAGE, page * PAGE + PAGE).map((f) => {
          const inc = isIncluded(f, threshold, kind);
          const reasons = [...(f.sharp < threshold ? ["blurry"] : []), ...(f.flags || []).filter((x: string) => BOX_FLAGS.includes(x)).map((x: string) => FLAG_TEXT[x])];
          return (
            <button key={f.i} className={`framecell ${inc ? "in" : "out"}`} onClick={() => toggle(f)}
              title={`Frame ${f.i + 1} · sharpness ${f.sharp}${reasons.length ? " · " + reasons.join(", ") : ""}`}>
              <AuthedImg src={`/sessions/${s.id}/frames/${f.i}?thumb=true`} token={token} alt={`Frame ${f.i + 1}`} className="framethumb" />
              {f.box && (
                <span className="framebox2" style={{ left: `${(f.box[0] / s.width) * 100}%`, top: `${(f.box[1] / s.height) * 100}%`, width: `${((f.box[2] - f.box[0]) / s.width) * 100}%`, height: `${((f.box[3] - f.box[1]) / s.height) * 100}%` }} />
              )}
              <small>{f.i + 1}{reasons.length > 0 && ` · ${reasons[0]}`}{f.manual !== null && f.manual !== undefined && " · yours"}</small>
            </button>
          );
        })}
      </div>
      {pages > 1 && (
        <div className="actions">
          <button className="secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
          <span>Page {page + 1} of {pages}</span>
          <button className="secondary" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>Next</button>
        </div>
      )}
    </>
  );
}
