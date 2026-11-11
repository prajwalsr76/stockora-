import { useState } from "react";
import { LuCamera, LuCircleCheck, LuReceipt, LuScanLine, LuTriangleAlert } from "react-icons/lu";
import { api, type Bill, type Issue, type Match } from "../api";
import { Button, Card, Chip, ErrorNote, PageHeader } from "../components/ui";
import { t } from "../i18n";

type Review = { bill: Bill; issues: Issue[]; matches: Match[] };

export default function Bills({ llmEnabled }: { llmEnabled: boolean }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [posted, setPosted] = useState<number | null>(null);
  const [drag, setDrag] = useState(false);

  const pick = (f: File | null) => {
    setFile(f);
    setReview(null);
    setPosted(null);
    setError(null);
    setPreview(f ? URL.createObjectURL(f) : null);
  };

  const read = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      setReview(await api.extractBill(file));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const choose = (line: number, productId: number) => {
    if (!review) return;
    setReview({ ...review, matches: review.matches.map((m) => (m.line === line ? { ...m, product_id: productId } : m)) });
  };

  const post = async () => {
    if (!review) return;
    setBusy(true);
    setError(null);
    try {
      setPosted((await api.postBill(review.bill, review.matches)).purchase_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const errors = review?.issues.filter((i) => i.severity === "error") ?? [];
  const warnings = review?.issues.filter((i) => i.severity === "warning") ?? [];
  const unmatched = review?.matches.filter((m) => m.product_id === null).length ?? 0;
  const noExpiry = review?.bill.lines.filter((l) => !l.expiry).length ?? 0;

  return (
    <div className="space-y-7">
      <PageHeader
        icon={LuReceipt}
        title={t("Add stock from a bill")}
        sub={t("Photograph a distributor's bill. Stockora reads quantities, batches and expiry dates, checks the GST and totals, and matches each line to your products. Nothing is added until you confirm.")}
      />

      {!llmEnabled && (
        <div className="neu flex items-start gap-4 rounded-[22px] p-5">
          <span className="neu-in grid size-11 shrink-0 place-items-center rounded-2xl text-exp">
            <LuTriangleAlert className="size-5" />
          </span>
          <p className="text-[13.5px] text-ink-2">
            <span className="font-semibold text-ink">{t("Reading bills needs the AI.")}</span> {t("Add your free Groq key as")} <code className="font-semibold">GROQ_API_KEY=...</code> {t("in")}{" "}
            <code className="font-semibold">backend/.env</code>{t(", then restart Stockora.")}
          </p>
        </div>
      )}

      <label
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          pick(e.dataTransfer.files?.[0] ?? null);
        }}
        className={`flex cursor-pointer flex-col items-center gap-4 rounded-[26px] px-6 py-10 text-center transition-shadow ${drag ? "neu-in" : "neu"}`}
      >
        <span className="neu-in grid size-16 place-items-center rounded-3xl text-brand">
          <LuCamera className="size-7" />
        </span>
        <span>
          <span className="block text-[15px] font-semibold">{file ? file.name : t("Drop a bill photo here, or tap to take one")}</span>
          <span className="block text-[13px] text-ink-3">{t("JPG or PNG. A clear, flat photo of the whole bill works best.")}</span>
        </span>
        <input type="file" accept="image/*" capture="environment" className="sr-only" onChange={(e) => pick(e.target.files?.[0] ?? null)} />
      </label>
      <div className="flex justify-center">
        <Button icon={LuScanLine} onClick={read} disabled={!file || busy || !llmEnabled}>
          {busy && !review ? t("Reading bill…") : t("Read bill")}
        </Button>
      </div>

      {error && <ErrorNote error={error} />}

      {review && (
        <div className="grid gap-6 lg:grid-cols-[18rem_1fr]">
          {preview && <img src={preview} alt={t("Bill photo")} className="neu max-h-[34rem] w-full rounded-[22px] object-contain p-2" />}
          <div className="min-w-0 space-y-6">
            <Card title={t("{d}, bill {n}", { d: review.bill.distributor_name, n: review.bill.bill_no })} icon={LuReceipt} aside={review.bill.bill_date}>
              {errors.length === 0 && warnings.length === 0 ? (
                <Chip t="ok" icon={LuCircleCheck}>{t("GST and totals check out")}</Chip>
              ) : (
                <ul className="space-y-2">
                  {errors.map((i, k) => (
                    <li key={`e${k}`} className="rounded-2xl bg-out-soft px-4 py-2.5 text-[13.5px] font-medium text-out">{i.message}</li>
                  ))}
                  {warnings.map((i, k) => (
                    <li key={`w${k}`} className="rounded-2xl bg-exp-soft px-4 py-2.5 text-[13.5px]">{i.message}</li>
                  ))}
                </ul>
              )}
            </Card>

            <Card
              title={t("Lines")}
              aside={[unmatched ? t("{n} need a product", { n: unmatched }) : t("All matched"), noExpiry ? t("{n} without expiry date", { n: noExpiry }) : ""].filter(Boolean).join(" · ")}
              flush
            >
              <ul className="px-3">
                {review.bill.lines.map((l, idx) => {
                  const m = review.matches[idx];
                  return (
                    <li key={idx} className="rounded-2xl px-3 py-3.5">
                      <div className="flex flex-wrap items-baseline justify-between gap-2">
                        <span className="font-semibold">{l.name}</span>
                        <span className="flex flex-wrap gap-1.5">
                          <Chip t="info">{l.free_qty ? t("{n} + {f} free units", { n: l.qty, f: l.free_qty }) : t("{n} units", { n: l.qty })}</Chip>
                          {l.batch && <Chip>batch {l.batch}</Chip>}
                          {l.expiry ? <Chip t="exp">{t("exp {d}", { d: l.expiry })}</Chip> : <Chip>{t("no expiry printed")}</Chip>}
                        </span>
                      </div>
                      <label className="mt-2.5 flex flex-wrap items-center gap-2 text-[13px]">
                        <span className="text-ink-2">{t("Your product")}</span>
                        <select
                          value={m.product_id ?? ""}
                          onChange={(e) => choose(idx, Number(e.target.value))}
                          className={`neu-in-sm min-h-10 rounded-xl px-3 ${m.product_id ? "" : "ring-2 ring-out/50"}`}
                        >
                          <option value="" disabled>{t("Pick the matching product")}</option>
                          {m.candidates.map((c) => (
                            <option key={c.product_id} value={c.product_id}>
                              {c.alias} ({t("{p}% match", { p: Math.round(c.score * 100) })})
                            </option>
                          ))}
                        </select>
                      </label>
                    </li>
                  );
                })}
              </ul>
              <div className="flex flex-wrap items-center justify-between gap-3 px-6 pt-3">
                <span className="text-[13px] text-ink-2">{t("{n} units on this bill", { n: review.bill.lines.reduce((a, l) => a + l.qty + (l.free_qty || 0), 0) })}</span>
                {posted ? (
                  <Chip t="ok" icon={LuCircleCheck}>{t("Added to stock (purchase #{n})", { n: posted })}</Chip>
                ) : (
                  <Button onClick={post} disabled={busy || unmatched > 0 || errors.length > 0}>{t("Add to stock")}</Button>
                )}
              </div>
              {errors.length > 0 && !posted && <p className="px-6 pt-2 text-[13px] text-out">{t("Sort out the bill errors above with your distributor before adding it.")}</p>}
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}
