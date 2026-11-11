import { useEffect, useRef, useState } from "react";
import { LuCameraOff, LuX } from "react-icons/lu";
import { t } from "../i18n";

type Detector = { detect: (src: HTMLVideoElement) => Promise<{ rawValue: string }[]> };
type DetectorCtor = new (opts: { formats: string[] }) => Detector;

/** Phone-camera barcode scanner. Uses the browser's built-in BarcodeDetector (Chrome on Android);
 *  elsewhere (iPhone Safari, Firefox) it loads the ZXing reader only when needed. */
export default function BarcodeScanner({ onCode, onClose }: { onCode: (code: string) => void; onClose: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState<string | null>(null);
  const done = useRef(false);

  useEffect(() => {
    let stream: MediaStream | null = null;
    let timer: number | undefined;
    let stopZxing: (() => void) | undefined;
    const found = (code: string) => {
      if (done.current) return;
      done.current = true;
      navigator.vibrate?.(60);
      onCode(code);
    };
    (async () => {
      if (!navigator.mediaDevices?.getUserMedia) {
        setError(t("This browser cannot use the camera. Type the barcode number or use a USB scanner."));
        return;
      }
      const Native = (window as unknown as { BarcodeDetector?: DetectorCtor }).BarcodeDetector;
      try {
        if (Native) {
          stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
          const v = video.current!;
          v.srcObject = stream;
          await v.play();
          const det = new Native({ formats: ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "qr_code"] });
          const tick = async () => {
            if (done.current) return;
            try {
              const codes = await det.detect(v);
              if (codes[0]?.rawValue) return found(codes[0].rawValue);
            } catch {
              /* frame not ready */
            }
            timer = window.setTimeout(tick, 200);
          };
          tick();
        } else {
          const { BrowserMultiFormatReader } = await import("@zxing/browser");
          const reader = new BrowserMultiFormatReader();
          const controls = await reader.decodeFromVideoDevice(undefined, video.current!, (result) => {
            if (result) found(result.getText());
          });
          stopZxing = () => controls.stop();
        }
      } catch (e) {
        const denied = e instanceof DOMException && e.name === "NotAllowedError";
        setError(denied ? t("Camera permission was refused. Allow the camera in the browser settings, or type the barcode.") : t("Could not start the camera. Type the barcode number instead."));
      }
    })();
    return () => {
      window.clearTimeout(timer);
      stopZxing?.();
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, [onCode]);

  return (
    <div className="fixed inset-0 z-50 grid place-items-center p-4" role="dialog" aria-modal="true" aria-label={t("Scan a barcode")}>
      <button className="absolute inset-0 bg-ink/40" aria-label={t("Close scanner")} onClick={onClose} />
      <div className="neu rise relative w-full max-w-md rounded-[26px] p-4">
        <div className="mb-3 flex items-center justify-between">
          <p className="font-semibold">{t("Point the camera at the barcode")}</p>
          <button className="neu-btn grid size-10 place-items-center rounded-xl" aria-label={t("Close scanner")} onClick={onClose}>
            <LuX className="size-5" />
          </button>
        </div>
        {error ? (
          <div className="neu-in flex items-start gap-3 rounded-2xl p-4 text-ink-2">
            <LuCameraOff className="mt-0.5 size-5 shrink-0 text-out" />
            <p>{error}</p>
          </div>
        ) : (
          <div className="neu-in relative overflow-hidden rounded-2xl">
            <video ref={video} className="aspect-[4/3] w-full object-cover" muted playsInline />
            <span className="pointer-events-none absolute inset-x-8 top-1/2 h-0.5 -translate-y-1/2 bg-out/80" />
          </div>
        )}
      </div>
    </div>
  );
}
