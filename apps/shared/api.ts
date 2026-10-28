export const API = "/api";
export async function call(path: string, token: string, body?: unknown) {
  const r = await fetch(API + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await r.text();
  let data: any = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`Store connection failed (HTTP ${r.status}). Try again.`);
  }
  if (!r.ok)
    throw Object.assign(
      new Error(
        typeof data.detail === "string"
          ? data.detail
          : JSON.stringify(data.detail) || `HTTP ${r.status}`,
      ),
      { status: r.status },
    );
  return data;
}
export const money = (minor: number) =>
  new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" }).format(
    minor / 100,
  );
export type Cart = {
  session_id: string;
  status: string;
  version: number;
  items: {
    product_id: string;
    name: string;
    quantity: number;
    price_minor: number;
    has_image?: boolean;
  }[];
  total_minor: number;
  pending: { id: string; tray_id: string; reasons: string[]; status: string }[];
  bracelet: { id: string; state: string; markers?: number[] } | null;
  exit_requested?: boolean;
  checkout_requested?: boolean; // pressed Checkout; the exit-reader tap bills the visit
  checkout_needs_tap?: boolean;
  exit_reader?: string;
  exit_blockers?: string[];
  receipt: any;
  mode: string;
  weights_enabled?: boolean;
  assisted_events?: any[];
  confirmed_events: any[];
  corrections: any[];
};
