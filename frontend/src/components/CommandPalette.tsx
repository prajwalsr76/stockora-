import { useEffect, useMemo, useRef, useState } from "react";
import type { IconType } from "react-icons";
import { LuCornerDownLeft, LuPackage, LuSearch } from "react-icons/lu";
import type { StockRow } from "../api";
import { StatusChip, daysText, stockStatus } from "./ui";
import { t } from "../i18n";

export type PageItem = { id: string; label: string; icon: IconType };
type Result = { kind: "page"; page: PageItem } | { kind: "product"; row: StockRow };

export default function CommandPalette({
  pages,
  products,
  onPage,
  onProduct,
  onClose,
}: {
  pages: PageItem[];
  products: StockRow[];
  onPage: (id: string) => void;
  onProduct: (id: number) => void;
  onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);

  const results: Result[] = useMemo(() => {
    const t = q.trim().toLowerCase();
    const ps = pages.filter((p) => !t || p.label.toLowerCase().includes(t)).map((page) => ({ kind: "page" as const, page }));
    const rs = t
      ? products
          .filter((r) => r.name.toLowerCase().includes(t) || r.category.replace(/_/g, " ").includes(t))
          .slice(0, 8)
          .map((row) => ({ kind: "product" as const, row }))
      : [];
    return [...rs, ...ps];
  }, [q, pages, products]);

  const choose = (r: Result | undefined) => {
    if (!r) return;
    if (r.kind === "page") onPage(r.page.id);
    else onProduct(r.row.product_id);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 grid place-items-start justify-center px-4 pt-[12vh]" role="dialog" aria-modal="true" aria-label={t("Search")}>
      <button className="absolute inset-0 bg-ink/25 backdrop-blur-[2px]" aria-label={t("Close search")} onClick={onClose} />
      <div className="neu rise relative w-full max-w-xl overflow-hidden rounded-[24px]">
        <div className="flex items-center gap-3 px-5 pt-5">
          <LuSearch className="size-5 text-ink-3" />
          <input
            ref={input}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setSel(0);
            }}
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setSel((s) => Math.min(s + 1, results.length - 1));
              }
              if (e.key === "ArrowUp") {
                e.preventDefault();
                setSel((s) => Math.max(s - 1, 0));
              }
              if (e.key === "Enter") choose(results[sel]);
            }}
            placeholder={t("Search products or go to a page…")}
            aria-label={t("Search products or pages")}
            className="neu-in-sm min-h-12 w-full rounded-2xl px-4 text-[15px] placeholder:text-ink-3 focus:outline-none"
          />
        </div>
        <ul className="max-h-[55vh] overflow-y-auto px-3 py-3" role="listbox">
          {results.length === 0 && <li className="px-3 py-6 text-center text-ink-2">No product or page matches "{q}".</li>}
          {results.map((r, i) => {
            const active = i === sel;
            const Icon = r.kind === "page" ? r.page.icon : LuPackage;
            return (
              <li key={r.kind === "page" ? `p${r.page.id}` : `r${r.row.product_id}`} role="option" aria-selected={active}>
                <button
                  onMouseEnter={() => setSel(i)}
                  onClick={() => choose(r)}
                  className={`flex w-full items-center gap-3 rounded-2xl px-3 py-2.5 text-left ${active ? "neu-in-sm" : ""}`}
                >
                  <span className={`grid size-9 shrink-0 place-items-center rounded-xl ${active ? "text-brand" : "text-ink-3"}`}>
                    <Icon className="size-[18px]" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-semibold">{r.kind === "page" ? r.page.label : r.row.name}</span>
                    <span className="block text-[12.5px] text-ink-3">
                      {r.kind === "page" ? t("Page") : t("{n} on hand · lasts {d}", { n: Math.round(r.row.stock), d: daysText(r.row.days_of_stock) })}
                    </span>
                  </span>
                  {r.kind === "product" && <StatusChip s={stockStatus(r.row.stock, r.row.days_of_stock)} />}
                  {active && <LuCornerDownLeft className="size-4 text-ink-3" />}
                </button>
              </li>
            );
          })}
        </ul>
        <p className="border-t border-line px-5 py-2.5 text-[12px] text-ink-3">{t("↑ ↓ to move · Enter to open · Esc to close")}</p>
      </div>
    </div>
  );
}
