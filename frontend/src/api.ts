// Typed client for the Stockora FastAPI backend.

export type StockRow = {
  product_id: number;
  name: string;
  category: string;
  unit: string | null;
  stock: number;
  mrp: number;
  shelf_life_days: number | null;
  daily_rate: number;
  forecast_7d: number;
  forecast_7d_low: number;
  forecast_7d_high: number;
  days_of_stock: number | null;
  runs_out_on: string | null;
  reorder_point: number | null;
  sold_7d: number;
  sold_28d: number;
  trend_pct: number | null;
};

export type ProductDetail = StockRow & {
  history: { day: string; units: number; stockout: boolean }[];
  forecast: { day: string; units: number; low: number; high: number; stock_after: number }[];
  batches: { batch_id: number; batch_no: string | null; qty: number; received: string; expiry: string | null; days_left: number | null; at_risk: number }[];
  festivals_in_window: string[];
  mode: "learning" | "model";
};

export type Trend = {
  actual: { day: string; units: number }[];
  forecast: { day: string; units: number; low: number; high: number }[];
  festivals: { name: string; day: string }[];
};

export type ExpiryAlert = {
  product_id: number;
  name: string;
  batch_id: number;
  batch_no: string | null;
  expiry: string;
  days_left: number;
  remaining: number;
  expected_to_sell: number;
  leftover: number;
  action: string;
};

export type Collect = { customer: string; phone: string; due: number; oldest_since: string };

export type Briefing = {
  date: string;
  yesterday_units: number;
  low_stock: StockRow[];
  expiring: ExpiryAlert[];
  reorder_items: number;
  collect_from: Collect[];
  festivals: string;
  stockout_days_30d: number | null;
};

export type PlanLine = {
  product_id: number;
  name: string;
  category: string;
  stock: number;
  pending: number;
  forecast_cover: number;
  safety: number;
  qty: number;
  distributor_id: number;
  distributor: string;
  unit_price: number;
  free_units: number;
  line_cost: number;
  priority: number;
  days_of_stock: number;
  reason: string;
  scheme_note: string;
};

export type OrderPlan = {
  day: string;
  cash_available: number;
  expected_collections: number;
  budget: number;
  total: number;
  lines: PlanLine[];
  deferred: PlanLine[];
  collect_from: Collect[];
  below_minimum: string[];
  messages: Record<string, string>;
  festival_note: string;
};

export type OrderLine = {
  id: number;
  product_id: number;
  name: string;
  qty_suggested: number;
  qty_final: number | null;
  unit_price: number;
  reason: string;
};

export type Order = {
  id: number;
  distributor_id: number;
  distributor: string;
  status: "draft" | "approved" | "sent" | "received" | "cancelled";
  total: number;
  message: string;
  created_at: string;
  lines: OrderLine[];
};

export type Receivable = { id: number; name: string; phone: string; due: number; oldest: string; last_paid: string | null };

export type LostSales = {
  period_days: number;
  stockout_days: number;
  items_affected: number;
  lost_units: number;
  top_items: { product_id: number; name: string; days_out: number; lost_units: number }[];
};

export type DeadStock = { product_id: number; name: string; qty: number; idle_days: number; action: string };
export type Metrics = {
  llm_calls: number;
  llm_cost_usd: number;
  avg_latency_ms: number;
  success_rate: number | null;
  owner_edit_rate: number | null;
};
export type Forecast = {
  product_id: number;
  name: string;
  daily: Record<string, number>;
  next_7_days: number;
  next_14_days: number;
  festivals_in_window: string[];
};
export type Alerts = {
  missed_requests: { item: string; asked_times: number; on_days: number; estimated_weekly_demand: number; action: string }[];
  count_mismatch: { product_id: number; name: string; missing_units: number; counts_short: number; action: string }[];
  udhaar_risk: { customer_id: number; name: string; risk: "high" | "medium" | "low"; why: string; action: string }[];
};
export type AreaTrend = { area: string; week: string; category: string; shops: number; share_up: number; median_change: number; message: string };
export type AreaTrends = { area: string | null; joined: boolean; trends: AreaTrend[] };
export type ModelTest = {
  dataset: string;
  level: string;
  what: string;
  accuracy_weekly_pct: number;
  baseline_weekly_pct: number;
  baseline: string;
  error_cut_pct: number;
};
export type ModelCard = { model: string | null; tests: ModelTest[]; note?: string };
export type Status = { mode: "learning" | "model"; history_days: number; model_min_days: number };
export type Store = {
  id: number;
  is_demo: boolean;
  products: number;
  name: string;
  city: string;
  state_code: string;
  gstin: string;
  cash_on_hand: number;
  llm_enabled: boolean;
  llm_model?: string | null;
};
export type Lang = "en" | "hi";
export type ChatTurn = { role: "user" | "assistant"; content: string };

