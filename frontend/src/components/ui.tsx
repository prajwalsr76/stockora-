import { useCallback, useEffect, useState, type ReactNode } from "react";
import type { IconType } from "react-icons";
import { LuCircleAlert, LuInbox } from "react-icons/lu";
import { locale, t as tr } from "../i18n";

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(fn, deps);
  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    run()
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [run]);
  useEffect(reload, [reload]);
  return { data, error, loading, reload, setData };
}

/* ---------------- formatting ---------------- */
export const units = (n: number) => Math.round(n).toLocaleString("en-IN");
export const num = (n: number, d = 1) => n.toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
export const daysText = (d: number | null) => (d === null ? "–" : d > 60 ? tr("60+ days") : d === 1 ? tr("1 day") : tr("{n} days", { n: d < 10 ? d.toFixed(1) : Math.round(d) }));
export const shortDate = (iso: string | null) =>
  iso ? new Date(iso + "T00:00:00").toLocaleDateString(locale(), { weekday: "short", day: "numeric", month: "short" }) : "–";
export const dayMonth = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString(locale(), { day: "numeric", month: "short" });

/* ---------------- status ---------------- */
export type Status = "out" | "soon" | "ok" | "idle" | "expiring";
export function stockStatus(stock: number, days: number | null): Status {
  if (stock <= 0) return "out";
  if (days === null) return "idle";
  if (days < 3) return "out";
  if (days < 7) return "soon";
  return "ok";
}
const STATUS: Record<Status, { label: string; tone: Tone }> = {
  out: { label: "Runs out", tone: "out" },
  soon: { label: "This week", tone: "exp" },
  ok: { label: "Healthy", tone: "ok" },
  idle: { label: "Not selling", tone: "plain" },
  expiring: { label: "May expire", tone: "exp" },
};

/* ---------------- layout ---------------- */
export function PageHeader({ title, sub, icon: Icon, children }: { title: string; sub?: ReactNode; icon?: IconType; children?: ReactNode }) {
  return (
    <header className="mb-7 flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
      <div className="flex min-w-0 items-start gap-4">
        {Icon && (
          <span className="neu-sm grid size-12 shrink-0 place-items-center rounded-2xl text-brand">
            <Icon className="size-[22px]" />
          </span>
        )}
        <div className="min-w-0 max-w-2xl">
          <h1 className="text-[1.65rem] font-bold leading-tight tracking-[-0.02em]">{title}</h1>
          {sub && <p className="mt-1 text-ink-2">{sub}</p>}
        </div>
      </div>
      {children && <div className="flex flex-wrap items-center gap-3">{children}</div>}
    </header>
  );
}

export function Card({
  title,
  icon: Icon,
  aside,
  children,
  flush,
  className = "",
}: {
  title?: ReactNode;
  icon?: IconType;
  aside?: ReactNode;
  children: ReactNode;
  flush?: boolean;
  className?: string;
}) {
  return (
    <section className={`neu min-w-0 rounded-[22px] ${className}`}>
      {title && (
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-6 pb-2 pt-5">
          <h2 className="flex items-center gap-2.5 text-[15px] font-semibold">
            {Icon && <Icon className="size-[18px] text-ink-3" />}
            {title}
          </h2>
          {aside && <div className="text-[13px] text-ink-2">{aside}</div>}
        </div>
      )}
      <div className={flush ? "pb-3" : "px-6 pb-6 pt-3"}>{children}</div>
    </section>
  );
}

/* ---------------- controls ---------------- */
type Tone = "out" | "exp" | "ok" | "info" | "plain";
const toneClass: Record<Tone, string> = {
  out: "bg-out-soft text-out",
  exp: "bg-exp-soft text-exp",
  ok: "bg-ok-soft text-ok",
  info: "bg-info-soft text-info",
  plain: "neu-in-sm text-ink-2",
};

export function Chip({ t = "plain", children, icon: Icon }: { t?: Tone; children: ReactNode; icon?: IconType }) {
  return (
    <span className={`inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-[12.5px] font-semibold ${toneClass[t]}`}>
      {Icon && <Icon className="size-3.5" />}
      {children}
    </span>
  );
}

