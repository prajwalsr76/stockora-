// English / Hindi for the whole app. The English text is the key, so the code stays readable and
// any string without a Hindi entry simply shows in English (never blank).
// Hindi is pure Hindi in Devanagari; product, brand and customer names stay as they are written.
import { HI } from "./i18n_hi";

export type Lang = "en" | "hi";

let current: Lang = readLang();

function readLang(): Lang {
  try {
    const v = localStorage.getItem("stockora_lang");
    if (v === "hi" || v === "en") return v;
  } catch {
    /* storage blocked */
  }
  return "en";
}

export function getLang(): Lang {
  return current;
}

export function setLang(l: Lang) {
  current = l;
  document.documentElement.lang = l === "hi" ? "hi" : "en";
  try {
    localStorage.setItem("stockora_lang", l);
  } catch {
    /* storage blocked */
  }
}

/** t("{n} items to restock", { n: 5 }) -> "5 सामान मंगाने हैं" in Hindi. */
export function t(en: string, vars?: Record<string, string | number>): string {
  let s = current === "hi" ? HI[en] ?? en : en;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(String(v));
  return s;
}

/** Locale for dates: Hindi month and weekday names in Hindi mode, digits stay 0-9. */
export const locale = () => (current === "hi" ? "hi-IN" : "en-IN");

/** Category codes from the backend, shown in the chosen language. */
const CATEGORIES: Record<string, [string, string]> = {
  dairy: ["Dairy", "डेयरी"],
  bakery: ["Bakery", "बेकरी"],
  snacks: ["Snacks", "नमकीन व बिस्कुट"],
  beverages: ["Beverages", "पेय पदार्थ"],
  staples: ["Staples", "राशन"],
  oil_ghee: ["Oil & ghee", "तेल व घी"],
  spices: ["Spices", "मसाले"],
  instant_food: ["Instant food", "झटपट खाना"],
  personal_care: ["Personal care", "निजी देखभाल"],
  home_care: ["Home care", "घर की सफ़ाई"],
  festive: ["Festive", "त्योहार का सामान"],
  fasting: ["Fasting", "व्रत का सामान"],
  stationery: ["Stationery", "लेखन सामग्री"],
  produce: ["Fruit & vegetables", "फल व सब्ज़ी"],
  meat_fish: ["Meat & fish", "मांस व मछली"],
  general: ["General", "सामान्य"],
};
export const categoryName = (c: string) => {
  const hit = CATEGORIES[c];
  if (!hit) return c.replace(/_/g, " ");
  return current === "hi" ? hit[1] : hit[0];
};

const FESTIVALS_HI: Record<string, string> = {
  "Makar Sankranti": "मकर संक्रांति", Holi: "होली", "Eid al-Fitr": "ईद-उल-फ़ित्र", "Eid al-Adha": "ईद-उल-अज़हा",
  "Raksha Bandhan": "रक्षा बंधन", Janmashtami: "जन्माष्टमी", "Ganesh Chaturthi": "गणेश चतुर्थी", Navratri: "नवरात्रि",
  Dussehra: "दशहरा", Diwali: "दिवाली", "Chhath Puja": "छठ पूजा", Christmas: "क्रिसमस",
};
const MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTHS_HI = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"];

/** "Diwali on 08 Nov, Chhath Puja (15 Nov)" from the backend, in Hindi when Hindi is chosen. */
export function festivalText(s: string): string {
  if (current !== "hi" || !s) return s;
  let out = s;
  for (const [en, hi] of Object.entries(FESTIVALS_HI)) out = out.split(en).join(hi);
  out = out.replace(/\b(\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b/g, (_, d, m) => `${Number(d)} ${MONTHS_HI[MONTHS_EN.indexOf(m)]}`);
  return out.replace(/ on /g, " — ").replace(/\bIncludes\b/g, "");
}
