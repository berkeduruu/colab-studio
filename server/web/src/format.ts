import type { PackStatus } from "./api";
import { t } from "./i18n";

// Süre ve boyut metinleri sohbette, kenar çubuğunda ve ayarlarda aynı biçimde yazılır. Ortağı olan
// bu yardımcılar ayrı modülde durur; Feed ile SystemPanel birbirini import etmez.

const GB = 1024 ** 3;

export function formatDuration(sec: number) {
  if (sec < 60) return t("{n} sn", { n: sec });
  const m = Math.floor(sec / 60);
  if (m < 60) return t("{m} dk {s} sn", { m, s: sec % 60 });
  const h = Math.floor(m / 60);
  return h < 48 ? t("{h} sa {m} dk", { h, m: m % 60 }) : t("{d} gün", { d: Math.floor(h / 24) });
}

export const formatGB = (b: number) => `${(b / GB).toFixed(1)}`;

/** "6.0/16.0 GB · 95 MB/s · ~2 dk" */
export function downloadText(p: PackStatus) {
  return [
    `${formatGB(p.done)}/${formatGB(p.total)} GB`,
    p.rate ? `${Math.round(p.rate / 2 ** 20)} MB/s` : null,
    p.eta ? `~${formatDuration(p.eta)}` : null,
  ].filter(Boolean).join(" · ");
}

/** Yüzde: Türkçede "%42", İngilizcede "42%". */
export const pct = (value: number, round: (n: number) => number = Math.round) => t("%{n}", { n: round(value) });