export function StatusChip({ s }: { s: Status }) {
  const { label, tone } = STATUS[s];
  return <Chip t={tone}>{tr(label)}</Chip>;
}

export function Button({
  children,
  onClick,
  kind = "primary",
  disabled,
  type = "button",
  size = "md",
  icon: Icon,
  title,
}: {
  children?: ReactNode;
  onClick?: () => void;
  kind?: "primary" | "soft" | "danger";
  disabled?: boolean;
  type?: "button" | "submit";
  size?: "sm" | "md";
  icon?: IconType;
  title?: string;
}) {
  const styles = {
    primary: "neu-primary",
    soft: "neu-btn text-ink",
    danger: "neu-btn text-out",
  }[kind];
  const sz = size === "sm" ? "min-h-9 px-3.5 text-[13px] rounded-xl gap-1.5" : "min-h-11 px-5 rounded-2xl gap-2";
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`inline-flex items-center justify-center font-semibold disabled:cursor-not-allowed disabled:opacity-45 ${sz} ${styles}`}
    >
      {Icon && <Icon className={size === "sm" ? "size-4" : "size-[18px]"} />}
      {children}
    </button>
  );
}

export function IconButton({ icon: Icon, label, onClick, pressed, badge }: { icon: IconType; label: string; onClick?: () => void; pressed?: boolean; badge?: number }) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      className="neu-btn relative grid size-11 place-items-center rounded-2xl text-ink-2 hover:text-ink"
    >
      <Icon className="size-[19px]" />
      {badge !== undefined && badge > 0 && (
        <span className="absolute -right-1 -top-1 grid min-w-5 place-items-center rounded-full bg-out px-1 text-[11px] font-bold text-white">
          {badge > 99 ? "99+" : badge}
        </span>
      )}
    </button>
  );
}

export const inputClass =
  "neu-in-sm min-h-11 rounded-2xl border-0 px-4 text-ink placeholder:text-ink-3 focus:outline-none focus:ring-2 focus:ring-brand/40";

export function Segments<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { id: T; label: ReactNode; count?: number }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="neu-in-sm inline-flex flex-wrap gap-1 rounded-2xl p-1.5">
      {options.map((o) => (
        <button
          key={o.id}
          role="radio"
          aria-checked={value === o.id}
          onClick={() => onChange(o.id)}
          className={`min-h-9 rounded-xl px-3.5 text-[13px] font-semibold transition-colors ${
            value === o.id ? "neu-sm text-brand" : "text-ink-2 hover:text-ink"
          }`}
        >
          {o.label}
          {o.count !== undefined && <span className="ml-1.5 tabular-nums text-ink-3">{o.count}</span>}
        </button>
      ))}
    </div>
  );
}

/* ---------------- data display ---------------- */
export function Stat({
  label,
  value,
  note,
  icon: Icon,
  tone = "plain",
  onClick,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  icon: IconType;
  tone?: Tone;
  onClick?: () => void;
}) {
  const color = { out: "text-out", exp: "text-exp", ok: "text-ok", info: "text-info", plain: "text-ink" }[tone];
  const Tag = onClick ? "button" : "div";
  return (
    <Tag onClick={onClick} className={`neu flex min-w-0 items-center gap-4 rounded-[22px] p-5 text-left ${onClick ? "transition-transform hover:-translate-y-0.5" : ""}`}>
      <span className={`neu-in grid size-12 shrink-0 place-items-center rounded-2xl ${color}`}>
        <Icon className="size-[21px]" />
      </span>
      <span className="min-w-0">
        <span className="block text-[13px] font-medium text-ink-2">{label}</span>
        <span className={`block text-[1.6rem] font-bold leading-tight tabular-nums ${tone === "plain" ? "" : color}`}>{value}</span>
        {note && <span className="block truncate text-[12.5px] text-ink-3">{note}</span>}
      </span>
    </Tag>
  );
}

