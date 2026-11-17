import { Layers, Plus, Power, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { SessionInfo, SessionLimits, SessionPlan } from "./api";
import { t } from "./i18n";

/**
 * Çoklu Colab oturumu şeridi.
 *
 * Kullanıcı sınırı burada görür ("Oturumlar 1/3"): her oturum ayrı bir GPU çalışma zamanı ve kredi
 * demektir. Oturum açmak düğmeyle yapılır — hiçbir oturum kendiliğinden açılmaz.
 */

const PHASES: Record<string, string> = {
  off: t("kapalı"),
  starting: t("açılıyor"),
  ready: t("hazır"),
  stopping: t("kapanıyor"),
  error: t("hata"),
};

function phaseLabel(session: SessionInfo): string {
  return PHASES[session.phase] ?? t("kapalı");
}

function pillClass(phase: SessionInfo["phase"]) {
  if (phase === "ready") return "ready";
  if (phase === "error") return "error";
  if (phase === "off") return "off";
  return "busy";
}

function minutes(seconds: number | null): string {
  return typeof seconds === "number" && Number.isFinite(seconds) ? t("{n} dk", { n: Math.round(seconds / 60) }) : "—";
}

export function SessionsBar({ sessions, limits, busy, onStart, onStop, onOpenPlan }: {
  sessions: SessionInfo[] | undefined;
  limits: SessionLimits | undefined;
  busy: boolean;
  onStart: () => void;
  onStop: (name: string) => void;
  onOpenPlan: () => void;
}) {
  if (!sessions || sessions.length === 0) return null; // eski sunucu: şerit görünmez
  const plan = limits?.plan ?? sessions.length;
  const max = limits?.max ?? plan;
  // Plan açık oturumları sayar (sunucuyla aynı: kapalı varsayılan oturum yer tutmaz). Ek oturum
  // yuvaları en fazla max-1'dir: varsayılan oturum kapalıyken de bundan fazlası açılamaz.
  const open = sessions.filter((s) => s.phase !== "off").length;
  const extras = sessions.filter((s) => s.slot !== 0).length;
  const full = open >= plan || extras >= max - 1;
  return (
    <div className="sessions-bar" aria-label={t("Colab oturumları")}>
      <span className="stat"><Layers size={14} /> {t("Oturumlar")} {open}/{plan}</span>
      {sessions.map((s) => (
        <span
          key={s.name}
          className={`chip session-chip ${pillClass(s.phase)}`}
          title={t("port {port} · {minutes}", { port: s.port, minutes: minutes(s.session_seconds) })}
        >
          {s.name} · {phaseLabel(s)}
          {s.phase !== "off" && s.slot !== 0 && (
            <button className="icon-btn" onClick={() => onStop(s.name)} aria-label={t("{name} oturumunu kapat", { name: s.name })}>
              <Power size={12} />
            </button>
          )}
        </span>
      ))}
      <button
        className="btn"
        onClick={onStart}
        disabled={busy || full}
        title={full ? t("Plan dolu ({plan}); planı yükselt", { plan }) : t("Yeni Colab oturumu aç (kredi harcar)")}
      >
        <Plus size={14} /> {t("Oturum aç")}
      </button>
      <button className="btn ghost" onClick={onOpenPlan}>{t("Plan: {plan}/{max}", { plan, max })}</button>
    </div>
  );
}

/** Plan sorusunun açıklamaları: sınır, oturum başına tek iş ve kredi maliyeti. */
const EXPLAIN = [
  t("Her oturum ayrı bir Colab GPU oturumu: aynı anda iki video, ya da bir video + bir görsel + bir seslendirme üretilebilir."),
  t("Oturum başına tek iş paralel çalışır; farklı oturumlar birbirini beklemez."),
  t("Her açık oturum kredi harcar; boşta kalanlar otomatik kapanır (keep-warm seçilirse kapanmaz)."),
];

/** İlk kullanımda bir kez sorulan soru: kaç oturum? Cevap sunucuda saklanır. */
export function SessionPlanModal({ plan, busy, onSave, onClose }: {
  plan: SessionPlan;
  busy: boolean;
  onSave: (value: number, keepWarm: boolean) => void;
  onClose: () => void;
}) {
  const [value, setValue] = useState(plan.plan);
  const [keepWarm, setKeepWarm] = useState(Boolean(plan.keep_warm));
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label={t("Kaç Colab oturumu kullanılsın?")}>
      <div className="dialog" ref={ref} tabIndex={-1}>
        <div className="dialog-head">
          <h2>{t("Kaç Colab oturumu kullanılsın?")}</h2>
          <button className="icon-btn" onClick={onClose} aria-label={t("Kapat")}><X size={16} /></button>
        </div>
        <p className="muted">
          {t("Aynı anda kaç GPU oturumunun açık tutulacağını belirler. En fazla {max}, önerilen {default}. Sonradan plan düğmesinden değiştirilebilir.",
            { max: plan.max, default: plan.default })}
        </p>
        <ul className="plan-info">
          {EXPLAIN.map((line) => <li key={line}>{line}</li>)}
        </ul>
        <div className="plan-choices" role="radiogroup" aria-label={t("Oturum sayısı")}>
          {Array.from({ length: plan.max }, (_, i) => i + 1).map((n) => (
            <button
              key={n}
              role="radio"
              aria-checked={value === n}
              className={`btn ${value === n ? "primary" : ""}`}
              onClick={() => setValue(n)}
            >
              {n}
              {n === plan.default && <span className="muted"> {t("(önerilen)")}</span>}
            </button>
          ))}
        </div>
        <label className="plan-warm">
          <input type="checkbox" checked={keepWarm} onChange={(e) => setKeepWarm(e.target.checked)} />
          {t("Oturumlar boşta kapanmasın")}
        </label>
        <div className="dialog-actions">
          <button className="btn ghost" onClick={onClose}>{t("Şimdi değil")}</button>
          <button className="btn primary" disabled={busy} onClick={() => onSave(value, keepWarm)}>{t("Kaydet")}</button>
        </div>
      </div>
    </div>
  );
}
