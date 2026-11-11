import { useEffect, useRef, useState, type ReactNode } from "react";
import { dayMonth, num, units } from "./ui";
import { locale, t } from "../i18n";

/** Measures the rendered width so charts draw at real pixels (crisp text at any size). */
function useWidth<T extends HTMLElement>(fallback = 640) {
  const ref = useRef<T>(null);
  const [w, setW] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(260, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

function niceMax(v: number) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

type Point = { day: string; units: number; low?: number; high?: number; kind: "actual" | "forecast"; stockout?: boolean };

function Tooltip({ x, w, children }: { x: number; w: number; children: ReactNode }) {
  const left = Math.min(Math.max(x - 90, 0), w - 180);
  return (
    <div className="neu-sm pointer-events-none absolute top-1 z-10 w-[180px] rounded-xl px-3 py-2 text-[12.5px]" style={{ left }}>
      {children}
    </div>
  );
}

/**
 * Units per day: actual (bars or area) then forecast (line + 80% band).
 * Used for the whole shop (area) and for one product (bars, stockout days marked).
 */
export function DemandChart({
  actual,
  forecast,
  height = 240,
  bars = false,
  markers = [],
  label,
}: {
  actual: { day: string; units: number; stockout?: boolean }[];
  forecast: { day: string; units: number; low: number; high: number }[];
  height?: number;
  bars?: boolean;
  markers?: { day: string; name: string }[];
  label: string;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const pts: Point[] = [
    ...actual.map((a) => ({ ...a, kind: "actual" as const })),
    ...forecast.map((f) => ({ ...f, kind: "forecast" as const })),
  ];
  const pad = { l: 40, r: 12, t: 16, b: 28 };
  const iw = w - pad.l - pad.r;
  const ih = height - pad.t - pad.b;
  const max = niceMax(Math.max(...pts.map((p) => p.high ?? p.units), 1));
  const step = iw / Math.max(pts.length, 1);
  const x = (i: number) => pad.l + step * (i + 0.5);
  const y = (v: number) => pad.t + ih - (v / max) * ih;
  const split = actual.length;
  const fIdx = (i: number) => split + i;

  const area = actual.length
    ? `M${x(0)},${y(0)} ` + actual.map((a, i) => `L${x(i)},${y(a.units)}`).join(" ") + ` L${x(split - 1)},${y(0)} Z`
    : "";
  const line = actual.map((a, i) => `${i ? "L" : "M"}${x(i)},${y(a.units)}`).join(" ");
  const fLine = forecast.map((f, i) => `${i ? "L" : "M"}${x(fIdx(i))},${y(f.units)}`).join(" ");
  const band = forecast.length
    ? forecast.map((f, i) => `${i ? "L" : "M"}${x(fIdx(i))},${y(f.high)}`).join(" ") +
      " " +
      [...forecast].reverse().map((f, i) => `L${x(fIdx(forecast.length - 1 - i))},${y(f.low)}`).join(" ") +
      " Z"
    : "";
  const ticks = [0, max / 2, max];
  const labelEvery = Math.max(1, Math.round(pts.length / Math.max(3, Math.floor(iw / 70))));
  const markerIdx = markers
    .map((m) => ({ ...m, i: pts.findIndex((p) => p.day === m.day) }))
    .filter((m) => m.i >= 0);
  const hp = hover !== null ? pts[hover] : null;

  return (
    <div ref={ref} className="relative w-full select-none">
      <svg
        width={w}
        height={height}
        role="img"
        aria-label={label}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const i = Math.floor((e.clientX - r.left - pad.l) / step);
          setHover(i >= 0 && i < pts.length ? i : null);
        }}
      >
        <defs>
          <linearGradient id="area-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--brand)" stopOpacity="0.28" />
            <stop offset="100%" stopColor="var(--brand)" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={w - pad.r} y1={y(t)} y2={y(t)} stroke="var(--line)" strokeDasharray={t ? "3 4" : undefined} />
            <text x={pad.l - 8} y={y(t) + 4} textAnchor="end" fontSize="11" fill="var(--ink-3)">
              {t >= 1000 ? `${num(t / 1000, t % 1000 ? 1 : 0)}k` : num(t, t % 1 ? 1 : 0)}
            </text>
          </g>
        ))}
        {split > 0 && forecast.length > 0 && (
          <g>
            <rect x={x(split) - step / 2} y={pad.t} width={w - pad.r - (x(split) - step / 2)} height={ih} fill="var(--info)" opacity="0.05" />
            <line x1={x(split) - step / 2} x2={x(split) - step / 2} y1={pad.t - 6} y2={pad.t + ih} stroke="var(--ink-3)" strokeDasharray="2 3" />
            <text x={x(split) - step / 2 + 6} y={pad.t + 18} fontSize="11" fill="var(--ink-2)" fontWeight="600">
              {t("Today")}
            </text>
          </g>
        )}
        {bars
          ? actual.map((a, i) => (
              <rect
                key={a.day}
                x={x(i) - Math.max(step * 0.32, 1)}
                width={Math.max(step * 0.64, 2)}
                y={y(a.units)}
                height={Math.max(ih - (y(a.units) - pad.t), 0)}
                rx={Math.min(3, step * 0.3)}
                fill={a.stockout ? "var(--out)" : "var(--brand)"}
                opacity={hover === i ? 1 : a.stockout ? 0.85 : 0.6}
              />
            ))
          : actual.length > 0 && (
              <g>
                <path d={area} fill="url(#area-fill)" />
                <path d={line} fill="none" stroke="var(--brand)" strokeWidth="2.2" strokeLinejoin="round" />
              </g>
            )}
        {bars &&
          actual.map((a, i) =>
            a.stockout ? <circle key={`s${a.day}`} cx={x(i)} cy={pad.t + ih + 6} r="2.5" fill="var(--out)" /> : null,
          )}
        {forecast.length > 0 && (
          <g>
            <path d={band} fill="var(--info)" opacity="0.14" />
            <path d={fLine} fill="none" stroke="var(--info)" strokeWidth="2.4" strokeDasharray="6 4" strokeLinecap="round" />
            {forecast.map((f, i) => (
              <circle key={f.day} cx={x(fIdx(i))} cy={y(f.units)} r={hover === fIdx(i) ? 4.5 : 2.6} fill="var(--info)" />
            ))}
          </g>
        )}
        {markerIdx.map((m) => (
          <g key={m.name}>
            <line x1={x(m.i)} x2={x(m.i)} y1={pad.t} y2={pad.t + ih} stroke="var(--exp)" strokeWidth="1.5" strokeDasharray="1 3" />
            <rect x={x(m.i) - 4} y={pad.t - 4} width="8" height="8" transform={`rotate(45 ${x(m.i)} ${pad.t})`} fill="var(--exp)" />
          </g>
        ))}
        {pts.map((p, i) =>
          i % labelEvery === 0 ? (
            <text key={p.day} x={x(i)} y={height - 6} textAnchor="middle" fontSize="11" fill="var(--ink-3)">
              {dayMonth(p.day)}
            </text>
          ) : null,
        )}
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={pad.t + ih} stroke="var(--ink-3)" strokeWidth="1" />}
      </svg>
      {hp && (
        <Tooltip x={x(hover!)} w={w}>
          <p className="font-semibold">{new Date(hp.day + "T00:00:00").toLocaleDateString(locale(), { weekday: "short", day: "numeric", month: "short" })}</p>
          {hp.kind === "actual" ? (
            <p className="text-ink-2">
              {t("Sold")} <span className="font-semibold text-ink tabular-nums">{num(hp.units, hp.units % 1 ? 1 : 0)}</span>
              {hp.stockout && <span className="text-out"> · {t("out of stock")}</span>}
            </p>
          ) : (
            <>
              <p className="text-ink-2">
                {t("Forecast")} <span className="font-semibold text-info tabular-nums">{num(hp.units)}</span>
              </p>
              <p className="text-ink-3 tabular-nums">
                {t("80% range")} {num(hp.low ?? 0)}–{num(hp.high ?? 0)}
              </p>
            </>
          )}
          {markerIdx.find((m) => m.i === hover) && <p className="text-exp">{markerIdx.find((m) => m.i === hover)!.name}</p>}
        </Tooltip>
      )}
    </div>
  );
}

