import { EN, SERVER_PATTERNS } from "./en";

// Arayüz metinleri kaynakta Türkçe yazılır ve t() ile seçili dile çevrilir; İngilizce karşılıklar
// en.ts'de. Sunucunun gönderdiği (Türkçe) durum ve hata metinleri tServer() ile çevrilir: önce birebir
// eşleşme, sonra yer tutuculu kalıplar. Tanınmayan metin (ör. ComfyUI'nin ham hatası) olduğu gibi kalır.

export type Lang = "en" | "tr";
export const LANGS: [Lang, string][] = [["en", "English"], ["tr", "Türkçe"]];
const KEY = "studio.lang";

function initial(): Lang {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "en" || v === "tr") return v;
  } catch {
    // localStorage kapalıysa varsayılan dil
  }
  return "en";
}

export const lang: Lang = initial();
document.documentElement.lang = lang;

type Vars = Record<string, string | number>;

const fill = (s: string, vars?: Vars) =>
  vars ? s.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k]) : m)) : s;

/** Türkçe kaynak metni seçili dile çevirir; {ad} yer tutucuları vars ile doldurulur. */
export function t(tr: string, vars?: Vars): string {
  return fill(lang === "en" ? (EN[tr] ?? tr) : tr, vars);
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// "GPU açılamadı: {detail}" -> /^GPU açılamadı: ([\s\S]*)$/ ; yakalananlar da çevrilir (iç içe mesajlar).
const COMPILED = SERVER_PATTERNS.map(([tr, en]) => {
  const names: string[] = [];
  const source = tr.split(/(\{\w+\})/).map((part) => {
    const m = part.match(/^\{(\w+)\}$/);
    if (!m) return escape(part);
    names.push(m[1]);
    return "([\\s\\S]*?)";
  }).join("");
  return { re: new RegExp(`^${source}$`), names, en };
});

/** Sunucudan gelen Türkçe metni seçili dile çevirir. */
export function tServer(msg: string | null | undefined): string {
  if (!msg) return "";
  if (lang === "tr") return msg;
  const exact = EN[msg];
  if (exact !== undefined) return exact;
  for (const { re, names, en } of COMPILED) {
    const m = msg.match(re);
    if (m) return fill(en, Object.fromEntries(names.map((n, i) => [n, tServer(m[i + 1])])));
  }
  return msg;
}

export function setLang(l: Lang) {
  try {
    localStorage.setItem(KEY, l);
  } catch {
    // kaydedilemese de bu oturumda geçerli olsun
  }
  location.reload();
}
