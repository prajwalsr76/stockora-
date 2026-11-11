import { useEffect, useState } from "react";
import { LuBoxes, LuCalendarClock, LuChartLine, LuPackage, LuTarget, LuTrendingDown, LuTrendingUp, LuX } from "react-icons/lu";
import { api, ApiError, type ProductDetail } from "../api";
import { DemandChart, StockProjection } from "./charts";
import { Button, Chip, ErrorNote, Loading, StatusChip, daysText, inputClass, num, shortDate, stockStatus, units } from "./ui";
import { categoryName, festivalText, t } from "../i18n";

function Fact({ label, value, sub, icon: Icon }: { label: string; value: string; sub?: string; icon: typeof LuPackage }) {
  return (
    <div className="neu-in-sm rounded-2xl px-4 py-3">
      <p className="flex items-center gap-1.5 text-[12px] font-medium text-ink-3">
        <Icon className="size-3.5" />
        {label}
      </p>
      <p className="mt-0.5 text-lg font-bold tabular-nums">{value}</p>
      {sub && <p className="text-[12px] text-ink-2">{sub}</p>}
    </div>
  );
}

export default function ProductDrawer({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged?: () => void }) {
  const [d, setD] = useState<ProductDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sold, setSold] = useState("");
  const [counted, setCounted] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () =>
    api
      .product(id)
      .then(setD)
      .catch((e: Error) => setErr(e.message));
  useEffect(() => {
    setD(null);
    setErr(null);
    setMsg(null);
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const run = async (fn: () => Promise<string>) => {
    setBusy(true);
    setMsg(null);
    try {
      setMsg(await fn());
      await load();
      onChanged?.();
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : t("Could not save. Is the server running?"));
    } finally {
      setBusy(false);
    }
  };

  const status = d ? stockStatus(d.stock, d.days_of_stock) : null;
  const risk = d ? d.batches.reduce((a, b) => a + b.at_risk, 0) : 0;

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={d?.name ?? t("Product")}>
      <button className="absolute inset-0 bg-ink/25 backdrop-blur-[2px]" aria-label={t("Close")} onClick={onClose} />
      <aside className="slide-in relative flex h-full w-full max-w-[640px] flex-col overflow-y-auto bg-base shadow-[-12px_0_32px_var(--sh-dark)]">
        <header className="sticky top-0 z-10 flex items-start justify-between gap-4 bg-base/95 px-6 pb-4 pt-6 backdrop-blur">
          <div className="min-w-0">
            <p className="text-[12.5px] font-semibold capitalize text-ink-3">{d ? categoryName(d.category) : " "}</p>
            <h2 className="text-xl font-bold leading-snug">{d?.name ?? t("Loading…")}</h2>
            {d && status && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                <StatusChip s={risk >= 1 && status !== "out" ? "expiring" : status} />
                {d.mode === "learning" && <Chip t="info">{t("Learning mode")}</Chip>}
                {d.festivals_in_window.map((f) => (
                  <Chip key={f} t="exp">{festivalText(f)}</Chip>
                ))}
              </div>
            )}
          </div>
          <button onClick={onClose} className="neu-btn grid size-10 shrink-0 place-items-center rounded-xl text-ink-2" aria-label={t("Close")}>
            <LuX className="size-5" />
          </button>
        </header>

        <div className="space-y-6 px-6 pb-10">
          {err && <ErrorNote error={err} />}
          {!d && !err && <Loading what={t("Loading product")} />}
          {d && (
            <>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Fact icon={LuBoxes} label={t("On hand")} value={units(d.stock)} sub={t("{n} sold in 28 days", { n: units(d.sold_28d) })} />
                <Fact icon={LuChartLine} label={t("Sells per day")} value={num(d.daily_rate, 2)} sub={t("{n} sold last 7 days", { n: units(d.sold_7d) })} />
                <Fact icon={LuCalendarClock} label={t("Runs out")} value={d.stock <= 0 ? t("Now") : shortDate(d.runs_out_on)} sub={d.stock <= 0 ? t("out of stock") : daysText(d.days_of_stock)} />
                <Fact icon={LuTarget} label={t("Reorder point")} value={d.reorder_point === null ? "–" : num(d.reorder_point)} sub={d.reorder_point !== null && d.stock <= d.reorder_point ? t("below: reorder now") : t("units")} />
              </div>

              <section className="neu rounded-[22px] p-5">
                <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
                  <h3 className="font-semibold">{t("Sales and forecast")}</h3>
                  <p className="flex items-center gap-1.5 text-[13px] text-ink-2">
                    Next 7 days: <span className="font-bold text-info tabular-nums">{num(d.forecast_7d)}</span>
                    <span className="tabular-nums text-ink-3">
                      ({num(d.forecast_7d_low)}–{num(d.forecast_7d_high)})
                    </span>
                    {d.trend_pct !== null && (
                      <Chip t={d.trend_pct >= 0 ? "ok" : "exp"} icon={d.trend_pct >= 0 ? LuTrendingUp : LuTrendingDown}>
                        {d.trend_pct > 0 ? "+" : ""}
                        {d.trend_pct}%
                      </Chip>
                    )}
                  </p>
                </div>
                <DemandChart
                  bars
                  label={t("Daily sales for the last 8 weeks and forecast for the next 2 weeks for {name}", { name: d.name })}
                  actual={d.history}
                  forecast={d.forecast}
                  height={220}
                />
                <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-ink-2">
                  <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-sm bg-brand/60" /> {t("sold")}</span>
                  <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-sm bg-out" /> {t("out of stock that day")}</span>
                  <span className="flex items-center gap-1.5"><span className="h-0.5 w-4 bg-info" /> {t("forecast")}</span>
                  <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-sm bg-info/20" /> {t("80% range")}</span>
                </p>
              </section>

              <section className="neu rounded-[22px] p-5">
                <h3 className="mb-1 font-semibold">{t("Stock on the shelf, next 14 days")}</h3>
                <p className="mb-2 text-[13px] text-ink-2">{t("Starts from today's stock and subtracts the daily forecast. Incoming deliveries are not included.")}</p>
                <StockProjection start={d.stock} forecast={d.forecast} reorderPoint={d.reorder_point} />
              </section>

              <section className="neu rounded-[22px] p-5">
                <h3 className="mb-3 font-semibold">{t("Batches on the shelf")}</h3>
                {d.batches.length === 0 ? (
                  <p className="text-ink-2">{t("No stock on the shelf.")}</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[26rem] text-left text-[13.5px]">
                      <thead className="text-[12px] text-ink-3">
                        <tr>
                          <th className="pb-2 font-semibold">{t("Batch")}</th>
                          <th className="pb-2 text-right font-semibold">{t("Qty")}</th>
                          <th className="pb-2 pl-4 font-semibold">{t("Expires")}</th>
                          <th className="pb-2 text-right font-semibold">{t("At risk")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.batches.map((b) => (
                          <tr key={b.batch_id} className="border-t border-line">
                            <td className="py-2">{b.batch_no ?? `#${b.batch_id}`}</td>
                            <td className="py-2 text-right tabular-nums">{num(b.qty, b.qty % 1 ? 1 : 0)}</td>
                            <td className="py-2 pl-4">
                              {b.expiry ? (
                                <>
                                  {shortDate(b.expiry)}{" "}
                                  <span className={b.days_left !== null && b.days_left <= 3 ? "font-semibold text-out" : "text-ink-3"}>
                                    ({t("{n} d", { n: b.days_left ?? 0 })})
                                  </span>
                                </>
                              ) : (
                                <span className="text-ink-3">{t("no date")}</span>
                              )}
                            </td>
                            <td className="py-2 text-right">
                              {b.at_risk >= 1 ? <Chip t="exp">{t("{n} units", { n: units(b.at_risk) })}</Chip> : <span className="text-ink-3">–</span>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>

              <section className="neu rounded-[22px] p-5">
                <h3 className="mb-3 font-semibold">{t("Update stock")}</h3>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div>
                    <label htmlFor="drawer-sold" className="text-[13px] font-medium text-ink-2">{t("Sold just now")}</label>
                    <div className="mt-1.5 flex gap-2">
                      <input id="drawer-sold" inputMode="decimal" value={sold} onChange={(e) => setSold(e.target.value.replace(/[^\d.]/g, ""))} className={`${inputClass} w-full min-w-0 text-right tabular-nums`} placeholder="0" />
                      <Button size="sm" disabled={busy || !Number(sold)} onClick={() => run(async () => {
                        const r = await api.sell(d.product_id, Number(sold));
                        setSold("");
                        return t("Sale recorded. {n} left.", { n: units(r.stock) });
                      })}>{t("Record")}</Button>
                    </div>
                  </div>
                  <div>
                    <label htmlFor="drawer-count" className="text-[13px] font-medium text-ink-2">{t("Counted on the shelf")}</label>
                    <div className="mt-1.5 flex gap-2">
                      <input id="drawer-count" inputMode="decimal" value={counted} onChange={(e) => setCounted(e.target.value.replace(/[^\d.]/g, ""))} className={`${inputClass} w-full min-w-0 text-right tabular-nums`} placeholder={units(d.stock)} />
                      <Button size="sm" kind="soft" disabled={busy || counted === ""} onClick={() => run(async () => {
                        const r = await api.count(d.product_id, Number(counted));
                        setCounted("");
                        const diff = Math.round(r.difference);
                        return diff === 0 ? t("Count matches the records.") : t("Stock corrected by {d}.", { d: `${diff > 0 ? "+" : ""}${diff}` });
                      })}>{t("Save")}</Button>
                    </div>
                  </div>
                </div>
                {msg && <p className="mt-3 text-[13px] font-medium text-brand" role="status">{msg}</p>}
              </section>
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
