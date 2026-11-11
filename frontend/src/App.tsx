import { useCallback, useEffect, useMemo, useState } from "react";
import type { IconType } from "react-icons";
import {
  LuActivity,
  LuBell,
  LuBot,
  LuFileText,
  LuHourglass,
  LuLayoutDashboard,
  LuMenu,
  LuMoon,
  LuLanguages,
  LuStore,
  LuPlus,
  LuChevronDown,
  LuPackage,
  LuReceipt,
  LuReceiptText,
  LuSearch,
  LuShoppingCart,
  LuSparkles,
  LuSun,
  LuWallet,
  LuX,
} from "react-icons/lu";
import { api, waitForServer, type ExpiryAlert, type Shop, type StockRow, type Store } from "./api";
import WakeScreen from "./components/WakeScreen";
import { getLang, setLang, t, type Lang } from "./i18n";
import Landing from "./pages/Landing";
import Setup from "./pages/Setup";
import { AppContext, type Page } from "./components/appContext";
import CommandPalette from "./components/CommandPalette";
import ErrorBoundary from "./components/ErrorBoundary";
import ProductDrawer from "./components/ProductDrawer";
import { daysText, stockStatus } from "./components/ui";
import Assistant from "./pages/Assistant";
import Bills from "./pages/Bills";
import Counter from "./pages/Counter";
import Expiry from "./pages/Expiry";
import Invoices from "./pages/Invoices";
import Overview from "./pages/Overview";
import Restock from "./pages/Restock";
import Signals from "./pages/Signals";
import Stock from "./pages/Stock";
import Udhaar from "./pages/Udhaar";

export type { Page };

type NavItem = { id: Page; label: string; icon: IconType };
const GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "Monitor",
    items: [
      { id: "overview", label: "Overview", icon: LuLayoutDashboard },
      { id: "stock", label: "Inventory", icon: LuPackage },
      { id: "expiry", label: "Expiry", icon: LuHourglass },
    ],
  },
  {
    label: "Sell",
    items: [
      { id: "counter", label: "New bill", icon: LuReceiptText },
      { id: "invoices", label: "Bills & returns", icon: LuFileText },
    ],
  },
  {
    label: "Act",
    items: [
      { id: "restock", label: "Restock", icon: LuShoppingCart },
      { id: "bills", label: "Add from bill", icon: LuReceipt },
      { id: "udhaar", label: "Udhaar", icon: LuWallet },
    ],
  },
  {
    label: "Insight",
    items: [
      { id: "signals", label: "Signals", icon: LuActivity },
      { id: "ask", label: "Ask Stockora", icon: LuSparkles },
    ],
  },
];
const ALL = GROUPS.flatMap((g) => g.items);
const MOBILE: Page[] = ["overview", "counter", "stock", "restock", "ask"];

function firstVisit(): boolean {
  try {
    return !localStorage.getItem("stockora_seen");
  } catch {
    return false;
  }
}

function pageFromHash(): Page {
  const h = window.location.hash.replace("#", "");
  if (h === "welcome" || h === "setup") return h;
  if (h === "today" || h === "") return firstVisit() ? "welcome" : "overview";
  return (ALL.some((n) => n.id === h) ? h : "overview") as Page;
}

function Logo() {
  return (
    <svg viewBox="0 0 40 40" className="size-10" aria-hidden>
      <rect width="40" height="40" rx="12" fill="#0e7f75" />
      <rect width="40" height="20" rx="12" fill="#17a394" opacity=".55" />
      <rect x="9" y="10" width="22" height="4.2" rx="2.1" fill="#fff" />
      <rect x="9" y="17.9" width="15" height="4.2" rx="2.1" fill="#fff" opacity=".85" />
      <rect x="9" y="25.8" width="8" height="4.2" rx="2.1" fill="#ffc9b8" />
    </svg>
  );
}

