import { useMemo, useState } from "react";
import { LuArrowDown, LuArrowUp, LuDownload, LuPackage, LuSearch, LuTrendingDown, LuTrendingUp } from "react-icons/lu";
import type { StockRow } from "../api";
import { useApp } from "../components/appContext";
import {
  Button,
  Empty,
  Loading,
  PageHeader,
  RunwayBar,
  Segments,
  StatusChip,
  canDownload,
  downloadCsv,
  num,
  shortDate,
  stockStatus,
  units,
  useAsync,
  type Status,
} from "../components/ui";
import { api } from "../api";
import { categoryName, t } from "../i18n";

type Filter = "all" | "out" | "soon" | "expiring" | "below" | "idle";
type SortKey = "name" | "stock" | "daily_rate" | "forecast_7d" | "days_of_stock" | "trend_pct";

function Trend({ pct }: { pct: number | null }) {
  if (pct === null) return <span className="text-ink-3">–</span>;
  const up = pct >= 0;
  return (
    <span className={`inline-flex items-center gap-1 font-semibold tabular-nums ${Math.abs(pct) < 5 ? "text-ink-2" : up ? "text-ok" : "text-exp"}`}>
      {up ? <LuTrendingUp className="size-3.5" /> : <LuTrendingDown className="size-3.5" />}
      {pct > 0 ? "+" : ""}
      {pct}%
    </span>
  );
}

