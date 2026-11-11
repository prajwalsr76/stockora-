import type { IconType } from "react-icons";
import {
  LuArrowRight,
  LuBell,
  LuChartSpline,
  LuHourglass,
  LuLanguages,
  LuMessageCircle,
  LuMoon,
  LuReceiptText,
  LuShieldCheck,
  LuShoppingCart,
  LuSparkles,
  LuSun,
  LuUpload,
  LuWallet,
} from "react-icons/lu";
import type { Page } from "../components/appContext";
import { Ring } from "../components/ui";
import { t, type Lang } from "../i18n";

function Logo({ size = 40 }: { size?: number }) {
  return (
    <svg viewBox="0 0 40 40" width={size} height={size} aria-hidden>
      <rect width="40" height="40" rx="12" fill="#0e7f75" />
      <rect width="40" height="20" rx="12" fill="#17a394" opacity=".55" />
      <rect x="9" y="10" width="22" height="4.2" rx="2.1" fill="#fff" />
      <rect x="9" y="17.9" width="15" height="4.2" rx="2.1" fill="#fff" opacity=".85" />
      <rect x="9" y="25.8" width="8" height="4.2" rx="2.1" fill="#ffc9b8" />
    </svg>
  );
}

/** A still picture of the app, drawn with the app's own components and clearly marked as sample data. */
function Preview() {
  const rows: [string, number, string, string][] = [
    ["Amul Taaza Toned Milk 500ml", 8, "var(--out)", t("Tomorrow")],
    ["Parle-G Glucose Biscuits 250g", 30, "var(--exp)", t("in {n} days", { n: 4 })],
    ["Tata Salt 1kg", 64, "var(--ok)", t("in {n} days", { n: 9 })],
  ];
  return (
    <div className="relative sm:pb-14 sm:pt-24" aria-hidden>
      <div className="neu float-slow relative rounded-[28px] p-5 sm:p-6 sm:pb-16">
        <div className="mb-4 flex items-center justify-between">
          <p className="text-[13px] font-bold">{t("Runs out first")}</p>
          <span className="rounded-full bg-info-soft px-2.5 py-0.5 text-[11.5px] font-semibold text-info">{t("sample data")}</span>
        </div>
        <ul className="space-y-3.5">
          {rows.map(([name, pct, color, when], i) => (
            <li key={name} className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1.5">
              <span className="truncate text-[13px] font-semibold">{name}</span>
              <span className="text-right text-[12.5px] font-bold" style={{ color }}>{when}</span>
              <span className="neu-in-sm col-span-2 h-2.5 overflow-hidden rounded-full">
                <span className="grow-x block h-full rounded-full" style={{ width: `${pct}%`, background: color, animationDelay: `${200 + i * 120}ms` }} />
              </span>
            </li>
          ))}
        </ul>
      </div>
      <div className="neu float-slower absolute bottom-0 -left-4 hidden w-56 items-center gap-4 rounded-[24px] p-4 sm:flex lg:-left-12">
        <Ring value={0.72} size={84} stroke={9} color="var(--ok)">
          <span className="text-[17px] font-extrabold">72%</span>
        </Ring>
        <p className="text-[12.5px] font-semibold leading-snug text-ink-2">{t("of items have a week of stock or more")}</p>
      </div>
      <div className="neu float-slow absolute -right-3 top-0 hidden w-60 rounded-[22px] p-4 sm:block lg:-right-10">
        <p className="flex items-center gap-2 text-[12.5px] font-bold text-exp"><LuHourglass className="size-4" /> {t("May expire in 2 days")}</p>
        <p className="mt-1 text-[13px] font-semibold">Mother Dairy Dahi 400g</p>
        <p className="text-[12px] text-ink-3">{t("12 of 30 won't sell in time · move to the counter")}</p>
      </div>
    </div>
  );
}

function Feature({ icon: Icon, title, text, tone }: { icon: IconType; title: string; text: string; tone: string }) {
  return (
    <div className="neu lift rounded-[24px] p-6">
      <span className={`neu-in grid size-12 place-items-center rounded-2xl ${tone}`}>
        <Icon className="size-[22px]" />
      </span>
      <h3 className="mt-4 text-[16px] font-bold">{title}</h3>
      <p className="mt-1.5 text-[13.5px] leading-relaxed text-ink-2">{text}</p>
    </div>
  );
}

