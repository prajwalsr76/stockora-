import { useState } from "react";
import { LuCircleCheck, LuClock, LuMessageCircle, LuUsers, LuWallet } from "react-icons/lu";
import { api, rs, type Receivable } from "../api";
import { Button, Card, Chip, Empty, ErrorNote, Loading, PageHeader, Stat, inputClass, shortDate, useAsync } from "../components/ui";
import { getLang, t } from "../i18n";

const ageDays = (iso: string) => Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);

function Row({ r, risk, onSaved }: { r: Receivable; risk?: "high" | "medium" | "low"; onSaved: () => void }) {
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const age = ageDays(r.oldest);
  const record = async () => {
    const n = Number(amount);
    if (!n || n <= 0) return;
    setBusy(true);
    await api.addCredit(r.id, -n, t("payment received")).finally(() => setBusy(false));
    setAmount("");
    onSaved();
  };
  const reminder = getLang() === "hi"
    ? `नमस्ते ${r.name} जी, आपका ${rs(r.due)} का हिसाब बाकी है। सुविधा अनुसार जमा कर दें। धन्यवाद!`
    : `Hello ${r.name}, ${rs(r.due)} is due on your account. Please pay when convenient. Thank you!`;
  const initials = r.name.split(" ").map((w) => w[0]).slice(0, 2).join("");

  return (
    <li className="grid items-center gap-x-6 gap-y-3 rounded-2xl px-3 py-4 hover:bg-line/20 lg:grid-cols-[1fr_auto]">
      <div className="flex items-center gap-4">
        <span className="neu-sm grid size-11 shrink-0 place-items-center rounded-2xl text-[13px] font-bold text-brand">{initials}</span>
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">{r.name}</span>
            {risk && risk !== "low" && <Chip t={risk === "high" ? "out" : "exp"}>{risk === "high" ? t("Collect first") : t("Send a reminder")}</Chip>}
          </p>
          <p className="text-[12.5px] text-ink-3">
            <span className="text-[15px] font-bold tabular-nums text-ink">{rs(r.due)}</span> {t("due since {d} ({n} days)", { d: shortDate(r.oldest.slice(0, 10)), n: age })}
            {r.last_paid && ` · ${t("last paid {d}", { d: shortDate(r.last_paid.slice(0, 10)) })}`}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <label>
          <span className="sr-only">{t("Payment received from {name}", { name: r.name })}</span>
          <input inputMode="decimal" placeholder={t("₹ received")} value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))} className={`${inputClass} w-32 text-right tabular-nums`} />
        </label>
        <Button size="sm" kind="soft" onClick={record} disabled={busy || !amount}>
          {t("Record payment")}
        </Button>
        <a className="neu-btn inline-flex min-h-9 items-center gap-1.5 rounded-xl px-3.5 text-[13px] font-semibold text-ok" href={`https://wa.me/?text=${encodeURIComponent(reminder)}`} target="_blank" rel="noreferrer">
          <LuMessageCircle className="size-4" />
          WhatsApp
        </a>
      </div>
    </li>
  );
}

export default function Udhaar() {
  const { data, error, loading, reload } = useAsync(api.udhaar);
  const alerts = useAsync(api.alerts);
  const risk = new Map((alerts.data?.udhaar_risk ?? []).map((u) => [u.customer_id, u.risk]));
  const total = (data ?? []).reduce((a, r) => a + r.due, 0);
  const order: Record<string, number> = { high: 0, medium: 1, low: 2 };
  const sorted = [...(data ?? [])].sort((a, b) => (order[risk.get(a.id) ?? ""] ?? 3) - (order[risk.get(b.id) ?? ""] ?? 3) || b.due - a.due);
  const old = (data ?? []).filter((r) => ageDays(r.oldest) > 30);

  return (
    <div className="space-y-7">
      <PageHeader icon={LuWallet} title={t("Udhaar")} sub={t("What customers owe the shop. Old, growing dues are flagged so you collect from those customers first.")} />
      {loading && <Loading />}
      {error && <ErrorNote error={error} onRetry={reload} />}
      {data && (
        <div className="grid gap-6 sm:grid-cols-3">
          <Stat icon={LuWallet} tone="info" label={t("Total due")} value={rs(total)} />
          <Stat icon={LuUsers} label={t("Customers")} value={data.length} note={t("with money due")} />
          <Stat icon={LuClock} tone={old.length ? "exp" : "plain"} label={t("Older than 30 days")} value={old.length} note={old.length ? rs(old.reduce((a, r) => a + r.due, 0)) : t("none")} />
        </div>
      )}
      {data && (
        <Card title={t("Customers")} icon={LuUsers} flush>
          {data.length === 0 ? (
            <Empty icon={LuCircleCheck}>{t("No one owes the shop money right now.")}</Empty>
          ) : (
            <ul className="px-3">
              {sorted.map((r) => (
                <Row key={r.id} r={r} risk={risk.get(r.id)} onSaved={reload} />
              ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  );
}