export type Reply = {
  answer: string;
  intent: string;
  language: string;
  grounded: boolean;
  used_llm: boolean;
  product: string | null;
  flags: string[];
};

export type BillLine = {
  name: string;
  hsn: string | null;
  qty: number;
  free_qty: number;
  rate: number;
  discount_pct: number;
  gst_rate: number;
  mrp: number | null;
  batch: string | null;
  expiry: string | null;
  amount: number | null;
};
export type Bill = {
  distributor_name: string;
  distributor_gstin: string | null;
  bill_no: string;
  bill_date: string;
  lines: BillLine[];
  taxable_total: number | null;
  cgst: number | null;
  sgst: number | null;
  igst: number | null;
  grand_total: number;
};
export type Issue = { severity: "error" | "warning"; field: string; message: string };
export type Match = {
  line: number;
  name: string;
  product_id: number | null;
  confidence: number;
  candidates: { product_id: number; alias: string; score: number }[];
};

/* ---------------- counter billing ---------------- */
export type CounterProduct = {
  id: number;
  name: string;
  brand: string | null;
  category: string;
  unit: string | null;
  pack_size: number | null;
  mrp: number;
  hsn: string | null;
  gst_rate: number;
  barcode: string | null;
  loose: number;
  stock: number;
};
export type InvoiceLine = {
  id: number;
  line_no: number;
  product_id: number;
  name: string;
  hsn: string | null;
  qty: number;
  unit: "pcs" | "kg" | "L";
  unit_price: number;
  mrp: number | null;
  discount: number;
  taxable: number;
  gst_rate: number;
  cgst: number;
  sgst: number;
  igst: number;
  amount: number;
  returned_qty: number;
};
export type CreditNote = {
  id: number;
  number: string;
  day: string;
  reason: string;
  total: number;
  refund_mode: "cash" | "upi" | "udhaar";
};
export type Invoice = {
  id: number;
  number: string;
  doc_type: "tax_invoice" | "bill_of_supply" | "bill";
  title: string;
  day: string;
  customer_id: number | null;
  customer_name: string | null;
  customer_phone: string | null;
  customer_gstin: string | null;
  place_of_supply: string;
  place_of_supply_name: string;
  supply_type: "intra" | "inter";
  gross: number;
  discount: number;
  taxable: number;
  cgst: number;
  sgst: number;
  igst: number;
  round_off: number;
  total: number;
  paid_cash: number;
  paid_upi: number;
  udhaar: number;
  status: "active" | "cancelled";
  cancel_reason: string | null;
  lines: InvoiceLine[];
  credit_notes: CreditNote[];
  can_cancel: boolean;
};
export type InvoiceRow = {
  id: number;
  number: string;
  doc_type: Invoice["doc_type"];
  day: string;
  customer_name: string | null;
  customer_phone: string | null;
  total: number;
  status: Invoice["status"];
  paid_cash: number;
  paid_upi: number;
  udhaar: number;
  lines: number;
  credit_notes: number;
};
export type DaySummary = {
  day: string;
  bills: number;
  cancelled: number;
  returns: number;
  units: { pcs: number; kg: number; L: number };
  by_category: { category: string; unit: string; qty: number }[];
  items: { product_id: number; name: string; category: string; unit: string; qty: number }[];
};
export type StoreSettings = {
  name: string;
  address: string | null;
  phone: string | null;
  city: string | null;
  gstin: string | null;
  state_code: string;
  state: string;
  gst_scheme: "regular" | "composition" | "unregistered";
  invoice_prefix: string;
  doc_type: Invoice["doc_type"];
};
export type Shop = { id: number; name: string; city: string | null; state_code: string; size: string | null; is_demo: boolean; active: boolean };
export type CatalogItem = { sku: string; name: string; brand: string; category: string; mrp: number; gst_rate: number; unit: string; pack_size: number };
export type ImportField = { name: string; field: string | null };
export type ImportPreview = { columns: ImportField[]; rows: number; missing: string[]; sample: Record<string, string>[]; fields: Record<string, string> };
export type ImportResult = { added: number; updated: number; skipped: string[]; filled: string[]; warnings: string[]; ignored_columns: string[] };
export type Customer = { id: number; name: string; phone: string | null; due: number };
export type NewInvoice = {
  lines: { product_id: number; qty: number; qty_unit?: string; unit_price?: number; discount?: number }[];
  payment: { cash: number; upi: number; udhaar: number };
  customer?: { id?: number; name?: string; phone?: string; gstin?: string; save?: boolean } | null;
  bill_discount: number;
};
export type PdfLayout = "a4" | "80mm" | "58mm";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const BASE = (import.meta as unknown as { env?: { VITE_API_URL?: string } }).env?.VITE_API_URL ?? "";

