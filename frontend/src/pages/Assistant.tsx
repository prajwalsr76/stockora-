import { useEffect, useRef, useState, type FormEvent } from "react";
import { LuArrowUpRight, LuBot, LuCheck, LuCopy, LuMic, LuSend, LuShieldCheck, LuSparkles, LuTrash2, LuTriangleAlert } from "react-icons/lu";
import { api, type ChatTurn, type Lang, type Reply, type Store } from "../api";
import { useApp } from "../components/appContext";
import { Chip, PageHeader, Segments } from "../components/ui";
import { getLang, t as tr } from "../i18n";

type Turn = { q: string; r?: Reply; error?: string };

const TEXT = {
  en: {
    hello: "Ask about your shop",
    sub: "Stock, run-out dates, forecasts, expiry, restocking and udhaar. Every number is checked against your own data.",
    placeholder: "Ask anything about your stock…",
    thinking: "Checking your data",
    suggestions: [
      "What will run out this week?",
      "What is about to expire?",
      "What should I restock today?",
      "How many Maggi are left?",
      "How much will Parle-G sell next week?",
      "How much udhaar is due?",
    ],
  },
  hi: {
    hello: "अपनी दुकान के बारे में पूछें",
    sub: "स्टॉक, खत्म होने की तारीख, अनुमान, एक्सपायरी, रीस्टॉक और उधार। हर संख्या आपके अपने डेटा से जाँची जाती है।",
    placeholder: "अपने स्टॉक के बारे में कुछ भी पूछें…",
    thinking: "आपका डेटा देखा जा रहा है",
    suggestions: [
      "इस हफ्ते कौन सा सामान खत्म होगा?",
      "क्या एक्सपायर होने वाला है?",
      "आज क्या मंगाना चाहिए?",
      "मैगी कितनी बची है?",
      "अगले हफ्ते पारले-जी कितना बिकेगा?",
      "कुल उधार कितना बाकी है?",
    ],
  },
} as const;

type SpeechRec = {
  lang: string;
  interimResults: boolean;
  start: () => void;
  onresult: ((e: { results: { 0: { transcript: string } }[] }) => void) | null;
  onend: (() => void) | null;
};
function getRecognizer(lang: Lang): SpeechRec | null {
  const w = window as unknown as { SpeechRecognition?: new () => SpeechRec; webkitSpeechRecognition?: new () => SpeechRec };
  const Ctor = w.SpeechRecognition ?? w.webkitSpeechRecognition;
  if (!Ctor) return null;
  const r = new Ctor();
  r.lang = lang === "hi" ? "hi-IN" : "en-IN";
  r.interimResults = false;
  return r;
}

// Replies start in the app's language; the switch below changes only the reply language.
const readLang = (): Lang => getLang();

