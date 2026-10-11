import { useState, useEffect, useRef, FormEvent, ReactNode } from "react";
import { QRCodeSVG } from "qrcode.react";
import type { IScannerControls } from "@zxing/browser";
import {
  ShoppingBag,
  ArrowRight,
  CheckCircle,
  WarningCircle,
  Storefront,
  Tray,
  Users,
  Camera,
  Receipt,
  SignOut,
  WifiHigh,
  QrCode,
  Scales,
} from "@phosphor-icons/react";
import { call, money, Cart } from "./api";

function Brand() {
  return (
    <a className="brand" href="/">
      <span className="brandmark">
        <Storefront size={24} />
      </span>
      ShelfFlow<span className="brandlabel">SMART STORE</span>
    </a>
  );
}
function Notice({ children }: { children: ReactNode }) {
  return (
    <div className="notice">
      <WarningCircle size={20} />
      <span>{children}</span>
    </div>
  );
}
function Login({
  staff,
  onLogin,
}: {
  staff?: boolean;
  onLogin: (t: string) => void;
}) {
  const [value, setValue] = useState(""),
    [accountPassword, setAccountPassword] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const data = await call(
        staff ? "/auth/staff" : "/auth/customer",
        "",
        staff
          ? { password: value }
          : { alias: value, password: accountPassword },
      );
      sessionStorage.setItem(
        staff ? "staff-token" : "customer-token",
        data.token,
      );
      onLogin(data.token);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <header>
        <Brand />
        <span className="tag">Prototype · simulated payment</span>
      </header>
      <main className="welcome">
        <div>
          <p className="eyebrow">
            {staff ? "STORE OPERATIONS" : "A LITTLE LESS CHECKOUT"}
          </p>
          <h1>
            {staff
              ? "A clear view of\nyour store."
              : "Pick it up.\nWe’ll keep track."}
          </h1>
          <p className="lede">
            {staff
              ? "Review shelf evidence, manage bracelets, and keep every cart accountable."
              : "Your shopping cart updates as you browse. Ambiguous interactions go to staff for review."}
          </p>
          <Notice>
            Demo coverage: two trays, one camera. Pick with the bracelet-wearing
            hand; hidden markers may need staff review.
          </Notice>
        </div>
        <form className="panel login" onSubmit={submit}>
          <ShoppingBag size={32} />
          <h2>{staff ? "Staff sign in" : "Start your visit"}</h2>
          <label>
            {staff ? "Staff password" : "Your demo name"}
            <input
              type={staff ? "password" : "text"}
              required
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={staff ? "Enter configured password" : "e.g. Rohit"}
              autoComplete={staff ? "current-password" : "given-name"}
            />
          </label>
          {!staff && (
            <label>
              Account password (leave empty for simulation)
              <input
                type="password"
                value={accountPassword}
                onChange={(e) => setAccountPassword(e.target.value)}
                autoComplete="current-password"
              />
            </label>
          )}
          <button disabled={busy}>
            {busy ? "Connecting…" : staff ? "Open operations" : "Get entry QR"}
            <ArrowRight />
          </button>
          <p className="muted">
            {staff
              ? "Local default password is documented in setup."
              : "Demo enrollment. No real payment will be collected."}
          </p>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
        </form>
      </main>
    </>
  );
}