function readTheme(): "light" | "dark" {
  try {
    const t = localStorage.getItem("stockora_theme");
    if (t === "dark" || t === "light") return t;
  } catch {
    /* storage blocked */
  }
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

type Alert = { key: string; tone: "out" | "exp"; title: string; detail: string; product?: number; page: Page };

export default function App() {
  const [page, setPage] = useState<Page>(pageFromHash);
  const [store, setStore] = useState<Store | null>(null);
  const [server, setServer] = useState<"waiting" | "ready" | "down">("waiting");
  const [slow, setSlow] = useState(false);
  const [stock, setStock] = useState<StockRow[]>([]);
  const [stockLoaded, setStockLoaded] = useState(false);
  const [expiry, setExpiry] = useState<ExpiryAlert[]>([]);
  const [product, setProduct] = useState<number | null>(null);
  const [palette, setPalette] = useState(false);
  const [bell, setBell] = useState(false);
  const [menu, setMenu] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">(readTheme);
  const [lang, setLangState] = useState<Lang>(getLang);
  const [shops, setShops] = useState<Shop[] | null>(null);
  const [shopMenu, setShopMenu] = useState(false);
  const switchLang = () => {
    const next = lang === "en" ? "hi" : "en";
    setLang(next);
    setLangState(next);
  };
  const reloadStore = useCallback(() => {
    api.store().then(setStore).catch(() => setStore(null));
  }, []);
  const openShop = async (id: number) => {
    setShopMenu(false);
    await api.openShop(id);
    reloadStore();
    refreshStock();
    setPage("overview");
    window.location.hash = "overview";
  };

  const refreshStock = useCallback(() => {
    api.stock().then((s) => { setStock(s); setStockLoaded(true); }).catch(() => setStock([]));
    api.expiry().then(setExpiry).catch(() => setExpiry([]));
  }, []);

  useEffect(() => {
    const onHash = () => setPage(pageFromHash());
    window.addEventListener("hashchange", onHash);
    // Wait for the server before loading (the free online server may be asleep); then load the shop.
    const slowTimer = setTimeout(() => setSlow(true), 1500);
    waitForServer()
      .then(() => {
        setServer("ready");
        api.store().then(setStore).catch(() => setStore(null));
        refreshStock();
      })
      .catch(() => setServer("down"))
      .finally(() => clearTimeout(slowTimer));
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((p) => !p);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("keydown", onKey);
    };
  }, [refreshStock]);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("stockora_theme", theme);
    } catch {
      /* storage blocked */
    }
  }, [theme]);

  const go = useCallback((p: Page) => {
    window.location.hash = p;
    setPage(p);
    setMenu(false);
    setBell(false);
    window.scrollTo({ top: 0 });
  }, []);

  const alerts: Alert[] = useMemo(() => {
    const a: Alert[] = [];
    for (const r of stock) {
      if (stockStatus(r.stock, r.days_of_stock) === "out")
        a.push({
          key: `s${r.product_id}`,
          tone: "out",
          title: r.stock <= 0 ? t("{name} is out of stock", { name: r.name }) : t("{name} runs out in {d}", { name: r.name, d: daysText(r.days_of_stock) }),
          detail: t("{n} on hand · sells {r}/day", { n: Math.round(r.stock), r: r.daily_rate.toFixed(1) }),
          product: r.product_id,
          page: "stock",
        });
    }
    for (const e of expiry.filter((x) => x.days_left <= 2))
      a.push({ key: `e${e.batch_id}`, tone: "exp", title: t("{name}: {n} may expire", { name: e.name, n: Math.round(e.leftover) }), detail: `${e.days_left <= 0 ? t("Expired") : t("{n} days left", { n: e.days_left })} · ${t(e.action)}`, product: e.product_id, page: "expiry" });
    return a;
  }, [stock, expiry, lang]);

  const ctx = useMemo(() => ({ go, openProduct: setProduct, stock, refreshStock, stockLoaded }), [go, stock, refreshStock, stockLoaded]);
  const today = new Date().toLocaleDateString(lang === "hi" ? "hi-IN" : "en-IN", { weekday: "long", day: "numeric", month: "long" });
  const current = ALL.find((n) => n.id === page) ?? ALL[0];

  if (page === "welcome") {
    return (
      <Landing
        lang={lang}
        onLang={switchLang}
        theme={theme}
        onTheme={() => setTheme(theme === "dark" ? "light" : "dark")}
        onStart={(target) => {
          try {
            localStorage.setItem("stockora_seen", "1");
          } catch {
            /* storage blocked */
          }
          go(target);
        }}
      />
    );
  }

  const Nav = ({ onPick }: { onPick: (p: Page) => void }) => (
    <nav aria-label="Main" className="space-y-6">
      {GROUPS.map((g) => (
        <div key={g.label}>
          <p className="px-3 pb-2 text-[12px] font-semibold text-ink-3">{t(g.label)}</p>
          <div className="space-y-1.5">
            {g.items.map((n) => (
              <button
                key={n.id}
                onClick={() => onPick(n.id)}
                aria-current={page === n.id ? "page" : undefined}
                className={`flex min-h-11 w-full items-center gap-3 rounded-2xl px-3 text-left font-semibold transition-colors ${
                  page === n.id ? "neu-in-sm text-brand" : "text-ink-2 hover:text-ink"
                }`}
              >
                <n.icon className="size-[19px]" />
                {t(n.label)}
                {n.id === "expiry" && expiry.length > 0 && (
                  <span className="ml-auto rounded-full bg-exp-soft px-2 text-[11.5px] font-bold text-exp">{expiry.length}</span>
                )}
              </button>
            ))}
          </div>
        </div>
      ))}
    </nav>
  );

  if (server !== "ready") {
    // Brief blank while a running server answers; the wake-up screen only if it takes longer
    return server === "down" || slow ? <WakeScreen down={server === "down"} onRetry={() => window.location.reload()} /> : <div className="min-h-dvh" />;
  }

  return (
    <AppContext.Provider value={ctx}>
      <div className="min-h-dvh lg:grid lg:grid-cols-[17.5rem_1fr]">
        {/* desktop sidebar */}
        <aside className="hidden p-5 pr-0 lg:block">
          <div className="neu sticky top-5 flex h-[calc(100dvh-2.5rem)] flex-col rounded-[28px] p-4">
            <div className="flex items-center gap-3 px-2 pb-5 pt-2">
              <Logo />
              <div>
                <p className="text-[19px] font-extrabold leading-none tracking-[-0.02em]">Stockora</p>
                <p className="mt-1 text-[11.5px] font-medium text-ink-3">{t("Stock intelligence")}</p>
              </div>
            </div>
            {store && (
              <div className="relative mb-6">
                <button
                  onClick={() => {
                    setShopMenu(!shopMenu);
                    api.shops().then(setShops).catch(() => setShops([]));
                  }}
                  aria-expanded={shopMenu}
                  className="neu-in-sm flex w-full items-center gap-2 rounded-2xl px-3.5 py-3 text-left"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13.5px] font-bold">{store.name}</span>
                    <span className="block text-[12px] text-ink-3">{store.is_demo ? t("Demo shop · sample data") : store.city}</span>
                  </span>
                  <LuChevronDown className="size-4 text-ink-3" />
                </button>
                {shopMenu && (
                  <div className="neu rise absolute inset-x-0 top-[calc(100%+8px)] z-40 rounded-2xl p-2">
                    {(shops ?? []).map((s) => (
                      <button key={s.id} onClick={() => openShop(s.id)} className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-[13.5px] hover:bg-line/40 ${s.active ? "font-bold text-brand" : ""}`}>
                        <LuStore className="size-4 shrink-0" />
                        <span className="truncate">{s.name}</span>
                        {s.is_demo && <span className="ml-auto text-[11px] text-ink-3">{t("demo")}</span>}
                      </button>
                    ))}
                    <button onClick={() => { setShopMenu(false); go("setup"); }} className="mt-1 flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-[13.5px] font-semibold text-brand hover:bg-line/40">
                      <LuPlus className="size-4" /> {t("Set up a new shop")}
                    </button>
                  </div>
                )}
              </div>
            )}
            <div className="min-h-0 flex-1 overflow-y-auto pr-1">
              <Nav onPick={go} />
            </div>
            <div className="neu-in-sm mt-4 flex items-center gap-3 rounded-2xl px-3.5 py-3">
              <span className={`grid size-8 shrink-0 place-items-center rounded-xl ${store?.llm_enabled ? "text-ok" : "text-ink-3"}`}>
                <LuBot className="size-[18px]" />
              </span>
              <p className="text-[12px] leading-snug text-ink-2">
                {store?.llm_enabled ? (
                  <>
                    {t("AI connected")}
                    <span className="block truncate text-ink-3">{store.llm_model ?? "Groq"}</span>
                  </>
                ) : (
                  <>
                    {t("AI offline")}
                    <span className="block text-ink-3">{t("Add GROQ_API_KEY in backend/.env")}</span>
                  </>
                )}
              </p>
            </div>
          </div>
        </aside>

        <div className="min-w-0">
          {/* top bar */}
          <header className="sticky top-0 z-30 bg-base/85 px-4 pb-3 pt-4 backdrop-blur-md sm:px-6 lg:px-9 lg:pt-5">
            <div className="flex items-center gap-3">
              <button className="neu-btn grid size-11 place-items-center rounded-2xl text-ink-2 lg:hidden" aria-label={t("Menu")} onClick={() => setMenu(true)}>
                <LuMenu className="size-5" />
              </button>
              <div className="flex min-w-0 items-center gap-2 lg:hidden">
                <Logo />
              </div>
              <button
                onClick={() => setPalette(true)}
                className="neu-in-sm hidden min-h-11 flex-1 items-center gap-3 rounded-2xl px-4 text-left text-ink-3 sm:flex lg:max-w-md"
              >
                <LuSearch className="size-[18px]" />
                <span className="flex-1">{t("Search products, pages…")}</span>
                <kbd className="neu-sm rounded-lg px-2 py-0.5 text-[11.5px] font-semibold text-ink-2">Ctrl K</kbd>
              </button>
              <p className="ml-auto hidden text-[13px] font-medium text-ink-2 xl:block">{today}</p>
              <div className="ml-auto flex items-center gap-2.5 xl:ml-4">
                <button className="neu-btn grid size-11 place-items-center rounded-2xl text-ink-2 sm:hidden" aria-label={t("Search")} onClick={() => setPalette(true)}>
                  <LuSearch className="size-[19px]" />
                </button>
                <div className="relative">
                  <button
                    onClick={() => setBell(!bell)}
                    aria-label={`Alerts: ${alerts.length}`}
                    aria-expanded={bell}
                    className="neu-btn relative grid size-11 place-items-center rounded-2xl text-ink-2 hover:text-ink"
                  >
                    <LuBell className="size-[19px]" />
                    {alerts.length > 0 && (
                      <span className="absolute -right-1 -top-1 grid min-w-5 place-items-center rounded-full bg-out px-1 text-[11px] font-bold text-white">
                        {alerts.length > 99 ? "99+" : alerts.length}
                      </span>
                    )}
                  </button>
                  {bell && (
                    <div className="neu rise absolute right-0 top-14 z-40 w-[min(24rem,calc(100vw-2rem))] rounded-[22px] p-3">
                      <p className="px-2 pb-2 pt-1 text-[13px] font-bold">{t("Needs attention ({n})", { n: alerts.length })}</p>
                      <ul className="max-h-[60vh] space-y-1 overflow-y-auto">
                        {alerts.length === 0 && <li className="px-2 py-4 text-ink-2">{t("All clear. Nothing runs out or expires soon.")}</li>}
                        {alerts.slice(0, 30).map((a) => (
                          <li key={a.key}>
                            <button
                              onClick={() => {
                                setBell(false);
                                if (a.product) setProduct(a.product);
                                else go(a.page);
                              }}
                              className="flex w-full gap-3 rounded-2xl px-2 py-2 text-left hover:bg-line/40"
                            >
                              <span className={`mt-1.5 size-2 shrink-0 rounded-full ${a.tone === "out" ? "bg-out" : "bg-exp"}`} />
                              <span className="min-w-0">
                                <span className="block text-[13.5px] font-semibold leading-snug">{a.title}</span>
                                <span className="block truncate text-[12px] text-ink-3">{a.detail}</span>
                              </span>
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
                <button
                  onClick={switchLang}
                  aria-label={lang === "en" ? "हिन्दी में देखें" : "View in English"}
                  title={lang === "en" ? "हिन्दी" : "English"}
                  className="neu-btn flex h-11 min-w-11 items-center justify-center gap-1.5 rounded-2xl px-3 text-[13px] font-bold text-ink-2 hover:text-ink"
                >
                  <LuLanguages className="size-[17px]" />
                  {lang === "en" ? "हिं" : "EN"}
                </button>
                <button
                  onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
                  aria-label={theme === "dark" ? t("Switch to light theme") : t("Switch to dark theme")}
                  className="neu-btn grid size-11 place-items-center rounded-2xl text-ink-2 hover:text-ink"
                >
                  {theme === "dark" ? <LuSun className="size-[19px]" /> : <LuMoon className="size-[19px]" />}
                </button>
              </div>
            </div>
          </header>

          <main key={lang} className="mx-auto w-full max-w-[1320px] px-4 pb-32 pt-4 sm:px-6 lg:px-9 lg:pb-16" aria-label={t(page === "setup" ? "Set up your shop" : current.label)}>
            {store?.is_demo && page !== "setup" && (
              <div className="neu-in-sm mb-6 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-2xl px-4 py-3 text-[13.5px]">
                <span className="text-ink-2">{t("You are looking at a demo shop. All sales in it are sample data, not a real shop.")}</span>
                <button onClick={() => go("setup")} className="ml-auto font-bold text-brand hover:underline">{t("Set up your own shop")} →</button>
              </div>
            )}
            <ErrorBoundary resetKey={page}>
              {page === "setup" && <Setup onDone={() => { reloadStore(); refreshStock(); go("overview"); }} onOpenShop={openShop} onShopCreated={() => { reloadStore(); refreshStock(); }} />}
              {page === "overview" && <Overview />}
              {page === "stock" && <Stock />}
              {page === "expiry" && <Expiry />}
              {page === "counter" && <Counter />}
              {page === "invoices" && <Invoices />}
              {page === "restock" && <Restock />}
              {page === "bills" && <Bills llmEnabled={store?.llm_enabled ?? false} />}
              {page === "udhaar" && <Udhaar />}
              {page === "signals" && <Signals />}
              {page === "ask" && <Assistant store={store} />}
            </ErrorBoundary>
          </main>
        </div>

        {/* phone bottom bar */}
        <nav
          aria-label="Main"
          className="neu fixed inset-x-3 bottom-3 z-30 grid grid-cols-5 rounded-[22px] px-1 py-1.5 lg:hidden"
          style={{ marginBottom: "env(safe-area-inset-bottom, 0px)" }}
        >
          {MOBILE.map((id) => {
            const n = ALL.find((x) => x.id === id)!;
            const active = page === id;
            return (
              <button
                key={id}
                onClick={() => go(id)}
                aria-current={active ? "page" : undefined}
                className={`mx-0.5 flex min-h-13 flex-col items-center justify-center gap-0.5 rounded-2xl text-[11px] font-semibold ${active ? "neu-in-sm text-brand" : "text-ink-3"}`}
              >
                <n.icon className="size-5" />
                {t(id === "ask" ? "Ask" : id === "stock" ? "Stock" : id === "counter" ? "Bill" : n.label)}
              </button>
            );
          })}
        </nav>

        {/* phone drawer menu */}
        {menu && (
          <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label={t("Menu")}>
            <button className="absolute inset-0 bg-ink/25" aria-label={t("Close menu")} onClick={() => setMenu(false)} />
            <div className="slide-in relative h-full w-[min(19rem,85vw)] overflow-y-auto bg-base p-5 shadow-[12px_0_32px_var(--sh-dark)]">
              <div className="mb-6 flex items-center justify-between">
                <span className="flex items-center gap-3">
                  <Logo />
                  <span className="text-lg font-extrabold">Stockora</span>
                </span>
                <button className="neu-btn grid size-10 place-items-center rounded-xl" aria-label={t("Close menu")} onClick={() => setMenu(false)}>
                  <LuX className="size-5" />
                </button>
              </div>
              <Nav onPick={go} />
              <button onClick={() => go("setup")} className="mt-6 flex min-h-11 w-full items-center gap-3 rounded-2xl px-3 font-semibold text-brand">
                <LuPlus className="size-[19px]" /> {t("Set up a new shop")}
              </button>
              <button onClick={() => go("welcome")} className="flex min-h-11 w-full items-center gap-3 rounded-2xl px-3 font-semibold text-ink-2">
                <LuStore className="size-[19px]" /> {t("About Stockora")}
              </button>
            </div>
          </div>
        )}

        {palette && (
          <CommandPalette
            pages={ALL}
            products={stock}
            onPage={(id) => go(id as Page)}
            onProduct={(id) => setProduct(id)}
            onClose={() => setPalette(false)}
          />
        )}
        {product !== null && <ProductDrawer id={product} onClose={() => setProduct(null)} onChanged={refreshStock} />}
      </div>
    </AppContext.Provider>
  );
}
