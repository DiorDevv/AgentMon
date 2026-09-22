import type { Product, State } from "./types";

export const ALL_PRODUCTS: Product[] = ["ad", "cortex", "ksc", "si"];

// Yoqilgan mahsulotlar (PRODUCTS_ENABLED) — /api/me javobidan, ilova chizilishidan oldin o'rnatiladi.
// `let` eksporti "jonli" bog'lanish: import qilgan modullar yangilangan qiymatni ko'radi.
export let PRODUCTS: Product[] = [...ALL_PRODUCTS];
export function setProducts(list: string[] | undefined) {
  const wanted = new Set(list ?? ALL_PRODUCTS);
  PRODUCTS = ALL_PRODUCTS.filter((p) => wanted.has(p));
}

export const PRODUCT_NAMES: Record<Product, string> = {
  ad: "Active Directory",
  cortex: "Cortex XDR",
  ksc: "Kaspersky",
  si: "SearchInform",
};

export const PRODUCT_SHORT: Record<Product, string> = { ad: "AD", cortex: "Cortex", ksc: "KSC", si: "SI" };

// Kategorik rang mahsulotga bog'langan (tartib bo'yicha emas) — filtr o'zgarsa ham rang o'zgarmaydi.
export const PRODUCT_COLOR: Record<Product, string> = {
  ad: "var(--series-1)",
  cortex: "var(--series-2)",
  ksc: "var(--series-3)",
  si: "var(--series-4)",
};

export const STATE_ORDER: State[] = ["OK", "UNHEALTHY", "CONFLICT", "NO_SIGNAL", "STOPPED", "NOT_INSTALLED", "PENDING", "OFFLINE"];
export const PROBLEM_STATES: State[] = ["UNHEALTHY", "STOPPED", "NOT_INSTALLED", "NO_SIGNAL", "CONFLICT"];

const LABELS: Record<State, string> = {
  OK: "Ishlayapti",
  UNHEALTHY: "Himoya to'liq emas",
  STOPPED: "To'xtatilgan",
  NOT_INSTALLED: "O'rnatilmagan",
  NO_SIGNAL: "Signal yo'q",
  CONFLICT: "Ziddiyat",
  PENDING: "Tekshirilmoqda",
  OFFLINE: "Oflayn",
};
const AD_LABELS: Partial<Record<State, string>> = {
  OK: "Domenda",
  STOPPED: "DC bilan aloqa yo'q",
  NOT_INSTALLED: "Domenda emas",
  NO_SIGNAL: "DC bilan aloqa yo'q",
};

export function stateLabel(product: Product | null, s: State): string {
  return (product === "ad" && AD_LABELS[s]) || LABELS[s] || s;
}

export const STATE_HELP: Record<State, string> = {
  OK: "Agent o'z serveriga muntazam murojaat qilmoqda va konsol sog'lom deb ko'rsatmoqda",
  UNHEALTHY: "Agent ishlaydi, lekin konsol himoya to'liq emasligini ko'rsatmoqda",
  STOPPED: "Konsolda bor, lekin host tirik bo'la turib serverga murojaat yo'q",
  NOT_INSTALLED: "Konsolda yo'q va serverga trafik yo'q",
  NO_SIGNAL: "Host tirik, lekin agent serverga murojaat qilmayapti",
  CONFLICT: "Tarmoq va konsol ma'lumotlari bir-biriga zid",
  PENDING: "Host yaqinda yoqilgan — xulosa uchun yetarli vaqt o'tmagan",
  OFFLINE: "Host hozir tarmoqda ko'rinmayapti",
};

// Status ranglari faqat holat uchun; har doim ikonka + matn bilan (rang yolg'iz ma'no tashimaydi).
export const STATE_COLOR: Record<State, string> = {
  OK: "var(--good)",
  UNHEALTHY: "var(--warning)",
  CONFLICT: "var(--serious)",
  NO_SIGNAL: "var(--serious)",
  STOPPED: "var(--critical)",
  NOT_INSTALLED: "var(--critical)",
  PENDING: "var(--neutral)",
  OFFLINE: "var(--neutral)",
};

// Jiddiylik darajasi — saralash va jadval chizig'i uchun.
export const STATE_SEVERITY: Record<State, number> = {
  NOT_INSTALLED: 5, STOPPED: 5, NO_SIGNAL: 4, CONFLICT: 3, UNHEALTHY: 2, PENDING: 1, OFFLINE: 0, OK: 0,
};
