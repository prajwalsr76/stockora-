import { useMemo, useState } from "react";
import { LuCheck, LuCircleCheck, LuCopy, LuDownload, LuFlame, LuHistory, LuMinus, LuPlus, LuShoppingCart, LuTriangleAlert, LuX } from "react-icons/lu";
import { api, type Order } from "../api";
import { useApp } from "../components/appContext";
import { Button, Card, Chip, Empty, ErrorNote, Loading, PageHeader, RunwayBar, Stat, canDownload, downloadCsv, num, shortDate, units, useAsync } from "../components/ui";
import { festivalText, t } from "../i18n";

type Line = { product_id: number; name: string; stock: number; days: number | null; qty: number; reason: string };

function why(reason: string) {
  const [main, ...notes] = reason.split("; ");
  const m = main.match(/forecast (\d+) over (\d+) days.*safety (\d+)/);
  return { head: m ? t("Sells ~{a} in the next {d} days, plus {b} buffer", { a: m[1], d: m[2], b: m[3] }) : main, notes };
}
const noteLabel = (n: string) => t(n.split(":")[0].replace(/\s*\(.*/, "").replace(/, so .*/, ""));
function noteTone(n: string): "out" | "exp" | "plain" {
  return n.startsWith("urgent") ? "out" : n.startsWith("festival") ? "exp" : "plain";
}
const listText = (lines: { name: string; qty: number }[]) => lines.map((l) => `${l.name} x ${l.qty}`).join("\n");

function Stepper({ value, onChange, changed, label }: { value: number; onChange: (v: number) => void; changed: boolean; label: string }) {
  return (
    <div className={`inline-flex items-center gap-1 rounded-2xl p-1 ${changed ? "neu-in-sm ring-2 ring-brand/40" : "neu-in-sm"}`}>
      <button className="neu-btn grid size-8 place-items-center rounded-xl text-ink-2" aria-label={t("Less {name}", { name: label })} onClick={() => onChange(Math.max(0, value - 1))}>
        <LuMinus className="size-3.5" />
      </button>
      <input
        aria-label={t("Quantity of {name}", { name: label })}
        inputMode="numeric"
        value={value}
        onChange={(e) => onChange(Math.max(0, Number(e.target.value.replace(/\D/g, "")) || 0))}
        className="w-12 bg-transparent text-center font-bold tabular-nums focus:outline-none"
      />
      <button className="neu-btn grid size-8 place-items-center rounded-xl text-ink-2" aria-label={t("More {name}", { name: label })} onClick={() => onChange(value + 1)}>
        <LuPlus className="size-3.5" />
      </button>
    </div>
  );
}

export default function Restock() {
  const { openProduct, stock, refreshStock } = useApp();
  const plan = useAsync(api.plan);
  const orders = useAsync(() => api.orders());
  const [qty, setQty] = useState<Record<number, number>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const drafts = (orders.data ?? []).filter((o) => o.status === "draft");
  const done = (orders.data ?? []).filter((o) => o.status !== "draft" && o.status !== "cancelled");
  const stockBy = useMemo(() => new Map(stock.map((r) => [r.product_id, r])), [stock]);

  const lines: Line[] = useMemo(() => {
    if (drafts.length)
      return drafts.flatMap((o) =>
        o.lines.map((l) => {
          const s = stockBy.get(l.product_id);
          return { product_id: l.product_id, name: l.name, stock: s?.stock ?? 0, days: s?.days_of_stock ?? null, qty: l.qty_suggested, reason: l.reason };
        }),
      );
    return (plan.data?.lines ?? []).map((l) => ({ product_id: l.product_id, name: l.name, stock: l.stock + l.pending, days: l.days_of_stock, qty: l.qty, reason: l.reason }));
  }, [drafts, plan.data, stockBy]);
  const sorted = [...lines].sort((a, b) => (a.days ?? 99) - (b.days ?? 99));
  const q = (l: Line) => qty[l.product_id] ?? l.qty;
  const urgent = sorted.filter((l) => l.reason.includes("urgent")).length;
  const festive = sorted.filter((l) => l.reason.includes("festival")).length;
  const changedCount = sorted.filter((l) => q(l) !== l.qty).length;

  const refresh = () => {
    plan.reload();
    orders.reload();
    refreshStock();
    setQty({});
  };

  const approve = async () => {
    setBusy(true);
    setErr(null);
    try {
      let pending: Order[] = drafts;
      if (!pending.length) {
        await api.draftOrders();
        pending = await api.orders("draft");
      }
      for (const o of pending) {
        const edits: Record<number, number> = {};
        for (const l of o.lines) if (qty[l.product_id] !== undefined && qty[l.product_id] !== l.qty_suggested) edits[l.product_id] = qty[l.product_id];
        await api.approve(o.id, edits);
      }
      refresh();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const discard = async () => {
    setBusy(true);
    try {
      for (const o of drafts) await api.reject(o.id);
      refresh();
    } finally {
      setBusy(false);
    }
  };

  const copy = async (key: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(key);
    } catch {
      setCopied(null);
    }
  };

  const history = useMemo(() => {
    const m = new Map<string, { name: string; qty: number }[]>();
    for (const o of done) {
      const day = o.created_at.slice(0, 10);
      m.set(day, [...(m.get(day) ?? []), ...o.lines.map((l) => ({ name: l.name, qty: l.qty_final ?? l.qty_suggested }))]);
    }
    return [...m.entries()].slice(0, 6);
  }, [done]);

  const loading = plan.loading || orders.loading;
  return (
    <div className="space-y-7">
      <PageHeader
        icon={LuShoppingCart}
        title={t("Restock")}
        sub={t("What to bring in so nothing runs out before the next delivery, without ordering stock that would expire on the shelf. Adjust any quantity; Stockora learns from your changes.")}
      >
        {canDownload && <Button kind="soft" icon={LuDownload} disabled={!sorted.length} onClick={() => downloadCsv("stockora-restock.csv", [[t("Product"), t("On hand"), t("Days left"), t("Bring in"), t("Why")], ...sorted.map((l) => [l.name, l.stock, l.days, q(l), l.reason])])}>
          {t("Export CSV")}
        </Button>}
      </PageHeader>

      {loading && <Loading what={t("Working out what to restock")} />}
      {plan.error && <ErrorNote error={plan.error} onRetry={plan.reload} />}

      {!loading && sorted.length > 0 && (
        <div className="grid gap-6 sm:grid-cols-3">
          <Stat icon={LuShoppingCart} tone="info" label={t("Items to restock")} value={sorted.length} note={t("{n} units in total", { n: units(sorted.reduce((a, l) => a + q(l), 0)) })} />
          <Stat icon={LuTriangleAlert} tone={urgent ? "out" : "plain"} label={t("Urgent")} value={urgent} note={t("run out before a normal delivery")} />
          <Stat icon={LuFlame} tone={festive ? "exp" : "plain"} label={t("Festival boost")} value={festive} note={festivalText(plan.data?.festival_note || "") || t("no festival in the next 3 weeks")} />
        </div>
      )}

      {!loading && (
        <Card title={drafts.length ? t("Waiting for your approval") : t("Suggested today")} icon={LuShoppingCart} aside={changedCount ? t("{n} changed by you", { n: changedCount }) : t("{n} items", { n: sorted.length })} flush>
          {sorted.length === 0 ? (
            <Empty icon={LuCircleCheck}>{t("Nothing needs restocking right now. Every item lasts until the next delivery.")}</Empty>
          ) : (
            <>
              <div className="overflow-x-auto px-3">
                <table className="w-full text-left text-[13.5px]">
                  <thead className="text-[12px] text-ink-3">
                    <tr className="border-b border-line">
                      <th className="px-3 py-3 font-semibold">{t("Product")}</th>
                      <th className="hidden px-3 py-3 font-semibold md:table-cell">{t("Lasts now")}</th>
                      <th className="hidden px-3 py-3 font-semibold lg:table-cell">{t("Why this quantity")}</th>
                      <th className="px-3 py-3 text-right font-semibold">{t("Bring in")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sorted.map((l) => {
                      const w = why(l.reason);
                      return (
                        <tr key={l.product_id} className="border-b border-line/70 align-middle last:border-0">
                          <td className="px-3 py-3">
                            <button onClick={() => openProduct(l.product_id)} className="text-left">
                              <span className="block font-semibold hover:text-brand">{l.name}</span>
                              <span className="block text-[12px] tabular-nums text-ink-3">
                                {t("{n} on hand", { n: units(l.stock) })}
                                <span className="md:hidden"> · {l.days === null ? t("no sales") : t("{n} days", { n: num(l.days) })}</span>
                              </span>
                              <span className="mt-1 flex flex-wrap gap-1 lg:hidden">
                                {w.notes.map((n) => (
                                  <Chip key={n} t={noteTone(n)}>{noteLabel(n)}</Chip>
                                ))}
                              </span>
                            </button>
                          </td>
                          <td className="hidden px-3 py-3 md:table-cell">
                            <div className="flex items-center gap-3">
                              <RunwayBar days={l.days} stock={l.stock} />
                              <span className="whitespace-nowrap tabular-nums text-ink-2">{l.days === null ? "–" : t("{n} d", { n: num(l.days) })}</span>
                            </div>
                          </td>
                          <td className="hidden max-w-[22rem] px-3 py-3 lg:table-cell">
                            <span className="block text-ink-2">{w.head}</span>
                            <span className="mt-1 flex flex-wrap gap-1">
                              {w.notes.map((n) => (
                                <Chip key={n} t={noteTone(n)}>{noteLabel(n)}</Chip>
                              ))}
                            </span>
                          </td>
                          <td className="px-3 py-3 text-right">
                            <Stepper label={l.name} value={q(l)} changed={q(l) !== l.qty} onChange={(v) => setQty({ ...qty, [l.product_id]: v })} />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="flex flex-wrap items-center justify-between gap-3 px-6 pt-4">
                <p className="text-[13px] text-ink-2">
                  {t("{n} units across {m} items", { n: units(sorted.reduce((a, l) => a + q(l), 0)), m: sorted.length })}
                </p>
                <div className="flex flex-wrap gap-3">
                  <Button kind="soft" icon={copied === "now" ? LuCheck : LuCopy} onClick={() => copy("now", listText(sorted.map((l) => ({ name: l.name, qty: q(l) }))))}>
                    {copied === "now" ? t("Copied") : t("Copy list")}
                  </Button>
                  {drafts.length > 0 && (
                    <Button kind="danger" icon={LuX} onClick={discard} disabled={busy}>
                      {t("Discard")}
                    </Button>
                  )}
                  <Button icon={LuCheck} onClick={approve} disabled={busy}>
                    {busy ? t("Saving…") : t("Approve restock")}
                  </Button>
                </div>
              </div>
              {err && <p className="px-6 pt-3 text-[13px] text-out">{err}</p>}
            </>
          )}
        </Card>
      )}

      <Card title={t("Approved restock lists")} icon={LuHistory} flush>
        {history.length === 0 ? (
          <Empty icon={LuHistory}>{t("Approved lists appear here, ready to copy and send.")}</Empty>
        ) : (
          <ul className="px-3">
            {history.map(([day, items]) => (
              <li key={day} className="flex flex-wrap items-center justify-between gap-3 rounded-2xl px-3 py-3 hover:bg-line/25">
                <span>
                  <span className="block font-semibold">{shortDate(day)}</span>
                  <span className="text-[12.5px] text-ink-3">
                    {t("{n} items · {u} units", { n: items.length, u: units(items.reduce((a, i) => a + i.qty, 0)) })}
                  </span>
                </span>
                <Button size="sm" kind="soft" icon={copied === day ? LuCheck : LuCopy} onClick={() => copy(day, listText(items))}>
                  {copied === day ? t("Copied") : t("Copy")}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
