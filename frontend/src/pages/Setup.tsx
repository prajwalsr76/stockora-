import { useEffect, useMemo, useState } from "react";
import {
  LuCircleCheck,
  LuClipboardList,
  LuFileSpreadsheet,
  LuPackagePlus,
  LuSearch,
  LuStore,
  LuUpload,
} from "react-icons/lu";
import { api, type CatalogItem, type ImportPreview, type ImportResult } from "../api";
import { Button, Card, Chip, ErrorNote, PageHeader, inputClass } from "../components/ui";
import { categoryName, getLang, t } from "../i18n";

const STATES: [string, string, string][] = [
  ["01", "Jammu and Kashmir", "जम्मू और कश्मीर"], ["02", "Himachal Pradesh", "हिमाचल प्रदेश"], ["03", "Punjab", "पंजाब"],
  ["04", "Chandigarh", "चंडीगढ़"], ["05", "Uttarakhand", "उत्तराखंड"], ["06", "Haryana", "हरियाणा"], ["07", "Delhi", "दिल्ली"],
  ["08", "Rajasthan", "राजस्थान"], ["09", "Uttar Pradesh", "उत्तर प्रदेश"], ["10", "Bihar", "बिहार"], ["11", "Sikkim", "सिक्किम"],
  ["12", "Arunachal Pradesh", "अरुणाचल प्रदेश"], ["13", "Nagaland", "नागालैंड"], ["14", "Manipur", "मणिपुर"], ["15", "Mizoram", "मिज़ोरम"],
  ["16", "Tripura", "त्रिपुरा"], ["17", "Meghalaya", "मेघालय"], ["18", "Assam", "असम"], ["19", "West Bengal", "पश्चिम बंगाल"],
  ["20", "Jharkhand", "झारखंड"], ["21", "Odisha", "ओडिशा"], ["22", "Chhattisgarh", "छत्तीसगढ़"], ["23", "Madhya Pradesh", "मध्य प्रदेश"],
  ["24", "Gujarat", "गुजरात"], ["26", "Dadra and Nagar Haveli and Daman and Diu", "दादरा नगर हवेली और दमन दीव"], ["27", "Maharashtra", "महाराष्ट्र"],
  ["29", "Karnataka", "कर्नाटक"], ["30", "Goa", "गोवा"], ["31", "Lakshadweep", "लक्षद्वीप"], ["32", "Kerala", "केरल"], ["33", "Tamil Nadu", "तमिलनाडु"],
  ["34", "Puducherry", "पुडुचेरी"], ["35", "Andaman and Nicobar Islands", "अंडमान और निकोबार"], ["36", "Telangana", "तेलंगाना"],
  ["37", "Andhra Pradesh", "आंध्र प्रदेश"], ["38", "Ladakh", "लद्दाख"],
];

type Step = "shop" | "how" | "import" | "pick" | "done";

function Steps({ step }: { step: Step }) {
  const order: [Step[], string][] = [[["shop"], "Shop details"], [["how", "import", "pick"], "Add items"], [["done"], "Ready"]];
  const idx = order.findIndex(([s]) => s.includes(step));
  return (
    <ol className="mb-7 flex flex-wrap gap-3" aria-label={t("Steps")}>
      {order.map(([, label], i) => (
        <li key={label} className={`flex items-center gap-2 rounded-full px-3.5 py-1.5 text-[13px] font-semibold ${i === idx ? "neu-sm text-brand" : i < idx ? "text-ok" : "text-ink-3"}`}>
          <span className={`grid size-6 place-items-center rounded-full text-[12px] ${i <= idx ? "bg-brand text-white" : "neu-in-sm"}`}>{i < idx ? "✓" : i + 1}</span>
          {t(label)}
        </li>
      ))}
    </ol>
  );
}

function Choice({ icon: Icon, title, text, onClick, active }: { icon: typeof LuStore; title: string; text: string; onClick: () => void; active?: boolean }) {
  return (
    <button onClick={onClick} aria-pressed={active} className={`neu-btn flex w-full items-start gap-4 rounded-[22px] p-5 text-left ${active ? "ring-2 ring-brand/50" : ""}`}>
      <span className="neu-in grid size-12 shrink-0 place-items-center rounded-2xl text-brand">
        <Icon className="size-[22px]" />
      </span>
      <span>
        <span className="block text-[15.5px] font-bold">{title}</span>
        <span className="mt-0.5 block text-[13.5px] text-ink-2">{text}</span>
      </span>
    </button>
  );
}

