import { useEffect, useState } from "react";
import { LuRefreshCw, LuServer } from "react-icons/lu";
import { t } from "../i18n";

/** Shown while the server starts. The free online demo server sleeps after 15 idle minutes and
 *  takes about a minute to wake; locally the server is already running and this never appears. */
export default function WakeScreen({ down, onRetry }: { down: boolean; onRetry: () => void }) {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    if (down) return;
    const id = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [down]);
  const pct = Math.min(95, Math.round((secs / 60) * 100));
  return (
    <div className="grid min-h-dvh place-items-center p-6">
      <div className="neu w-full max-w-md rounded-[28px] p-8 text-center" role="status" aria-live="polite">
        <span className={`neu-in mx-auto grid size-16 place-items-center rounded-[22px] ${down ? "text-out" : "text-brand"}`}>
          <LuServer className={`size-7 ${down ? "" : "animate-pulse"}`} />
        </span>
        {down ? (
          <>
            <h1 className="mt-5 text-xl font-bold">{t("The server is not answering")}</h1>
            <p className="mt-2 text-[14px] text-ink-2">{t("Check your internet connection and try again in a minute.")}</p>
            <button onClick={onRetry} className="neu-primary mx-auto mt-6 flex min-h-12 items-center gap-2 rounded-2xl px-6 font-semibold">
              <LuRefreshCw className="size-4" /> {t("Try again")}
            </button>
          </>
        ) : (
          <>
            <h1 className="mt-5 text-xl font-bold">{t("Starting the demo server")}</h1>
            <p className="mt-2 text-[14px] text-ink-2">
              {t("The free server sleeps when nobody is using it. Waking it takes about a minute; after that, pages open at once.")}
            </p>
            <div className="neu-in-sm mt-6 h-2.5 overflow-hidden rounded-full" aria-hidden>
              <span className="block h-full rounded-full bg-brand transition-[width] duration-1000" style={{ width: `${Math.max(6, pct)}%` }} />
            </div>
            <p className="mt-2 text-[12.5px] tabular-nums text-ink-3">{t("{n} seconds", { n: secs })}</p>
          </>
        )}
      </div>
    </div>
  );
}
