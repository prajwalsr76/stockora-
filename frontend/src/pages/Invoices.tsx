import { useEffect, useState } from "react";
import {
  LuBan,
  LuChartBar,
  LuFileText,
  LuPackage,
  LuPrinter,
  LuReceiptText,
  LuRotateCcw,
  LuSearch,
  LuSettings,
  LuShare2,
  LuX,
} from "react-icons/lu";
import { api, type InvoiceRow, type PdfLayout, type StoreSettings } from "../api";
import { useApp } from "../components/appContext";
import { openPdf, shareBill } from "../components/billDocs";
import { Button, Card, Chip, Empty, ErrorNote, Loading, PageHeader, Segments, Stat, inputClass, shortDate, useAsync } from "../components/ui";
import { t } from "../i18n";

const money = (n: number) => "₹" + n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const qtyText = (q: number, unit: string) => (unit === "pcs" ? `${q.toLocaleString("en-IN")}` : `${q.toLocaleString("en-IN")} ${unit}`);
const todayIso = () => {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
};

function Today() {
  const [day, setDay] = useState(todayIso());
  const { data, error, loading, reload } = useAsync(() => api.daySummary(day), [day]);
  return (
    <Card title={t("Sales summary (units)")} icon={LuChartBar} aside={<input type="date" value={day} max={todayIso()} onChange={(e) => setDay(e.target.value || todayIso())} className={`${inputClass} min-h-9 px-3`} aria-label={t("Day")} />}>
      {loading && <Loading />}
      {error && <ErrorNote error={error} onRetry={reload} />}
      {data && (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Stat icon={LuReceiptText} label={t("Bills")} value={data.bills} note={data.cancelled ? t("{n} cancelled", { n: data.cancelled }) : t("none cancelled")} />
            <Stat icon={LuPackage} tone="info" label={t("Packs sold")} value={data.units.pcs.toLocaleString("en-IN")} />
            <Stat icon={LuPackage} label={t("Loose sold")} value={`${data.units.kg.toLocaleString("en-IN")} kg`} note={data.units.L ? `${data.units.L} L` : undefined} />
            <Stat icon={LuRotateCcw} tone={data.returns ? "exp" : "plain"} label={t("Returns")} value={data.returns} note={t("credit notes")} />
          </div>
          {data.items.length > 0 ? (
            <ul className="mt-5 grid gap-x-8 gap-y-1.5 sm:grid-cols-2">
              {data.items.slice(0, 12).map((it) => (
                <li key={`${it.product_id}${it.unit}`} className="flex justify-between gap-3 border-b border-line/60 py-1.5">
                  <span className="truncate">{it.name}</span>
                  <span className="font-semibold tabular-nums">{qtyText(it.qty, it.unit)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-4 text-ink-2">{t("No sales billed on this day yet.")}</p>
          )}
        </>
      )}
    </Card>
  );
}

function Settings() {
  const [s, setS] = useState<StoreSettings | null>(null);
  const [open, setOpen] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => {
    api.storeSettings().then(setS).catch(() => setS(null));
  }, []);
  if (!s) return null;
  const save = async () => {
    try {
      const out = await api.saveStoreSettings({
        name: s.name, address: s.address ?? "", phone: s.phone ?? "", city: s.city ?? "",
        gstin: s.gstin ?? "", state_code: s.state_code, gst_scheme: s.gst_scheme, invoice_prefix: s.invoice_prefix,
      });
      setS(out);
      setMsg({ ok: true, text: t("Saved. New bills use these details.") });
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    }
  };
  const field = (label: string, key: keyof StoreSettings, extra = "") => (
    <label className="text-[13px] font-semibold text-ink-2">
      {t(label)}
      <input value={(s[key] as string) ?? ""} onChange={(e) => setS({ ...s, [key]: e.target.value })} className={`${inputClass} mt-1 w-full ${extra}`} />
    </label>
  );
  return (
    <Card title={t("Bill settings")} icon={LuSettings} aside={<button className="font-semibold text-brand" onClick={() => setOpen(!open)}>{open ? t("Close") : t("Edit")}</button>}>
      {!open ? (
        <p className="text-ink-2">
          {s.name} · {s.gst_scheme === "regular" ? t("GST registered ({g})", { g: s.gstin ?? "" }) : s.gst_scheme === "composition" ? t("Composition scheme ({g})", { g: s.gstin ?? "" }) : t("Not GST registered")} · {t("bills numbered")}{" "}
          <span className="font-mono">{s.invoice_prefix}/YY-YY/0001</span>
        </p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {field("Shop name", "name")}
          {field("Phone", "phone")}
          {field("Address", "address")}
          {field("City", "city")}
          <label className="text-[13px] font-semibold text-ink-2">
            {t("GST registration")}
            <select value={s.gst_scheme} onChange={(e) => setS({ ...s, gst_scheme: e.target.value as StoreSettings["gst_scheme"] })} className={`${inputClass} mt-1 w-full`}>
              <option value="regular">{t("Registered (tax invoice with GST)")}</option>
              <option value="composition">{t("Composition scheme (bill of supply)")}</option>
              <option value="unregistered">{t("Not registered (simple bill)")}</option>
            </select>
          </label>
          {field("GSTIN", "gstin", "font-mono uppercase")}
          {field("State code (2 digits)", "state_code")}
          {field("Bill number prefix (up to 5 letters)", "invoice_prefix", "uppercase")}
          <div className="flex items-center gap-4 sm:col-span-2">
            <Button onClick={save}>{t("Save settings")}</Button>
            {msg && <p className={msg.ok ? "text-ok" : "text-out"}>{msg.text}</p>}
          </div>
        </div>
      )}
    </Card>
  );
}

function Detail({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged: () => void }) {
  const { refreshStock } = useApp();
  const { data: inv, error, loading, reload, setData } = useAsync(() => api.invoice(id), [id]);
  const [action, setAction] = useState<"none" | "cancel" | "return">("none");
  const [reason, setReason] = useState("");
  const [qty, setQty] = useState<Record<number, string>>({});
  const [refund, setRefund] = useState<"cash" | "upi" | "udhaar">("cash");
  const [restock, setRestock] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const cancel = () =>
    run(async () => {
      setData(await api.cancelInvoice(id, reason));
      setAction("none");
      refreshStock();
      onChanged();
    });
  const doReturn = () =>
    run(async () => {
      const items = Object.entries(qty).map(([k, v]) => ({ invoice_line_id: Number(k), qty: Number(v) })).filter((x) => x.qty > 0);
      const cn = await api.returnItems(id, { items, reason, refund_mode: refund, restock });
      setAction("none");
      setQty({});
      reload();
      refreshStock();
      onChanged();
      await openPdf("credit-notes", cn.id, "a4");
    });

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={t("Bill details")}>
      <button className="absolute inset-0 bg-ink/25" aria-label={t("Close")} onClick={onClose} />
      <div className="slide-in relative h-full w-[min(40rem,100vw)] overflow-y-auto bg-base p-5 shadow-[-12px_0_32px_var(--sh-dark)] sm:p-7">
        <div className="mb-5 flex items-start justify-between gap-4">
          <div>
            <p className="text-[13px] font-semibold text-ink-3">{inv ? t(inv.title) : t("Bill")}</p>
            <h2 className="text-xl font-bold">{inv?.number ?? "…"}</h2>
          </div>
          <button className="neu-btn grid size-10 place-items-center rounded-xl" aria-label={t("Close")} onClick={onClose}>
            <LuX className="size-5" />
          </button>
        </div>
        {loading && <Loading />}
        {error && <ErrorNote error={error} onRetry={reload} />}
        {inv && (
          <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-2">
              {inv.status === "cancelled" ? <Chip t="out">{t("Cancelled")}: {inv.cancel_reason}</Chip> : <Chip t="ok">{t("Active")}</Chip>}
              <Chip>{shortDate(inv.day)}</Chip>
              {inv.customer_name && <Chip>{inv.customer_name}</Chip>}
              {inv.customer_gstin && <Chip t="info">GSTIN {inv.customer_gstin}</Chip>}
              {inv.supply_type === "inter" && <Chip t="info">IGST · {inv.place_of_supply_name}</Chip>}
            </div>
            <ul className="neu-in-sm divide-y divide-line rounded-2xl px-4">
              {inv.lines.map((l) => (
                <li key={l.id} className="flex items-center justify-between gap-3 py-2.5">
                  <span className="min-w-0">
                    <span className="block truncate font-semibold">{l.name}</span>
                    <span className="block text-[12.5px] text-ink-3">
                      {qtyText(l.qty, l.unit)} × {money(l.unit_price)}
                      {l.discount > 0 && ` − ${money(l.discount)}`} · {t("GST")} {l.gst_rate}%{l.returned_qty > 0 && ` · ${t("{q} returned", { q: qtyText(l.returned_qty, l.unit) })}`}
                    </span>
                  </span>
                  {action === "return" ? (
                    <input
                      inputMode="decimal"
                      placeholder="0"
                      value={qty[l.id] ?? ""}
                      onChange={(e) => setQty({ ...qty, [l.id]: e.target.value.replace(/[^\d.]/g, "") })}
                      className={`${inputClass} min-h-9 w-20 text-right`}
                      aria-label={t("Quantity of {name} to return (up to {n})", { name: l.name, n: l.qty - l.returned_qty })}
                    />
                  ) : (
                    <span className="font-semibold tabular-nums">{money(l.amount)}</span>
                  )}
                </li>
              ))}
            </ul>
            <dl className="space-y-1 text-[14px]">
              {inv.discount > 0 && (
                <div className="flex justify-between"><dt className="text-ink-2">{t("Discount")}</dt><dd className="tabular-nums">−{money(inv.discount)}</dd></div>
              )}
              {inv.doc_type === "tax_invoice" && (
                <>
                  <div className="flex justify-between"><dt className="text-ink-2">{t("Taxable value")}</dt><dd className="tabular-nums">{money(inv.taxable)}</dd></div>
                  {inv.igst > 0 ? (
                    <div className="flex justify-between"><dt className="text-ink-2">IGST</dt><dd className="tabular-nums">{money(inv.igst)}</dd></div>
                  ) : (
                    <div className="flex justify-between"><dt className="text-ink-2">CGST + SGST</dt><dd className="tabular-nums">{money(inv.cgst)} + {money(inv.sgst)}</dd></div>
                  )}
                </>
              )}
              <div className="flex justify-between pt-1 text-[16px] font-bold"><dt>{t("Total")}</dt><dd className="tabular-nums">{money(inv.total)}</dd></div>
              <p className="text-right text-[12.5px] text-ink-3">
                {[inv.paid_cash && t("Cash {a}", { a: money(inv.paid_cash) }), inv.paid_upi && t("UPI {a}", { a: money(inv.paid_upi) }), inv.udhaar && t("Udhaar {a}", { a: money(inv.udhaar) })].filter(Boolean).join(" · ")}
              </p>
            </dl>

            <div className="flex flex-wrap gap-2.5">
              {(["a4", "80mm", "58mm"] as PdfLayout[]).map((l) => (
                <Button key={l} size="sm" kind="soft" icon={LuPrinter} onClick={() => run(() => openPdf("invoices", inv.id, l))}>
                  {l === "a4" ? t("A4 / PDF") : l}
                </Button>
              ))}
              <Button size="sm" kind="soft" icon={LuShare2} onClick={() => run(() => shareBill(inv.id, inv.number))}>WhatsApp</Button>
            </div>

            {inv.credit_notes.length > 0 && (
              <div>
                <p className="mb-2 text-[13px] font-semibold text-ink-2">{t("Returns")}</p>
                <ul className="space-y-1.5">
                  {inv.credit_notes.map((c) => (
                    <li key={c.id} className="flex items-center justify-between gap-3">
                      <span>{c.number} · {shortDate(c.day)} · {c.reason}</span>
                      <button className="flex items-center gap-1 font-semibold text-brand" onClick={() => run(() => openPdf("credit-notes", c.id, "a4"))}>
                        <LuFileText className="size-4" /> {money(c.total)}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {inv.status === "active" && action === "none" && (
              <div className="flex flex-wrap gap-2.5 border-t border-line pt-4">
                <Button size="sm" kind="soft" icon={LuRotateCcw} onClick={() => { setAction("return"); setReason(""); }}>{t("Return items")}</Button>
                {inv.can_cancel && <Button size="sm" kind="danger" icon={LuBan} onClick={() => { setAction("cancel"); setReason(""); }}>{t("Cancel bill")}</Button>}
              </div>
            )}
            {action !== "none" && (
              <div className="neu space-y-3 rounded-2xl p-4">
                <p className="font-semibold">{action === "cancel" ? t("Cancel this bill") : t("Return items (a credit note will be made)")}</p>
                {action === "cancel" && <p className="text-[13px] text-ink-2">{t("All items go back into stock and any udhaar from this bill is removed.")}</p>}
                <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder={t("Reason (for example: wrong item, customer changed mind)")} className={`${inputClass} w-full`} aria-label={t("Reason")} />
                {action === "return" && (
                  <div className="flex flex-wrap items-center gap-4">
                    <Segments
                      label={t("Refund")}
                      value={refund}
                      onChange={setRefund}
                      options={[{ id: "cash", label: t("Cash back") }, { id: "upi", label: t("UPI back") }, ...(inv.customer_id ? [{ id: "udhaar" as const, label: t("Reduce udhaar") }] : [])]}
                    />
                    <label className="flex items-center gap-2 text-[13px] text-ink-2">
                      <input type="checkbox" checked={restock} onChange={(e) => setRestock(e.target.checked)} className="size-4 accent-[var(--brand)]" />
                      {t("Goods can be sold again (untick if damaged)")}
                    </label>
                  </div>
                )}
                {err && <p className="text-out">{err}</p>}
                <div className="flex gap-2.5">
                  <Button size="sm" kind={action === "cancel" ? "danger" : "primary"} disabled={busy || reason.trim().length < 2} onClick={action === "cancel" ? cancel : doReturn}>
                    {action === "cancel" ? t("Cancel bill") : t("Make credit note")}
                  </Button>
                  <Button size="sm" kind="soft" onClick={() => { setAction("none"); setErr(null); }}>{t("Back")}</Button>
                </div>
              </div>
            )}
            {err && action === "none" && <p className="text-out">{err}</p>}
          </div>
        )}
      </div>
    </div>
  );
}

export default function Invoices() {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<"all" | "active" | "cancelled">("all");
  const [open, setOpen] = useState<number | null>(null);
  const { data, error, loading, reload } = useAsync(() => api.invoices({ q: query, status: status === "all" ? undefined : status, limit: 100 }), [query, status]);
  useEffect(() => {
    const t = setTimeout(() => setQuery(q), 250);
    return () => clearTimeout(t);
  }, [q]);
  const rows: InvoiceRow[] = data ?? [];
  return (
    <div className="space-y-7">
      <PageHeader icon={LuFileText} title={t("Bills & returns")} sub={t("Every bill made at the counter. Open one to print, share, cancel it, or take back items with a credit note.")} />
      <Today />
      <Card title={t("Bills")} icon={LuReceiptText} flush aside={<Segments label={t("Show")} value={status} onChange={setStatus} options={[{ id: "all", label: t("All") }, { id: "active", label: t("Active") }, { id: "cancelled", label: t("Cancelled") }]} />}>
        <div className="px-6 pb-3">
          <label className="relative block">
            <span className="sr-only">{t("Search bills")}</span>
            <LuSearch className="pointer-events-none absolute left-4 top-1/2 size-[18px] -translate-y-1/2 text-ink-3" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("Bill number, customer, phone or item")} className={`${inputClass} w-full pl-11`} />
          </label>
        </div>
        {loading && <div className="px-6"><Loading /></div>}
        {error && <div className="px-6"><ErrorNote error={error} onRetry={reload} /></div>}
        {data && rows.length === 0 && <Empty icon={LuReceiptText}>{query ? t("No bill matches \"{q}\".", { q: query }) : t("No bills yet. Make the first one on the New bill page.")}</Empty>}
        {rows.length > 0 && (
          <ul className="px-3">
            {rows.map((r) => (
              <li key={r.id}>
                <button onClick={() => setOpen(r.id)} className="grid w-full grid-cols-[1fr_auto] items-center gap-3 rounded-2xl px-3 py-3 text-left hover:bg-line/30">
                  <span className="min-w-0">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-[13.5px] font-semibold">{r.number}</span>
                      {r.status === "cancelled" && <Chip t="out">{t("Cancelled")}</Chip>}
                      {r.credit_notes > 0 && <Chip t="exp">{t("Returned items")}</Chip>}
                      {r.udhaar > 0 && <Chip t="info">{t("Udhaar")}</Chip>}
                    </span>
                    <span className="block truncate text-[12.5px] text-ink-3">
                      {shortDate(r.day)} · {r.lines === 1 ? t("1 item") : t("{n} items", { n: r.lines })}{r.customer_name ? ` · ${r.customer_name}` : ""}
                    </span>
                  </span>
                  <span className={`font-bold tabular-nums ${r.status === "cancelled" ? "text-ink-3 line-through" : ""}`}>{money(r.total)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Settings />
      {open !== null && <Detail id={open} onClose={() => setOpen(null)} onChanged={reload} />}
    </div>
  );
}
