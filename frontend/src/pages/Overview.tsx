import { useMemo, useState, type FormEvent } from "react";
import {
  LuArrowRight,
  LuCalendarClock,
  LuChartSpline,
  LuCircleCheck,
  LuClipboardList,
  LuHourglass,
  LuLayoutDashboard,
  LuMessageCircle,
  LuPackageX,
  LuShoppingCart,
  LuTriangleAlert,
  LuWallet,
} from "react-icons/lu";
import { api } from "../api";
import { useApp, type Page } from "../components/appContext";
import { DemandChart } from "../components/charts";
import {
  Button,
  Card,
  Chip,
  Empty,
  ErrorNote,
  Loading,
  PageHeader,
  Ring,
  RunwayBar,
  Stat,
  inputClass,
  num,
  shortDate,
  stockStatus,
  units,
  useAsync,
} from "../components/ui";
import { categoryName, festivalText, locale, t } from "../i18n";

function AskedFor() {
  const [item, setItem] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!item.trim()) return;
    try {
      const r = await api.logRequest(item.trim());
      setMsg(r.matched_product ? t("Noted: {item}. You stock it, but it had run out.", { item: r.matched_product }) : t("Noted: {item}. You'll see it in Signals once customers ask 3 times.", { item: r.logged }));
      setItem("");
    } catch {
      setMsg(t("Could not save. Is the server running?"));
    }
  };
  return (
    <form onSubmit={submit}>
      <label htmlFor="asked" className="block text-[13.5px] text-ink-2">
        {t("A customer asked for something that wasn't on the shelf? Note it here. Repeated requests show up as items worth stocking.")}
      </label>
      <div className="mt-3 flex gap-3">
        <input id="asked" value={item} onChange={(e) => setItem(e.target.value)} placeholder={t("e.g. Amul Kool Kesar 200ml")} maxLength={120} className={`${inputClass} min-w-0 flex-1`} />
        <Button type="submit" kind="soft" disabled={!item.trim()}>
          {t("Note it")}
        </Button>
      </div>
      {msg && <p className="mt-2 text-[13px] font-medium text-brand" role="status">{msg}</p>}
    </form>
  );
}