export function Customer() {
  const [token, setToken] = useState(
      sessionStorage.getItem("customer-token") || "",
    ),
    [cart, setCart] = useState<Cart | null>(null),
    [connected, setConnected] = useState(false),
    [error, setError] = useState(""),
    [qr, setQr] = useState<{ token: string; expires_in: number } | null>(null),
    [busy, setBusy] = useState(false),
    [tab, setTab] = useState("cart");
  useEffect(() => {
    if (!token) return;
    let stop = false,
      ws: WebSocket,
      timer: ReturnType<typeof setTimeout>;
    const open = () => {
      ws = new WebSocket(
        `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/cart`,
      );
      ws.onopen = () => {
        if (stop) {
          ws.close();
          return;
        }
        ws.send(JSON.stringify({ token }));
      };
      ws.onmessage = (e) => {
        if (stop) return;
        const data = JSON.parse(e.data);
        setConnected(true);
        if (data.type === "snapshot")
          setCart((prev) =>
            !prev || data.version >= prev.version ? data : prev,
          );
      };
      ws.onclose = (event) => {
        if (stop) return;
        setConnected(false);
        if (event.code === 1008) {
          sessionStorage.removeItem("customer-token");
          setToken("");
          setCart(null);
          return;
        }
        if (!stop) timer = setTimeout(open, 2000);
      };
      ws.onerror = () => {
        if (!stop) setConnected(false);
      };
    };
    open();
    call("/customer/cart", token)
      .then((snapshot) => {
        if (!stop) setCart(snapshot);
      })
      .catch((e) => {
        if (stop) return;
        if (e.status === 401) {
          sessionStorage.removeItem("customer-token");
          setToken("");
          setCart(null);
        } else setError(String(e));
      });
    return () => {
      stop = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, [token]);
  async function action(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      setCart(await call("/customer/cart", token));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    if (qr) {
      const timer = setTimeout(() => setQr(null), qr.expires_in * 1000);
      return () => clearTimeout(timer);
    }
  }, [qr]);
  if (!token) return <Login onLogin={setToken} />;
  return (
    <>
      <header>
        <Brand />
        <div className="headeractions">
          <span className={connected ? "live" : "offline"}>
            <WifiHigh />
            {connected ? "Live cart" : "Reconnecting"}
          </span>
          <button
            className="iconbutton"
            aria-label="Sign out"
            onClick={() => {
              sessionStorage.removeItem("customer-token");
              setToken("");
              setCart(null);
            }}
          >
            <SignOut />
          </button>
        </div>
      </header>
      <div className="customerlayout">
        <main>
          <p className="eyebrow">YOUR VISIT</p>
          <h1>
            A good shop,
            <br />
            without the queue.
          </h1>
          <p className="lede">Pick up what you love. Return what you don’t.</p>
          <div className="visitsteps">
            {["Entry", "Shopping", "Receipt"].map((s, i) => (
              <span
                key={s}
                className={
                  cart &&
                  ((i === 0 && cart.status === "ENTRY") ||
                    (i === 1 && cart.status === "ACTIVE") ||
                    (i === 2 && cart.status === "COMPLETE"))
                    ? "current"
                    : ""
                }
              >
                {String(i + 1).padStart(2, "0")} {s}
              </span>
            ))}
          </div>
          <Notice>
            Use your bracelet-wearing hand. One camera covers this shelf;
            uncertain interactions stay pending and are excluded from your
            total.
          </Notice>
          <div className="tabs">
            <button
              className={tab === "cart" ? "selected" : ""}
              onClick={() => setTab("cart")}
            >
              Shopping cart
            </button>
            <button
              className={tab === "visit" ? "selected" : ""}
              onClick={() => setTab("visit")}
            >
              Entry & bracelet
            </button>
            <button
              className={tab === "receipt" ? "selected" : ""}
              onClick={() => setTab("receipt")}
            >
              Receipt
            </button>
          </div>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          {!cart ? (
            <p>Loading your visit…</p>
          ) : tab === "visit" || cart.status === "ENTRY" ? (
            <section className="panel entry">
              <QrCode size={28} />
              <h2>Your entry pass</h2>
              <p>
                Show this QR to the staff gate scanner. Staff verifies bracelet
                release before your cart activates.
              </p>
              {qr ? (
                <>
                  <QRCodeSVG value={qr.token} size={220} />
                  <details>
                    <summary>Manual token fallback</summary>
                    <code className="token">{qr.token}</code>
                  </details>
                  <p className="muted">
                    Expires after two minutes. Single use.
                  </p>
                </>
              ) : (
                <button
                  disabled={busy}
                  onClick={() =>
                    action(async () =>
                      setQr(await call("/customer/entry-token", token, {})),
                    )
                  }
                >
                  Generate entry QR
                </button>
              )}
              <p>
                Bracelet:{" "}
                {cart.bracelet
                  ? `${cart.bracelet.id} · ${cart.bracelet.state}`
                  : "Awaiting assignment"}
              </p>
            </section>
          ) : tab === "receipt" ? (
            <section className="panel">
              <Receipt size={28} />
              <h2>Your receipt</h2>
              {cart.receipt ? (
                <>
                  <p className="tag">SIMULATED PAYMENT</p>
                  {cart.receipt.items.map((i: any) => (
                    <div className="row" key={i.product_id}>
                      <span>
                        {i.quantity} × {i.name}
                      </span>
                      <b>{money(i.quantity * i.price_minor)}</b>
                    </div>
                  ))}
                  <div className="row">
                    <b>Total</b>
                    <b>{money(cart.receipt.total_minor)}</b>
                  </div>
                  <p className="muted">Order {cart.receipt.order_id}</p>
                  {cart.corrections.map((c: any) => (
                    <Notice key={c.id}>
                      Audited simulated adjustment: {money(c.amount_minor)} ·{" "}
                      {c.reason}
                    </Notice>
                  ))}
                </>
              ) : (
                <p>
                  Your receipt appears after reconciliation and simulated
                  payment.
                </p>
              )}
            </section>
          ) : (
            <section className="panel cart">
              <div className="sectionhead">
                <h2>Your basket</h2>
                <span>
                  {cart.items.reduce((n, i) => n + i.quantity, 0)} items
                </span>
              </div>
              {cart.items.length ? (
                cart.items.map((i) => (
                  <div className="cartrow" key={i.product_id}>
                    <div className="producticon">
                      <ShoppingBag size={30} />
                    </div>
                    <div>
                      <h3>{i.name}</h3>
                      <span className="muted">
                        {money(i.price_minor)} each · confirmed
                      </span>
                    </div>
                    <span className="quantity">× {i.quantity}</span>
                    <b>{money(i.quantity * i.price_minor)}</b>
                  </div>
                ))
              ) : (
                <div className="empty">
                  <ShoppingBag size={42} />
                  <h3>Room for something good.</h3>
                  <p>Confirmed pickups will appear here automatically.</p>
                </div>
              )}
              <p className="muted">
                Cart version {cart.version}. Prices are set by the store.
              </p>
            </section>
          )}
          {cart && cart.pending.length > 0 && (
            <section className="pending">
              <h2>
                {cart.pending.length} interaction
                {cart.pending.length > 1 ? "s" : ""} being reviewed
              </h2>
              <p>
                These are separate from your confirmed basket. Staff will
                reconcile them before payment.
              </p>
              {cart.pending.map((p) => (
                <p key={p.id}>
                  {p.tray_id} ·{" "}
                  {p.reasons.join(", ").replaceAll("_", " ").toLowerCase()}
                </p>
              ))}
            </section>
          )}
        </main>
        <aside className="summary panel">
          <p className="eyebrow">VISIT SUMMARY</p>
          <h2>
            {cart?.status === "COMPLETE"
              ? "You’re all set."
              : "Your running total"}
          </h2>
          <div className="total">{money(cart?.total_minor || 0)}</div>
          <div className="row">
            <span>Confirmed items</span>
            <b>{cart?.items.reduce((n, i) => n + i.quantity, 0) || 0}</b>
          </div>
          <div className="row">
            <span>Pending review</span>
            <b>{cart?.pending.length || 0}</b>
          </div>
          <div className="row">
            <span>Bracelet</span>
            <b>{cart?.bracelet?.id || "Not assigned"}</b>
          </div>
          <button
            disabled={
              busy ||
              !connected ||
              cart?.status !== "ACTIVE" ||
              !!cart?.pending.length
            }
            onClick={() =>
              action(() =>
                call("/customer/checkout", token, {
                  idempotency_key: crypto.randomUUID(),
                  outcome: "SUCCESS",
                }),
              )
            }
          >
            Finish simulated shop
            <ArrowRight />
          </button>
          <p className="muted">
            No money is collected. Emergency exit always stays open.
          </p>
          <button className="textbutton" onClick={() => setTab("receipt")}>
            View receipt
          </button>
          <details>
            <summary>Dispute a confirmed interaction</summary>
            <Dispute
              token={token}
              onDone={() => action(async () => {})}
              events={cart?.confirmed_events || []}
            />
          </details>
        </aside>
      </div>
      <footer>
        ShelfFlow prototype <span>Continuous sensing · No shelf taps</span>
      </footer>
    </>
  );
}
function Dispute({
  token,
  onDone,
  events,
}: {
  token: string;
  onDone: () => void;
  events: any[];
}) {
  const [id, setId] = useState(""),
    [reason, setReason] = useState(""),
    [error, setError] = useState("");
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        try {
          await call(`/customer/disputes/${id}`, token, { reason });
          onDone();
        } catch (e) {
          setError(String(e));
        }
      }}
    >
      <label>
        Confirmed interaction
        <select value={id} onChange={(e) => setId(e.target.value)} required>
          <option value="">Choose item interaction</option>
          {events.map((e) => (
            <option key={e.id} value={e.id}>
              {e.action} · {e.product_id} × {e.quantity}
            </option>
          ))}
        </select>
      </label>
      <label>
        What happened?
        <input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          required
          minLength={3}
        />
      </label>
      <button className="secondary">I did not take this</button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

