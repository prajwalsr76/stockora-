import { useMemo, useState } from "react";
import { LuCircleCheck, LuClock, LuHourglass, LuLightbulb, LuTriangleAlert } from "react-icons/lu";
import { api, type ExpiryAlert } from "../api";
import { useApp } from "../components/appContext";
import { Card, Chip, Empty, ErrorNote, Loading, PageHeader, Segments, Stat, shortDate, units, useAsync } from "../components/ui";
import { t } from "../i18n";

type Bucket = "now" | "week" | "later";
const bucketOf = (e: ExpiryAlert): Bucket => (e.days_left <= 2 ? "now" : e.days_left <= 7 ? "week" : "later");
const TITLES: Record<Bucket, string> = { now: "Act today: expires within 2 days", week: "This week", later: "Later" }; // translated at render

function Row({ e, onOpen }: { e: ExpiryAlert; onOpen: () => void }) {
  const sold = Math.max(0, e.remaining - e.leftover);
  const pct = e.remaining > 0 ? (sold / e.remaining) * 100 : 0;
  return (
    <li className="rounded-2xl px-3 py-4 hover:bg-line/25">
      <div className="grid gap-x-6 gap-y-3 md:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_auto] md:items-center">
        <button onClick={onOpen} className="min-w-0 text-left">
          <span className="block truncate font-semibold">{e.name}</span>
          <span className="block text-[12.5px] text-ink-3">
            {t("Batch {b} · expires {d}", { b: e.batch_no ?? `#${e.batch_id}`, d: shortDate(e.expiry) })}
          </span>
        </button>
        <div>
          <div className="neu-in-sm flex h-3 overflow-hidden rounded-full" aria-hidden>
            <div className="grow-x h-full rounded-l-full bg-ok" style={{ width: `${pct}%` }} />
            <div className="h-full flex-1 bg-[repeating-linear-gradient(135deg,var(--exp)_0_4px,var(--exp-soft)_4px_8px)]" />
          </div>
          <p className="mt-1.5 text-[12.5px] tabular-nums text-ink-2">
            <span className="font-semibold text-ok">{t("{n} sell in time", { n: units(sold) })}</span> ·{" "}
            <span className="font-semibold text-exp">{t("{n} won't", { n: units(e.leftover) })}</span> · {t("of {n}", { n: units(e.remaining) })}
          </p>
        </div>
        <Chip t={e.days_left <= 2 ? "out" : "exp"} icon={LuClock}>
          {e.days_left <= 0 ? t("Expired") : t("{n} days left", { n: e.days_left })}
        </Chip>
      </div>
      <p className="mt-2.5 flex items-start gap-2 text-[13px] text-ink-2">
        <LuLightbulb className="mt-0.5 size-4 shrink-0 text-exp" />
        {t(e.action)}
      </p>
    </li>
  );
}

export default function Expiry() {
  const { openProduct } = useApp();
  const { data, error, loading, reload } = useAsync(api.expiry);
  const [view, setView] = useState<"all" | Bucket>("all");
  const groups = useMemo(() => {
    const g: Record<Bucket, ExpiryAlert[]> = { now: [], week: [], later: [] };
    for (const e of data ?? []) g[bucketOf(e)].push(e);
    return g;
  }, [data]);
  const total = (data ?? []).reduce((a, e) => a + e.leftover, 0);
  const urgent = groups.now.reduce((a, e) => a + e.leftover, 0);

  return (
    <div className="space-y-7">
      <PageHeader
        icon={LuHourglass}
        title={t("Expiry")}
        sub={t("Batches that will still be on the shelf when they expire, at the forecast sales rate. Stock sells oldest-expiry first (FEFO).")}
      />
      {loading && <Loading />}
      {error && <ErrorNote error={error} onRetry={reload} />}
      {data && (
        <div className="grid gap-6 sm:grid-cols-3">
          <Stat icon={LuHourglass} tone={total ? "exp" : "plain"} label={t("Units at risk")} value={units(total)} note={t("{n} batches", { n: data.length })} />
          <Stat icon={LuTriangleAlert} tone={urgent ? "out" : "plain"} label={t("Expire within 2 days")} value={units(urgent)} note={t("{n} batches, act today", { n: groups.now.length })} />
          <Stat icon={LuCircleCheck} tone="ok" label={t("Will sell in time")} value={units(data.reduce((a, e) => a + Math.max(0, e.remaining - e.leftover), 0))} note={t("units from these batches")} />
        </div>
      )}
      {data && data.length === 0 && (
        <Card>
          <Empty icon={LuCircleCheck}>{t("No batch is at risk. Everything on the shelf should sell before it expires.")}</Empty>
        </Card>
      )}
      {data && data.length > 0 && (
        <>
          <Segments
            label={t("Filter by time left")}
            value={view}
            onChange={setView}
            options={[
              { id: "all", label: t("All"), count: data.length },
              { id: "now", label: t("Within 2 days"), count: groups.now.length },
              { id: "week", label: t("This week"), count: groups.week.length },
              { id: "later", label: t("Later"), count: groups.later.length },
            ]}
          />
          {(["now", "week", "later"] as Bucket[])
            .filter((b) => (view === "all" || view === b) && groups[b].length)
            .map((b) => (
              <Card key={b} title={t(TITLES[b])} icon={b === "now" ? LuTriangleAlert : LuClock} aside={t("{n} batches", { n: groups[b].length })} flush>
                <ul className="px-3">
                  {groups[b].map((e) => (
                    <Row key={e.batch_id} e={e} onOpen={() => openProduct(e.product_id)} />
                  ))}
                </ul>
              </Card>
            ))}
        </>
      )}
    </div>
  );
}