export default function Overview() {
  const { go, openProduct, stock: rows } = useApp();
  const brief = useAsync(api.briefing);
  const trend = useAsync(api.trend);
  const expiry = useAsync(api.expiry);
  const status = useAsync(api.status);

  const groups = useMemo(() => {
    const g = { out: 0, soon: 0, ok: 0, idle: 0 };
    for (const r of rows) g[stockStatus(r.stock, r.days_of_stock) as keyof typeof g]++;
    return g;
  }, [rows]);
  const categories = useMemo(() => {
    const m = new Map<string, { items: number; atRisk: number; days: number[] }>();
    for (const r of rows) {
      const c = m.get(r.category) ?? { items: 0, atRisk: 0, days: [] };
      c.items++;
      if (stockStatus(r.stock, r.days_of_stock) === "out") c.atRisk++;
      if (r.days_of_stock !== null) c.days.push(Math.min(r.days_of_stock, 60));
      m.set(r.category, c);
    }
    return [...m.entries()]
      .map(([name, c]) => ({ name, ...c, median: c.days.length ? [...c.days].sort((a, b) => a - b)[Math.floor(c.days.length / 2)] : null }))
      .sort((a, b) => b.atRisk - a.atRisk || (a.median ?? 99) - (b.median ?? 99));
  }, [rows]);

  if (brief.loading && !brief.data) return <Loading what={t("Reading your stock")} />;
  if (brief.error || !brief.data) return <ErrorNote error={brief.error ?? t("no data")} onRetry={brief.reload} />;
  const b = brief.data;
  const exp = expiry.data ?? [];
  const total = rows.length || 1;
  const healthy = groups.ok / total;
  const expUnits = exp.filter((e) => e.days_left <= 14).reduce((a, e) => a + e.leftover, 0);
  const runningOut = [...rows]
    .filter((r) => ["out", "soon"].includes(stockStatus(r.stock, r.days_of_stock)))
    .sort((a, c) => (a.stock <= 0 ? -1 : a.days_of_stock ?? 99) - (c.stock <= 0 ? -1 : c.days_of_stock ?? 99))
    .slice(0, 8);
  const firstExpiry = new Map<number, number>();
  for (const e of exp) if (!firstExpiry.has(e.product_id) || e.days_left < firstExpiry.get(e.product_id)!) firstExpiry.set(e.product_id, e.days_left);
  const f7 = trend.data?.forecast.slice(0, 7) ?? [];
  const next7 = f7.reduce((a, f) => a + f.units, 0);
  const past7 = trend.data?.actual.slice(-7).reduce((a, f) => a + f.units, 0) ?? 0;
  const change = past7 > 0 ? Math.round((next7 / past7 - 1) * 100) : null;
  const day = new Date(b.date + "T00:00:00").toLocaleDateString(locale(), { weekday: "long", day: "numeric", month: "long", year: "numeric" });

  const steps: { label: string; detail: string; page: Page; icon: typeof LuShoppingCart; tone: string }[] = [];
  if (b.reorder_items) steps.push({ label: t("Approve restock for {n} items", { n: b.reorder_items }), detail: t("Sized to forecast demand until the next delivery"), page: "restock", icon: LuShoppingCart, tone: "text-brand" });
  if (exp.length) steps.push({ label: t("Clear {n} batches before they expire", { n: exp.length }), detail: exp.slice(0, 2).map((e) => e.name).join(", "), page: "expiry", icon: LuHourglass, tone: "text-exp" });
  if (groups.out) steps.push({ label: t("{n} items run out within 3 days", { n: groups.out }), detail: t("Restock or move them up the list"), page: "stock", icon: LuTriangleAlert, tone: "text-out" });
  if (b.collect_from.length) steps.push({ label: t("Collect udhaar from {name}", { name: b.collect_from[0].customer }), detail: t("Oldest dues first"), page: "udhaar", icon: LuWallet, tone: "text-exp" });

  return (
    <div className="space-y-7">
      <PageHeader title={t("Overview")} icon={LuLayoutDashboard} sub={day}>
        {b.festivals && <Chip t="exp" icon={LuCalendarClock}>{festivalText(b.festivals)}</Chip>}
      </PageHeader>

      {status.data?.mode === "learning" && (
        <div className="neu flex items-start gap-4 rounded-[22px] p-5">
          <span className="neu-in grid size-11 shrink-0 place-items-center rounded-2xl text-info">
            <LuChartSpline className="size-5" />
          </span>
          <p className="text-[13.5px] text-ink-2">
            <span className="font-semibold text-ink">{t("Learning this shop:")}</span>{" "}
            {t("{n} of {m} days of sales so far. Forecasts blend your recent sales with typical demand for similar items until the full model takes over.", { n: status.data.history_days, m: status.data.model_min_days })}
          </p>
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-[22rem_1fr]">
        <section className="neu flex flex-col items-center gap-5 rounded-[22px] p-6 sm:flex-row xl:flex-col">
          <Ring value={healthy} size={168} stroke={14} color={healthy >= 0.6 ? "var(--ok)" : healthy >= 0.35 ? "var(--exp)" : "var(--out)"}>
            <p className="text-[2.1rem] font-extrabold leading-none tabular-nums">{Math.round(healthy * 100)}%</p>
            <p className="mt-1 text-[12px] font-semibold text-ink-3">{t("stock healthy")}</p>
          </Ring>
          <ul className="w-full space-y-2.5 text-[13.5px]">
            {[
              [t("Runs out within 3 days"), groups.out, "bg-out"],
              [t("Runs out this week"), groups.soon, "bg-exp"],
              [t("A week or more"), groups.ok, "bg-ok"],
              [t("No recent sales"), groups.idle, "bg-ink-3"],
            ].map(([label, n, c]) => (
              <li key={label as string} className="flex items-center gap-3">
                <span className={`size-2.5 rounded-full ${c}`} />
                <span className="flex-1 text-ink-2">{label}</span>
                <span className="font-bold tabular-nums">{n as number}</span>
              </li>
            ))}
          </ul>
        </section>

        <div className="grid min-w-0 gap-6 sm:grid-cols-2">
          <Stat icon={LuTriangleAlert} tone={groups.out ? "out" : "plain"} label={t("Run out within 3 days")} value={units(groups.out)} note={t("of {n} products", { n: rows.length })} onClick={() => go("stock")} />
          <Stat icon={LuHourglass} tone={expUnits ? "exp" : "plain"} label={t("Units may expire, 14 days")} value={units(expUnits)} note={t("{n} batches at risk", { n: exp.length })} onClick={() => go("expiry")} />
          <Stat
            icon={LuChartSpline}
            tone="info"
            label={t("Forecast, next 7 days")}
            value={units(next7)}
            note={change === null ? t("units across the shop") : t("{pct}% vs last 7 days", { pct: `${change >= 0 ? "+" : ""}${change}` })}
          />
          <Stat
            icon={LuPackageX}
            label={t("Out-of-stock days, 30 days")}
            value={b.stockout_days_30d === null ? "–" : units(b.stockout_days_30d)}
            note={b.stockout_days_30d === null ? t("Starts once an item runs out") : t("{n} units sold yesterday", { n: units(b.yesterday_units) })}
            onClick={() => go("signals")}
          />
        </div>
      </div>

      <Card
        title={t("Shop demand")}
        icon={LuChartSpline}
        aside={
          trend.data && (
            <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <span className="flex items-center gap-1.5"><span className="h-0.5 w-4 rounded bg-brand" /> {t("sold")}</span>
              <span className="flex items-center gap-1.5"><span className="h-0.5 w-4 rounded bg-info" /> {t("forecast")}</span>
              <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-sm bg-info/20" /> {t("80% range")}</span>
            </span>
          )
        }
      >
        {trend.error && <ErrorNote error={trend.error} onRetry={trend.reload} />}
        {!trend.data && !trend.error && <Loading what={t("Loading demand")} />}
        {trend.data && (
          <DemandChart
            label={t("Units sold per day for the last 4 weeks and forecast for the next 2 weeks, whole shop")}
            actual={trend.data.actual}
            forecast={trend.data.forecast}
            markers={trend.data.festivals}
            height={260}
          />
        )}
      </Card>

      <div className="grid gap-6 xl:grid-cols-[1.35fr_1fr]">
        <Card title={t("Runs out first")} icon={LuTriangleAlert} aside={<button className="font-semibold text-brand hover:underline" onClick={() => go("stock")}>{t("Full inventory")}</button>} flush>
          {runningOut.length === 0 ? (
            <Empty icon={LuCircleCheck}>{t("Every product has at least a week of stock.")}</Empty>
          ) : (
            <ul className="px-3">
              {runningOut.map((r) => (
                <li key={r.product_id}>
                  <button onClick={() => openProduct(r.product_id)} className="grid w-full grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1.5 rounded-2xl px-3 py-3 text-left hover:bg-line/30 sm:grid-cols-[minmax(0,1fr)_auto_7.5rem]">
                    <span className="min-w-0">
                      <span className="block truncate font-semibold">{r.name}</span>
                      <span className="block text-[12.5px] tabular-nums text-ink-3">
                        {t("{n} on hand · {r}/day", { n: units(r.stock), r: num(r.daily_rate) })}
                      </span>
                    </span>
                    <span className="hidden sm:block">
                      <RunwayBar days={r.days_of_stock} stock={r.stock} expiryAt={firstExpiry.get(r.product_id)} />
                    </span>
                    <span className="text-right">
                      <span className={`block text-[13.5px] font-bold ${stockStatus(r.stock, r.days_of_stock) === "out" ? "text-out" : "text-exp"}`}>
                        {r.stock <= 0 ? t("Out now") : shortDate(r.runs_out_on)}
                      </span>
                      <span className="block text-[12px] tabular-nums text-ink-3">{r.stock <= 0 ? t("restock today") : t("in {n} days", { n: num(r.days_of_stock ?? 0) })}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title={t("Next steps")} icon={LuClipboardList} flush>
          {steps.length === 0 ? (
            <Empty icon={LuCircleCheck}>{t("Nothing needs you right now.")}</Empty>
          ) : (
            <ul className="space-y-1 px-3">
              {steps.map((s) => (
                <li key={s.label}>
                  <button onClick={() => go(s.page)} className="flex w-full items-center gap-4 rounded-2xl px-3 py-3 text-left hover:bg-line/30">
                    <span className={`neu-in-sm grid size-10 shrink-0 place-items-center rounded-xl ${s.tone}`}>
                      <s.icon className="size-[18px]" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block font-semibold">{s.label}</span>
                      <span className="block truncate text-[12.5px] text-ink-3">{s.detail}</span>
                    </span>
                    <LuArrowRight className="size-4 text-ink-3" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card title={t("Expiring soon")} icon={LuHourglass} aside={<button className="font-semibold text-brand hover:underline" onClick={() => go("expiry")}>{t("All batches")}</button>} flush>
          {exp.length === 0 ? (
            <Empty icon={LuCircleCheck}>{t("No batch is at risk of expiring unsold.")}</Empty>
          ) : (
            <ul className="px-3">
              {exp.slice(0, 5).map((e) => {
                const sells = Math.max(0, e.remaining - e.leftover);
                return (
                  <li key={e.batch_id}>
                    <button onClick={() => openProduct(e.product_id)} className="grid w-full grid-cols-[1fr_auto] items-center gap-x-4 gap-y-2 rounded-2xl px-3 py-3 text-left hover:bg-line/30">
                      <span className="min-w-0">
                        <span className="block truncate font-semibold">{e.name}</span>
                        <span className="block text-[12.5px] tabular-nums text-ink-3">
                          {t("{n} sell in time", { n: units(sells) })} · <span className="font-semibold text-exp">{t("{n} won't", { n: units(e.leftover) })}</span> · {t("of {n}", { n: units(e.remaining) })}
                        </span>
                      </span>
                      <Chip t={e.days_left <= 2 ? "out" : "exp"}>{e.days_left <= 0 ? t("Expired") : e.days_left === 1 ? t("1 day") : t("{n} days", { n: e.days_left })}</Chip>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>

        <Card title={t("Stock cover by category")} icon={LuCalendarClock} aside={t("median days of stock")} flush>
          <div className="overflow-x-auto px-6">
            <table className="w-full text-left text-[13.5px]">
              <thead className="text-[12px] text-ink-3">
                <tr>
                  <th className="pb-2 font-semibold">{t("Category")}</th>
                  <th className="pb-2 text-right font-semibold">{t("Items")}</th>
                  <th className="pb-2 text-right font-semibold">{t("Run out ≤3d")}</th>
                  <th className="pb-2 pl-4 font-semibold">{t("Median cover")}</th>
                </tr>
              </thead>
              <tbody>
                {categories.slice(0, 7).map((c) => (
                  <tr key={c.name} className="border-t border-line">
                    <td className="py-2.5 font-semibold">{categoryName(c.name)}</td>
                    <td className="py-2.5 text-right tabular-nums text-ink-2">{c.items}</td>
                    <td className={`py-2.5 text-right font-semibold tabular-nums ${c.atRisk ? "text-out" : "text-ink-3"}`}>{c.atRisk}</td>
                    <td className="py-2.5 pl-4">
                      <span className="flex items-center gap-2.5">
                        <span className="neu-in-sm h-2 w-20 overflow-hidden rounded-full">
                          <span className="block h-full rounded-full bg-brand" style={{ width: `${Math.min(100, ((c.median ?? 0) / 14) * 100)}%` }} />
                        </span>
                        <span className="tabular-nums text-ink-2">{c.median === null ? "–" : t("{n} d", { n: num(c.median) })}</span>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <Card title={t("Customer asked for something?")} icon={LuMessageCircle}>
        <AskedFor />
      </Card>
    </div>
  );
}