function ShopForm({ onCreated }: { onCreated: () => void }) {
  const [f, setF] = useState({ name: "", city: "", state_code: "27", size: "small", gst_scheme: "unregistered", gstin: "", phone: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const hi = getLang() === "hi";
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.createShop({ ...f, gstin: f.gstin.trim() || undefined, phone: f.phone.trim() || undefined });
      onCreated();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const label = "block text-[13.5px] font-semibold text-ink-2";
  return (
    <Card title={t("Your shop")} icon={LuStore}>
      <div className="grid gap-5 sm:grid-cols-2">
        <label className={label}>
          {t("Shop name")}
          <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder={t("e.g. Patil Kirana Stores")} className={`${inputClass} mt-1.5 w-full text-[15px]`} autoFocus />
        </label>
        <label className={label}>
          {t("City or town")}
          <input value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} placeholder={t("e.g. Pune")} className={`${inputClass} mt-1.5 w-full text-[15px]`} />
        </label>
        <label className={label}>
          {t("State")}
          <select value={f.state_code} onChange={(e) => setF({ ...f, state_code: e.target.value })} className={`${inputClass} mt-1.5 w-full text-[15px]`}>
            {STATES.map(([c, en, hiName]) => (
              <option key={c} value={c}>{hi ? hiName : en}</option>
            ))}
          </select>
        </label>
        <label className={label}>
          {t("Phone (printed on bills)")}
          <input inputMode="tel" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value.replace(/[^\d+ ]/g, "") })} className={`${inputClass} mt-1.5 w-full text-[15px]`} />
        </label>
      </div>
      <p className={`${label} mt-6`}>{t("Shop size")}</p>
      <div className="mt-2 grid gap-3 sm:grid-cols-3">
        {[["small", "Small", "Up to about 300 items"], ["medium", "Medium", "300 to 1,000 items"], ["large", "Large", "More than 1,000 items"]].map(([id, title, text]) => (
          <button key={id} onClick={() => setF({ ...f, size: id })} aria-pressed={f.size === id} className={`neu-btn min-h-16 rounded-2xl px-4 py-3 text-left ${f.size === id ? "text-brand ring-2 ring-brand/50" : ""}`}>
            <span className="block font-bold">{t(title)}</span>
            <span className="block text-[12.5px] text-ink-3">{t(text)}</span>
          </button>
        ))}
      </div>
      <p className={`${label} mt-6`}>{t("GST registration")}</p>
      <div className="mt-2 grid gap-3 sm:grid-cols-3">
        {[["unregistered", "Not registered", "Simple bills, no GST"], ["regular", "Registered", "Tax invoices with GST"], ["composition", "Composition scheme", "Bill of supply, no GST charged"]].map(([id, title, text]) => (
          <button key={id} onClick={() => setF({ ...f, gst_scheme: id })} aria-pressed={f.gst_scheme === id} className={`neu-btn min-h-16 rounded-2xl px-4 py-3 text-left ${f.gst_scheme === id ? "text-brand ring-2 ring-brand/50" : ""}`}>
            <span className="block font-bold">{t(title)}</span>
            <span className="block text-[12.5px] text-ink-3">{t(text)}</span>
          </button>
        ))}
      </div>
      {f.gst_scheme !== "unregistered" && (
        <label className={`${label} mt-5 block max-w-sm`}>
          GSTIN
          <input value={f.gstin} maxLength={15} onChange={(e) => setF({ ...f, gstin: e.target.value.toUpperCase().replace(/\s/g, "") })} className={`${inputClass} mt-1.5 w-full font-mono text-[15px]`} />
        </label>
      )}
      {error && <div className="mt-5"><ErrorNote error={error} /></div>}
      <div className="mt-6">
        <Button onClick={save} disabled={busy || f.name.trim().length < 2}>{busy ? t("Saving…") : t("Save and continue")}</Button>
      </div>
    </Card>
  );
}

