import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  LuBarcode,
  LuCircleCheck,
  LuMinus,
  LuPlus,
  LuPrinter,
  LuReceiptText,
  LuScanLine,
  LuSearch,
  LuShare2,
  LuShoppingBasket,
  LuTrash2,
  LuUserRound,
} from "react-icons/lu";
import { api, ApiError, type CounterProduct, type Customer, type Invoice, type PdfLayout } from "../api";
import { useApp } from "../components/appContext";
import BarcodeScanner from "../components/BarcodeScanner";
import { openPdf, shareBill } from "../components/billDocs";
import { Button, Card, Chip, Empty, ErrorNote, Loading, PageHeader, Segments, inputClass } from "../components/ui";
import { t } from "../i18n";

type LooseUnit = "kg" | "g" | "L" | "ml";
type Line = { key: number; p: CounterProduct; qty: string; unit: "pcs" | LooseUnit; price: string; discount: string };
type PayMode = "cash" | "upi" | "udhaar" | "split";

const money = (n: number) => "₹" + n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const toNum = (s: string) => (s.trim() === "" ? 0 : Number(s));
const baseQty = (l: Line) => (l.unit === "g" || l.unit === "ml" ? toNum(l.qty) / 1000 : toNum(l.qty));
const lineValue = (l: Line) => Math.max(0, baseQty(l) * toNum(l.price) - toNum(l.discount));
const isLoose = (p: CounterProduct) => p.loose === 1;
const baseUnit = (p: CounterProduct): "kg" | "L" => ((p.unit ?? "").toLowerCase() === "l" || (p.unit ?? "").toLowerCase() === "ml" ? "L" : "kg");
const decimal = (s: string) => s.replace(/[^\d.]/g, "").replace(/(\..*)\./g, "$1");

function search(products: CounterProduct[], q: string): CounterProduct[] {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  return products
    .filter((p) => words.every((w) => `${p.name} ${p.brand ?? ""} ${p.barcode ?? ""}`.toLowerCase().includes(w)))
    .slice(0, 8);
}

