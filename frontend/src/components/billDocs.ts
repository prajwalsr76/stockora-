import { api, type PdfLayout } from "../api";
import { t } from "../i18n";

/** Open a bill or credit-note PDF in a new tab (to print or save). The tab is opened first, so
 *  pop-up blockers allow it, then filled once the PDF has downloaded. */
export async function openPdf(kind: "invoices" | "credit-notes", id: number, layout: PdfLayout) {
  const tab = window.open("", "_blank");
  try {
    const blob = await api.pdf(kind, id, layout);
    const url = URL.createObjectURL(blob);
    if (tab) tab.location.href = url;
    else window.location.href = url;
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (e) {
    tab?.close();
    throw e;
  }
}

/** Share the bill. On phones that can share files (Android Chrome, iPhone Safari) the PDF goes
 *  straight into WhatsApp or any app; otherwise WhatsApp opens with the bill as text. */
export async function shareBill(id: number, number: string) {
  const nav = navigator as Navigator & { canShare?: (d: ShareData) => boolean };
  try {
    const blob = await api.pdf("invoices", id, "a4");
    const file = new File([blob], `${number.replace(/\//g, "-")}.pdf`, { type: "application/pdf" });
    if (nav.canShare?.({ files: [file] })) {
      await nav.share({ files: [file], title: t("Bill {n}", { n: number }) });
      return;
    }
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") return; // the owner closed the share sheet
  }
  const { url } = await api.whatsapp(id);
  window.open(url, "_blank", "noopener");
}