const nav = [
  ["Overview", Storefront],
  ["Gate & bracelets", QrCode],
  ["Shelf setup", Tray],
  ["Evidence review", Camera],
  ["Sessions & receipts", Users],
] as const;
export function Staff() {
  const [token, setToken] = useState(
      sessionStorage.getItem("staff-token") || "",
    ),
    [page, setPage] = useState("Overview"),
    [data, setData] = useState<any>(null),
    [error, setError] = useState(""),
    [connectionError, setConnectionError] = useState("");
  const reload = async () => {
    try {
      setData(await call("/staff/state", token));
      setConnectionError("");
    } catch (e) {
      if ((e as { status?: number }).status === 401) {
        sessionStorage.removeItem("staff-token");
        setToken("");
        return;
      }
      setConnectionError(String(e));
    }
  };
  useEffect(() => {
    if (token) {
      reload();
      const t = setInterval(reload, 2000);
      return () => clearInterval(t);
    }
  }, [token]);
  const action = async (path: string, body: unknown) => {
    setError("");
    try {
      const result = await call(path, token, body);
      await reload();
      return result;
    } catch (e) {
      setError(String(e));
      throw e;
    }
  };
  if (!token) return <Login staff onLogin={setToken} />;
  const pending =
    data?.hypotheses.filter((h: any) =>
      ["PENDING_REVIEW", "DISPUTED"].includes(h.status),
    ) || [];
  return (
    <>
      <header>
        <Brand />
        <span className="tag">
          Store operations · {data?.mode || "connecting"}
        </span>
        <button
          className="iconbutton"
          aria-label="Sign out"
          onClick={() => {
            sessionStorage.removeItem("staff-token");
            setToken("");
          }}
        >
          <SignOut />
        </button>
      </header>
      <div className="stafflayout">
        <nav>
          {nav.map(([name, Icon]) => (
            <button
              key={name}
              className={page === name ? "active" : ""}
              onClick={() => setPage(name)}
            >
              <Icon size={21} />
              {name}
              {name === "Evidence review" && pending.length > 0 && (
                <span className="count">{pending.length}</span>
              )}
            </button>
          ))}
          <div className="navnote">
            ONE CAMERA
            <br />
            Two independently weighed trays
            <br />
            <b>Physical validation pending</b>
          </div>
        </nav>
        <main>
          <p className="eyebrow">SHELF 01 / DEMONSTRATION STORE</p>
          <h1>
            {page === "Overview" ? "Every interaction, accounted for." : page}
          </h1>
          <p className="lede">
            {page === "Overview"
              ? "A live view of your shelf, shoppers, and evidence."
              : "Changes are recorded in the store audit trail."}
          </p>
          {(error || connectionError) && (
            <p role="alert" className="error">
              {error || connectionError}
            </p>
          )}
          {!data ? (
            <p>Connecting to store…</p>
          ) : (
            <>
              {page === "Overview" && (
                <>
                  <div className="metrics">
                    <div>
                      <span>Active visits</span>
                      <strong>
                        {
                          data.sessions.filter(
                            (s: any) => s.status === "ACTIVE",
                          ).length
                        }
                      </strong>
                    </div>
                    <div>
                      <span>Awaiting review</span>
                      <strong>{pending.length}</strong>
                    </div>
                    <div>
                      <span>Available bracelets</span>
                      <strong>
                        {
                          data.bracelets.filter(
                            (b: any) => b.state === "AVAILABLE",
                          ).length
                        }
                      </strong>
                    </div>
                    <div>
                      <span>Tray stock</span>
                      <strong>
                        {data.trays.reduce(
                          (n: number, t: any) => n + t.stock,
                          0,
                        )}
                      </strong>
                    </div>
                  </div>
                  <div className="overviewgrid">
                    <section className="panel">
                      <div className="sectionhead">
                        <h2>Shelf status</h2>
                        <span className="tag">
                          {data.mode === "simulation"
                            ? "SIMULATION"
                            : "REVIEW FIRST"}
                        </span>
                      </div>
                      {data.trays.map((t: any) => (
                        <div className="shelftray" key={t.id}>
                          <Tray size={30} />
                          <div>
                            <h3>{t.id}</h3>
                            <p>
                              {
                                data.products.find(
                                  (p: any) => p.id === t.product_id,
                                )?.name
                              }
                            </p>
                          </div>
                          <b>
                            {t.stock}
                            <small>in stock</small>
                          </b>
                        </div>
                      ))}
                      <Notice>
                        No live shelf images connected yet. Configure the camera
                        and validate visibility before physical testing.
                      </Notice>
                    </section>
                    <section className="panel">
                      <h2>Device health</h2>
                      {data.devices.map((d: any) => (
                        <div className="row" key={d.id}>
                          <span>{d.id}</span>
                          <span
                            className={
                              Date.now() / 1000 - d.last_seen < 30
                                ? "live"
                                : "offline"
                            }
                          >
                            {d.health.ok === false
                              ? "Sensor fault"
                              : Date.now() / 1000 - d.last_seen < 30
                                ? "Recently seen"
                                : "No recent heartbeat"}
                          </span>
                        </div>
                      ))}
                      <p className="muted">
                        A recent message does not prove physical sensor
                        accuracy.
                      </p>
                    </section>
                  </div>
                  <section className="panel">
                    <h2>Simulation bench</h2>
                    <p>
                      These controls generate labelled evidence through device
                      ingestion and fusion. They are staff demo controls.
                    </p>
                    <Simulation data={data} action={action} />
                  </section>
                </>
              )}
              {page === "Gate & bracelets" && (
                <>
                  <Gate token={token} data={data} action={action} />
                  <section className="panel">
                    <h2>Bracelet inventory</h2>
                    <details>
                      <summary>Register a physical bracelet</summary>
                      <JsonForm
                        label="Bracelet registration"
                        initial={{
                          bracelet_id: "",
                          rfid: "",
                          markers: [],
                          cleaned: false,
                          reason: "",
                        }}
                        onSubmit={(body) => {
                          const { bracelet_id, ...setup } = body;
                          return action(
                            `/staff/bracelets/${encodeURIComponent(bracelet_id)}/setup`,
                            setup,
                          );
                        }}
                      />
                    </details>
                    <table>
                      <thead>
                        <tr>
                          <th>Bracelet</th>
                          <th>UID / markers</th>
                          <th>State</th>
                          <th>Reuse</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.bracelets.map((b: any) => (
                          <tr key={b.id}>
                            <td>{b.id}</td>
                            <td>
                              {b.rfid}
                              <br />
                              <small>{b.markers.join(", ")}</small>
                            </td>
                            <td>{b.state}</td>
                            <td>
                              <button
                                className="secondary"
                                onClick={() => {
                                  const reason = prompt(
                                    "Confirm physical return and cleaning. Audit reason:",
                                  );
                                  if (reason)
                                    action(`/staff/bracelets/${b.id}/return`, {
                                      reason,
                                      cleaned: true,
                                    }).catch(() => {});
                                }}
                              >
                                Confirm cleaned return
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </section>
                </>
              )}
              {page === "Shelf setup" && (
                <Setup data={data} action={action} token={token} />
              )}
              {page === "Evidence review" && (
                <section className="panel">
                  <h2>Pending evidence</h2>
                  {pending.length === 0 ? (
                    <div className="empty">
                      <CheckCircle size={36} />
                      <h3>Nothing awaiting review.</h3>
                      <p>Ambiguous interactions will appear here.</p>
                    </div>
                  ) : (
                    pending.map((h: any) => (
                      <ReviewCard
                        key={h.id}
                        h={h}
                        data={data}
                        token={token}
                        action={action}
                      />
                    ))
                  )}
                </section>
              )}
              {page === "Sessions & receipts" && (
                <>
                  <section className="panel">
                    <h2>Customer accounts</h2>
                    <p>
                      Staff can provision a local account for hardware
                      demonstrations. Alias-only entry remains limited to
                      simulation.
                    </p>
                    <JsonForm
                      label="Customer account"
                      initial={{
                        username: "",
                        alias: "",
                        password: "",
                        reason: "",
                      }}
                      onSubmit={(body) =>
                        action("/staff/customer-accounts", body)
                      }
                    />
                  </section>
                  <section className="panel">
                    <h2>Assisted exit reconciliation</h2>
                    <JsonForm
                      label="Flush request"
                      initial={{ session_id: "", reason: "" }}
                      onSubmit={(body) => action("/staff/reconciliation", body)}
                    />
                    <p className="muted">
                      Inspect device acknowledgments below. Offline or uncertain
                      devices require assisted reconciliation; egress stays
                      open.
                    </p>
                    {data.barriers.map((b: any) => (
                      <div className="row" key={b.id}>
                        <span>{b.device_id}</span>
                        <span>
                          {b.acknowledged_event
                            ? "Durably acknowledged"
                            : "Awaiting device checkpoint"}
                        </span>
                      </div>
                    ))}
                    <details>
                      <summary>Assisted simulated payment</summary>
                      <JsonForm
                        label="Assisted checkout"
                        initial={{
                          session_id: "",
                          idempotency_key: crypto.randomUUID(),
                          outcome: "SUCCESS",
                          reason: "",
                        }}
                        onSubmit={(body) =>
                          action("/staff/assisted-checkout", body)
                        }
                      />
                    </details>
                    <details>
                      <summary>
                        Reconcile an unpaid order after cart correction
                      </summary>
                      <JsonForm
                        label="Unpaid order"
                        initial={{ session_id: "", reason: "" }}
                        onSubmit={(body) =>
                          action("/staff/order-reconcile", body)
                        }
                      />
                    </details>
                  </section>
                  <section className="panel">
                    <h2>Visits</h2>
                    {data.sessions.map((s: any) => (
                      <div className="row" key={s.id}>
                        <code>{s.id.slice(0, 8)}</code>
                        <span>{s.status}</span>
                        <span>cart v{s.cart_version}</span>
                      </div>
                    ))}
                  </section>
                  <section className="panel">
                    <h2>Digital receipts</h2>
                    {data.receipts.length === 0 ? (
                      <p>No completed simulated payments yet.</p>
                    ) : (
                      data.receipts.map((r: any) => (
                        <div className="row" key={r.id}>
                          <Receipt />
                          <code>{r.order_id.slice(0, 8)}</code>
                          <b>{money(r.content.total_minor)}</b>
                          <span>SIMULATED</span>
                        </div>
                      ))
                    )}
                  </section>
                </>
              )}
            </>
          )}
        </main>
      </div>
      <footer>
        ShelfFlow prototype
        <span>
          Ownership stays with original picker. Transfers require staff.
        </span>
      </footer>
    </>
  );
}
type Action = (path: string, body: unknown) => Promise<any>;
function CameraPreview({ token }: { token: string }) {
  const [image, setImage] = useState(""),
    [error, setError] = useState(""),
    [enabled, setEnabled] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    let stopped = false,
      last = "";
    async function load() {
      try {
        const r = await fetch("/api/staff/camera/preview", {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!r.ok) throw new Error((await r.json()).detail);
        const blob = await r.blob();
        if (stopped) return;
        if (last) URL.revokeObjectURL(last);
        last = URL.createObjectURL(blob);
        setImage(last);
        setError("");
      } catch (e) {
        setError(String(e));
      }
    }
    load();
    const t = setInterval(load, 2000);
    return () => {
      stopped = true;
      clearInterval(t);
      if (last) URL.revokeObjectURL(last);
    };
  }, [token, enabled]);
  return (
    <section className="panel">
      <h2>Single-camera preview</h2>
      <p>
        Original JPEG preview. Tray ROIs use normalized coordinates of this full
        frame.
      </p>
      <button className="secondary" onClick={() => setEnabled(!enabled)}>
        {enabled ? "Stop preview" : "Start preview"}
      </button>
      {error && <p className="error">{error}</p>}
      {enabled && image && (
        <img
          style={{ maxWidth: "100%", display: "block", marginTop: 20 }}
          src={image}
          alt="Live single OV3660 shelf camera frame"
        />
      )}
    </section>
  );
}
function Gate({ data, action }: { token: string; data: any; action: Action }) {
  const [value, setValue] = useState(""),
    [result, setResult] = useState<any>(null),
    [scanError, setScanError] = useState(""),
    [scanning, setScanning] = useState(false);
  const video = useRef<HTMLVideoElement>(null);
  const controls = useRef<IScannerControls | null>(null);
  const liveResult =
    result &&
    (data.gates.find((g: any) => g.id === result.command_id) || result);
  useEffect(() => () => controls.current?.stop(), []);
  async function scan() {
    setScanning(true);
    try {
      controls.current = await new (
        await import("@zxing/browser")
      ).BrowserQRCodeReader().decodeFromVideoDevice(
        undefined,
        video.current!,
        (r) => {
          if (r) {
            setValue(r.getText());
            controls.current?.stop();
            setScanning(false);
          }
        },
      );
    } catch (e) {
      setScanError(String(e));
      setScanning(false);
    }
  }
  return (
    <section className="panel">
      <h2>Entry & dispenser</h2>
      <p>
        Use a phone or laptop camera here. The shelf OV3660 remains at the
        shelf.
      </p>
      <video ref={video} className="scanner" muted playsInline />
      <div className="actions">
        <button className="secondary" onClick={scan} disabled={scanning}>
          Start QR scanner
        </button>
        <button
          className="secondary"
          onClick={() => {
            controls.current?.stop();
            setScanning(false);
          }}
        >
          Stop camera
        </button>
      </div>
      {scanError && <p className="error">{scanError}</p>}
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            setResult(
              await action("/gate/dispense", {
                token: value,
                command_id: crypto.randomUUID(),
              }),
            );
          } catch {}
        }}
      >
        <label>
          Scanned entry token / manual fallback
          <input
            required
            value={value}
            onChange={(e) => setValue(e.target.value)}
            autoComplete="off"
          />
        </label>
        <button>Validate & reserve bracelet</button>
      </form>
      {result && (
        <Notice>
          Command {result.command_id} · {result.bracelet_id} ·{" "}
          {liveResult.state}. Physical UID and release sensors must acknowledge
          before activation.
        </Notice>
      )}
      <h3>Dispenser transactions</h3>
      {data.gates.map((g: any) => (
        <div className="row" key={g.id}>
          <code>{g.id.slice(0, 8)}</code>
          <span>
            {g.bracelet_id} · {g.state}
          </span>
          {data.mode === "simulation" && g.state === "DISPENSING" && (
            <button
              className="secondary"
              onClick={() =>
                action("/staff/simulation/gate", {
                  command_id: g.id,
                  scenario: "SUCCESS",
                }).catch(() => {})
              }
            >
              Simulate verified release
            </button>
          )}
          {g.detail && <span className="error">{g.detail}</span>}
          {["FAULTED", "DISPENSING"].includes(g.state) && (
            <details>
              <summary>Cancel after physical recovery</summary>
              <JsonForm
                label="Recovered bracelet"
                initial={{
                  actual_uid: "",
                  physically_recovered: false,
                  cleaned: false,
                  reason: "",
                }}
                onSubmit={(body) => action(`/staff/gates/${g.id}/cancel`, body)}
              />
            </details>
          )}
        </div>
      ))}
      <details>
        <summary>Audited manual assignment / fault recovery</summary>
        <JsonForm
          label="Assign verified bracelet"
          initial={{
            session_id: "",
            bracelet_id: "B01",
            actual_uid: "",
            physical_confirmed: false,
            reason: "",
          }}
          onSubmit={(b) => action("/staff/manual-assignment", b)}
        />
      </details>
    </section>
  );
}
function Simulation({ data, action }: { data: any; action: Action }) {
  const [sid, setSid] = useState(""),
    [tray, setTray] = useState("tray-a");
  return (
    <div className="simulation">
      <label>
        Active customer
        <select value={sid} onChange={(e) => setSid(e.target.value)}>
          <option value="">Choose visit</option>
          {data.sessions
            .filter((s: any) => s.status === "ACTIVE")
            .map((s: any) => (
              <option key={s.id} value={s.id}>
                {s.id.slice(0, 8)}
              </option>
            ))}
        </select>
      </label>
      <label>
        Tray
        <select value={tray} onChange={(e) => setTray(e.target.value)}>
          <option>tray-a</option>
          <option>tray-b</option>
        </select>
      </label>
      {[
        "PICKUP",
        "RETURN",
        "HIDDEN_MARKER",
        "CROSSED_HANDS",
        "CAMERA_FAILURE",
        "WEIGHT_FAULT",
      ].map((scenario) => (
        <button
          key={scenario}
          className="secondary"
          disabled={!sid}
          onClick={() =>
            action("/staff/simulation/interaction", {
              session_id: sid,
              tray_id: tray,
              scenario,
            }).catch(() => {})
          }
        >
          {scenario.replaceAll("_", " ").toLowerCase()}
        </button>
      ))}
    </div>
  );
}
function JsonForm({
  label,
  initial,
  onSubmit,
}: {
  label: string;
  initial: any;
  onSubmit: (b: any) => Promise<any>;
}) {
  const [values, setValues] = useState<Record<string, any>>(initial),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const labels: Record<string, string> = {
    username: "Account username",
    password: "Account password (at least 12 characters)",
    alias: "Customer alias",
    rfid: "Actual RFID UID (uppercase hex)",
    markers: "Printed marker IDs (comma separated)",
    cleaned: "Physical bracelet cleaned and ready",
    outcome: "Simulated payment outcome: SUCCESS, FAILURE or TIMEOUT",
    name: "Product name",
    price_minor: "Price in paise",
    unit_g: "Unit weight (g)",
    tolerance_g: "Weight tolerance (g)",
    measured: "Use measured weights",
    sample_weights_g: "Actual sample weights (g, comma separated)",
    product_id: "Catalogue SKU",
    roi: "Tray region: x1, y1, x2, y2 (0–1)",
    zero_raw: "Empty tray raw reading",
    mass_raw: "Known mass raw reading",
    known_mass_g: "Known mass (g)",
    noise_g: "Measured noise (g)",
    version: "Calibration version",
    delta: "Units added to tray",
    reason: "Audit reason",
    session_id: "Shopping visit ID",
    bracelet_id: "Bracelet ID",
    actual_uid: "Actual read RFID UID",
    physical_confirmed: "Physical release verified",
    quantity_delta: "Cart quantity adjustment",
    inventory_delta: "Verified stock adjustment",
  };
  function set(key: string, value: any) {
    setValues((previous) => ({
      ...previous,
      [key]: value,
      ...(previous.idempotency_key
        ? { idempotency_key: crypto.randomUUID() }
        : {}),
    }));
  }
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        try {
          const body = { ...values };
          for (const key of Object.keys(initial))
            if (Array.isArray(initial[key]) && typeof body[key] === "string")
              body[key] = body[key]
                .split(",")
                .filter((x: string) => x.trim())
                .map(Number);
          await onSubmit(body);
          setError("Saved");
        } catch (e) {
          setError(String(e));
        } finally {
          setBusy(false);
        }
      }}
    >
      {Object.keys(initial)
        .filter((key) => key !== "idempotency_key")
        .map((key) => (
          <label key={key}>
            {labels[key] || key.replaceAll("_", " ")}
            {typeof initial[key] === "boolean" ? (
              <input
                style={{
                  width: "auto",
                  display: "inline-block",
                  marginLeft: 12,
                }}
                type="checkbox"
                checked={values[key]}
                onChange={(e) => set(key, e.target.checked)}
              />
            ) : (
              <input
                type={
                  key === "password"
                    ? "password"
                    : typeof initial[key] === "number"
                      ? "number"
                      : "text"
                }
                step="any"
                value={
                  Array.isArray(values[key])
                    ? values[key].join(", ")
                    : values[key]
                }
                onChange={(e) =>
                  set(
                    key,
                    typeof initial[key] === "number"
                      ? Number(e.target.value)
                      : e.target.value,
                  )
                }
                required={key !== "sample_weights_g"}
              />
            )}
          </label>
        ))}
      <button className="secondary" disabled={busy}>
        {busy ? "Saving…" : `Save ${label.toLowerCase()}`}
      </button>
      {error && <p role="status">{error}</p>}
    </form>
  );
}
function Setup({
  data,
  action,
  token,
}: {
  data: any;
  action: Action;
  token: string;
}) {
  return (
    <>
      <CameraPreview token={token} />
      <Notice>
        Seed unit weights are simulated. Measure actual samples and enter a
        distribution tolerance before hardware testing. Calibration never runs
        automatically.
      </Notice>
      {data.trays.map((t: any) => (
        <section className="panel" key={t.id}>
          <h2>
            {t.id} · {t.product_id}
          </h2>
          {t.latest_sample ? (
            <div className="row">
              <span>Live raw reading: {t.latest_sample.weight.raw}</span>
              <span>
                Filtered: {t.latest_sample.weight.filtered ?? "Uncalibrated"} g
              </span>
              <span>
                {t.latest_sample.health.flags.join(", ") || "No reported flags"}
              </span>
            </div>
          ) : (
            <p className="muted">
              No raw sensor reading yet. Connect the sensor controller before
              calibration.
            </p>
          )}
          <div className="setupgrid">
            <JsonForm
              label="Tray ROI"
              initial={{ product_id: t.product_id, roi: t.roi }}
              onSubmit={(b) => action(`/staff/trays/${t.id}/setup`, b)}
            />
            <JsonForm
              label="Calibration"
              initial={{
                zero_raw: 0,
                mass_raw: 100000,
                known_mass_g: 1000,
                noise_g: 1,
                version: "measured-v1",
                reason: "Explicit empty-tray tare and known mass",
              }}
              onSubmit={(b) => action(`/staff/trays/${t.id}/calibration`, b)}
            />
            <JsonForm
              label="Restock"
              initial={{
                delta: 1,
                idempotency_key: crypto.randomUUID(),
                reason: "Physical restock verified",
              }}
              onSubmit={(b) => action(`/staff/trays/${t.id}/restock`, b)}
            />
          </div>
        </section>
      ))}
      <section className="panel">
        <h2>Catalogue</h2>
        {data.products.map((p: any) => (
          <details key={p.id}>
            <summary>
              {p.name} · {money(p.price_minor)} · {p.weight_source}
            </summary>
            <JsonForm
              label="Product"
              initial={{
                name: p.name,
                price_minor: p.price_minor,
                unit_g: p.unit_g,
                tolerance_g: p.tolerance_g,
                measured: false,
                sample_weights_g: [],
              }}
              onSubmit={(b) => action(`/staff/products/${p.id}`, b)}
            />
          </details>
        ))}
      </section>
    </>
  );
}
function ReviewCard({
  h,
  data,
  token,
  action,
}: {
  h: any;
  data: any;
  token: string;
  action: Action;
}) {
  const [evidence, setEvidence] = useState<any>(null),
    [sid, setSid] = useState(h.session_id || ""),
    [reason, setReason] = useState(""),
    [qty, setQty] = useState(h.proposal.quantity || 1);
  return (
    <article className="review">
      <h3>
        {h.tray_id} · {h.proposal.action || "Unresolved observation"}
      </h3>
      <p>{h.reason.join(" · ").replaceAll("_", " ").toLowerCase()}</p>
      <code>{h.id}</code>
      <button
        className="textbutton"
        onClick={() =>
          call(`/staff/evidence/${h.event_id}`, token).then(setEvidence)
        }
      >
        Load evidence
      </button>
      {evidence && (
        <>
          <EvidenceGallery refs={evidence.references} token={token} />
          <p className="muted">
            Model: {evidence.model_status} · clock uncertainty{" "}
            {evidence.clock_uncertainty_ms} ms · weight{" "}
            {evidence.weight
              ? `${evidence.weight.pre_g} → ${evidence.weight.post_g} g`
              : "unavailable"}
          </p>
          <details>
            <summary>Evidence references & health</summary>
            <pre>{JSON.stringify(evidence, null, 2)}</pre>
          </details>
        </>
      )}
      <label>
        Attributed session
        <select value={sid} onChange={(e) => setSid(e.target.value)}>
          <option value="">Choose verified owner</option>
          {data.sessions.map((s: any) => (
            <option value={s.id} key={s.id}>
              {s.id.slice(0, 8)} · {s.status}
            </option>
          ))}
        </select>
      </label>
      <label>
        Quantity
        <input
          type="number"
          min={1}
          max={10}
          value={qty}
          onChange={(e) => setQty(Number(e.target.value))}
        />
      </label>
      <label>
        Evidence-based decision reason
        <input value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      <div className="actions">
        <button
          disabled={!sid || reason.length < 3}
          onClick={() =>
            action(`/staff/review/${h.id}`, {
              decision: "CONFIRM",
              session_id: sid,
              quantity: qty,
              reason,
            }).catch(() => {})
          }
        >
          Confirm from evidence
        </button>
        <button
          className="secondary"
          disabled={reason.length < 3}
          onClick={() =>
            action(`/staff/review/${h.id}`, {
              decision: "DISMISS",
              session_id: null,
              quantity: qty,
              reason,
            }).catch(() => {})
          }
        >
          Dismiss
        </button>
      </div>
      <details>
        <summary>Audited correction / disputed or late event</summary>
        <JsonForm
          label="Correction"
          initial={{
            session_id: h.session_id || "",
            product_id: h.proposal.product_id || "",
            quantity_delta: -1,
            inventory_delta: 0,
            idempotency_key: crypto.randomUUID(),
            reason: "",
          }}
          onSubmit={(b) => action(`/staff/corrections/${h.id}`, b)}
        />
      </details>
    </article>
  );
}