export default function Stock() {
  const { stock: rows, openProduct, refreshStock, stockLoaded, go } = useApp();
  const expiry = useAsync(api.expiry);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "days_of_stock", dir: 1 });

  const atRisk = useMemo(() => new Set((expiry.data ?? []).filter((e) => e.leftover >= 1).map((e) => e.product_id)), [expiry.data]);
  const withStatus = useMemo(
    () =>
      rows.map((r) => {
        const base = stockStatus(r.stock, r.days_of_stock);
        return { r, s: (base !== "out" && atRisk.has(r.product_id) ? "expiring" : base) as Status };
      }),
    [rows, atRisk],
  );
  const count = (f: Filter) =>
    f === "all" ? withStatus.length : f === "below" ? withStatus.filter(({ r }) => r.reorder_point !== null && r.stock <= r.reorder_point).length : withStatus.filter((x) => x.s === f).length;

  const shown = useMemo(() => {
    const t = q.trim().toLowerCase();
    const val = (r: StockRow): number | string => {
      if (sort.key === "name") return r.name.toLowerCase();
      if (sort.key === "days_of_stock") return r.stock <= 0 ? -1 : r.days_of_stock ?? 9999;
      return (r[sort.key] as number | null) ?? -9999;
    };
    return withStatus
      .filter(({ r, s }) => {
        if (t && !r.name.toLowerCase().includes(t) && !r.category.includes(t)) return false;
        if (filter === "all") return true;
        if (filter === "below") return r.reorder_point !== null && r.stock <= r.reorder_point;
        return s === filter;
      })
      .sort((a, b) => {
        const x = val(a.r), y = val(b.r);
        return (x < y ? -1 : x > y ? 1 : 0) * sort.dir;
      });
  }, [withStatus, q, filter, sort]);

  const Th = ({ k, label, right }: { k: SortKey; label: string; right?: boolean }) => (
    <th className={`px-3 py-3 font-semibold ${right ? "text-right" : ""}`}>
      <button
        onClick={() => setSort((s) => ({ key: k, dir: s.key === k ? ((-s.dir) as 1 | -1) : 1 }))}
        className={`inline-flex items-center gap-1 hover:text-ink ${sort.key === k ? "text-ink" : ""}`}
      >
        {label}
        {sort.key === k && (sort.dir === 1 ? <LuArrowUp className="size-3" /> : <LuArrowDown className="size-3" />)}
      </button>
    </th>
  );

  const exportCsv = () =>
    downloadCsv("stockora-inventory.csv", [
      [t("Product"), t("Category"), t("On hand"), t("Sells per day"), t("Forecast 7 days"), t("Forecast low"), t("Forecast high"), t("Days left"), t("Runs out on"), t("Reorder point"), t("Sold last 7 days"), t("Status")],
      ...shown.map(({ r, s }) => [r.name, r.category, r.stock, r.daily_rate, r.forecast_7d, r.forecast_7d_low, r.forecast_7d_high, r.days_of_stock, r.runs_out_on, r.reorder_point, r.sold_7d, s]),
    ]);

  return (
    <div>
      <PageHeader icon={LuPackage} title={t("Inventory")} sub={t("Every product with its sales rate, forecast range and the date it runs out. Click a product for its full history, forecast and batches.")}>
        {canDownload && (
          <Button kind="soft" icon={LuDownload} onClick={exportCsv}>
            {t("Export CSV")}
          </Button>
        )}
        <Button kind="soft" onClick={refreshStock}>
          {t("Refresh")}
        </Button>
      </PageHeader>

      <div className="mb-6 flex flex-wrap items-center gap-4">
        <label className="neu-in-sm flex min-h-11 min-w-[15rem] flex-1 items-center gap-3 rounded-2xl px-4">
          <LuSearch className="size-[18px] text-ink-3" />
          <span className="sr-only">{t("Search products")}</span>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("Search Maggi, atta, oil, dairy…")} className="w-full bg-transparent placeholder:text-ink-3 focus:outline-none" />
        </label>
        <Segments
          label={t("Filter")}
          value={filter}
          onChange={setFilter}
          options={[
            { id: "all", label: t("All"), count: count("all") },
            { id: "out", label: t("Runs out"), count: count("out") },
            { id: "soon", label: t("This week"), count: count("soon") },
            { id: "below", label: t("Below reorder"), count: count("below") },
            { id: "expiring", label: t("May expire"), count: count("expiring") },
            { id: "idle", label: t("Not selling"), count: count("idle") },
          ]}
        />
      </div>

      {rows.length === 0 && !stockLoaded && <Loading />}
      {rows.length === 0 && stockLoaded && (
        <section className="neu rounded-[22px]">
          <Empty icon={LuPackage}>
            {t("No items yet. Add them with the shop setup (import from your billing app or pick common items).")}{" "}
            <button className="font-semibold text-brand hover:underline" onClick={() => go("setup")}>{t("Add items")}</button>
          </Empty>
        </section>
      )}

      {rows.length > 0 && (
        <section className="neu overflow-hidden rounded-[22px]">
          {/* desktop table */}
          <div className="hidden overflow-x-auto md:block">
            <table className="w-full text-left text-[13.5px]">
              <thead className="text-[12px] text-ink-3">
                <tr className="border-b border-line">
                  <Th k="name" label={t("Product")} />
                  <Th k="stock" label={t("On hand")} right />
                  <Th k="daily_rate" label={t("Per day")} right />
                  <Th k="forecast_7d" label={t("Forecast 7d (range)")} right />
                  <Th k="days_of_stock" label={t("Runs out")} />
                  <th className="px-3 py-3 text-right font-semibold">{t("Reorder at")}</th>
                  <Th k="trend_pct" label={t("Trend")} right />
                  <th className="px-5 py-3 font-semibold">{t("Status")}</th>
                </tr>
              </thead>
              <tbody>
                {shown.map(({ r, s }) => (
                  <tr key={r.product_id} onClick={() => openProduct(r.product_id)} className="cursor-pointer border-b border-line/70 transition-colors last:border-0 hover:bg-line/30">
                    <td className="max-w-[18rem] px-5 py-3">
                      <button className="block w-full text-left" onClick={(e) => { e.stopPropagation(); openProduct(r.product_id); }}>
                        <span className="block truncate font-semibold">{r.name}</span>
                        <span className="block text-[12px] text-ink-3">{categoryName(r.category)}</span>
                      </button>
                    </td>
                    <td className={`px-3 py-3 text-right font-semibold tabular-nums ${r.reorder_point !== null && r.stock <= r.reorder_point ? "text-out" : ""}`}>{units(r.stock)}</td>
                    <td className="px-3 py-3 text-right tabular-nums text-ink-2">{num(r.daily_rate)}</td>
                    <td className="whitespace-nowrap px-3 py-3 text-right tabular-nums">
                      <span className="font-semibold">{num(r.forecast_7d)}</span>{" "}
                      <span className="text-[12px] text-ink-3">
                        ({units(r.forecast_7d_low)}–{units(r.forecast_7d_high)})
                      </span>
                    </td>
                    <td className="px-3 py-3">
                      <div className="flex items-center gap-3">
                        <RunwayBar days={r.days_of_stock} stock={r.stock} />
                        <span className="whitespace-nowrap">
                          <span className="block text-[13px] font-semibold">{r.stock <= 0 ? t("Out now") : r.days_of_stock === null ? "–" : shortDate(r.runs_out_on)}</span>
                          <span className="block text-[11.5px] tabular-nums text-ink-3">
                            {r.stock <= 0 ? t("empty") : r.days_of_stock === null ? t("no sales") : r.days_of_stock > 60 ? t("60+ days") : t("{n} days", { n: num(r.days_of_stock) })}
                          </span>
                        </span>
                      </div>
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums text-ink-2">{r.reorder_point === null ? "–" : num(r.reorder_point)}</td>
                    <td className="px-3 py-3 text-right"><Trend pct={r.trend_pct} /></td>
                    <td className="px-5 py-3"><StatusChip s={s} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* phone cards */}
          <ul className="divide-y divide-line md:hidden">
            {shown.map(({ r, s }) => (
              <li key={r.product_id}>
                <button onClick={() => openProduct(r.product_id)} className="w-full px-5 py-4 text-left">
                  <span className="flex items-start justify-between gap-3">
                    <span className="min-w-0">
                      <span className="block font-semibold leading-snug">{r.name}</span>
                      <span className="block text-[12px] text-ink-3">{categoryName(r.category)}</span>
                    </span>
                    <StatusChip s={s} />
                  </span>
                  <span className="mt-3 grid grid-cols-3 gap-2 text-[12px] text-ink-3">
                    <span>
                      {t("On hand")}<span className="block text-[15px] font-bold tabular-nums text-ink">{units(r.stock)}</span>
                    </span>
                    <span>
                      {t("Per day")}<span className="block text-[15px] font-bold tabular-nums text-ink">{num(r.daily_rate)}</span>
                    </span>
                    <span>
                      {t("Runs out")}<span className="block text-[15px] font-bold text-ink">{r.stock <= 0 ? t("Now") : r.days_of_stock === null ? "–" : shortDate(r.runs_out_on)}</span>
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {shown.length === 0 && <Empty icon={LuSearch}>{q ? t("No product matches \"{q}\".", { q }) : t("No products in this group.")}</Empty>}
          <p className="border-t border-line px-5 py-3 text-[12px] text-ink-3">
            {t("{n} of {m} products. Forecast range is the 80% interval for the next 7 days. Reorder at = expected sales until a delivery arrives plus a safety buffer.", { n: shown.length, m: rows.length })}

          </p>
        </section>
      )}
    </div>
  );
}