/** Resolves once the server answers /health. The free demo server can take about a minute to wake. */
export async function waitForServer(maxSeconds = 180): Promise<void> {
  const until = Date.now() + maxSeconds * 1000;
  while (Date.now() < until) {
    try {
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), 15_000);
      const res = await fetch(BASE + "/health", { signal: ctl.signal });
      clearTimeout(timer);
      if (res.ok) return;
    } catch {
      /* asleep or starting: try again */
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
  throw new Error("server not answering");
}

async function send(path: string, init?: RequestInit): Promise<Response> {
  let key: string | null = null;
  try {
    key = localStorage.getItem("stockora_api_key");
  } catch {
    /* storage blocked (private mode, embedded views): no key */
  }
  const headers = new Headers(init?.headers);
  if (key) headers.set("X-API-Key", key);
  if (init?.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(BASE + path, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return res;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return (await send(path, init)).json() as Promise<T>;
}

const put = <T,>(path: string, body: unknown) => request<T>(path, { method: "PUT", body: JSON.stringify(body) });
const qs = (o: Record<string, string | number | undefined | null>) =>
  new URLSearchParams(Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== "") as [string, string][]).toString();

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  store: () => request<Store>("/api/store"),
  briefing: () => request<Briefing>("/api/briefing"),
  status: () => request<Status>("/api/status"),
  alerts: () => request<Alerts>("/api/alerts"),
  logRequest: (item: string) => post<{ logged: string; matched_product: string | null; note: string }>("/api/requests", { item }),
  areaTrends: () => request<AreaTrends>("/api/area/trends"),
  sell: (product_id: number, qty: number) => post<{ ok: boolean; stock: number }>("/api/sales", { product_id, qty }),
  count: (id: number, counted: number) =>
    post<{ ok: boolean; stock: number; difference: number }>(`/api/stock/${id}/count`, { counted }),
  stock: (lowOnly = false) => request<StockRow[]>(`/api/stock?low_only=${lowOnly}`),
  forecast: (id: number) => request<Forecast>(`/api/products/${id}/forecast`),
  plan: () => request<OrderPlan>("/api/orders/plan"),
  draftOrders: () => post<{ order_ids: number[] }>("/api/orders/draft"),
  orders: (status?: string) => request<Order[]>(`/api/orders${status ? `?status=${status}` : ""}`),
  approve: (id: number, edits: Record<number, number>) => post<Order>(`/api/orders/${id}/approve`, { edits }),
  reject: (id: number) => post<{ ok: boolean }>(`/api/orders/${id}/reject`),
  expiry: () => request<ExpiryAlert[]>("/api/expiry"),
  deadStock: () => request<DeadStock[]>("/api/dead-stock"),
  lostSales: () => request<LostSales>("/api/lost-sales"),
  udhaar: () => request<Receivable[]>("/api/udhaar"),
  addCredit: (customer_id: number, amount: number, note = "") => post("/api/udhaar", { customer_id, amount, note }),
  ask: (question: string, language: Lang, history: ChatTurn[]) =>
    post<Reply>("/api/assistant/ask", { question, language, history: history.slice(-6) }),
  product: (id: number) => request<ProductDetail>(`/api/products/${id}`),
  trend: () => request<Trend>("/api/trend"),
  extractBill: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<{ bill: Bill; issues: Issue[]; matches: Match[] }>("/api/bills/extract", { method: "POST", body: fd });
  },
  validateBill: (bill: Bill) => post<{ issues: Issue[]; matches: Match[]; needs_review: boolean }>("/api/bills/validate", bill),
  postBill: (bill: Bill, matches: Match[]) => post<{ purchase_id: number }>("/api/bills/post", { bill, matches }),
  metrics: () => request<Metrics>("/api/metrics"),
  // counter billing
  counterProducts: () => request<CounterProduct[]>("/api/billing/products"),
  byBarcode: (code: string) => request<CounterProduct>(`/api/billing/barcode/${encodeURIComponent(code)}`),
  linkBarcode: (productId: number, barcode: string | null) => post<CounterProduct>(`/api/products/${productId}/barcode`, { barcode }),
  customers: (q = "") => request<Customer[]>(`/api/customers?${qs({ q })}`),
  createInvoice: (body: NewInvoice) => post<Invoice>("/api/invoices", body),
  invoices: (f: { q?: string; from?: string; to?: string; status?: string; limit?: number } = {}) =>
    request<InvoiceRow[]>(`/api/invoices?${qs(f)}`),
  invoice: (id: number) => request<Invoice>(`/api/invoices/${id}`),
  daySummary: (day?: string) => request<DaySummary>(`/api/invoices/summary?${qs({ day })}`),
  cancelInvoice: (id: number, reason: string) => post<Invoice>(`/api/invoices/${id}/cancel`, { reason }),
  returnItems: (id: number, body: { items: { invoice_line_id: number; qty: number }[]; reason: string; refund_mode: string; restock: boolean }) =>
    post<CreditNote>(`/api/invoices/${id}/credit-notes`, body),
  whatsapp: (id: number) => request<{ text: string; url: string }>(`/api/invoices/${id}/whatsapp`),
  pdf: async (kind: "invoices" | "credit-notes", id: number, layout: PdfLayout) =>
    (await send(`/api/${kind}/${id}/pdf?layout=${layout}`)).blob(),
  shops: () => request<Shop[]>("/api/shops"),
  createShop: (b: { name: string; city: string; state_code: string; size: string; gst_scheme: string; gstin?: string; phone?: string; address?: string }) =>
    post<{ id: number }>("/api/shops", b),
  openShop: (id: number) => post<{ id: number }>(`/api/shops/${id}/open`),
  catalog: () => request<CatalogItem[]>("/api/catalog"),
  addItems: (items: { sku?: string; name?: string; mrp?: number; gst_rate?: number; category?: string; loose?: boolean; stock: number }[]) =>
    post<{ added: number; updated: number; skipped: string[] }>("/api/shops/current/items", { items }),
  importPreview: (kind: "products" | "sales", file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<ImportPreview>(`/api/import/preview?kind=${kind}`, { method: "POST", body: fd });
  },
  importFile: (kind: "products" | "sales", file: File, mapping: Record<string, string | null>) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<ImportResult>(`/api/import?kind=${kind}&mapping=${encodeURIComponent(JSON.stringify(mapping))}`, { method: "POST", body: fd });
  },
  storeSettings: () => request<StoreSettings>("/api/store/settings"),
  saveStoreSettings: (s: Partial<StoreSettings>) => put<StoreSettings>("/api/store/settings", s),
  modelCard: () => request<ModelCard>("/api/model-card"),
};

export const rs = (n: number) => "₹" + Math.round(n).toLocaleString("en-IN");