export default function Assistant({ store }: { store: Store | null }) {
  const { stock, openProduct } = useApp();
  const [lang, setLang] = useState<Lang>(readLang);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [copied, setCopied] = useState<number | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const t = TEXT[lang];
  const canSpeak = typeof window !== "undefined" && getRecognizer(lang) !== null;


  useEffect(() => {
    // Block body on purpose: scrollIntoView can return a Promise in newer browsers.
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  const ask = async (q: string) => {
    const question = q.trim();
    if (!question || busy) return;
    const history: ChatTurn[] = turns.flatMap((x) => (x.r ? [{ role: "user" as const, content: x.q }, { role: "assistant" as const, content: x.r.answer }] : []));
    setText("");
    setBusy(true);
    setTurns((ts) => [...ts, { q: question }]);
    try {
      const r = await api.ask(question, lang, history);
      setTurns((ts) => ts.map((x, i) => (i === ts.length - 1 ? { ...x, r } : x)));
    } catch (e) {
      setTurns((ts) => ts.map((x, i) => (i === ts.length - 1 ? { ...x, error: (e as Error).message } : x)));
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void ask(text);
  };

  const speak = () => {
    const rec = getRecognizer(lang);
    if (!rec) return;
    setListening(true);
    rec.onresult = (e) => void ask(e.results[0][0].transcript);
    rec.onend = () => setListening(false);
    rec.start();
  };

  const copy = async (i: number, s: string) => {
    try {
      await navigator.clipboard.writeText(s);
      setCopied(i);
    } catch {
      setCopied(null);
    }
  };

  const productId = (name: string | null) => (name ? stock.find((r) => r.name === name)?.product_id : undefined);
  const tools = (r: Reply) => r.flags.find((f) => f.startsWith("tools:"))?.slice(6).split(",") ?? [];

  return (
    <div className="mx-auto flex min-h-[calc(100dvh-12rem)] max-w-4xl flex-col">
      <PageHeader icon={LuSparkles} title={tr("Ask Stockora")} sub={store?.llm_enabled ? tr("AI assistant · {m}", { m: store.llm_model ?? "Groq" }) : tr("Offline answers · add a Groq key for the full AI assistant")}>
        <Segments label={tr("Reply language")} value={lang} onChange={setLang} options={[{ id: "en", label: "English" }, { id: "hi", label: <span className="font-hindi">हिन्दी</span> }]} />
        {turns.length > 0 && (
          <button onClick={() => setTurns([])} className="neu-btn grid size-11 place-items-center rounded-2xl text-ink-2" aria-label={tr("Clear chat")} title={tr("Clear chat")}>
            <LuTrash2 className="size-[18px]" />
          </button>
        )}
      </PageHeader>

      <div className={`flex-1 space-y-6 ${lang === "hi" ? "font-hindi" : ""}`} aria-live="polite">
        {turns.length === 0 && (
          <div className="neu flex flex-col items-center rounded-[28px] px-6 py-10 text-center">
            <span className="neu-in grid size-20 place-items-center rounded-[26px] text-brand">
              <LuBot className="size-9" />
            </span>
            <h2 className="mt-5 text-xl font-bold">{t.hello}</h2>
            <p className="mt-1 max-w-md text-[13.5px] text-ink-2">{t.sub}</p>
            <div className="mt-7 grid w-full gap-3 sm:grid-cols-2">
              {t.suggestions.map((s) => (
                <button key={s} onClick={() => void ask(s)} className="neu-btn flex items-center justify-between gap-3 rounded-2xl px-4 py-3.5 text-left text-[13.5px] font-semibold">
                  {s}
                  <LuArrowUpRight className="size-4 shrink-0 text-ink-3" />
                </button>
              ))}
            </div>
            {!store?.llm_enabled && (
              <p className="mt-6 flex max-w-lg items-start gap-2 text-left text-[12.5px] text-ink-3">
                <LuTriangleAlert className="mt-0.5 size-4 shrink-0" />
                <span>
                  {tr("Without a Groq key, answers come from fixed templates.")} {tr("Add")} <code>GROQ_API_KEY</code> {tr("in")} <code>backend/.env</code> {tr("for free-form questions and follow-ups.")}
                </span>
              </p>
            )}
          </div>
        )}

        {turns.map((x, i) => (
          <div key={i} className="rise space-y-3">
            <div className="flex justify-end">
              <p className="max-w-[85%] rounded-[20px] rounded-br-md bg-gradient-to-br from-brand to-brand-2 px-4 py-2.5 text-white shadow-[4px_4px_10px_var(--sh-dark)]">{x.q}</p>
            </div>
            {x.r && (
              <div className="flex gap-3">
                <span className="neu-sm mt-1 grid size-9 shrink-0 place-items-center rounded-xl text-brand">
                  <LuBot className="size-[18px]" />
                </span>
                <div className="neu min-w-0 max-w-[88%] rounded-[20px] rounded-tl-md px-5 py-4">
                  <p className="whitespace-pre-line leading-relaxed">{x.r.answer}</p>
                  <div className="mt-3 flex flex-wrap items-center gap-1.5">
                    {x.r.grounded && x.r.intent !== "help" && x.r.intent !== "refused" && (
                      <Chip t="ok" icon={LuShieldCheck}>{tr("Checked against your data")}</Chip>
                    )}
                    {!x.r.grounded && <Chip t="exp">{tr("AI answer had unchecked numbers; showing the checked answer")}</Chip>}
                    {x.r.intent === "refused" && <Chip t="out">{tr("Not a shop question")}</Chip>}
                    {x.r.used_llm && <Chip t="info" icon={LuSparkles}>AI</Chip>}
                    {tools(x.r).map((tool) => (
                      <Chip key={tool}>{tool.replace(/_/g, " ")}</Chip>
                    ))}
                    {productId(x.r.product) !== undefined && (
                      <button onClick={() => openProduct(productId(x.r!.product)!)} className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[12.5px] font-semibold text-brand hover:underline">
                        View {x.r.product} <LuArrowUpRight className="size-3.5" />
                      </button>
                    )}
                    <button onClick={() => copy(i, x.r!.answer)} className="ml-auto grid size-8 place-items-center rounded-lg text-ink-3 hover:text-ink" aria-label={tr("Copy answer")}>
                      {copied === i ? <LuCheck className="size-4" /> : <LuCopy className="size-4" />}
                    </button>
                  </div>
                </div>
              </div>
            )}
            {x.error && <p className="pl-12 text-[13px] text-out">{x.error}</p>}
            {!x.r && !x.error && (
              <div className="flex items-center gap-3 pl-1">
                <span className="neu-sm grid size-9 place-items-center rounded-xl text-brand">
                  <LuBot className="size-[18px]" />
                </span>
                <span className="neu-in-sm flex items-center gap-2 rounded-2xl px-4 py-2.5 text-[13px] text-ink-2">
                  {t.thinking}
                  <span className="dot-blink flex gap-1">
                    <span className="size-1.5 rounded-full bg-brand" />
                    <span className="size-1.5 rounded-full bg-brand" />
                    <span className="size-1.5 rounded-full bg-brand" />
                  </span>
                </span>
              </div>
            )}
          </div>
        ))}
        <div ref={end} />
      </div>

      <form onSubmit={submit} className="sticky bottom-24 mt-8 lg:bottom-6">
        <div className="neu flex items-center gap-2 rounded-[22px] p-2">
          <label className="min-w-0 flex-1">
            <span className="sr-only">{tr("Your question")}</span>
            <input
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={t.placeholder}
              maxLength={500}
              className={`neu-in-sm min-h-12 w-full rounded-2xl px-4 placeholder:text-ink-3 focus:outline-none ${lang === "hi" ? "font-hindi" : ""}`}
            />
          </label>
          {canSpeak && (
            <button
              type="button"
              onClick={speak}
              aria-label={tr("Ask by voice")}
              className={`grid size-12 shrink-0 place-items-center rounded-2xl ${listening ? "bg-out text-white" : "neu-btn text-ink-2"}`}
            >
              <LuMic className="size-5" />
            </button>
          )}
          <button type="submit" disabled={busy || !text.trim()} aria-label={tr("Send")} className="neu-primary grid size-12 shrink-0 place-items-center rounded-2xl disabled:opacity-45">
            <LuSend className="size-5" />
          </button>
        </div>
      </form>
    </div>
  );
}
