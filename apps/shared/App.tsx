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
  Sparkle,
  ChartLine,
  GraduationCap,
  VideoCamera,
} from "@phosphor-icons/react";
import { call, money, Cart } from "./api";
import { Preintegration } from "./Preintegration";
import { ManualCapture } from "./ManualCapture";
import { Go, Home, LiveCamera, ProductsHub, ReviewQueue, StatusChips, Tabs, pendingOf, useCameraStatus } from "./StaffHub";
import { Insights, HelpTheCamera, ShelfMap, EvidencePlayer, Suggestion, ProductThumb, useCartToasts } from "./Insights";

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
export function Notice({ children }: { children: ReactNode }) {
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
            Shelf camera and weight sensors record changes. Staff review
            uncertain items and customer attribution before cart changes.
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
    [tab, setTab] = useState("cart"),
    [disputeFor, setDisputeFor] = useState("");
  const [toasts, dismissToast] = useCartToasts(
    cart ? [...cart.confirmed_events, ...(cart.assisted_events || [])] : undefined,
  );
  const disputeBox = useRef<HTMLDetailsElement>(null);
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
          ) : cart.status === "COMPLETE" ? (
            <section className="panel exitsummary">
              <CheckCircle size={36} />
              <h2>Thanks for shopping with us.</h2>
              <p>Your simulated payment is complete. Please drop your bracelet at the gate on the way out.</p>
              {(cart.receipt?.items || []).map((i: any) => (
                <div className="cartrow" key={i.product_id}>
                  <ProductThumb productId={i.product_id} token={token} hasImage={cart.items.find((c) => c.product_id === i.product_id)?.has_image ?? true} />
                  <div>
                    <h3>{i.name}</h3>
                    <span className="muted">{money(i.price_minor)} each</span>
                  </div>
                  <span className="quantity">× {i.quantity}</span>
                  <b>{money(i.quantity * i.price_minor)}</b>
                </div>
              ))}
              <div className="row">
                <b>Total paid (simulated)</b>
                <b>{money(cart.receipt?.total_minor || 0)}</b>
              </div>
              <p className="muted">
                Something wrong? Use “Dispute a confirmed interaction” within this visit, or ask staff. Order {cart.receipt?.order_id?.slice(0, 8)}.
              </p>
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
                    <ProductThumb productId={i.product_id} token={token} hasImage={i.has_image} />
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
          <details ref={disputeBox}>
            <summary>Dispute a confirmed interaction</summary>
            <Dispute
              key={disputeFor}
              token={token}
              preselect={disputeFor}
              onDone={() => action(async () => {})}
              events={cart?.confirmed_events || []}
            />
          </details>
        </aside>
      </div>
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div className="toast" key={t.id}>
            <span>
              {t.action === "RETURN" ? "Put back" : "Added"}: <b>{t.name || t.product_id}</b>
              {t.quantity > 1 ? ` × ${t.quantity}` : ""}
            </span>
            {t.weight_verified === false ? <span className="muted">Ask staff if this isn't yours</span> : <button
              className="textbutton"
              onClick={() => {
                setDisputeFor(t.id);
                if (disputeBox.current) disputeBox.current.open = true;
                disputeBox.current?.scrollIntoView({ behavior: "smooth", block: "center" });
                dismissToast(t.id);
              }}
            >
              Not mine?
            </button>}
          </div>
        ))}
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
  preselect = "",
}: {
  token: string;
  onDone: () => void;
  events: any[];
  preselect?: string;
}) {
  const [id, setId] = useState(preselect),
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
              {e.action === "RETURN" ? "Put back" : "Picked up"} · {e.name || e.product_id} × {e.quantity}
              {e.at ? ` · ${new Date(e.at * 1000).toLocaleTimeString()}` : ""}
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
  ["Daily", [["Home", Storefront], ["Live camera", VideoCamera], ["Review queue", Camera], ["Shoppers & checkout", Users], ["Stock", Scales]]],
  ["Products", [["Products & training", GraduationCap]]],
  ["Advanced", [["Insights", ChartLine], ["Shelf setup", Tray]]],
] as const;
const intro: Record<string, string> = {
  Home: "Everything at a glance. Click a tile to go where it needs you.",
  "Live camera": "Watch the shelf live, with or without the recognition boxes.",
  "Review queue": "Things the shelf could not settle on its own. Pick one on the left, decide on the right.",
  "Shoppers & checkout": "Check shoppers in with a bracelet, follow their visit, and check them out.",
  Stock: "How many of each product are on the shelf.",
  "Products & training": "Add products and teach the camera to recognise them.",
  Insights: "How well the shelf is doing over time.",
  "Shelf setup": "Tray regions, calibration and catalogue details. Rarely needed.",
};
export function Staff() {
  const [token, setToken] = useState(sessionStorage.getItem("staff-token") || ""),
    [route, setRoute] = useState<{ page: string; tab?: string; product?: string }>(() => {
      try {
        const saved = JSON.parse(sessionStorage.getItem("staff-route") || "null");
        return saved && intro[saved.page] ? saved : { page: "Home" };
      } catch {
        return { page: "Home" };
      }
    }),
    [data, setData] = useState<any>(null),
    [error, setError] = useState(""),
    [connectionError, setConnectionError] = useState("");
  const page = route.page;
  const go: Go = (next, tab, product) => {
    const value = { page: next, tab: tab || (next === "Products & training" ? "catalogue" : undefined), product };
    setRoute(value);
    setError("");
    try {
      sessionStorage.setItem("staff-route", JSON.stringify(value));
    } catch {}
    window.scrollTo({ top: 0 });
  };
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
  const camera = useCameraStatus(token);
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
  const pending = pendingOf(data);
  return (
    <>
      <header>
        <Brand />
        {data && <StatusChips data={data} camera={camera} go={go} />}
        <span className="tag">{data?.mode === "simulation" ? "Simulation" : "Live shelf"}</span>
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
          {nav.map(([group, items]) => (
            <div className="navgroup" key={group}>
              <p>{group}</p>
              {items.map(([name, Icon]) => (
                <button key={name} className={page === name ? "active" : ""} onClick={() => go(name)}>
                  <Icon size={21} />
                  {name}
                  {name === "Review queue" && pending.length > 0 && <span className="count">{pending.length}</span>}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <main>
          <h1>{page}</h1>
          <p className="lede">{intro[page]}</p>
          {(error || connectionError) && (
            <p role="alert" className="error">
              {error || connectionError}
            </p>
          )}
          {!data ? (
            <p>Connecting to store…</p>
          ) : (
            <>
              {page === "Home" && <Home data={data} token={token} action={action} camera={camera} go={go} />}
              {page === "Live camera" && <LiveCamera data={data} token={token} go={go} />}
              {page === "Review queue" && <ReviewQueue data={data} token={token} action={action} reload={reload} />}
              {page === "Shoppers & checkout" && <ShoppersPage data={data} token={token} action={action} />}
              {page === "Stock" &&
                (data.weights_enabled === false ? (
                  <Preintegration data={data} token={token} action={action} stockOnly />
                ) : (
                  <TrayLoading data={data} action={action} token={token} />
                ))}
              {page === "Products & training" && (
                <ProductsHub data={data} token={token} action={action} tab={route.tab || "catalogue"} product={route.product} go={go} />
              )}
              {page === "Insights" && <Insights data={data} action={action} token={token} />}
              {page === "Shelf setup" && <Setup data={data} action={action} token={token} />}
            </>
          )}
        </main>
      </div>
    </>
  );
}
function ShoppersPage({ data, token, action }: { data: any; token: string; action: Action }) {
  const [tab, setTab] = useState("entry");
  return (
    <>
      <Tabs tabs={[["entry", "Check-in & bracelets"], ["visits", "Visits & checkout"], ["accounts", "Accounts & receipts"]]} value={tab} onChange={setTab} />
      {tab === "entry" && (
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
      {tab === "visits" && (
        <>
          <section className="panel">
            <h2>Visits</h2>
            {data.sessions.length === 0 && <p className="muted">No visits yet.</p>}
            {data.sessions.map((s: any) => (
              <div className="row" key={s.id}>
                <b>{s.customer_alias || "Shopper"}</b>
                <code>{s.id.slice(0, 8)}</code>
                <span>{s.status.toLowerCase()}</span>
                <span className="muted">cart v{s.cart_version}</span>
              </div>
            ))}
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

        </>
      )}
      {tab === "accounts" && (
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
      <VerifiedAssignment data={data} action={action} />
    </section>
  );
}
function VerifiedAssignment({data,action}:{data:any;action:Action}){
  const [sid,setSid]=useState(""),[bid,setBid]=useState(""),[uid,setUid]=useState(""),
    [confirmed,setConfirmed]=useState(false),[reason,setReason]=useState("");
  const bracelet=data.bracelets.find((b:any)=>b.id===bid);
  const latest=data.recent_taps?.find((tap:any)=>Date.now()/1000-tap.captured<30);
  return <div className="panel">
    <h3>Assign a verified tag</h3>
    <p>Use this staff-assisted entry while the physical dispenser is unavailable.</p>
    <label>Customer visit<select value={sid} onChange={e=>setSid(e.target.value)}>
      <option value="">Choose customer</option>{data.sessions.filter((s:any)=>s.status==="ENTRY").map((s:any)=><option key={s.id} value={s.id}>{s.customer_alias || s.id.slice(0,8)}</option>)}
    </select></label>
    <label>Available bracelet<select value={bid} onChange={e=>{setBid(e.target.value);setConfirmed(false);}}>
      <option value="">Choose cleaned bracelet</option>{data.bracelets.filter((b:any)=>b.clean && b.state==="AVAILABLE").map((b:any)=><option key={b.id} value={b.id}>{b.id} · {b.rfid}</option>)}
    </select></label>
    <label>Verified RFID UID<input value={uid} onChange={e=>{setUid(e.target.value.toUpperCase());setConfirmed(false);}} /></label>
    <button className="secondary" disabled={!latest} onClick={()=>{setUid(latest.uid);setConfirmed(false);}}>Use latest tap</button>
    <label><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)} /> I checked the tag and issued it to this customer.</label>
    <label>Assignment note<input value={reason} onChange={e=>setReason(e.target.value)} /></label>
    <button disabled={!sid || !bracelet || uid!==bracelet.rfid || !confirmed || reason.trim().length<3}
      onClick={()=>action('/staff/manual-assignment',{session_id:sid,bracelet_id:bid,actual_uid:uid,physical_confirmed:confirmed,reason}).then(()=>{setSid("");setConfirmed(false);setReason("");}).catch(()=>{})}>Assign tag and start visit</button>
  </div>;
}
export function Simulation({ data, action }: { data: any; action: Action }) {
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
      <Notice>
        Seed unit weights are simulated. Measure actual samples and enter a
        distribution tolerance before hardware testing. Calibration never runs
        automatically.
      </Notice>
      {data.trays.map((t: any) => (
        <section className="panel" key={t.id}>
          <h2>
            {t.id} · {t.unit_tracking ? "mixed products (load via Tray loading)" : t.product_id}
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
export function ReviewCard({
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
    [qty, setQty] = useState(h.proposal.quantity || 1),
    [units,setUnits]=useState<string[]>(h.proposal.unit_ids || []);
  const mixed=data.trays.find((t:any)=>t.id===h.tray_id)?.unit_tracking;
  const eligible=data.tray_units.filter((u:any)=>u.tray_id===h.tray_id &&
    (h.proposal.action==="RETURN" ? u.status==="IN_CART" && u.session_id===sid : u.status==="ON_TRAY"));
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
          <EvidencePlayer eventId={h.event_id} token={token} />
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
      <Suggestion
        hypothesisId={h.id}
        token={token}
        onUse={(s) => {
          if (s.session_id) setSid(s.session_id);
          if (s.unit_ids?.length) setUnits(s.unit_ids);
          setQty(s.quantity || 1);
          setReason("Checked suggestion: " + s.why.join(" ").slice(0, 400));
          if (!evidence) call(`/staff/evidence/${h.event_id}`, token).then(setEvidence).catch(() => {});
        }}
      />
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
      {mixed && <fieldset><legend>Verify the physical items</legend>
        {eligible.map((u:any)=><label key={u.id}>
          <input type="checkbox" checked={units.includes(u.id)} onChange={e=>setUnits(e.target.checked?[...units,u.id]:units.filter(id=>id!==u.id))} />
          {data.products.find((p:any)=>p.id===u.product_id)?.name || u.product_id} · {u.weight_g.toFixed(1)} g · {u.id.slice(0,8)}
        </label>)}
        {!eligible.length && <p>No eligible loaded items. Check the customer and tray inventory.</p>}
      </fieldset>}
      <div className="actions">
        <button
          disabled={!sid || reason.length < 3 || (mixed && !units.length)}
          onClick={() =>
            action(`/staff/review/${h.id}`, {
              decision: "CONFIRM",
              session_id: sid,
              quantity: qty,
              reason,
              ...(mixed ? {unit_ids:units} : {}),
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
type WeightEvent = {
  event_id: string;
  delta_g: number;
  noise_g: number;
  capture_end: number;
  calibration_version: string;
  sensor_health: { ok: boolean; flags: string[] };
};
type Step = "choose" | "waiting" | "review";
function CatalogueImage({ name, token, alt }: { name: string; token: string; alt: string }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    let stopped = false,
      made = "";
    fetch(`/api/staff/catalogue-file/${encodeURIComponent(name)}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.blob() : Promise.reject()))
      .then((blob) => {
        if (stopped) return;
        made = URL.createObjectURL(blob);
        setUrl(made);
      })
      .catch(() => {});
    return () => {
      stopped = true;
      if (made) URL.revokeObjectURL(made);
    };
  }, [name, token]);
  return url ? <img className="loadphoto" src={url} alt={alt} /> : <div className="loadphoto empty-photo">No image</div>;
}
function TrayLoading({ data, action, token }: { data: any; action: Action; token: string }) {
  const [trayId, setTrayId] = useState(data.trays[0]?.id || ""),
    [reason, setReason] = useState(""),
    [step, setStep] = useState<Step>("choose"),
    [productId, setProductId] = useState(data.products[0]?.id || ""),
    [spot, setSpot] = useState(""),
    [since, setSince] = useState(0),
    [before, setBefore] = useState(""),
    [after, setAfter] = useState(""),
    [found, setFound] = useState<WeightEvent | null>(null),
    [ignored, setIgnored] = useState(""),
    [camera, setCamera] = useState("unknown"),
    [simGrams, setSimGrams] = useState(330),
    [result, setResult] = useState<any>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const tray = data.trays.find((t: any) => t.id === trayId);
  const load = data.tray_loads.find((l: any) => l.tray_id === trayId && l.status === "OPEN");
  const product = (id: string) => data.products.find((p: any) => p.id === id)?.name || id;
  const trayUnits = data.tray_units.filter((u: any) => u.tray_id === trayId);
  const onTray = trayUnits.filter((u: any) => u.status === "ON_TRAY");
  const loaded = load ? trayUnits.filter((u: any) => u.load_id === load.id) : [];
  const usedSpots = new Set(onTray.map((u: any) => u.spot).filter((s: any) => s !== null));
  const simulation = data.mode === "simulation";

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function still(loadId: string) {
    // A missing camera must not block loading; the unit is then saved without photos.
    try {
      const r = await call(`/staff/loads/${loadId}/snapshot`, token, {});
      setCamera("ok");
      return r.ref as string;
    } catch {
      setCamera("unavailable");
      return "";
    }
  }
  // Wait for the first settled increase recorded after "place item" was pressed.
  useEffect(() => {
    if (step !== "waiting" || !load) return;
    let stopped = false;
    const poll = async () => {
      try {
        const events: WeightEvent[] = await call(`/staff/trays/${trayId}/weight-events?since=${since}`, token);
        const fresh = events.filter((e) => e.capture_end >= since);
        const rising = fresh.find((e) => e.delta_g > 0);
        const falling = fresh.find((e) => e.delta_g < 0);
        if (falling) setIgnored(`Weight decreased by ${Math.abs(falling.delta_g).toFixed(1)} g — nothing should be removed while loading.`);
        if (rising && !stopped) {
          setFound(rising);
          setAfter(await still(load.id));
          setStep("review");
        }
      } catch (e) {
        setError(String(e));
      }
    };
    poll();
    const t = setInterval(poll, 1000);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [step, since, trayId, load?.id]);

  function resetItem() {
    setStep("choose");
    setFound(null);
    setBefore("");
    setAfter("");
    setIgnored("");
  }
  const live = tray?.latest_sample?.weight;
  return (
    <>
      <section className="panel">
        <div className="sectionhead">
          <h2>Load a mixed-product tray</h2>
          <label className="inline">
            Tray
            <select value={trayId} onChange={(e) => { setTrayId(e.target.value); resetItem(); setResult(null); }}>
              {data.trays.map((t: any) => (
                <option key={t.id} value={t.id}>{t.id}</option>
              ))}
            </select>
          </label>
        </div>
        <p>
          Each item is weighed on its own as you place it: the weight it adds becomes that item's fingerprint, and
          camera stills are saved with it. Place items one at a time and keep hands off the tray until the weight
          settles. The scale is never zeroed; each weight is the difference between two stable readings.
        </p>
        {error && <p role="alert" className="error">{error}</p>}
        {!tray ? null : !tray.unit_tracking ? (
          <div className="wizardstep">
            <h3>Step 1 · Switch this tray to per-item tracking</h3>
            {tray.stock > 0 ? (
              <Notice>
                {trayId} still counts {tray.stock} items in single-product mode. Empty and reconcile the tray before
                switching.
              </Notice>
            ) : (
              <>
                <label>
                  Audit reason
                  <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. mixed demo tray" />
                </label>
                <button disabled={busy || reason.trim().length < 3}
                  onClick={() => run(async () => { await action(`/staff/trays/${trayId}/unit-tracking`, { enabled: true, reason }); setReason(""); })}>
                  Enable per-item tracking
                </button>
              </>
            )}
          </div>
        ) : !load ? (
          <div className="wizardstep">
            <h3>Step 2 · Start a loading session</h3>
            <p className="muted">
              {onTray.length} item{onTray.length === 1 ? "" : "s"} currently on {trayId}. Calibration {tray.calibration_version}.
            </p>
            {tray.calibration_version === "UNSET" && (
              <Notice>Calibrate this tray first (Shelf setup). Weights from an uncalibrated tray are refused.</Notice>
            )}
            <label>
              Audit reason
              <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. morning restock" />
            </label>
            <button disabled={busy || reason.trim().length < 3}
              onClick={() => run(async () => { await action(`/staff/trays/${trayId}/loads`, { reason }); setReason(""); resetItem(); setResult(null); })}>
              Start loading
            </button>
          </div>
        ) : (
          <div className="wizardstep">
            <h3>Step 3 · Add items one by one</h3>
            <ol className="steps">
              <li className={step === "choose" ? "current" : "done"}>Choose product</li>
              <li className={step === "waiting" ? "current" : step === "review" ? "done" : ""}>Place one item</li>
              <li className={step === "review" ? "current" : ""}>Check and save</li>
            </ol>
            {step === "choose" && (
              <>
                <div className="setupgrid">
                  <label>
                    Product
                    <select value={productId} onChange={(e) => setProductId(e.target.value)}>
                      {data.products.map((p: any) => (
                        <option key={p.id} value={p.id}>{p.name}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Spot on tray (optional)
                    <select value={spot} onChange={(e) => setSpot(e.target.value)}>
                      <option value="">Not specified</option>
                      {[0, 1, 2, 3, 4, 5].map((n) => (
                        <option key={n} value={n} disabled={usedSpots.has(n)}>
                          Spot {n + 1}{usedSpots.has(n) ? " (occupied)" : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <button disabled={busy || !productId}
                  onClick={() => run(async () => {
                    setResult(null);
                    setBefore(await still(load.id));
                    setSince(Date.now() / 1000 - 2);
                    setStep("waiting");
                  })}>
                  Ready — I will place one {product(productId)}
                </button>
              </>
            )}
            {step === "waiting" && (
              <div className="waiting">
                <p className="weightreadout">
                  <Scales size={28} />
                  {live?.filtered != null ? `${Number(live.filtered).toFixed(1)} g on tray` : "Waiting for the tray reading…"}
                </p>
                <p>
                  Place <b>one {product(productId)}</b>{spot !== "" ? ` in spot ${Number(spot) + 1}` : ""} now, then take your
                  hands away. The item is recorded as soon as the weight settles.
                </p>
                {ignored && <Notice>{ignored}</Notice>}
                {simulation && (
                  <div className="simrow">
                    <label>
                      Simulated item weight (g)
                      <input type="number" step="any" value={simGrams} onChange={(e) => setSimGrams(Number(e.target.value))} />
                    </label>
                    <button className="secondary" disabled={busy}
                      onClick={() => run(async () => { await call("/staff/simulation/weight", token, { tray_id: trayId, delta_g: simGrams }); })}>
                      Simulate placement
                    </button>
                  </div>
                )}
                <button className="textbutton" onClick={resetItem}>Cancel this item</button>
              </div>
            )}
            {step === "review" && found && (
              <div className="review">
                <div className="metrics">
                  <div><span>Product</span><strong>{product(productId)}</strong></div>
                  <div><span>Item weight</span><strong>{found.delta_g.toFixed(1)} g</strong></div>
                  <div><span>Sensor noise</span><strong>±{Number(found.noise_g).toFixed(1)} g</strong></div>
                  <div><span>Spot</span><strong>{spot === "" ? "—" : Number(spot) + 1}</strong></div>
                </div>
                {!found.sensor_health.ok && <Notice>Sensor reported a fault for this reading. Saving will be refused; redo the placement.</Notice>}
                {found.calibration_version !== tray.calibration_version && (
                  <Notice>This reading used calibration {found.calibration_version}, not the tray's {tray.calibration_version}. Saving will be refused.</Notice>
                )}
                <div className="photos">
                  {before ? <CatalogueImage name={before} token={token} alt="Tray before placement" /> : <div className="loadphoto empty-photo">Before: no camera</div>}
                  {after ? <CatalogueImage name={after} token={token} alt="Tray after placement" /> : <div className="loadphoto empty-photo">After: no camera</div>}
                </div>
                {camera === "unavailable" && <p className="muted">Camera unavailable — the item will be saved without photos.</p>}
                <div className="actions">
                  <button disabled={busy}
                    onClick={() => run(async () => {
                      const r = await action(`/staff/loads/${load.id}/units`, {
                        product_id: productId,
                        weight_event_id: found.event_id,
                        spot: spot === "" ? null : Number(spot),
                        crop_refs: [before, after].filter(Boolean),
                      });
                      setResult({ ...r, product: product(productId) });
                      setSpot("");
                      resetItem();
                    })}>
                    Save item
                  </button>
                  <button className="secondary" disabled={busy} onClick={resetItem}>Redo this item</button>
                </div>
              </div>
            )}
            {result && (
              <div className={result.warnings.length ? "result warn" : "result"} role="status">
                <CheckCircle size={20} /> Saved {result.product} at {result.weight_g.toFixed(1)} g.
                {result.warnings.map((w: any) => (
                  <p key={w.unit_id}>
                    Weight is within {w.difference_g} g of a {product(w.product_id)} on this tray. The scale alone may not
                    tell them apart; the camera or staff review will decide.
                  </p>
                ))}
              </div>
            )}
            <h3>Loaded in this session ({loaded.length})</h3>
            <UnitTable units={loaded} product={product} action={action} />
            <button className="secondary" disabled={busy || step !== "choose"}
              onClick={() => run(async () => { await action(`/staff/loads/${load.id}/close`, {}); resetItem(); setResult(null); })}>
              Finish loading
            </button>
          </div>
        )}
      </section>
      {tray?.unit_tracking && (
        <section className="panel">
          <h2>On {trayId} now ({onTray.length})</h2>
          <UnitTable units={onTray} product={product} action={action} />
          <p className="muted">
            Items in carts: {trayUnits.filter((u: any) => u.status === "IN_CART").length}. Removed:{" "}
            {trayUnits.filter((u: any) => u.status === "REMOVED").length}.
          </p>
        </section>
      )}
    </>
  );
}
function UnitTable({ units, product, action }: { units: any[]; product: (id: string) => string; action: Action }) {
  if (!units.length) return <p className="muted">No items yet.</p>;
  return (
    <table>
      <thead>
        <tr><th>Product</th><th>Weight</th><th>Spot</th><th>Status</th><th>Look learned</th><th></th></tr>
      </thead>
      <tbody>
        {units.map((u) => (
          <tr key={u.id}>
            <td>{product(u.product_id)}</td>
            <td>{u.weight_g.toFixed(1)} g</td>
            <td>{u.spot === null ? "—" : u.spot + 1}</td>
            <td>{u.status.replace("_", " ").toLowerCase()}</td>
            <td>{lookStatus(u.reference_status)}</td>
            <td>
              {u.status === "ON_TRAY" && (
                <button className="textbutton"
                  onClick={() => {
                    const reason = prompt("Remove this item from the tray record. Audit reason:");
                    if (reason) action(`/staff/units/${u.id}/remove`, { reason }).catch(() => {});
                  }}>
                  Remove
                </button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
function lookStatus(status: string | null) {
  if (!status) return "—";
  if (status === "DONE") return "✓ learned";
  if (status === "PENDING") return "learning…";
  if (status === "NO_STILLS") return "no photo (camera off)";
  return "✗ " + status.replace("FAILED:", "").replaceAll("_", " ").toLowerCase();
}
// Manual snapshots are independent of the automatic teach collector.
function TeachCamera({ token, products, tray, startOpen }: {
  startOpen?: boolean; token: string; products: any[]; refs: any[]; sessions: any[]; tray: string; busy: boolean;
  run: (fn: () => Promise<any>) => Promise<void>; action: Action;
}) {
  return <ManualCapture token={token} products={products} tray={tray} startOpen={startOpen} />;
}
export function ProductsRecognition({ data, action, token, compact, openProduct }: {
  data: any; action: Action; token: string;
  /** Inside Products & training: no intro or add-product form (the Catalogue tab has them), capture open. */
  compact?: boolean; openProduct?: string;
}) {
  const [info, setInfo] = useState<any>(null),
    [error, setError] = useState(""),
    [newProduct, setNewProduct] = useState({ product_id: "", name: "", price: "" }),
    [teachTray, setTeachTray] = useState(data.trays[0]?.id || ""),
    [seconds, setSeconds] = useState(20),
    [busy, setBusy] = useState(false),
    [open, setOpen] = useState<string>(openProduct || "");
  const load = async () => {
    try {
      setInfo(await call("/staff/references", token));
      setError("");
    } catch (e) {
      setError(String(e));
    }
  };
  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [token]);
  async function run(fn: () => Promise<any>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  const refs: any[] = info?.references || [];
  const status = info?.gallery_status;
  const teaching = (info?.teach_sessions || []).find((t: any) => t.status === "ACTIVE" && t.until > Date.now() / 1000);
  const unitsOf = (pid: string) => data.tray_units.filter((u: any) => u.product_id === pid && u.status === "ON_TRAY").length;
  const pairs: any[] = status?.pairs || [];
  const groups: any[] = status?.groups || [];
  return (
    <>
      {!compact && <section className="panel">
        <h2>How new products are learned</h2>
        <p>
          Use <b>Capture now</b> to save an original frame, draw a box around the product, and review its SAM crop
          before adding it to the gallery. Automatic <b>Teach</b> still collects diverse views over a short session.
          Measured restocking can also supply before/after reference crops. Reject references that show the wrong thing.
          <b>Train products</b> (left menu) records a short video per product, boxes it with SAM 2.1 and trains
          the YOLO26n product model.
        </p>
        <Notice>
          Vision never charges on its own. Per-item weight and position come first; if the camera disagrees, the event
          goes to review. A product that was never loaded or taught can be mistaken for a similar-looking one.
        </Notice>
      </section>}
      {error && <p role="alert" className="error">{error}</p>}
      <section className="panel">
        <div className="sectionhead">
          <h2>Recognition status</h2>
          <span className="tag">{status ? status.version : "vision not connected"}</span>
        </div>
        {status ? (
          <>
            <p className="muted">
              {status.live_references} live reference{status.live_references === 1 ? "" : "s"} learned on this shelf · updated{" "}
              {Math.max(0, Math.round(Date.now() / 1000 - status.received))} s ago
            </p>
            {pairs.length === 0 ? (
              <p>No look-alike products: the camera tells every enrolled product apart.</p>
            ) : (
              pairs.map((p: any) => {
                const group = groups.find((g: any) => g.products.includes(p.a));
                return (
                  <div key={p.a + p.b} className={group?.usable ? "result" : "result warn"}>
                    <b>{data.products.find((x: any) => x.id === p.a)?.name || p.a}</b> and{" "}
                    <b>{data.products.find((x: any) => x.id === p.b)?.name || p.b}</b> look alike (similarity {p.similarity}).
                    <p>
                      {group?.usable
                        ? `A dedicated look-alike check tells them apart (validated ${Math.round(group.cv_accuracy * 100)}% on held-out views).`
                        : group?.advice || "Rely on weight and position, or keep them on different trays."}
                    </p>
                  </div>
                );
              })
            )}
          </>
        ) : (
          <p className="muted">Start the vision service to sync references and report look-alikes.</p>
        )}
      </section>
      {!compact && <section className="panel">
        <h2>Add a product</h2>
        <div className="setupgrid">
          <label>
            Product ID (lowercase, e.g. hand-cream)
            <input value={newProduct.product_id} onChange={(e) => setNewProduct({ ...newProduct, product_id: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-") })} />
          </label>
          <label>
            Name
            <input value={newProduct.name} onChange={(e) => setNewProduct({ ...newProduct, name: e.target.value })} />
          </label>
          <label>
            Price (₹)
            <input type="number" min={0} step="0.01" value={newProduct.price} onChange={(e) => setNewProduct({ ...newProduct, price: e.target.value })} />
          </label>
        </div>
        <button
          disabled={busy || newProduct.product_id.length < 2 || !newProduct.name || newProduct.price === ""}
          onClick={() =>
            run(async () => {
              await action("/staff/catalogue", { product_id: newProduct.product_id, name: newProduct.name, price_minor: Math.round(Number(newProduct.price) * 100) });
              setNewProduct({ product_id: "", name: "", price: "" });
            })
          }
        >
          Create product
        </button>
        <p className="muted">Then teach it (below) and load units with the tray-loading wizard to record their weights.</p>
      </section>}
      <section className="panel">
        <div className="sectionhead">
          <h2>Catalogue</h2>
          <label className="inline">
            Teach on
            <select value={teachTray} onChange={(e) => setTeachTray(e.target.value)}>
              {data.trays.map((t: any) => (
                <option key={t.id} value={t.id}>{t.id}</option>
              ))}
            </select>
            <select value={seconds} onChange={(e) => setSeconds(Number(e.target.value))}>
              {[10, 20, 30, 45].map((n) => (
                <option key={n} value={n}>{n} s</option>
              ))}
            </select>
          </label>
        </div>
        {teaching && teaching.max_refs > 1 && (
          <div className="result" role="status">
            Teaching <b>{data.products.find((p: any) => p.id === teaching.product_id)?.name}</b>: turn it slowly over the tray —
            front, back, sides, tilted, in your hand. {teaching.accepted}/{teaching.max_refs} views kept ·{" "}
            {Math.max(0, Math.round(teaching.until - Date.now() / 1000))} s left.{" "}
            <button className="textbutton" onClick={() => run(() => action(`/staff/teach/${teaching.id}/stop`, {}))}>Stop</button>
          </div>
        )}
        <TeachCamera startOpen={compact} token={token} products={data.products} refs={refs} sessions={info?.teach_sessions || []}
          tray={teachTray} busy={busy || !!teaching} run={run} action={action} />

        <table>
          <thead>
            <tr><th>Product</th><th>Price</th><th>On shelf</th><th>Weight</th><th>References</th><th></th></tr>
          </thead>
          <tbody>
            {data.products.map((p: any) => {
              const mine = refs.filter((r) => r.product_id === p.id);
              const active = mine.filter((r) => r.status === "ACTIVE").length;
              const enrolled = status?.products?.[p.id] || 0;
              return (
                <tr key={p.id}>
                  <td>{p.name}<br /><small className="muted">{p.id}</small></td>
                  <td>{money(p.price_minor)}</td>
                  <td>{unitsOf(p.id)}{data.tray_units.some((u: any) => u.product_id === p.id) && unitsOf(p.id) <= 1 && <small className="lowstock"> low</small>}</td>
                  <td>{p.weight_source === "MEASURED" && p.unit_g ? `${p.unit_g.toFixed(1)} g` : "not measured"}</td>
                  <td>
                    {active} new{enrolled ? ` · ${enrolled} views in gallery` : ""}
                    {active + enrolled === 0 && <small className="lowstock"> not learned</small>}
                  </td>
                  <td>
                    <button className="secondary" disabled={busy || !!teaching}
                      onClick={() => run(() => action(`/staff/products/${p.id}/teach`, { tray_id: teachTray, seconds, max_refs: 20, reason: "Teach product appearance" }))}>
                      Teach
                    </button>{" "}
                    <button className="textbutton" onClick={() => setOpen(open === p.id ? "" : p.id)}>
                      {open === p.id ? "Hide" : "References"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {open && (
          <div className="refgrid">
            {refs.filter((r) => r.product_id === open).length === 0 && <p className="muted">No references yet.</p>}
            {refs
              .filter((r) => r.product_id === open)
              .map((r) => (
                <figure key={r.id} className={r.status === "ACTIVE" ? "" : "rejected"}>
                  <CatalogueImage name={r.file} token={token} alt={`Reference of ${open}`} />
                  <figcaption>
                    {r.source === "TEACH" ? "teach" : "restock"} · {r.status.toLowerCase()}
                    <button className="textbutton"
                      onClick={() => {
                        const reason = prompt(r.status === "ACTIVE" ? "Reject this reference. Reason:" : "Restore this reference. Reason:");
                        if (reason) run(() => action(`/staff/references/${r.id}/${r.status === "ACTIVE" ? "reject" : "restore"}`, { reason }));
                      }}>
                      {r.status === "ACTIVE" ? "Reject" : "Restore"}
                    </button>
                  </figcaption>
                </figure>
              ))}
          </div>
        )}
      </section>
    </>
  );
}

export function OverviewAlerts({ data, pending, token }: { data: any; pending: any[]; token: string }) {
  const [drift, setDrift] = useState<any[]>([]),
    [open, setOpen] = useState(0);
  useEffect(() => {
    const load = () => {
      call("/staff/reports/drift", token).then(setDrift).catch(() => {});
      call("/staff/uncertain", token).then((r) => setOpen(r.length)).catch(() => {});
    };
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [token]);
  const reasons = (code: string) => pending.filter((h: any) => (h.reason || []).includes(code)).length;
  const tracked = data.trays.some((t: any) => t.unit_tracking);
  const onShelf = (pid: string) => data.tray_units.filter((u: any) => u.product_id === pid && u.status === "ON_TRAY").length;
  const stocked = new Set(data.tray_units.map((u: any) => u.product_id));
  const alerts: string[] = [];
  if (reasons("RETURNED_TO_OTHER_TRAY")) alerts.push(`${reasons("RETURNED_TO_OTHER_TRAY")} item(s) put back on the wrong tray: confirm in Evidence review to move them.`);
  if (reasons("WEIGHT_NO_UNIT_MATCH")) alerts.push(`${reasons("WEIGHT_NO_UNIT_MATCH")} weight change(s) match no known item: a foreign object, or an item placed without loading.`);
  if (tracked)
    for (const p of data.products.filter((p: any) => stocked.has(p.id) && onShelf(p.id) <= 1))
      alerts.push(`${p.name}: ${onShelf(p.id)} left on the shelf — restock with the tray-loading wizard.`);
  const unlearned = data.tray_units.filter((u: any) => u.status === "ON_TRAY" && (u.reference_status || "").startsWith("FAILED")).length;
  if (unlearned) alerts.push(`${unlearned} loaded item(s) could not be cut out of the camera stills: teach those products in Products & recognition.`);
  for (const t of drift.filter((t) => t.status !== "OK")) alerts.push(`${t.tray_id}: ${t.advice}`);
  if (open) alerts.push(`${open} item(s) the camera was unsure about: answer them under Products & recognition → Help the camera.`);
  if (!alerts.length) return null;
  return (
    <section className="panel">
      <h2>Needs attention</h2>
      {alerts.map((a) => (
        <Notice key={a}>{a}</Notice>
      ))}
    </section>
  );
}
