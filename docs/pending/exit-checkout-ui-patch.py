import pathlib
p = pathlib.Path("apps/shared/App.tsx")
s = p.read_text(encoding="utf-8")


def rep(a, b):
    global s
    assert s.count(a) == 1, a
    s = s.replace(a, b)


# Staff: exit check-out panel at the top of "Visits & checkout".
rep('''      {tab === "visits" && (
        <>
          <section className="panel">
            <h2>Visits</h2>''', '''      {tab === "visits" && (
        <>
          <ExitCheckout data={data} token={token} action={action} />
          <section className="panel">
            <h2>Visits</h2>''')

rep('''/** Register or relabel a physical bracelet: its RFID tag and the pattern printed on it. */''', '''/** Exit: find the visit (bracelet tap or list), check the cart, check out once, then take the bracelet back. */
function ExitCheckout({ data, token, action }: { data: any; token: string; action: Action }) {
  const [sid, setSid] = useState(""), [since, setSince] = useState(0), [visit, setVisit] = useState<any>(null), [error, setError] = useState("");
  const active = data.sessions.filter((x: any) => x.status === "ACTIVE");
  const tap = since > 0 ? data.recent_taps?.find((t: any) => t.captured >= since) : null;
  const tapped = tap && data.bracelets.find((b: any) => b.rfid === tap.uid);
  useEffect(() => { if (tapped?.session_id && tapped.session_id !== sid) setSid(tapped.session_id); }, [tapped?.session_id]);
  useEffect(() => {
    if (!sid) { setVisit(null); return; }
    fetch(`/api/staff/visits/${sid}`, { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.statusText))).then(setVisit).catch((e) => setError(String(e)));
  }, [sid, data]);
  const bracelet = visit?.bracelet && data.bracelets.find((b: any) => b.id === visit.bracelet.id);
  const money = (minor: number) => `₹${(minor / 100).toFixed(2)}`;
  return (
    <section className="panel">
      <h2>Check out at the exit</h2>
      <div className="actions">
        <button className="secondary" onClick={() => { setSince(Date.now() / 1000); setError(""); }}>Find by bracelet tap</button>
        <select value={sid} onChange={(e) => { setSid(e.target.value); setError(""); }}>
          <option value="">…or choose an active visit</option>
          {active.map((x: any) => <option key={x.id} value={x.id}>{x.customer_alias || x.id.slice(0, 8)}</option>)}
        </select>
      </div>
      {since > 0 && !tap && <p>Tap the customer's bracelet on a shelf reader…</p>}
      {tap && !tapped && <p className="error">Tag {tap.uid} is not a registered bracelet.</p>}
      {tapped && !tapped.session_id && <p className="error">{tapped.id} is not assigned to a visit.</p>}
      {error && <p className="error">{error}</p>}
      {visit && (
        <div className="result">
          <p><b>{data.sessions.find((x: any) => x.id === sid)?.customer_alias || "Shopper"}</b> · {visit.status.toLowerCase()} · bracelet {visit.bracelet ? `${visit.bracelet.id} (${patternOf(visit.bracelet.markers)})` : "none"}</p>
          {visit.items.length === 0 ? <p className="muted">Cart is empty.</p> : visit.items.map((i: any) => (
            <div className="row" key={i.product_id}><span>{i.quantity} × {i.name}</span><b>{money(i.quantity * i.price_minor)}</b></div>
          ))}
          <div className="row"><span>Total</span><b>{money(visit.total_minor)}</b></div>
          {visit.status === "ACTIVE" && visit.pending.length > 0 && (
            <p className="error">{visit.pending.length} pickup/return event(s) still need a decision in the Review queue before this visit can be billed.</p>
          )}
          {visit.status === "ACTIVE" && (
            <button disabled={visit.pending.length > 0}
              onClick={() => action("/staff/exit", { session_id: sid }).catch((e: any) => setError(String(e.message || e)))}>
              Check out and generate bill
            </button>
          )}
          {visit.status === "COMPLETE" && (
            <>
              <Notice>Bill generated: {money(visit.receipt?.total_minor || 0)}. Payment is SIMULATED (no money collected). The customer's app shows the receipt.</Notice>
              {bracelet?.session_id === sid && (
                <button className="secondary" onClick={() => action(`/staff/bracelets/${bracelet.id}/return`, { reason: "Returned at exit and cleaned", cleaned: true }).catch(() => {})}>
                  Bracelet {bracelet.id} returned and cleaned: release it
                </button>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}

/** Register or relabel a physical bracelet: its RFID tag and the pattern printed on it. */''')

# Customer: in hardware mode checkout happens at the exit with staff.
rep('''          <div className="row">
            <span>Bracelet</span>
            <b>{cart?.bracelet?.id || "Not assigned"}</b>
          </div>
          <button
            disabled={''', '''          <div className="row">
            <span>Bracelet</span>
            <b>{cart?.bracelet ? `${cart.bracelet.id}${cart.bracelet.markers?.length ? ` · ${patternOf(cart.bracelet.markers)}` : ""}` : "Not assigned"}</b>
          </div>
          {cart?.mode === "hardware" ? (
            cart.status === "ACTIVE" && <p><b>Done shopping?</b> Go to the exit: staff check you out and your bill appears here.</p>
          ) : (
          <button
            disabled={''')
rep('''            Finish simulated shop
            <ArrowRight />
          </button>''', '''            Finish simulated shop
            <ArrowRight />
          </button>
          )}''')
p.write_text(s, encoding="utf-8")
print("patched")