/** Projected stock on the shelf, day by day, against the reorder point. */
export function StockProjection({
  start,
  forecast,
  reorderPoint,
  height = 170,
}: {
  start: number;
  forecast: { day: string; stock_after: number }[];
  reorderPoint: number | null;
  height?: number;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const pts = [{ day: "now", stock_after: start }, ...forecast];
  const pad = { l: 40, r: 12, t: 14, b: 26 };
  const iw = w - pad.l - pad.r;
  const ih = height - pad.t - pad.b;
  const max = niceMax(Math.max(start, reorderPoint ?? 0, 1));
  const x = (i: number) => pad.l + (iw * i) / Math.max(pts.length - 1, 1);
  const y = (v: number) => pad.t + ih - (Math.max(v, 0) / max) * ih;
  const zeroAt = pts.findIndex((p) => p.stock_after <= 0);
  const path = pts.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.stock_after)}`).join(" ");
  return (
    <div ref={ref} className="w-full">
      <svg width={w} height={height} role="img" aria-label={t("Projected stock over the next 14 days")}>
        {[0, max / 2, max].map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={w - pad.r} y1={y(t)} y2={y(t)} stroke="var(--line)" strokeDasharray={t ? "3 4" : undefined} />
            <text x={pad.l - 8} y={y(t) + 4} textAnchor="end" fontSize="11" fill="var(--ink-3)">
              {units(t)}
            </text>
          </g>
        ))}
        {reorderPoint !== null && reorderPoint > 0 && (
          <g>
            <line x1={pad.l} x2={w - pad.r} y1={y(reorderPoint)} y2={y(reorderPoint)} stroke="var(--exp)" strokeWidth="1.5" strokeDasharray="5 4" />
            <text x={w - pad.r} y={y(reorderPoint) - 6} textAnchor="end" fontSize="11" fill="var(--exp)" fontWeight="600">
              {t("Reorder at {n}", { n: num(reorderPoint) })}
            </text>
          </g>
        )}
        <path d={`${path} L${x(pts.length - 1)},${y(0)} L${x(0)},${y(0)} Z`} fill="var(--brand)" opacity="0.1" />
        <path d={path} fill="none" stroke="var(--brand)" strokeWidth="2.4" strokeLinejoin="round" />
        {zeroAt > 0 && (
          <g>
            <circle cx={x(zeroAt)} cy={y(0)} r="5" fill="var(--out)" stroke="var(--base)" strokeWidth="2" />
            <text x={Math.min(x(zeroAt) + 8, w - pad.r - 70)} y={y(0) - 8} fontSize="11.5" fill="var(--out)" fontWeight="700">
              {t("Empty {d}", { d: dayMonth(pts[zeroAt].day) })}
            </text>
          </g>
        )}
        <text x={pad.l} y={height - 6} fontSize="11" fill="var(--ink-3)">
          {t("Now")}
        </text>
        <text x={w - pad.r} y={height - 6} textAnchor="end" fontSize="11" fill="var(--ink-3)">
          {pts.length > 1 ? dayMonth(pts[pts.length - 1].day) : ""}
        </text>
      </svg>
    </div>
  );
}

/** Tiny trend line for table rows. */
export function Sparkline({ values, color = "var(--brand)", w = 84, h = 26 }: { values: number[]; color?: string; w?: number; h?: number }) {
  if (values.length < 2) return null;
  const max = Math.max(...values, 1);
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - 2 - (v / max) * (h - 4)}`).join(" ");
  return (
    <svg width={w} height={h} aria-hidden className="overflow-visible">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