export default function Counter() {
  const { refreshStock } = useApp();
  const [products, setProducts] = useState<CounterProduct[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [lines, setLines] = useState<Line[]>([]);
  const [billDiscount, setBillDiscount] = useState("");
  const [mode, setMode] = useState<PayMode>("cash");
  const [split, setSplit] = useState({ cash: "", upi: "", udhaar: "" });
  const [customer, setCustomer] = useState<{ id?: number; name: string; phone: string; gstin: string }>({ name: "", phone: "", gstin: "" });
  const [suggest, setSuggest] = useState<Customer[]>([]);
  const [business, setBusiness] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [unknownCode, setUnknownCode] = useState<string | null>(null);
  const [linkQuery, setLinkQuery] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState<Invoice | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const keyRef = useRef(1);

  const load = useCallback(() => {
    api.counterProducts().then(setProducts).catch((e: Error) => setLoadError(e.message));
  }, []);
  useEffect(load, [load]);

  const results = useMemo(() => (products ? search(products, query) : []), [products, query]);
  const linkResults = useMemo(() => (products ? search(products, linkQuery) : []), [products, linkQuery]);
  const subtotal = lines.reduce((a, l) => a + lineValue(l), 0);
  const total = Math.max(0, Math.round(subtotal - toNum(billDiscount)));
  const pay = mode === "split" ? { cash: toNum(split.cash), upi: toNum(split.upi), udhaar: toNum(split.udhaar) } : { cash: 0, upi: 0, udhaar: 0, [mode]: total };
  const paid = pay.cash + pay.upi + pay.udhaar;
  const needsCustomer = pay.udhaar > 0;

  const add = useCallback((p: CounterProduct) => {
    setError(null);
    setDone(null);
    setLines((ls) => {
      const same = ls.find((l) => l.p.id === p.id && !isLoose(p));
      if (same) return ls.map((l) => (l === same ? { ...l, qty: String(toNum(l.qty) + 1) } : l));
      const unit = isLoose(p) ? baseUnit(p) : "pcs";
      return [...ls, { key: keyRef.current++, p, qty: "1", unit, price: String(p.mrp), discount: "" }];
    });
    setQuery("");
    searchRef.current?.focus();
  }, []);

  const byCode = useCallback(
    async (code: string) => {
      setScanning(false);
      try {
        add(await api.byBarcode(code));
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          setUnknownCode(code);
          setLinkQuery("");
        } else setError((e as Error).message);
      }
    },
    [add],
  );

  const onSearchKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== "Enter") return;
    const q = query.trim();
    if (/^\d{8,14}$/.test(q)) byCode(q); // USB scanners type the digits and press Enter
    else if (results[0]) add(results[0]);
  };

  const link = async (p: CounterProduct) => {
    if (!unknownCode) return;
    try {
      const updated = await api.linkBarcode(p.id, unknownCode);
      setProducts((ps) => (ps ?? []).map((x) => (x.id === p.id ? { ...x, barcode: updated.barcode } : x)));
      add({ ...p, barcode: unknownCode });
      setNote(t("Barcode {code} is now linked to {name}. Next time it adds the item directly.", { code: unknownCode, name: p.name }));
      setUnknownCode(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const update = (key: number, patch: Partial<Line>) => setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  const remove = (key: number) => setLines((ls) => ls.filter((l) => l.key !== key));

  useEffect(() => {
    const q = customer.name.trim();
    if (customer.id || q.length < 2) return setSuggest([]);
    const t = setTimeout(() => api.customers(q).then(setSuggest).catch(() => setSuggest([])), 200);
    return () => clearTimeout(t);
  }, [customer.name, customer.id]);

  const problem = (() => {
    if (!lines.length) return t("Add an item to start the bill.");
    for (const l of lines) {
      const q = baseQty(l);
      if (!(q > 0)) return t("{name}: enter a quantity.", { name: l.p.name });
      if (l.unit === "pcs" && !Number.isInteger(q)) return t("{name}: whole packs only.", { name: l.p.name });
      if (toNum(l.price) > l.p.mrp) return isLoose(l.p) ? t("{name}: price is above its list price (₹{p}).", { name: l.p.name, p: l.p.mrp }) : t("{name}: price is above MRP (₹{p}).", { name: l.p.name, p: l.p.mrp });
      if (toNum(l.discount) > baseQty(l) * toNum(l.price)) return t("{name}: discount is more than the item value.", { name: l.p.name });
    }
    if (mode === "split" && Math.abs(paid - total) > 0.001) return t("Payments add up to {a}; the bill is {b}.", { a: money(paid), b: money(total) });
    if (needsCustomer && !customer.id && !customer.name.trim()) return t("Udhaar needs the customer's name.");
    return null;
  })();

  const save = async () => {
    if (problem) return;
    setSaving(true);
    setError(null);
    try {
      const inv = await api.createInvoice({
        lines: lines.map((l) => ({
          product_id: l.p.id,
          qty: toNum(l.qty),
          qty_unit: l.unit,
          unit_price: toNum(l.price),
          discount: toNum(l.discount),
        })),
        payment: pay,
        customer: customer.id || customer.name.trim() || (business && customer.gstin.trim())
          ? { id: customer.id, name: customer.name.trim() || undefined, phone: customer.phone.trim() || undefined, gstin: business ? customer.gstin.trim() || undefined : undefined }
          : null,
        bill_discount: toNum(billDiscount),
      });
      setDone(inv);
      setLines([]);
      setBillDiscount("");
      setMode("cash");
      setSplit({ cash: "", upi: "", udhaar: "" });
      setCustomer({ name: "", phone: "", gstin: "" });
      setBusiness(false);
      setNote(null);
      load();
      refreshStock();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const doc = async (fn: () => Promise<void>) => {
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="space-y-7">
      <PageHeader icon={LuReceiptText} title={t("New bill")} sub={t("Search or scan items, take payment, and print or share the bill. Stock updates as soon as the bill is saved.")} />
      {loadError && <ErrorNote error={loadError} onRetry={load} />}
      {!products && !loadError && <Loading what={t("Loading items")} />}

      {done && (
        <section className="neu rise flex flex-wrap items-center gap-5 rounded-[22px] p-5" aria-live="polite">
          <span className="neu-in grid size-12 shrink-0 place-items-center rounded-2xl text-ok">
            <LuCircleCheck className="size-6" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="font-semibold">
              {t("{title} {number} saved", { title: t(done.title), number: done.number })} · <span className="tabular-nums">{money(done.total)}</span>
            </p>
            <p className="text-[13px] text-ink-2">
              {[done.paid_cash && t("Cash {a}", { a: money(done.paid_cash) }), done.paid_upi && t("UPI {a}", { a: money(done.paid_upi) }), done.udhaar && t("Udhaar {a} to {name}", { a: money(done.udhaar), name: done.customer_name ?? "" })]
                .filter(Boolean)
                .join(" · ")}
              . {t("Stock is updated.")}
            </p>
          </div>
          <div className="flex flex-wrap gap-2.5">
            {(["a4", "80mm", "58mm"] as PdfLayout[]).map((l) => (
              <Button key={l} size="sm" kind="soft" icon={LuPrinter} onClick={() => doc(() => openPdf("invoices", done.id, l))}>
                {l === "a4" ? t("A4 / PDF") : l}
              </Button>
            ))}
            <Button size="sm" kind="soft" icon={LuShare2} onClick={() => doc(() => shareBill(done.id, done.number))}>
              WhatsApp
            </Button>
          </div>
        </section>
      )}

      {products && (
        <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <div className="min-w-0 space-y-6">
            <Card>
              <div className="flex gap-3">
                <label className="relative flex-1">
                  <span className="sr-only">{t("Search items or type a barcode")}</span>
                  <LuSearch className="pointer-events-none absolute left-4 top-1/2 size-[18px] -translate-y-1/2 text-ink-3" />
                  <input
                    ref={searchRef}
                    autoFocus
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={onSearchKey}
                    placeholder={t("Search item, or type / scan a barcode")}
                    className={`${inputClass} w-full pl-11 text-[15px]`}
                  />
                </label>
                <Button kind="soft" icon={LuScanLine} onClick={() => setScanning(true)} title={t("Scan with the camera")}>
                  <span className="hidden sm:inline">{t("Scan")}</span>
                </Button>
              </div>
              {results.length > 0 && (
                <ul className="mt-3 space-y-1" aria-label={t("Matching items")}>
                  {results.map((p) => (
                    <li key={p.id}>
                      <button onClick={() => add(p)} className="flex min-h-12 w-full items-center gap-3 rounded-2xl px-3 text-left hover:bg-line/40">
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-semibold">{p.name}</span>
                          <span className="block text-[12.5px] text-ink-3">
                            {isLoose(p) ? t("₹{p} per {u}", { p: p.mrp, u: baseUnit(p) }) : `MRP ₹${p.mrp}`} · {p.stock <= 0 ? t("out of stock") : isLoose(p) ? `${p.stock.toLocaleString("en-IN")} ${baseUnit(p)}` : t("{n} in stock", { n: p.stock.toLocaleString("en-IN") })}
                          </span>
                        </span>
                        {isLoose(p) && <Chip t="info">{t("Loose")}</Chip>}
                        <LuPlus className="size-5 text-brand" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {query.trim() && results.length === 0 && !/^\d{8,14}$/.test(query.trim()) && <p className="mt-3 px-1 text-ink-2">{t("No item matches \"{q}\".", { q: query })}</p>}
              {note && <p className="mt-3 rounded-2xl bg-ok-soft px-4 py-2.5 text-[13.5px] text-ok">{note}</p>}
            </Card>

            {unknownCode && (
              <Card title={t("Barcode {code} is not linked yet", { code: unknownCode })} icon={LuBarcode} aside={<button className="font-semibold text-brand" onClick={() => setUnknownCode(null)}>{t("Cancel")}</button>}>
                <p className="mb-3 text-ink-2">{t("Pick the item this barcode belongs to. Stockora will remember it for the next scan.")}</p>
                <input autoFocus value={linkQuery} onChange={(e) => setLinkQuery(e.target.value)} placeholder={t("Search the item")} className={`${inputClass} w-full`} aria-label={t("Search the item for this barcode")} />
                <ul className="mt-2 space-y-1">
                  {linkResults.map((p) => (
                    <li key={p.id}>
                      <button onClick={() => link(p)} className="flex min-h-11 w-full items-center justify-between rounded-2xl px-3 text-left hover:bg-line/40">
                        <span className="truncate font-semibold">{p.name}</span>
                        {p.barcode && <span className="text-[12px] text-ink-3">{t("has {code}", { code: p.barcode })}</span>}
                      </button>
                    </li>
                  ))}
                </ul>
              </Card>
            )}

            <Card title={t("Items ({n})", { n: lines.length })} icon={LuShoppingBasket} flush>
              {lines.length === 0 ? (
                <Empty icon={LuShoppingBasket}>{t("The bill is empty. Search above or scan a barcode to add items.")}</Empty>
              ) : (
                <ul className="divide-y divide-line px-3">
                  {lines.map((l) => {
                    const loose = isLoose(l.p);
                    const short = baseQty(l) > l.p.stock + 1e-9;
                    return (
                      <li key={l.key} className="grid gap-3 px-3 py-4 sm:grid-cols-[minmax(0,1fr)_auto]">
                        <div className="min-w-0">
                          <p className="flex flex-wrap items-center gap-2">
                            <span className="font-semibold">{l.p.name}</span>
                            {short && <Chip t="out">{t("only {n} in stock", { n: l.p.stock.toLocaleString("en-IN") })}</Chip>}
                          </p>
                          <p className="text-[12.5px] text-ink-3">
                            {loose ? t("List price ₹{p}/{u}", { p: l.p.mrp, u: baseUnit(l.p) }) : `MRP ₹${l.p.mrp}`} · {t("GST")} {l.p.gst_rate}%
                          </p>
                          <div className="mt-3 flex flex-wrap items-center gap-2.5">
                            {loose ? (
                              <>
                                <input inputMode="decimal" value={l.qty} onChange={(e) => update(l.key, { qty: decimal(e.target.value) })} className={`${inputClass} w-24 text-right tabular-nums`} aria-label={t("Weight of {name}", { name: l.p.name })} />
                                <select value={l.unit} onChange={(e) => update(l.key, { unit: e.target.value as LooseUnit })} className={`${inputClass} w-20 px-3`} aria-label={t("Unit")}>
                                  {(baseUnit(l.p) === "kg" ? ["kg", "g"] : ["L", "ml"]).map((u) => (
                                    <option key={u}>{u}</option>
                                  ))}
                                </select>
                                {(baseUnit(l.p) === "kg" ? [["250", "g"], ["500", "g"], ["1", "kg"]] : [["500", "ml"], ["1", "L"]]).map(([q, u]) => (
                                  <button key={q + u} onClick={() => update(l.key, { qty: q, unit: u as LooseUnit })} className="neu-btn min-h-9 rounded-xl px-3 text-[13px] font-semibold">
                                    {q} {u}
                                  </button>
                                ))}
                              </>
                            ) : (
                              <div className="neu-in-sm flex items-center rounded-2xl p-1">
                                <button className="grid size-10 place-items-center rounded-xl text-ink-2 hover:text-ink" aria-label={t("One less")} onClick={() => (toNum(l.qty) <= 1 ? remove(l.key) : update(l.key, { qty: String(toNum(l.qty) - 1) }))}>
                                  <LuMinus className="size-4" />
                                </button>
                                <input inputMode="numeric" value={l.qty} onChange={(e) => update(l.key, { qty: e.target.value.replace(/\D/g, "") })} className="w-12 bg-transparent text-center font-semibold tabular-nums focus:outline-none" aria-label={t("Quantity of {name}", { name: l.p.name })} />
                                <button className="grid size-10 place-items-center rounded-xl text-ink-2 hover:text-ink" aria-label={t("One more")} onClick={() => update(l.key, { qty: String(toNum(l.qty) + 1) })}>
                                  <LuPlus className="size-4" />
                                </button>
                              </div>
                            )}
                            <label className="flex items-center gap-1.5 text-[13px] text-ink-2">
                              {t("Price")}
                              <input inputMode="decimal" value={l.price} onChange={(e) => update(l.key, { price: decimal(e.target.value) })} className={`${inputClass} w-24 text-right tabular-nums`} />
                            </label>
                            <label className="flex items-center gap-1.5 text-[13px] text-ink-2">
                              {t("Discount ₹")}
                              <input inputMode="decimal" value={l.discount} placeholder="0" onChange={(e) => update(l.key, { discount: decimal(e.target.value) })} className={`${inputClass} w-20 text-right tabular-nums`} />
                            </label>
                          </div>
                        </div>
                        <div className="flex items-center justify-between gap-3 sm:flex-col sm:items-end sm:justify-start">
                          <span className="text-[16px] font-bold tabular-nums">{money(lineValue(l))}</span>
                          <button onClick={() => remove(l.key)} className="neu-btn grid size-10 place-items-center rounded-xl text-out" aria-label={t("Remove {name}", { name: l.p.name })}>
                            <LuTrash2 className="size-4" />
                          </button>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </Card>
          </div>

          <div className="space-y-6 lg:sticky lg:top-24">
            <Card title={t("Payment")}>
              <dl className="space-y-1.5 text-[14px]">
                <div className="flex justify-between">
                  <dt className="text-ink-2">{t("Items")}</dt>
                  <dd className="tabular-nums">{money(subtotal)}</dd>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-ink-2">{t("Bill discount ₹")}</dt>
                  <dd>
                    <input inputMode="decimal" value={billDiscount} placeholder="0" onChange={(e) => setBillDiscount(decimal(e.target.value))} className={`${inputClass} min-h-9 w-24 text-right tabular-nums`} aria-label={t("Bill discount in rupees")} />
                  </dd>
                </div>
                <div className="flex items-end justify-between pt-2">
                  <dt className="font-semibold">{t("Total")}</dt>
                  <dd className="text-[1.9rem] font-extrabold leading-none tabular-nums" data-testid="bill-total">
                    ₹{total.toLocaleString("en-IN")}
                  </dd>
                </div>
                <p className="text-right text-[12px] text-ink-3">{t("Prices include GST · rounded to the rupee")}</p>
              </dl>
              <div className="mt-4">
                <Segments
                  label={t("How the customer pays")}
                  value={mode}
                  onChange={setMode}
                  options={[
                    { id: "cash", label: t("Cash") },
                    { id: "upi", label: "UPI" },
                    { id: "udhaar", label: t("Udhaar") },
                    { id: "split", label: t("Split") },
                  ]}
                />
              </div>
              {mode === "split" && (
                <div className="mt-3 grid grid-cols-3 gap-2">
                  {(["cash", "upi", "udhaar"] as const).map((k) => (
                    <label key={k} className="text-[12.5px] font-semibold text-ink-2">
                      {k === "upi" ? "UPI" : t(k === "cash" ? "Cash" : "Udhaar")}
                      <input inputMode="decimal" value={split[k]} placeholder="0" onChange={(e) => setSplit({ ...split, [k]: decimal(e.target.value) })} className={`${inputClass} mt-1 w-full px-3 text-right tabular-nums`} />
                    </label>
                  ))}
                  <p className={`col-span-3 text-[12.5px] ${Math.abs(paid - total) < 0.001 ? "text-ok" : "text-exp"}`}>
                    {Math.abs(paid - total) < 0.001 ? t("Payments match the bill.") : paid < total ? t("{a} still to enter", { a: money(Math.abs(total - paid)) }) : t("{a} too much", { a: money(Math.abs(total - paid)) })}
                  </p>
                </div>
              )}
            </Card>

            <Card title={needsCustomer ? t("Customer (needed for udhaar)") : t("Customer (optional)")} icon={LuUserRound}>
              <div className="relative space-y-2.5">
                <input
                  value={customer.name}
                  onChange={(e) => setCustomer({ ...customer, id: undefined, name: e.target.value })}
                  placeholder={t("Name")}
                  className={`${inputClass} w-full ${needsCustomer && !customer.name.trim() ? "ring-2 ring-exp/60" : ""}`}
                  aria-label={t("Customer name")}
                />
                {suggest.length > 0 && (
                  <ul className="neu absolute inset-x-0 top-12 z-20 max-h-56 overflow-y-auto rounded-2xl p-1.5">
                    {suggest.map((c) => (
                      <li key={c.id}>
                        <button
                          className="flex w-full items-center justify-between rounded-xl px-3 py-2 text-left hover:bg-line/40"
                          onClick={() => {
                            setCustomer({ id: c.id, name: c.name, phone: c.phone ?? "", gstin: "" });
                            setSuggest([]);
                          }}
                        >
                          <span className="font-semibold">{c.name}</span>
                          <span className="text-[12px] text-ink-3">{c.due > 0 ? t("owes ₹{n}", { n: Math.round(c.due).toLocaleString("en-IN") }) : c.phone}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                <input inputMode="tel" value={customer.phone} onChange={(e) => setCustomer({ ...customer, phone: e.target.value.replace(/[^\d+ ]/g, "") })} placeholder={t("Phone (for WhatsApp)")} className={`${inputClass} w-full`} aria-label={t("Customer phone")} />
                <label className="flex items-center gap-2 text-[13px] text-ink-2">
                  <input type="checkbox" checked={business} onChange={(e) => setBusiness(e.target.checked)} className="size-4 accent-[var(--brand)]" />
                  {t("Business customer with GSTIN")}
                </label>
                {business && (
                  <input value={customer.gstin} onChange={(e) => setCustomer({ ...customer, gstin: e.target.value.toUpperCase().replace(/\s/g, "") })} maxLength={15} placeholder={t("Customer GSTIN")} className={`${inputClass} w-full font-mono`} aria-label={t("Customer GSTIN")} />
                )}
                {customer.id && <p className="text-[12.5px] text-ok">{t("Existing customer selected.")}</p>}
              </div>
            </Card>

            {error && <ErrorNote error={error} />}
            <div className="space-y-2">
              <button
                onClick={save}
                disabled={!!problem || saving}
                className="neu-primary flex min-h-14 w-full items-center justify-center gap-2 rounded-2xl text-[16px] font-bold disabled:cursor-not-allowed disabled:opacity-45"
              >
                <LuReceiptText className="size-5" />
                {saving ? t("Saving…") : t("Save bill · ₹{n}", { n: total.toLocaleString("en-IN") })}
              </button>
              {problem && lines.length > 0 && <p className="text-center text-[13px] text-ink-2">{problem}</p>}
            </div>
          </div>
        </div>
      )}
      {scanning && <BarcodeScanner onCode={byCode} onClose={() => setScanning(false)} />}
    </div>
  );
}