function Importer({ kind, onDone }: { kind: "products" | "sales"; onDone: (r: ImportResult) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [pv, setPv] = useState<ImportPreview | null>(null);
  const [map, setMap] = useState<Record<string, string | null>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const pick = async (f: File | undefined) => {
    if (!f) return;
    setFile(f);
    setError(null);
    setPv(null);
    try {
      const p = await api.importPreview(kind, f);
      setPv(p);
      setMap(Object.fromEntries(p.columns.map((c) => [c.name, c.field])));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const chosen = new Set(Object.values(map).filter(Boolean));
  const need = kind === "products" ? ["name"] : ["date", "name", "qty"];
  const missing = need.filter((n) => !chosen.has(n));
  const run = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      onDone(await api.importFile(kind, file, map));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-4">
      <label className="neu-in flex cursor-pointer flex-col items-center gap-2 rounded-[22px] px-6 py-8 text-center">
        <LuUpload className="size-7 text-brand" />
        <span className="font-semibold">{file ? file.name : t("Choose an Excel or CSV file")}</span>
        <span className="text-[12.5px] text-ink-3">{kind === "products" ? t("Item list: name, MRP or price, GST %, stock. Other columns are optional.") : t("Sales report: date, item name, quantity sold.")}</span>
        <input type="file" accept=".csv,.xlsx,.xls" className="sr-only" onChange={(e) => pick(e.target.files?.[0])} aria-label={t("Choose an Excel or CSV file")} />
      </label>
      {error && <ErrorNote error={error} />}
      {pv && (
        <div className="space-y-4">
          <p className="text-[13.5px] text-ink-2">{t("{n} rows found. Check what each column means; fix any that are wrong.", { n: pv.rows })}</p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[34rem] text-left text-[13px]">
              <thead>
                <tr>
                  {pv.columns.map((c) => (
                    <th key={c.name} className="px-2 pb-2 align-bottom">
                      <span className="block truncate text-[12px] text-ink-3">{c.name}</span>
                      <select
                        value={map[c.name] ?? ""}
                        onChange={(e) => setMap({ ...map, [c.name]: e.target.value || null })}
                        className={`${inputClass} mt-1 min-h-9 w-full px-2 text-[12.5px] ${map[c.name] ? "text-brand" : "text-ink-3"}`}
                        aria-label={t("Meaning of column {c}", { c: c.name })}
                      >
                        <option value="">{t("Ignore")}</option>
                        {Object.entries(pv.fields).map(([k, v]) => (
                          <option key={k} value={k}>{t(v)}</option>
                        ))}
                      </select>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {pv.sample.map((r, i) => (
                  <tr key={i} className="border-t border-line">
                    {pv.columns.map((c) => (
                      <td key={c.name} className={`max-w-40 truncate px-2 py-1.5 ${map[c.name] ? "" : "text-ink-3"}`}>{r[c.name]}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {missing.length > 0 && <p className="text-[13.5px] font-semibold text-out">{t("Still needed: {f}", { f: missing.map((m) => t(pv.fields[m] ?? m)).join(", ") })}</p>}
          <Button onClick={run} disabled={busy || missing.length > 0}>{busy ? t("Importing…") : t("Import {n} rows", { n: pv.rows })}</Button>
        </div>
      )}
    </div>
  );
}

function ImportStep({ onDone }: { onDone: () => void }) {
  const [items, setItems] = useState<ImportResult | null>(null);
  const [sales, setSales] = useState<ImportResult | null>(null);
  const Result = ({ r, what }: { r: ImportResult; what: string }) => (
    <div className="neu-in-sm space-y-1 rounded-2xl p-4 text-[13.5px]">
      <p className="font-semibold text-ok">{t("{what}: {a} added, {u} updated", { what, a: r.added, u: r.updated })}</p>
      {r.filled.length > 0 && <p className="text-ink-2">{t("{n} details filled in from the common-items list (please check MRP and GST).", { n: r.filled.length })}</p>}
      {r.warnings.map((w) => <p key={w} className="text-ink-2">{w}</p>)}
      {r.skipped.length > 0 && (
        <details className="text-ink-2">
          <summary className="cursor-pointer font-semibold text-exp">{t("{n} rows skipped", { n: r.skipped.length })}</summary>
          <ul className="mt-1 list-disc pl-5">{r.skipped.slice(0, 20).map((s) => <li key={s}>{s}</li>)}</ul>
        </details>
      )}
    </div>
  );
  return (
    <div className="space-y-6">
      <Card title={t("1. Item list")} icon={LuFileSpreadsheet}>
        <p className="mb-4 text-[13.5px] text-ink-2">
          {t("Export it from your billing app: Vyapar → Reports → Item details; myBillBook → Items → Export; Tally → Stock Summary → Export (Excel). A hand-made Excel sheet also works.")}
        </p>
        {items ? <Result r={items} what={t("Items")} /> : <Importer kind="products" onDone={setItems} />}
      </Card>
      {items && (
        <Card title={t("2. Past sales (optional, makes forecasts better from day one)")} icon={LuFileSpreadsheet}>
          <p className="mb-4 text-[13.5px] text-ink-2">{t("A date-wise item sales report, as many months as you have. It teaches the forecast; it does not change today's stock.")}</p>
          {sales ? <Result r={sales} what={t("Sales days")} /> : <Importer kind="sales" onDone={setSales} />}
        </Card>
      )}
      {items && <Button onClick={onDone}>{t("Finish")}</Button>}
    </div>
  );
}

type Pick = { on: boolean; count: string };

function PickStep({ onDone }: { onDone: () => void }) {
  const [catalog, setCatalog] = useState<CatalogItem[] | null>(null);
  const [picks, setPicks] = useState<Record<string, Pick>>({});
  const [q, setQ] = useState("");
  const [own, setOwn] = useState<{ name: string; mrp: string; gst: string; loose: boolean; count: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.catalog().then(setCatalog).catch((e: Error) => setError(e.message));
  }, []);
  const groups = useMemo(() => {
    const m = new Map<string, CatalogItem[]>();
    for (const c of catalog ?? []) {
      if (q && !c.name.toLowerCase().includes(q.toLowerCase())) continue;
      m.set(c.category, [...(m.get(c.category) ?? []), c]);
    }
    return [...m.entries()];
  }, [catalog, q]);
  const chosen = Object.entries(picks).filter(([, p]) => p.on);
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const items = [
        ...chosen.map(([sku, p]) => ({ sku, stock: Number(p.count) || 0 })),
        ...own.filter((o) => o.name.trim() && Number(o.mrp) > 0).map((o) => ({ name: o.name.trim(), mrp: Number(o.mrp), gst_rate: Number(o.gst), loose: o.loose, stock: Number(o.count) || 0 })),
      ];
      if (!items.length) throw new Error(t("Choose at least one item."));
      await api.addItems(items);
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-6">
      <Card title={t("Pick the items you sell, and count what is on the shelf")} icon={LuClipboardList} aside={<Chip t="info">{t("{n} chosen", { n: chosen.length + own.filter((o) => o.name.trim()).length })}</Chip>}>
        <label className="relative mb-4 block">
          <LuSearch className="pointer-events-none absolute left-4 top-1/2 size-[18px] -translate-y-1/2 text-ink-3" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("Search common items")} className={`${inputClass} w-full pl-11`} aria-label={t("Search common items")} />
        </label>
        {!catalog && !error && <p className="text-ink-2">{t("Loading…")}</p>}
        <div className="space-y-5">
          {groups.map(([cat, list]) => (
            <div key={cat}>
              <p className="mb-2 text-[12.5px] font-bold uppercase tracking-wide text-ink-3">{categoryName(cat)}</p>
              <ul className="grid gap-2 sm:grid-cols-2">
                {list.map((c) => {
                  const p = picks[c.sku] ?? { on: false, count: "" };
                  return (
                    <li key={c.sku} className={`flex min-h-14 items-center gap-3 rounded-2xl px-3 py-2 ${p.on ? "neu-in-sm" : ""}`}>
                      <input type="checkbox" checked={p.on} onChange={(e) => setPicks({ ...picks, [c.sku]: { ...p, on: e.target.checked } })} className="size-5 accent-[var(--brand)]" aria-label={c.name} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13.5px] font-semibold">{c.name}</span>
                        <span className="block text-[12px] text-ink-3">MRP ₹{c.mrp} · GST {c.gst_rate}%</span>
                      </span>
                      {p.on && (
                        <input inputMode="numeric" value={p.count} placeholder={t("on shelf")} onChange={(e) => setPicks({ ...picks, [c.sku]: { ...p, count: e.target.value.replace(/\D/g, "") } })} className={`${inputClass} min-h-10 w-24 text-right`} aria-label={t("How many {name} are on the shelf", { name: c.name })} />
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      </Card>
      <Card title={t("Items not in the list")} icon={LuPackagePlus}>
        <div className="space-y-3">
          {own.map((o, i) => (
            <div key={i} className="grid gap-2 sm:grid-cols-[1fr_6rem_6rem_auto_6rem]">
              <input value={o.name} placeholder={t("Item name")} onChange={(e) => setOwn(own.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} className={inputClass} aria-label={t("Item name")} />
              <input inputMode="decimal" value={o.mrp} placeholder={o.loose ? t("₹ per kg") : "MRP ₹"} onChange={(e) => setOwn(own.map((x, j) => (j === i ? { ...x, mrp: e.target.value.replace(/[^\d.]/g, "") } : x)))} className={inputClass} aria-label="MRP" />
              <select value={o.gst} onChange={(e) => setOwn(own.map((x, j) => (j === i ? { ...x, gst: e.target.value } : x)))} className={inputClass} aria-label="GST %">
                {["0", "5", "18", "40"].map((g) => <option key={g} value={g}>GST {g}%</option>)}
              </select>
              <label className="flex items-center gap-2 text-[13px] text-ink-2">
                <input type="checkbox" checked={o.loose} onChange={(e) => setOwn(own.map((x, j) => (j === i ? { ...x, loose: e.target.checked } : x)))} className="size-4 accent-[var(--brand)]" />
                {t("Sold by weight")}
              </label>
              <input inputMode="decimal" value={o.count} placeholder={t("on shelf")} onChange={(e) => setOwn(own.map((x, j) => (j === i ? { ...x, count: e.target.value.replace(/[^\d.]/g, "") } : x)))} className={inputClass} aria-label={t("On shelf")} />
            </div>
          ))}
          <Button kind="soft" size="sm" onClick={() => setOwn([...own, { name: "", mrp: "", gst: "5", loose: false, count: "" }])}>{t("+ Add an item")}</Button>
        </div>
      </Card>
      {error && <ErrorNote error={error} />}
      <Button onClick={save} disabled={busy}>{busy ? t("Saving…") : t("Save items")}</Button>
    </div>
  );
}

export default function Setup({ onDone, onOpenShop, onShopCreated }: { onDone: () => void; onOpenShop: (id: number) => void; onShopCreated?: () => void }) {
  const [step, setStep] = useState<Step>("shop");
  const [demoId, setDemoId] = useState<number | null>(null);
  useEffect(() => {
    api.shops().then((s) => setDemoId(s.find((x) => x.is_demo)?.id ?? null)).catch(() => undefined);
  }, []);
  return (
    <div className="space-y-2">
      <PageHeader icon={LuStore} title={t("Set up your shop")} sub={t("About 10 minutes. You can change everything later.")} />
      <Steps step={step} />
      {step === "shop" && <ShopForm onCreated={() => { onShopCreated?.(); setStep("how"); }} />}
      {step === "how" && (
        <div className="grid gap-5 lg:grid-cols-3">
          <Choice icon={LuFileSpreadsheet} title={t("Import from my billing app")} text={t("Vyapar, myBillBook, Tally, Marg, Busy, GoFrugal or any Excel sheet. Best if you have past sales.")} onClick={() => setStep("import")} />
          <Choice icon={LuClipboardList} title={t("Pick items and count the shelf")} text={t("Tick the common items you sell and type how many are on the shelf. Good for a fresh start.")} onClick={() => setStep("pick")} />
          <Choice icon={LuCircleCheck} title={t("Skip for now")} text={t("Start with an empty shop and add items while billing.")} onClick={() => setStep("done")} />
        </div>
      )}
      {step === "import" && <ImportStep onDone={() => setStep("done")} />}
      {step === "pick" && <PickStep onDone={() => setStep("done")} />}
      {step === "done" && (
        <Card>
          <div className="flex flex-col items-start gap-4">
            <span className="neu-in grid size-14 place-items-center rounded-2xl text-ok"><LuCircleCheck className="size-7" /></span>
            <p className="text-xl font-bold">{t("Your shop is ready")}</p>
            <p className="max-w-xl text-ink-2">{t("Stockora is learning your shop in the background. Until it has 120 days of sales it blends your sales with typical demand for similar items, so the first forecasts are rough and get better every week.")}</p>
            <div className="flex flex-wrap gap-3">
              <Button onClick={onDone}>{t("Go to overview")}</Button>
              {demoId !== null && <Button kind="soft" onClick={() => onOpenShop(demoId)}>{t("Back to the demo shop")}</Button>}
            </div>
          </div>
        </Card>
      )}
      {step === "shop" && demoId !== null && (
        <p className="pt-4 text-[13.5px] text-ink-3">
          {t("Just looking?")}{" "}
          <button className="font-semibold text-brand hover:underline" onClick={() => onOpenShop(demoId)}>{t("Stay in the demo shop")}</button>
        </p>
      )}
    </div>
  );
}
