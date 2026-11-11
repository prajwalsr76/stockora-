import type { ReactNode } from "react";
import { LuActivity, LuChartColumn, LuMapPin, LuMessageCircle, LuPackageX, LuScanLine, LuShieldCheck, LuTarget } from "react-icons/lu";
import { api } from "../api";
import { useApp } from "../components/appContext";
import { Card, Chip, Empty, ErrorNote, PageHeader, units, useAsync } from "../components/ui";
import { t as tr } from "../i18n";

function Item({ title, detail, aside, onClick }: { title: string; detail: string; aside?: ReactNode; onClick?: () => void }) {
  const Tag = onClick ? "button" : "div";
  return (
    <li>
      <Tag onClick={onClick} className="flex w-full items-start justify-between gap-4 rounded-2xl px-3 py-3 text-left hover:bg-line/25">
        <span className="min-w-0">
          <span className="block font-semibold">{title}</span>
          <span className="block text-[12.5px] text-ink-3">{detail}</span>
        </span>
        {aside}
      </Tag>
    </li>
  );
}

export default function Signals() {
  const { openProduct } = useApp();
  const lost = useAsync(api.lostSales);
  const alerts = useAsync(api.alerts);
  const dead = useAsync(api.deadStock);
  const area = useAsync(api.areaTrends);
  const card = useAsync(api.modelCard);
  const metrics = useAsync(api.metrics);
  const A = alerts.data;

  return (
    <div className="space-y-7">
      <PageHeader icon={LuActivity} title={tr("Signals")} sub={tr("The patterns behind stockouts and expiry: what ran out, what customers asked for, what isn't moving, and how accurate the forecast is.")} />

      <div className="grid gap-6 xl:grid-cols-2">
        <Card title={tr("Ran out recently")} icon={LuPackageX} aside={lost.data && tr("last {n} days", { n: lost.data.period_days })} flush>
          {lost.error && <div className="px-6"><ErrorNote error={lost.error} onRetry={lost.reload} /></div>}
          {lost.data && lost.data.top_items.length === 0 && <Empty>{tr("No item ran out in this period.")}</Empty>}
          {lost.data && lost.data.top_items.length > 0 && (
            <>
              <p className="px-6 pb-2 text-[13.5px] text-ink-2">
                {lost.data.items_affected} items were out for {lost.data.stockout_days} shelf-days; about{" "}
                <span className="font-bold text-out">{units(lost.data.lost_units)} units</span> customers wanted were not on the shelf.
              </p>
              <ul className="px-3">
                {lost.data.top_items.slice(0, 6).map((t) => (
                  <Item key={t.product_id} onClick={() => openProduct(t.product_id)} title={t.name} detail={tr("~{n} units of demand missed", { n: units(t.lost_units) })} aside={<Chip t="out">{t.days_out === 1 ? tr("1 day out") : tr("{n} days out", { n: t.days_out })}</Chip>} />
                ))}
              </ul>
            </>
          )}
        </Card>

        <Card title={tr("Customers asked for")} icon={LuMessageCircle} aside={tr("from the Overview page")} flush>
          {A && A.missed_requests.length === 0 && <Empty icon={LuMessageCircle}>{tr("Note customer requests on the Overview page. Items asked for 3+ times on 2+ days show up here.")}</Empty>}
          <ul className="px-3">
            {A?.missed_requests.map((r) => (
              <Item key={r.item} title={r.item} detail={tr("{a}. Roughly {n} a week.", { a: tr(r.action), n: r.estimated_weekly_demand })} aside={<Chip t="info">{tr("asked {n}×", { n: r.asked_times })}</Chip>} />
            ))}
          </ul>
        </Card>

        <Card title={tr("Not selling")} icon={LuChartColumn} aside={tr("no sale in 30 days")} flush>
          {dead.data?.length === 0 && <Empty>{tr("Every item sold in the last 30 days.")}</Empty>}
          <ul className="px-3">
            {dead.data?.slice(0, 8).map((d) => (
              <Item key={d.product_id} onClick={() => openProduct(d.product_id)} title={d.name} detail={tr(d.action)} aside={<Chip>{tr("{n} on shelf", { n: units(d.qty) })}</Chip>} />
            ))}
          </ul>
        </Card>

        <Card title={tr("Shelf counts below records")} icon={LuScanLine} flush>
          {A && A.count_mismatch.length === 0 && <Empty icon={LuShieldCheck}>{tr("Shelf counts match the records. Count fast movers weekly to keep it that way.")}</Empty>}
          <ul className="px-3">
            {A?.count_mismatch.map((x) => (
              <Item key={x.product_id} onClick={() => openProduct(x.product_id)} title={x.name} detail={tr(x.action)} aside={<Chip t="exp">{tr("{n} missing", { n: units(x.missing_units) })}</Chip>} />
            ))}
          </ul>
        </Card>
      </div>

      <Card title={tr("Your area this week")} icon={LuMapPin} aside={tr("anonymous, from nearby shops")} flush>
        {area.data && !area.data.joined && (
          <Empty icon={LuMapPin}>
            {tr("Join area trends to see which categories are selling more around you. Each shop shares only noisy, category-level changes, and an area appears only once 5 or more shops take part.")}
          </Empty>
        )}
        {area.data?.joined && area.data.trends.length === 0 && <Empty>{tr("Not enough shops in your area yet. Trends appear once 5 or more shops share.")}</Empty>}
        <ul className="px-3">
          {area.data?.trends.map((t) => (
            <li key={`${t.week}${t.category}`} className="rounded-2xl px-3 py-3">{t.message}</li>
          ))}
        </ul>
      </Card>

      <Card title={tr("Forecast accuracy")} icon={LuTarget} aside={tr("tested on real retail data")} flush>
        <div className="grid gap-4 px-6 sm:grid-cols-2">
          {card.data?.tests.map((t) => (
            <div key={t.dataset} className="neu-in-sm rounded-2xl p-5">
              <p className="font-semibold">{t.dataset}</p>
              <p className="text-[12.5px] text-ink-3">{tr(t.what)}</p>
              <div className="mt-3 flex items-end gap-3">
                <span className="text-[2rem] font-extrabold leading-none tabular-nums text-brand">{t.accuracy_weekly_pct}%</span>
                <span className="pb-1 text-[12.5px] text-ink-2">
                  {tr("weekly accuracy")} · {t.baseline} {t.baseline_weekly_pct}% · {tr("{n}% less error", { n: t.error_cut_pct })}
                </span>
              </div>
            </div>
          ))}
        </div>
        {card.data?.model && <p className="px-6 pt-4 text-[12.5px] text-ink-3">{tr("Model")}: {card.data.model}</p>}
        {metrics.data && (
          <p className="px-6 pt-1 text-[12.5px] text-ink-3">
            {tr("{n} AI calls so far, average {ms} ms", { n: metrics.data.llm_calls, ms: metrics.data.avg_latency_ms })}
            {metrics.data.owner_edit_rate !== null && tr("; you changed {p}% of suggested quantities", { p: Math.round(metrics.data.owner_edit_rate * 100) })}.
          </p>
        )}
      </Card>
    </div>
  );
}
