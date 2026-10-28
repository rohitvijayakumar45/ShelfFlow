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
  Clock,
  Hand,
  Hourglass,
  DoorOpen,
} from "@phosphor-icons/react";
import { call, money, Cart } from "./api";
import { reasonText } from "./reasons";
import { PhotoButton } from "./PhotoButton";
import { Preintegration } from "./Preintegration";
import { ManualCapture } from "./ManualCapture";
import { Go, Home, LiveCamera, ProductsHub, ReviewQueue, StatusChips, Tabs, pendingOf, trayWeight, useCameraStatus, useStaffSocket } from "./StaffHub";
import { Insights, HelpTheCamera, ShelfMap, EvidencePlayer, Suggestion, ProductThumb, useCartToasts, AuthImage } from "./Insights";

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
    <div className="v-app">
      <header className="v-header">
        <Brand />
        <span className="v-chip">Demo · no real payments</span>
      </header>
      <main className={staff ? "v-main v-login staff" : "v-main v-login"}>
        <div className="v-intro">
          <h1>{staff ? "Run the store" : "Pick it up. We'll keep track."}</h1>
          <p className="muted">
            {staff
              ? "Check shoppers in and out, settle what the shelf couldn't, and keep stock right."
              : "Your basket fills itself as you shop. Anything the shelf isn't sure about is checked by staff before you're charged."}
          </p>
        </div>
        <form className="v-card" onSubmit={submit}>
          <h2>{staff ? "Staff sign in" : "Start your visit"}</h2>
          <label>
            {staff ? "Staff password" : "Your name or username"}
            <input
              type={staff ? "password" : "text"}
              required
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={staff ? "" : "shopper"}
              autoComplete={staff ? "current-password" : "username"}
            />
          </label>
          {!staff && (
            <label>
              Password
              <input type="password" value={accountPassword} onChange={(e) => setAccountPassword(e.target.value)} autoComplete="current-password" />
              <small className="muted">Leave empty in the simulator.</small>
            </label>
          )}
          <button className="v-primary" disabled={busy}>
            {busy ? "Signing in…" : staff ? "Sign in" : "Continue"}
            <ArrowRight />
          </button>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
        </form>
      </main>
    </div>
  );
}
/** Where the visit is: one screen that moves through entry, shopping, leaving and the bill. */
function phaseOf(cart: Cart | null) {
  if (!cart) return "loading";
  if (cart.status === "COMPLETE") return "bill";
  if (cart.status === "ACTIVE") return cart.exit_requested ? "leaving" : cart.checkout_requested ? "checkout" : "shopping";
  return "entry";
}
const STEPS = [["entry", "Enter"], ["shopping", "Shop"], ["bill", "Bill"]] as const;

function VisitSteps({ phase }: { phase: string }) {
  const at = phase === "leaving" || phase === "checkout" ? 1 : Math.max(0, STEPS.findIndex(([p]) => p === phase));
  return (
    <ol className="v-steps" aria-label="Visit progress">
      {STEPS.map(([p, label], i) => (
        <li key={p} className={i < at ? "done" : i === at ? "current" : ""} aria-current={i === at ? "step" : undefined}>
          <span>{i < at ? <CheckCircle size={14} weight="bold" /> : i + 1}</span>
          {label}
        </li>
      ))}
    </ol>
  );
}

function useCountdown(until: number | null) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!until) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [until]);
  return until ? Math.max(0, Math.round((until - now) / 1000)) : 0;
}