function EvidenceGallery({ refs, token }: { refs: string[]; token: string }) {
  const [images, setImages] = useState<string[]>([]),
    [index, setIndex] = useState(0),
    [error, setError] = useState("");
  useEffect(() => {
    let stopped = false;
    const urls: string[] = [];
    async function load() {
      try {
        const frames = refs.filter((ref) => ref.endsWith(".jpg"));
        for (const ref of frames.slice(0, 60)) {
          const r = await fetch(
            `/api/staff/evidence-file/${encodeURIComponent(ref)}`,
            { headers: { Authorization: `Bearer ${token}` } },
          );
          if (!r.ok) continue;
          const url = URL.createObjectURL(await r.blob());
          urls.push(url);
        }
        if (!stopped) {
          setImages([...urls]);
          setIndex(0);
        }
      } catch (e) {
        setError(String(e));
      }
    }
    load();
    return () => {
      stopped = true;
      for (const url of urls) URL.revokeObjectURL(url);
    };
  }, [refs, token]);
  return (
    <div className="evidencegallery">
      {images.length ? (
        <>
          <img
            src={images[index]}
            alt={`Original evidence frame ${index + 1} of ${images.length}`}
            style={{ maxWidth: "100%", display: "block", marginTop: 16 }}
          />
          <label>
            Evidence frame {index + 1} / {images.length}
            <input
              type="range"
              min={0}
              max={images.length - 1}
              value={index}
              onChange={(e) => setIndex(Number(e.target.value))}
            />
          </label>
          <p className="muted">
            Frames show evidence for review. Co-visibility alone does not prove
            ownership.
          </p>
        </>
      ) : (
        <p className="muted">
          No accessible original JPEG frames. Simulated references contain no
          physical video.
        </p>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