/** Circular gauge in an inset well. value 0..1 */
export function Ring({ value, size = 132, stroke = 12, color = "var(--brand)", children }: { value: number; size?: number; stroke?: number; color?: string; children?: ReactNode }) {
  const r = (size - stroke) / 2 - 6;
  const c = 2 * Math.PI * r;
  const off = c * (1 - Math.max(0, Math.min(1, value)));
  return (
    <div className="neu-in relative grid shrink-0 place-items-center rounded-full" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="absolute inset-0 -rotate-90" aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--line)" strokeWidth={stroke} />
        <circle
          className="sweep"
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={off}
          style={{ ["--sweep-from" as string]: c }}
        />
      </svg>
      <div className="relative text-center">{children}</div>
    </div>
  );
}

/** Days of stock as a capsule in an inset track (14-day scale, tick at one week). */
export function RunwayBar({ days, stock, expiryAt }: { days: number | null; stock: number; expiryAt?: number | null }) {
  const s = stockStatus(stock, days);
  const pct = days === null ? 0 : Math.min(100, (days / 14) * 100);
  const color = s === "out" ? "var(--out)" : s === "soon" ? "var(--exp)" : "var(--ok)";
  return (
    <div className="flex items-center gap-3" aria-label={tr("{d} of stock", { d: daysText(days) })}>
      <div className="neu-in-sm relative h-2.5 w-24 shrink-0 overflow-hidden rounded-full sm:w-32">
        <div
          className="h-full rounded-full"
          style={{ width: `${Math.max(pct, stock <= 0 ? 0 : 5)}%`, background: `linear-gradient(90deg, color-mix(in srgb, ${color} 70%, white), ${color})` }}
        />
        {expiryAt !== undefined && expiryAt !== null && expiryAt <= 14 && (
          <span className="absolute top-0 h-full w-0.5 bg-exp" style={{ left: `${(expiryAt / 14) * 100}%` }} title="A batch expires here" />
        )}
      </div>
    </div>
  );
}

export function Loading({ what = tr("Loading") }: { what?: string }) {
  return (
    <div className="space-y-4 py-4" role="status" aria-label={what}>
      {[0, 1, 2].map((i) => (
        <div key={i} className="neu-in-sm h-4 animate-pulse rounded-full" style={{ width: `${85 - i * 20}%` }} />
      ))}
      <span className="sr-only">{what}…</span>
    </div>
  );
}

export function ErrorNote({ error, onRetry }: { error: string; onRetry?: () => void }) {
  const offline = /fetch|network/i.test(error);
  return (
    <div role="alert" className="neu flex gap-4 rounded-[22px] p-5">
      <span className="neu-in grid size-11 shrink-0 place-items-center rounded-2xl text-out">
        <LuCircleAlert className="size-5" />
      </span>
      <div>
        <p className="font-semibold text-out">{offline ? tr("Can't reach the Stockora server") : tr("Something went wrong")}</p>
        <p className="text-[13.5px] text-ink-2">
          {offline ? (
            <>
              {tr("Start it with")} <code className="font-semibold">start.ps1</code>{tr(", or run")}{" "}
              <code className="font-semibold">uvicorn stockora.api.main:app --port 8000</code> {tr("in the backend folder.")}
            </>
          ) : (
            error
          )}
        </p>
        {onRetry && (
          <button className="mt-2 text-[13px] font-semibold text-brand hover:underline" onClick={onRetry}>
            {tr("Try again")}
          </button>
        )}
      </div>
    </div>
  );
}

export function Empty({ children, icon: Icon = LuInbox }: { children: ReactNode; icon?: IconType }) {
  return (
    <div className="flex items-center gap-4 px-6 py-6 text-ink-2">
      <span className="neu-in-sm grid size-10 shrink-0 place-items-center rounded-xl text-ink-3">
        <Icon className="size-[18px]" />
      </span>
      <p>{children}</p>
    </div>
  );
}

/** False in the hosted demo page, where file downloads are blocked; true in the real app. */
export const canDownload = !(window as unknown as { __STOCKORA_DEMO__?: unknown }).__STOCKORA_DEMO__;

/** CSV download (works in the real app; browsers may block it inside sandboxed previews). */
export function downloadCsv(name: string, rows: (string | number | null)[][]) {
  const esc = (v: string | number | null) => {
    const s = v === null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const blob = new Blob(["﻿" + rows.map((r) => r.map(esc).join(",")).join("\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