export default function Landing({ lang, onLang, theme, onTheme, onStart }: {
  lang: Lang; onLang: () => void; theme: "light" | "dark"; onTheme: () => void; onStart: (p: Page) => void;
}) {
  const results: [string, string, number, number][] = [
    [t("Supermarkets, Ecuador"), "Favorita", 91.5, 90.3],
    [t("Fresh food online, Czechia & Hungary"), "Rohlik", 84.5, 81.4],
    [t("Single items, USA"), "Walmart M5", 61.0, 59.9],
  ];
  return (
    <div className="min-h-dvh overflow-x-hidden">
      <header className="mx-auto flex max-w-6xl items-center gap-3 px-5 py-5 sm:px-8">
        <Logo />
        <span className="text-[20px] font-extrabold tracking-[-0.02em]">Stockora</span>
        <div className="ml-auto flex items-center gap-2.5">
          <button onClick={onLang} className="neu-btn flex h-11 items-center gap-1.5 rounded-2xl px-3 text-[13px] font-bold text-ink-2" aria-label={lang === "en" ? "हिन्दी में देखें" : "View in English"}>
            <LuLanguages className="size-[17px]" /> {lang === "en" ? "हिं" : "EN"}
          </button>
          <button onClick={onTheme} className="neu-btn grid size-11 place-items-center rounded-2xl text-ink-2" aria-label={theme === "dark" ? t("Switch to light theme") : t("Switch to dark theme")}>
            {theme === "dark" ? <LuSun className="size-[19px]" /> : <LuMoon className="size-[19px]" />}
          </button>
          <button onClick={() => onStart("overview")} className="neu-primary hidden min-h-11 items-center gap-2 rounded-2xl px-5 font-semibold sm:inline-flex">
            {t("Open the demo")}
          </button>
        </div>
      </header>

      <section className="mx-auto grid max-w-6xl items-center gap-14 px-5 pb-20 pt-8 sm:px-8 lg:grid-cols-[1.05fr_1fr] lg:pt-16">
        <div className="rise">
          <p className="neu-in-sm inline-flex items-center gap-2 rounded-full px-3.5 py-1.5 text-[12.5px] font-semibold text-brand">
            <LuSparkles className="size-4" /> {t("For kirana and small grocery shops")}
          </p>
          <h1 className="mt-5 text-[2.35rem] font-extrabold leading-[1.08] tracking-[-0.03em] sm:text-[3.1rem]">
            {t("Know what will run out")}<br />
            <span className="bg-gradient-to-r from-brand to-info bg-clip-text text-transparent">{t("and what will expire")}</span>
            <br />{t("before it happens.")}
          </h1>
          <p className="mt-5 max-w-xl text-[16px] leading-relaxed text-ink-2">
            {t("Stockora forecasts every item's sales, shows the date each one runs out, warns about batches that won't sell before expiry, and makes the restock list. Bill at the counter, ask questions in Hindi or English, keep udhaar in one place.")}
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <button onClick={() => onStart("overview")} className="neu-primary inline-flex min-h-13 items-center gap-2 rounded-2xl px-6 text-[15.5px] font-bold">
              {t("Try the demo shop")} <LuArrowRight className="size-5" />
            </button>
            <button onClick={() => onStart("setup")} className="neu-btn inline-flex min-h-13 items-center gap-2 rounded-2xl px-6 text-[15.5px] font-bold">
              {t("Set up my shop")}
            </button>
          </div>
          <p className="mt-4 text-[12.5px] text-ink-3">{t("Free and open source. Works on a phone. Every decision stays with the owner.")}</p>
        </div>
        <div className="rise px-4 pb-10 sm:px-10 lg:px-0" style={{ animationDelay: "120ms" }}>
          <Preview />
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-5 pb-20 sm:px-8">
        <h2 className="text-center text-[1.7rem] font-bold tracking-[-0.02em]">{t("Everything the counter needs, in one place")}</h2>
        <div className="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          <Feature icon={LuBell} tone="text-out" title={t("Run-out dates, not guesses")} text={t("Each item's sales are forecast day by day, with festivals and weekdays, so you see the exact day it runs out.")} />
          <Feature icon={LuHourglass} tone="text-exp" title={t("Expiry before it happens")} text={t("Batches are sold earliest-expiry first. Stockora flags what won't sell in time and suggests what to do.")} />
          <Feature icon={LuShoppingCart} tone="text-brand" title={t("A restock list you approve")} text={t("Quantities sized to demand until the next delivery, capped by shelf life. You edit and approve; it learns from your edits.")} />
          <Feature icon={LuReceiptText} tone="text-info" title={t("GST bills at the counter")} text={t("Scan or search, loose items by weight, cash, UPI or udhaar. A4 and thermal bills, shared on WhatsApp. Stock updates at once.")} />
          <Feature icon={LuMessageCircle} tone="text-ok" title={t("Ask in Hindi or English")} text={t("\"What should I order today?\" Answers come from your shop's data, and every number is checked before you see it.")} />
          <Feature icon={LuWallet} tone="text-exp" title={t("Udhaar that gets collected")} text={t("Dues by customer with the oldest first, and a polite reminder ready to send.")} />
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-5 pb-20 sm:px-8">
        <h2 className="text-center text-[1.7rem] font-bold tracking-[-0.02em]">{t("Start in three steps")}</h2>
        <ol className="mt-10 grid gap-6 lg:grid-cols-3">
          {[
            [LuUpload, t("Bring your items"), t("Import the item list and past sales from Vyapar, myBillBook, Tally or Excel, or tick common items and count the shelf.")],
            [LuReceiptText, t("Bill as usual"), t("Every bill takes stock out batch by batch, so the numbers stay true without extra work.")],
            [LuChartSpline, t("Act on the morning list"), t("Open the app each morning: what runs out, what may expire, what to order, whom to remind.")],
          ].map(([Icon, title, text], i) => {
            const I = Icon as IconType;
            return (
              <li key={i} className="neu-in rounded-[24px] p-6">
                <span className="flex items-center gap-3">
                  <span className="grid size-9 place-items-center rounded-full bg-brand text-[15px] font-bold text-white">{i + 1}</span>
                  <I className="size-5 text-brand" />
                </span>
                <h3 className="mt-4 text-[16px] font-bold">{title as string}</h3>
                <p className="mt-1.5 text-[13.5px] leading-relaxed text-ink-2">{text as string}</p>
              </li>
            );
          })}
        </ol>
      </section>

      <section className="mx-auto max-w-6xl px-5 pb-20 sm:px-8">
        <div className="neu rounded-[28px] p-6 sm:p-10">
          <div className="flex flex-wrap items-start gap-4">
            <span className="neu-in grid size-12 place-items-center rounded-2xl text-brand"><LuShieldCheck className="size-6" /></span>
            <div className="max-w-2xl">
              <h2 className="text-[1.5rem] font-bold tracking-[-0.02em]">{t("Measured honestly")}</h2>
              <p className="mt-1.5 text-[14px] text-ink-2">
                {t("The forecaster was chosen from 25 candidates and then tested on later weeks it had never seen, on three public datasets. Weekly accuracy (1 − WAPE):")}
              </p>
            </div>
          </div>
          <div className="mt-7 grid gap-5 md:grid-cols-3">
            {results.map(([what, name, acc, base]) => (
              <div key={name} className="neu-in rounded-[22px] p-5">
                <p className="text-[12.5px] font-semibold text-ink-3">{name}</p>
                <p className="text-[13.5px] font-semibold">{what}</p>
                <p className="mt-3 text-[2.2rem] font-extrabold leading-none tabular-nums text-brand">{acc.toFixed(1)}%</p>
                <p className="mt-1 text-[12.5px] text-ink-3">{t("best simple method: {n}%", { n: base.toFixed(1) })}</p>
                <div className="neu-in-sm mt-4 h-2.5 overflow-hidden rounded-full">
                  <span className="grow-x block h-full rounded-full bg-brand" style={{ width: `${acc}%` }} />
                </div>
              </div>
            ))}
          </div>
          <p className="mt-6 rounded-2xl bg-exp-soft px-4 py-3 text-[13.5px] font-semibold text-exp">
            {t("Not yet validated on Indian data. None of these datasets is from India; a real-shop pilot is planned once shop data can be collected.")}
          </p>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-5 pb-16 text-center sm:px-8">
        <h2 className="text-[1.7rem] font-bold tracking-[-0.02em]">{t("See it with a sample shop")}</h2>
        <p className="mx-auto mt-2 max-w-xl text-ink-2">{t("The demo shop has 94 real Indian products and a year of made-up sales, so every screen has something to show.")}</p>
        <button onClick={() => onStart("overview")} className="neu-primary mt-7 inline-flex min-h-13 items-center gap-2 rounded-2xl px-7 text-[15.5px] font-bold">
          {t("Open the demo")} <LuArrowRight className="size-5" />
        </button>
      </section>

      <footer className="border-t border-line py-8 text-center text-[12.5px] text-ink-3">
        {t("Stockora · a student project · forecasts tested on Favorita, Walmart M5 and Rohlik public data")}
      </footer>
    </div>
  );
}