export function Customer() {
  const [token, setToken] = useState(sessionStorage.getItem("customer-token") || ""),
    [cart, setCart] = useState<Cart | null>(null),
    [connected, setConnected] = useState(false),
    [error, setError] = useState(""),
    [qr, setQr] = useState<{ token: string; until: number } | null>(null),
    [busy, setBusy] = useState(false),
    [disputeFor, setDisputeFor] = useState("");
  const [toasts, dismissToast] = useCartToasts(cart ? [...cart.confirmed_events, ...(cart.assisted_events || [])] : undefined);
  const disputeBox = useRef<HTMLDetailsElement>(null);
  const left = useCountdown(qr?.until ?? null);
  useEffect(() => {
    if (!token) return;
    let stop = false,
      ws: WebSocket,
      timer: ReturnType<typeof setTimeout>;
    const signOut = () => {
      sessionStorage.removeItem("customer-token");
      setToken("");
      setCart(null);
    };
    const open = () => {
      ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/cart`);
      ws.onopen = () => (stop ? ws.close() : ws.send(JSON.stringify({ token })));
      ws.onmessage = (e) => {
        if (stop) return;
        const data = JSON.parse(e.data);
        setConnected(true);
        if (data.type === "snapshot") setCart((prev) => (!prev || data.version >= prev.version ? data : prev));
      };
      ws.onclose = (event) => {
        if (stop) return;
        setConnected(false);
        if (event.code === 1008) return signOut();
        timer = setTimeout(open, 2000);
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
        if (e.status === 401) signOut();
        else setError(String(e.message || e));
      });
    return () => {
      stop = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, [token]);
  useEffect(() => {
    if (qr && left === 0) setQr(null);
  }, [left]);
  useEffect(() => {
    if (cart && cart.status !== "ENTRY") setQr(null);
  }, [cart?.status]);
  async function action(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      setCart(await call("/customer/cart", token));
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }
  // Bracelet handed over after the bill: switch to the customer's next visit and show its entry code at once.
  async function handedOver() {
    setBusy(true);
    setError("");
    try {
      const next = await call("/customer/next-visit", token, {});
      const code = await call("/customer/entry-token", next.token, {});
      sessionStorage.setItem("customer-token", next.token);
      setCart(null);
      setToken(next.token);
      setQr({ token: code.token, until: Date.now() + code.expires_in * 1000 });
      window.scrollTo({ top: 0 });
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }
  if (!token) return <Login onLogin={setToken} />;
  const phase = phaseOf(cart);
  const count = cart?.items.reduce((n, i) => n + i.quantity, 0) || 0;
  const pending = cart?.pending.length || 0;
  const pattern = (cart?.bracelet?.markers || []).map((m) => PATTERNS[m]).filter(Boolean).join(", ") || null;
  const notMine = (id: string) => {
    setDisputeFor(id);
    if (disputeBox.current) disputeBox.current.open = true;
    disputeBox.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  };
  return (
    <div className="v-app">
      <header className="v-header">
        <Brand />
        <div className="headeractions">
          <span className={connected ? "live" : "offline"} role="status">
            <WifiHigh /> {connected ? "Live" : "Reconnecting…"}
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
      <main className="v-main">
        <VisitSteps phase={phase} />
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {phase === "loading" && <p className="muted">Loading your visit…</p>}

        {phase === "entry" && (
          <section className="v-card v-entry">
            <h1>Your entry pass</h1>
            <p className="muted">Show this code to staff at the entrance. They'll hand you a bracelet and your basket turns on.</p>
            {qr ? (
              <>
                <div className="v-qr">
                  <QRCodeSVG value={qr.token} size={232} marginSize={2} />
                </div>
                <p className="v-timer">
                  <Clock size={16} /> Valid for {Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")} · single use
                </p>
                <details>
                  <summary>Staff can't scan it?</summary>
                  <code className="token">{qr.token}</code>
                </details>
              </>
            ) : (
              <button
                className="v-primary"
                disabled={busy}
                onClick={() =>
                  action(async () => {
                    const r = await call("/customer/entry-token", token, {});
                    setQr({ token: r.token, until: Date.now() + r.expires_in * 1000 });
                  })
                }
              >
                <QrCode size={22} /> Show my entry code
              </button>
            )}
            <div className="v-hint">
              <Hand size={20} />
              <span>Wear the bracelet on the hand you pick items with, and pick items for yourself only.</span>
            </div>
          </section>
        )}

        {(phase === "shopping" || phase === "leaving" || phase === "checkout") && cart && (
          <>
            {phase === "checkout" ? (
              <section className="v-card v-leaving" aria-live="polite">
                <span className="v-chip warn">
                  <DoorOpen size={14} /> Checking out
                </span>
                <h1>Head to the exit</h1>
                <p>
                  Tap your bracelet on the exit reader{cart.exit_reader ? ` (${cart.exit_reader})` : ""} and hand it over. Your bill
                  appears here as soon as the tap is verified.
                </p>
                <p className="v-total money">{money(cart.total_minor)}</p>
                <button className="secondary" disabled={busy} onClick={() => action(() => call("/customer/checkout-request/withdraw", token, {}))}>
                  Keep shopping
                </button>
              </section>
            ) : phase === "leaving" ? (
              <section className="v-card v-leaving" aria-live="polite">
                <span className="v-chip warn">
                  <DoorOpen size={14} /> Exit registered
                </span>
                <h1>Finishing your bill</h1>
                <p className="muted">
                  {pending
                    ? `Staff are confirming ${pending} item${pending > 1 ? "s" : ""}. This usually takes under a minute.`
                    : cart.exit_blockers?.length
                      ? cart.exit_blockers[0]
                      : "Bracelet verified. Adding up your basket…"}
                </p>
                <div className="v-progress" role="progressbar" aria-label="Preparing your bill">
                  <span />
                </div>
              </section>
            ) : (
              <section className="v-card v-totalcard">
                <div className="v-row">
                  <span className="v-chip ok">
                    <CheckCircle size={14} /> Shopping
                  </span>
                  {pattern && <span className="v-chip">{pattern} bracelet</span>}
                </div>
                <p className="v-label">Your basket</p>
                <p className="v-total money">{money(cart.total_minor)}</p>
                <p className="muted">{count ? `${count} item${count > 1 ? "s" : ""} confirmed` : "Items you pick up appear here in a few seconds."}</p>
              </section>
            )}
            <section className="v-card">
              {cart.items.length ? (
                cart.items.map((i) => (
                  <div className="v-item" key={i.product_id}>
                    <ProductThumb productId={i.product_id} token={token} hasImage={i.has_image} />
                    <div className="v-itemtext">
                      <b>{i.name}</b>
                      <span className="muted money">
                        {money(i.price_minor)} × {i.quantity}
                      </span>
                    </div>
                    <b className="money">{money(i.quantity * i.price_minor)}</b>
                  </div>
                ))
              ) : (
                <div className="v-empty">
                  <ShoppingBag size={40} />
                  <b>Your basket is empty</b>
                  <span className="muted">Pick something up and it's added here automatically.</span>
                </div>
              )}
              {pending > 0 && (
                <div className="v-pending">
                  <Hourglass size={20} />
                  <div>
                    <b>
                      Checking {pending} item{pending > 1 ? "s" : ""}
                    </b>
                    <span>Staff are confirming who picked {pending > 1 ? "them" : "it"} up. You're not charged until it's confirmed.</span>
                  </div>
                </div>
              )}
            </section>
            {phase === "shopping" &&
              (cart.mode === "hardware" && cart.checkout_needs_tap ? (
                <button className="v-primary" disabled={busy || !connected || !cart.bracelet} onClick={() => action(() => call("/customer/checkout-request", token, {}))}>
                  Checkout <ArrowRight />
                </button>
              ) : cart.mode === "hardware" ? (
                <div className="v-hint v-exit">
                  <DoorOpen size={22} />
                  <span>
                    <b>Done?</b> Tap your bracelet on the exit reader. Your bill appears here.
                  </span>
                </div>
              ) : (
                <button
                  className="v-primary"
                  disabled={busy || !connected || pending > 0}
                  onClick={() => action(() => call("/customer/checkout", token, { idempotency_key: crypto.randomUUID(), outcome: "SUCCESS" }))}
                >
                  Finish shopping <ArrowRight />
                </button>
              ))}
            {phase === "shopping" && cart.mode !== "hardware" && pending > 0 && (
              <p className="muted v-note">You can finish once staff have checked the item above.</p>
            )}
          </>
        )}

        {phase === "bill" && cart && (
          <section className="v-card v-bill">
            <div className="v-row">
              <span className="v-chip ok">
                <CheckCircle size={14} /> Bill final
              </span>
              <span className="v-chip">Payment simulated</span>
            </div>
            <h1>Thanks for shopping</h1>
            <p className="v-total money">{money(cart.receipt?.total_minor || 0)}</p>
            <p className="muted">
              {cart.receipt?.created ? new Date(cart.receipt.created * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : ""}
              {cart.receipt?.order_id ? ` · Bill ${cart.receipt.order_id.slice(0, 8)}` : ""}
            </p>
            <div className="v-lines">
              {(cart.receipt?.items || []).map((i: any) => (
                <div className="v-line" key={i.product_id}>
                  <span>
                    {i.name} × {i.quantity}
                  </span>
                  <span className="money">{money(i.quantity * i.price_minor)}</span>
                </div>
              ))}
              <div className="v-line v-sum">
                <b>Total</b>
                <b className="money">{money(cart.receipt?.total_minor || 0)}</b>
              </div>
            </div>
            {cart.corrections.map((c: any) => (
              <div className="v-pending" key={c.id}>
                <Receipt size={20} />
                <div>
                  <b>Adjusted {money(c.amount_minor)}</b>
                  <span>{c.reason}</span>
                </div>
              </div>
            ))}
            <p className="v-note">No payment provider is connected in this demo, so no money was collected.</p>
            <div className="v-hint">
              <Hand size={20} />
              <span>Please hand your bracelet back to staff.</span>
            </div>
            <button className="v-primary" disabled={busy} onClick={handedOver}>
              <DoorOpen size={22} /> Bracelet handed over · Exit
            </button>
            <p className="muted v-note">Takes you back to the start with a new entry code for your next visit.</p>
          </section>
        )}

        {cart && cart.confirmed_events.length > 0 && (
          <details className="v-card v-dispute" ref={disputeBox}>
            <summary>Something here isn't yours?</summary>
            <Dispute key={disputeFor} token={token} preselect={disputeFor} onDone={() => action(async () => {})} events={cart.confirmed_events} />
          </details>
        )}
      </main>
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div className="toast" key={t.id}>
            <span>
              {t.action === "RETURN" ? "Removed" : "Added"} <b>{t.name || t.product_id}</b>
              {t.quantity > 1 ? ` × ${t.quantity}` : ""}
            </span>
            {t.weight_verified === false ? (
              <span className="muted">Ask staff if this isn't yours</span>
            ) : (
              <button
                className="textbutton"
                onClick={() => {
                  notMine(t.id);
                  dismissToast(t.id);
                }}
              >
                Not mine?
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
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
  ["Daily", [["Front desk", DoorOpen], ["Home", Storefront], ["Live camera", VideoCamera], ["Review queue", Camera], ["Shoppers & checkout", Users], ["Stock", Scales]]],
  ["Products", [["Products & training", GraduationCap]]],
  ["Advanced", [["Insights", ChartLine], ["Shelf setup", Tray]]],
] as const;
const intro: Record<string, string> = {
  "Front desk": "Check shoppers in, see who is in the store, and check them out. On a phone, pick one column.",
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
  // Pushed live weight (fresh for 10 s after the last message) and an immediate reload after each change.
  const live = useStaffSocket(token, reload);
  const view = data && { ...data, live_weight: Date.now() - live.at < 10000 ? live.trays : {} };
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
              {page === "Front desk" && <FrontDesk data={view} token={token} action={action} go={go} />}
              {page === "Home" && <Home data={view} token={token} action={action} camera={camera} go={go} />}
              {page === "Live camera" && <LiveCamera data={view} token={token} go={go} />}
              {page === "Review queue" && <ReviewQueue data={view} token={token} action={action} reload={reload} />}
              {page === "Shoppers & checkout" && <ShoppersPage data={view} token={token} action={action} />}
              {page === "Stock" && <StockPage data={view} token={token} action={action} />}
              {page === "Products & training" && (
                <ProductsHub data={view} token={token} action={action} tab={route.tab || "catalogue"} product={route.product} go={go} />
              )}
              {page === "Insights" && <Insights data={view} action={action} token={token} />}
              {page === "Shelf setup" && <Setup data={view} action={action} token={token} />}
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
                  <CheckIn data={data} action={action} />
                  <section className="panel">
                    <h2>Bracelet inventory</h2>
                    <BraceletRegister data={data} action={action} />
                    <table>
                      <thead>
                        <tr>
                          <th>Bracelet</th>
                          <th>Tag / pattern</th>
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
                              <small>{patternOf(b.markers)}</small>
                            </td>
                            <td>{b.state}{b.session_id && <><br /><small>{data.sessions.find((x: any) => x.id === b.session_id)?.customer_alias || b.session_id.slice(0, 8)}</small></>}</td>
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
                  <details className="panel">
                    <summary>Bracelet dispenser (simulated gate) and older manual assignment</summary>
                    <Gate token={token} data={data} action={action} />
                  </details>
        </>
      )}
      {tab === "visits" && (
        <>
          <ExitCheckout data={data} token={token} action={action} />
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
      ).BrowserQRCodeReader().decodeFromConstraints(
        { video: { facingMode: "environment" } },
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
// Bracelet patterns the camera model knows (classes band-stripe / band-circle). They are stored as the
// bracelet's marker ID, so the camera's band detection maps to the bracelet like a printed marker does.
const PATTERNS: Record<number, string> = { 200: "Stripes", 201: "Circles" };
const patternOf = (markers: number[] = []) => markers.map((m) => PATTERNS[m] || `marker ${m}`).join(", ") || "no pattern set";

/** Entry: scan the customer's QR, tap the bracelet you hand over on a shelf reader, assign. */
function CheckIn({ data, action }: { data: any; action: Action }) {
  const [token, setToken] = useState(""),
    [who, setWho] = useState<any>(null),
    [since, setSince] = useState(0),
    [scanning, setScanning] = useState(false),
    [error, setError] = useState(""),
    [done, setDone] = useState<any>(null);
  const video = useRef<HTMLVideoElement>(null);
  const controls = useRef<IScannerControls | null>(null);
  useEffect(() => () => controls.current?.stop(), []);
  const entry = data.rfid_roles?.entry;
  const tap = since > 0 ? data.recent_taps?.find((t: any) => t.captured >= since && (!entry || t.reader === entry)) : null;
  const bracelet = tap && data.bracelets.find((b: any) => b.rfid === tap.uid);
  async function identify(value: string) {
    setToken(value); setWho(null); setDone(null); setError("");
    try {
      setWho(await action("/staff/check-in/preview", { token: value }));
      setSince(Date.now() / 1000);
    } catch (e: any) { setError(String(e.message || e)); }
  }
  async function scan() {
    setScanning(true); setError("");
    try {
      controls.current = await new (await import("@zxing/browser")).BrowserQRCodeReader().decodeFromConstraints({ video: { facingMode: "environment" } }, video.current!, (r) => {
        if (r) { controls.current?.stop(); setScanning(false); identify(r.getText()); }
      });
    } catch (e) { setError(String(e)); setScanning(false); }
  }
  return (
    <section className="panel">
      <h2>Check in a customer</h2>
      <ol className="steps">
        <li><b>Scan</b> the entry QR on the customer's phone.</li>
        <li><b>Tap</b> the bracelet you are handing over on the entry reader{data.rfid_roles?.entry ? ` (${data.rfid_roles.entry})` : ""}.</li>
        <li><b>Assign</b>: the visit starts and the customer's cart turns on.</li>
      </ol>
      <video ref={video} className="scanner" muted playsInline />
      <div className="actions">
        <button className="secondary" onClick={scan} disabled={scanning}>Start QR scanner</button>
        <button className="secondary" onClick={() => { controls.current?.stop(); setScanning(false); }}>Stop camera</button>
      </div>
      <form onSubmit={(e) => { e.preventDefault(); identify(token.trim()); }}>
        <label>Entry QR (scanned, or paste the code)<input value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" /></label>
        <button className="secondary" disabled={token.trim().length < 10}>Look up customer</button>
      </form>
      {error && <p className="error">{error}</p>}
      {who && !done && (
        <div className="result">
          <p>Customer: <b>{who.customer_alias || "Shopper"}</b> · QR valid for {who.expires_in} s more.</p>
          {!tap ? <p>Now tap the bracelet on the entry reader{entry ? ` (${entry})` : ""}…</p> : !bracelet ? (
            <p className="error">Tag {tap.uid} is not a registered bracelet. Register it below first.</p>
          ) : (
            <>
              <p>Bracelet <b>{bracelet.id}</b> · pattern <b>{patternOf(bracelet.markers)}</b> · tag {bracelet.rfid} · {bracelet.state.toLowerCase()}</p>
              <button onClick={() => action("/staff/check-in", { token: token.trim(), actual_uid: bracelet.rfid })
                .then((r) => { setDone(r); setWho(null); setToken(""); }).catch((e: any) => setError(String(e.message || e)))}>
                Assign {bracelet.id} and start visit
              </button>
            </>
          )}
        </div>
      )}
      {done && <Notice>Visit started: bracelet {done.bracelet_id} ({patternOf(done.markers)}). The customer's cart is now active.</Notice>}
    </section>
  );
}

/** Exit: find the visit (bracelet tap or list), check the cart, check out once, then take the bracelet back. */
function ExitCheckout({ data, token, action, pick }: { data: any; token: string; action: Action; pick?: { sid: string; at: number } }) {
  const [sid, setSid] = useState(""), [since, setSince] = useState(0), [visit, setVisit] = useState<any>(null), [error, setError] = useState(""),
    [busy, setBusy] = useState(false), [cleaned, setCleaned] = useState(true);
  const active = data.sessions.filter((x: any) => x.status === "ACTIVE");
  const exitReader = data.rfid_roles?.exit;
  const waiting: any[] = data.pending_exits || [];
  const tap = since > 0 ? data.recent_taps?.find((t: any) => t.captured >= since && (!exitReader || t.reader === exitReader)) : null;
  const tapped = tap && data.bracelets.find((b: any) => b.rfid === tap.uid);
  useEffect(() => { if (tapped?.session_id && tapped.session_id !== sid) setSid(tapped.session_id); }, [tapped?.session_id]);
  // A customer tapped the exit reader but the bill is blocked: open that visit so staff can see why.
  useEffect(() => { if (!sid && waiting.length) setSid(waiting[0].session_id); }, [waiting[0]?.session_id]);
  useEffect(() => {
    if (!sid) { setVisit(null); return; }
    call(`/staff/visits/${encodeURIComponent(sid)}`, token).then(setVisit).catch((e) => setError(String(e.message || e)));
  }, [sid, data]);
  useEffect(() => { if (pick?.sid) setSid(pick.sid); }, [pick?.at]);
  const bracelet = visit?.bracelet && data.bracelets.find((b: any) => b.id === visit.bracelet.id);
  const blockers: any[] = visit?.exit_blockers || [];
  async function run(fn: () => Promise<any>) {
    setBusy(true); setError("");
    try { await fn(); setVisit(await call(`/staff/visits/${encodeURIComponent(sid)}`, token)); }
    catch (e: any) { setError(String(e.message || e)); }
    finally { setBusy(false); }
  }
  return (
    <section className="panel">
      <h2>Check out at the exit</h2>
      <ol className="steps">
        <li><b>Exit tap</b>: the customer taps the bracelet on the exit reader{exitReader ? ` (${exitReader})` : ""}. With nothing pending the bill is generated at once; otherwise the visit waits here.</li>
        <li><b>Or by hand</b>: find the visit (tap or list), check the cart with the customer, then generate the bill (once; repeating it shows the same bill).</li>
        <li><b>Take back</b> the bracelet: it is only released for the next customer after you confirm it.</li>
      </ol>
      <div className="actions">
        <button className="secondary" onClick={() => { setSince(Date.now() / 1000); setError(""); }}>Find by bracelet tap</button>
        <select value={sid} onChange={(e) => { setSid(e.target.value); setError(""); }}>
          <option value="">…or choose an active visit</option>
          {active.map((x: any) => <option key={x.id} value={x.id}>{x.customer_alias || x.id.slice(0, 8)}</option>)}
        </select>
      </div>
      {waiting.map((w: any) => (
        <p key={w.session_id} className={w.blockers.length ? "error" : ""}>
          <b>{w.customer_alias || "Shopper"}</b> tapped out with {w.bracelet_id}{w.blockers.length ? `: waiting (${w.blockers.map((b: any) => b.message).join("; ")})` : ": billing…"}
          {w.session_id !== sid && <button className="textbutton" onClick={() => setSid(w.session_id)}>Open</button>}
        </p>
      ))}
      {since > 0 && !tap && <p>Tap the customer's bracelet on the exit reader{exitReader ? ` (${exitReader})` : ""}…</p>}
      {tap && !tapped && <p className="error">Tag {tap.uid} is not a registered bracelet.</p>}
      {tapped && !tapped.session_id && <p className="error">{tapped.id} is not assigned to a visit.</p>}
      {error && <p className="error">{error}</p>}
      {visit && (
        <div className="result">
          <p><b>{visit.customer_alias || "Shopper"}</b> · {visit.status.toLowerCase()} · bracelet {visit.bracelet ? `${visit.bracelet.id} (${patternOf(visit.bracelet.markers)})` : "none"}</p>
          {visit.items.length === 0 ? <p className="muted">Cart is empty.</p> : visit.items.map((i: any) => (
            <div className="row" key={i.product_id}><span>{i.quantity} × {i.name}</span><b>{money(i.quantity * i.price_minor)}</b></div>
          ))}
          <div className="row"><span>Total</span><b>{money(visit.total_minor)}</b></div>
          {visit.status === "ACTIVE" && blockers.map((b: any) => (
            <p className="error" key={b.code}>{b.message}{b.wait_s ? ` (about ${b.wait_s} s)` : ""}.</p>
          ))}
          {visit.status === "ACTIVE" && (
            <button disabled={busy || blockers.length > 0} onClick={() => run(() => action("/staff/exit", { session_id: sid }))}>
              Check out and generate bill
            </button>
          )}
          {visit.status === "COMPLETE" && (
            <>
              <Notice>Bill final: {money(visit.receipt?.total_minor || 0)}. Payment is SIMULATED: no payment provider is connected, so no money was collected. The customer's app shows the bill.</Notice>
              {bracelet?.session_id === sid ? (
                <>
                  <label><input type="checkbox" checked={cleaned} onChange={(e) => setCleaned(e.target.checked)} /> Bracelet cleaned</label>
                  <button className="secondary" disabled={busy}
                    onClick={() => run(() => action(`/staff/bracelets/${bracelet.id}/return`, { reason: cleaned ? "Returned at exit and cleaned" : "Returned at exit, needs cleaning", cleaned }))}>
                    Bracelet {bracelet.id} is back: {cleaned ? "release it for the next customer" : "keep it until cleaned"}
                  </button>
                </>
              ) : <p className="muted">Bracelet returned.</p>}
            </>
          )}
        </div>
      )}
    </section>
  );
}

/** Stock: the catalogue with what is on the shelf now, plus the counting/loading tools. */
function StockPage({ data, token, action }: { data: any; token: string; action: Action }) {
  const [tab, setTab] = useState("catalogue");
  return (
    <>
      <Tabs tabs={[["catalogue", "Catalogue"], ["count", data.weights_enabled === false ? "Count stock" : "Load a tray"]]} value={tab} onChange={setTab} />
      {tab === "catalogue" ? (
        <ShelfCatalogue token={token} />
      ) : data.weights_enabled === false ? (
        <Preintegration data={data} token={token} action={action} stockOnly />
      ) : (
        <TrayLoading data={data} action={action} token={token} />
      )}
    </>
  );
}

function ShelfCatalogue({ token }: { token: string }) {
  const [stock, setStock] = useState<any>(null),
    [filter, setFilter] = useState("all"),
    [query, setQuery] = useState(""),
    [error, setError] = useState(""),
    [photoVersion, setPhotoVersion] = useState<Record<string, number>>({});
  useEffect(() => {
    let stop = false,
      timer: ReturnType<typeof setTimeout>;
    // Ask again only after the last answer: with the camera offline one answer can take ~2 s.
    const load = () =>
      call("/staff/shelf-stock", token)
        .then((r) => {
          if (!stop) {
            setStock(r);
            setError("");
          }
        })
        .catch((e) => {
          if (!stop) setError(String(e.message || e));
        })
        .finally(() => {
          if (!stop) timer = setTimeout(load, 2000);
        });
    load();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [token]);
  if (!stock) return <p className="muted">{error || "Loading the catalogue…"}</p>;
  const items: any[] = stock.items;
  const inStock = items.filter((i) => i.stock > 0).length;
  const shown = items
    .filter((i) => filter === "all" || (filter === "in" ? i.stock > 0 : i.stock === 0))
    .filter((i) => !query.trim() || i.name.toLowerCase().includes(query.trim().toLowerCase()));
  const ago = (t: number | null) => {
    if (!t) return "Not seen yet";
    const sec = Math.max(0, Math.round(stock.checked - t));
    if (sec < 60) return `Seen ${sec} s ago`;
    if (sec < 3600) return `Seen ${Math.round(sec / 60)} min ago`;
    return `Seen at ${new Date(t * 1000).toLocaleTimeString([], { timeStyle: "short" })}`;
  };
  return (
    <section className="catalogue">
      <div className="cat-summary">
        <div className="cat-stat">
          <span>Products</span>
          <b>{items.length}</b>
        </div>
        <div className="cat-stat ok">
          <span>In stock</span>
          <b>{inStock}</b>
        </div>
        <div className="cat-stat out">
          <span>Out of stock</span>
          <b>{items.length - inStock}</b>
        </div>
        <div className={stock.camera_live ? "cat-stat" : "cat-stat warn"}>
          <span>Shelf camera</span>
          <b className="cat-live">
            <span className={stock.camera_live ? "dot ok" : "dot bad"} /> {stock.camera_live ? "Live" : "Offline"}
          </b>
        </div>
      </div>
      {!stock.camera_live && <Notice>The shelf camera is offline, so stock shows what was last seen. It updates again once the camera is back.</Notice>}
      <div className="cat-tools">
        <div className="seg" role="tablist" aria-label="Filter products">
          {[["all", "All"], ["in", "In stock"], ["out", "Out of stock"]].map(([k, label]) => (
            <button key={k} role="tab" aria-selected={filter === k} className={filter === k ? "selected" : ""} onClick={() => setFilter(k)}>
              {label}
            </button>
          ))}
        </div>
        <input type="search" placeholder="Search products" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search products" />
      </div>
      {shown.length === 0 ? (
        <p className="muted">{items.length ? "No products match." : "No products yet. Add one under Products."}</p>
      ) : (
        <ul className="cat-grid">
          {shown.map((i) => (
            <li key={i.product_id} className={i.stock > 0 ? "in" : "out"}>
              <div className="cat-img">
                {i.has_image || photoVersion[i.product_id] ? (
                  <AuthImage path={`/staff/product-image/${encodeURIComponent(i.product_id)}?v=${photoVersion[i.product_id] || 0}`} token={token} alt="" />
                ) : (
                  <ShoppingBag size={32} />
                )}
              </div>
              <div className="cat-body">
                <b>{i.name}</b>
                <span className="muted money">{i.price_minor != null ? money(i.price_minor) : "No price set"}</span>
              </div>
              <div className="cat-foot">
                <span className={i.stock > 0 ? "v-chip ok" : "v-chip out"}>{i.stock > 0 ? `${i.stock} in stock` : "Out of stock"}</span>
                <small className="muted">{i.source === "scale" ? "Weighed on the tray" : ago(i.last_seen)}</small>
                {i.in_catalogue && (
                  <PhotoButton productId={i.product_id} token={token} hasPhoto={i.has_image}
                    onDone={() => setPhotoVersion((v) => ({ ...v, [i.product_id]: Date.now() }))} />
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Front desk: the three jobs at the door side by side (a phone shows one at a time). */
function FrontDesk({ data, token, action, go }: { data: any; token: string; action: Action; go: Go }) {
  const [column, setColumn] = useState(() => sessionStorage.getItem("frontdesk-column") || "checkin"),
    [pick, setPick] = useState<{ sid: string; at: number } | undefined>();
  const visits: any[] = data.active_visits || [];
  const waiting: any[] = data.pending_exits || [];
  const choose = (c: string) => {
    setColumn(c);
    try { sessionStorage.setItem("frontdesk-column", c); } catch {}
  };
  const columns: [string, string, number][] = [["checkin", "Check in", 0], ["instore", "In store", visits.length], ["exit", "Exit", waiting.length]];
  return (
    <div className="frontdesk">
      <div className="fd-switch" role="tablist" aria-label="Front desk column">
        {columns.map(([key, label, n]) => (
          <button key={key} role="tab" aria-selected={column === key} className={column === key ? "selected" : ""} onClick={() => choose(key)}>
            {label}{n > 0 && <span className="count">{n}</span>}
          </button>
        ))}
      </div>
      <div className={`fd-grid show-${column}`}>
        <div className="fd-col fd-checkin"><CheckIn data={data} action={action} /></div>
        <div className="fd-col fd-instore">
          <section className="panel">
            <h2>In store <span className="fd-n">{visits.length}</span></h2>
            {data.unowned_pending > 0 && (
              <button className="v-pending fd-unowned" onClick={() => go("Review queue")}>
                <Hourglass size={18} />
                <span><b>{data.unowned_pending} shelf event{data.unowned_pending > 1 ? "s" : ""} with no shopper yet.</b> Decide who it was before checking anyone out.</span>
              </button>
            )}
            {visits.length === 0 ? <p className="muted">Nobody is shopping right now.</p> : (
              <ul className="fd-visits">
                {visits.map((v) => {
                  const pattern = (v.bracelet?.markers || []).map((m: number) => PATTERNS[m]).filter(Boolean).join(", ");
                  return (
                    <li key={v.session_id} className={v.exit_requested ? "leaving" : ""}>
                      <div className="fd-who">
                        <b>{v.customer_alias || "Shopper"}</b>
                        <span className="muted">{v.bracelet ? `${v.bracelet.id}${pattern ? ` · ${pattern}` : ""}` : "No bracelet"}</span>
                      </div>
                      <div className="fd-what">
                        <b className="money">{money(v.total_minor)}</b>
                        <span className="muted">{v.items} item{v.items === 1 ? "" : "s"}</span>
                      </div>
                      <div className="fd-flags">
                        {v.exit_requested ? <span className="v-chip warn">At the exit</span>
                          : v.checkout_requested && <span className="v-chip">Heading to exit</span>}
                        {v.pending > 0 && <button className="v-chip warn fd-link" onClick={() => go("Review queue")}>{v.pending} to check</button>}
                        <button className="secondary fd-out" onClick={() => { setPick({ sid: v.session_id, at: Date.now() }); choose("exit"); }}>Check out</button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </div>
        <div className="fd-col fd-exit"><ExitCheckout data={data} token={token} action={action} pick={pick} /></div>
      </div>
    </div>
  );
}

/** Register or relabel a physical bracelet: its RFID tag and the pattern printed on it. */
function BraceletRegister({ data, action }: { data: any; action: Action }) {
  const [id, setId] = useState(""), [uid, setUid] = useState(""), [pattern, setPattern] = useState(""), [cleaned, setCleaned] = useState(true);
  const latest = data.recent_taps?.find((t: any) => Date.now() / 1000 - t.captured < 60);
  const taken = data.bracelets.find((b: any) => b.id !== id && pattern && b.markers.includes(Number(pattern)));
  return (
    <details>
      <summary>Register or relabel a bracelet</summary>
      <label>Bracelet name (e.g. B02)<input value={id} onChange={(e) => setId(e.target.value.toUpperCase())} /></label>
      <label>RFID tag UID<input value={uid} onChange={(e) => setUid(e.target.value.toUpperCase())} /></label>
      <button className="secondary" disabled={!latest} onClick={() => setUid(latest.uid)}>Use latest tap{latest ? ` (${latest.uid})` : ""}</button>
      <label>Pattern on the bracelet
        <select value={pattern} onChange={(e) => setPattern(e.target.value)}>
          <option value="">Choose…</option>
          {Object.entries(PATTERNS).map(([m, name]) => <option key={m} value={m}>{name}</option>)}
        </select>
      </label>
      {taken && <p className="error">{taken.id} already has this pattern; each pattern can belong to one bracelet.</p>}
      <label><input type="checkbox" checked={cleaned} onChange={(e) => setCleaned(e.target.checked)} /> Cleaned and ready to hand out</label>
      <button disabled={!id || !/^[0-9A-F]{8}([0-9A-F]{6})?([0-9A-F]{6})?$/.test(uid) || !pattern || !!taken}
        onClick={() => action(`/staff/bracelets/${encodeURIComponent(id)}/setup`, { rfid: uid, markers: [Number(pattern)], cleaned, reason: `Bracelet ${id}: ${PATTERNS[Number(pattern)]} pattern` })
          .then(() => { setId(""); setUid(""); setPattern(""); }).catch(() => {})}>
        Save bracelet
      </button>
    </details>
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
const FORM_TITLES: Record<string, string> = { "Tray ROI": "Tray region", Calibration: "Scale calibration", Restock: "Restock", Product: "Product details" };
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
      className="setupform"
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
      <h3 className="formtitle">{FORM_TITLES[label] || label}</h3>
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
        {busy ? "Saving…" : `Save ${(FORM_TITLES[label] || label).toLowerCase()}`}
      </button>
      {error && <p role="status" className={error === "Saved" ? "formok" : "error"}>{error}</p>}
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
          <div className="setuphead">
            <h2>{t.id.replace("tray-", "Tray ").toUpperCase().replace("TRAY ", "Tray ")}</h2>
            <span className="v-chip">{t.unit_tracking ? "Mixed products" : t.product_id}</span>
            <span className="v-chip">Calibration {t.calibration_version}</span>
          </div>
          {t.latest_sample ? (
            <div className="readout">
              <div><span>Raw reading</span><b className="money">{t.latest_sample.weight.raw}</b></div>
              <div><span>Weight</span><b className="money">{t.latest_sample.weight.filtered ?? "—"} g</b></div>
              <div><span>Scale health</span><b>{t.latest_sample.health.flags.join(", ") || "OK"}</b></div>
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
      <p>{h.reason.map(reasonText).join(" · ")}</p>
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
/** Guided loading: staff list what goes on the tray, then place items one at a time. The shelf LCD
 *  shows the same direction ("2/6 Vicks", live weight), each settled increase is saved as that item's
 *  weight and stock, and the next item is asked for automatically. */
type PlanItem = { product_id: string; name: string };
type RunState = "placing" | "saving" | "problem" | "done";

function GuidedLoad({ data, token, action, tray, load, still }: {
  data: any; token: string; action: Action; tray: any; load: any; still: (loadId: string) => Promise<string>;
}) {
  const [counts, setCounts] = useState<Record<string, number>>(() => Object.fromEntries(data.products.map((p: any) => [p.id, 1]))),
    [plan, setPlan] = useState<PlanItem[] | null>(null),
    [index, setIndex] = useState(0),
    [state, setState] = useState<RunState>("placing"),
    [since, setSince] = useState(0),
    [before, setBefore] = useState(""),
    [saved, setSaved] = useState<any[]>([]),
    [problem, setProblem] = useState(""),
    [warning, setWarning] = useState(""),
    [lcd, setLcd] = useState<string[]>([]),
    [simGrams, setSimGrams] = useState(50),
    [busy, setBusy] = useState(false);
  const simulation = data.mode === "simulation";
  const live = trayWeight(data, tray?.id);
  const loaded = data.tray_units.filter((u: any) => u.load_id === load.id);
  const product = (id: string) => data.products.find((p: any) => p.id === id)?.name || id;
  const item = plan?.[index];
  const total = plan?.length || 0;

  async function guide(lines: string[], seconds = 120) {
    setLcd(lines);
    try {
      await call(`/staff/trays/${tray.id}/lcd`, token, { lines, seconds });
    } catch {
      /* the app still shows the direction if the shelf screen cannot be reached */
    }
  }
  async function begin(next: number, items = plan) {
    const target = items?.[next];
    if (!target) return;
    setIndex(next);
    setProblem("");
    setWarning("");
    setState("placing");
    setBefore(await still(load.id));
    setSince(Date.now() / 1000 - 1);
    await guide([`${next + 1}/${items!.length} ${target.name}`, "place, wait #"]);
  }
  async function start() {
    const items: PlanItem[] = data.products.flatMap((p: any) => Array.from({ length: counts[p.id] || 0 }, () => ({ product_id: p.id, name: p.name })));
    if (!items.length) return;
    setPlan(items);
    setSaved([]);
    await begin(0, items);
  }
  async function stop() {
    setPlan(null);
    setState("placing");
    await guide([], 0);
  }
  // Keep the shelf screen alive while waiting (each request lasts 120 s).
  useEffect(() => {
    if (!plan || state !== "placing" || !item) return;
    const t = setInterval(() => guide([`${index + 1}/${total} ${item.name}`, "place, wait #"]), 60000);
    return () => clearInterval(t);
  }, [plan, state, index]);
  // Wait for the settled weight increase of this item, then save it straight away.
  useEffect(() => {
    if (!plan || state !== "placing" || !item) return;
    let stopped = false;
    const poll = async () => {
      try {
        const events: WeightEvent[] = await call(`/staff/trays/${tray.id}/weight-events?since=${since}`, token);
        const fresh = events.filter((e) => e.capture_end >= since);
        const falling = fresh.find((e) => e.delta_g < 0);
        if (falling && !stopped) {
          setWarning(`Weight went down by ${Math.abs(falling.delta_g).toFixed(1)} g. Put back anything taken off; nothing should leave the tray while loading.`);
          guide(["WEIGHT WENT DOWN", "put item back"], 30);
        }
        const rising = fresh.find((e) => e.delta_g > 0);
        if (!rising || stopped) return;
        stopped = true;
        setState("saving");
        const after = await still(load.id);
        try {
          const r = await action(`/staff/loads/${load.id}/units`, {
            product_id: item.product_id, weight_event_id: rising.event_id, spot: null, crop_refs: [before, after].filter(Boolean),
          });
          setSaved((s) => [...s, { ...r, name: item.name }]);
          if (index + 1 < total) {
            await guide([`SAVED ${r.weight_g.toFixed(1)}g`, `next: ${plan[index + 1].name}`], 3);
            await new Promise((ok) => setTimeout(ok, 2500));
            await begin(index + 1);
          } else {
            setState("done");
            await guide(["LOADING DONE", `${total} items saved`], 30);
          }
        } catch (e: any) {
          setProblem(String(e.message || e));
          setState("problem");
          await guide(["CHECK THE APP", item.name], 120);
        }
      } catch (e: any) {
        setProblem(String(e.message || e));
      }
    };
    poll();
    const t = setInterval(poll, 1000);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [plan, state, index, since]);

  const planned = data.products.reduce((n: number, p: any) => n + (counts[p.id] || 0), 0);
  return (
    <div className="wizardstep guided">
      {!plan ? (
        <>
          <h3>What goes on the tray?</h3>
          <p className="muted">Set how many of each product you'll place. You'll place them one at a time and the shelf screen tells you which one is next.</p>
          <ul className="planlist">
            {data.products.map((p: any) => (
              <li key={p.id}>
                <span>{p.name}</span>
                <div className="stepper" aria-label={`How many ${p.name}`}>
                  <button className="secondary" aria-label={`One less ${p.name}`} onClick={() => setCounts({ ...counts, [p.id]: Math.max(0, (counts[p.id] || 0) - 1) })}>−</button>
                  <b className="money">{counts[p.id] || 0}</b>
                  <button className="secondary" aria-label={`One more ${p.name}`} onClick={() => setCounts({ ...counts, [p.id]: Math.min(20, (counts[p.id] || 0) + 1) })}>+</button>
                </div>
              </li>
            ))}
          </ul>
          <button className="v-primary" disabled={busy || planned === 0} onClick={() => { setBusy(true); start().finally(() => setBusy(false)); }}>
            Start loading {planned} item{planned === 1 ? "" : "s"}
          </button>
        </>
      ) : (
        <>
          <div className="loadprogress" aria-label={`${saved.length} of ${total} saved`}>
            {plan.map((p, i) => (
              <span key={i} className={i < saved.length ? "done" : i === index && state !== "done" ? "current" : ""} title={p.name} />
            ))}
          </div>
          {state === "done" ? (
            <div className="loadcard done" role="status">
              <CheckCircle size={28} />
              <div>
                <b>All {total} items saved</b>
                <span>Each one's weight is recorded and it counts as stock on {tray.id}.</span>
              </div>
            </div>
          ) : (
            <div className={`loadcard ${state}`} aria-live="polite">
              <span className="loadstep">Item {index + 1} of {total}</span>
              <b className="loadname">{item?.name}</b>
              <span>
                {state === "placing" && "Place it on the tray, then take your hands away. It's saved once the weight settles."}
                {state === "saving" && "Weight settled. Saving…"}
                {state === "problem" && "This item couldn't be saved."}
              </span>
              <p className="weightreadout">
                <Scales size={24} />
                {live ? `${live.live.toFixed(1)} g on tray` : "Waiting for the tray reading…"}
              </p>
            </div>
          )}
          {lcd.length > 0 && (
            <div className="lcdmirror" aria-label="Shelf screen shows">
              <small>Shelf screen</small>
              <code>{(lcd[0] || "").padEnd(16).slice(0, 16)}</code>
              <code>{(lcd[1] || "").replace("#", live ? `${live.live.toFixed(1)}g` : "--").padEnd(16).slice(0, 16)}</code>
            </div>
          )}
          {warning && <Notice>{warning}</Notice>}
          {problem && <p className="error" role="alert">{problem}</p>}
          {state === "problem" && (
            <div className="actions">
              <button onClick={() => begin(index)}>Try this item again</button>
              <button className="secondary" onClick={() => (index + 1 < total ? begin(index + 1) : stop())}>Skip it</button>
            </div>
          )}
          {simulation && state === "placing" && (
            <div className="simrow">
              <label>
                Simulated item weight (g)
                <input type="number" step="any" value={simGrams} onChange={(e) => setSimGrams(Number(e.target.value))} />
              </label>
              <button className="secondary" onClick={() => call("/staff/simulation/weight", token, { tray_id: tray.id, delta_g: simGrams })}>Simulate placing it</button>
            </div>
          )}
          {saved.length > 0 && (
            <ul className="savedlist">
              {saved.map((s, i) => (
                <li key={i}>
                  <CheckCircle size={16} /> {s.name} <b className="money">{s.weight_g.toFixed(1)} g</b>
                  {s.warnings?.length > 0 && <span className="lowstock">similar weight to {product(s.warnings[0].product_id)}</span>}
                </li>
              ))}
            </ul>
          )}
          <div className="actions">
            {state !== "done" && <button className="textbutton" onClick={stop}>Stop guided loading</button>}
            {state === "done" && <button className="secondary" onClick={() => setPlan(null)}>Load more</button>}
          </div>
        </>
      )}
      <h3>Loaded in this session ({loaded.length})</h3>
      <UnitTable units={loaded} product={product} action={action} />
      <button className="secondary" disabled={!!plan && state !== "done"}
        onClick={async () => { await action(`/staff/loads/${load.id}/close`, {}); await guide([], 0); setPlan(null); }}>
        Finish loading
      </button>
    </div>
  );
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
          <h2>Load the tray</h2>
          <label className="inline">
            Tray
            <select value={trayId} onChange={(e) => { setTrayId(e.target.value); resetItem(); setResult(null); }}>
              {data.trays.map((t: any) => (
                <option key={t.id} value={t.id}>{t.id}</option>
              ))}
            </select>
          </label>
        </div>
        <p className="muted">
          Place items one at a time. Each one is weighed as it goes on, so the shelf knows exactly what it holds,
          and the shelf screen tells you which item to place next.
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
          <GuidedLoad data={data} token={token} action={action} tray={tray} load={load} still={still} />
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
