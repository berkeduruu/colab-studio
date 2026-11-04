import { Clock, Menu, Power, ScrollText, SlidersHorizontal } from "lucide-react";
import type { RuntimeState } from "./api";
import { LANGS, lang, setLang, t, tServer, type Lang } from "./i18n";

const PHASES: Record<RuntimeState["phase"], string> = {
  off: t("GPU kapalı"),
  starting: t("GPU açılıyor"),
  ready: t("hazır"),
  stopping: t("Kapanıyor"),
  error: t("Hata"),
};

function pillClass(phase: RuntimeState["phase"]) {
  if (phase === "ready") return "ready";
  if (phase === "error") return "error";
  if (phase === "off") return "off";
  return "busy";
}

interface Props {
  rt: RuntimeState | null;
  onStart: () => void;
  onStop: () => void;
  onToggleAuto: () => void;
  onToggleLog: () => void;
  onToggleSettings: () => void;
  onToggleMenu: () => void;
}

export function TopBar({ rt, onStart, onStop, onToggleAuto, onToggleLog, onToggleSettings, onToggleMenu }: Props) {
  const running = rt && rt.phase !== "off" && rt.phase !== "error";
  return (
    <div className="topbar">
      <button className="icon-btn menu-btn" onClick={onToggleMenu} aria-label={t("Menü")}>
        <Menu size={18} />
      </button>
      {rt && (
        <span className={`pill ${pillClass(rt.phase)}`} title={tServer(rt.detail)}>
          {rt.phase === "ready" ? `${rt.gpu} ${PHASES.ready}` : PHASES[rt.phase]}
        </span>
      )}
      {rt && (running ? (
        <button className="btn danger" onClick={onStop}><Power size={14} /> {t("Kapat")}</button>
      ) : (
        <button className="btn" onClick={onStart}><Power size={14} /> {t("{gpu} başlat", { gpu: rt.gpu })}</button>
      ))}
      {rt?.idle_seconds !== null && rt?.idle_seconds !== undefined && rt.auto_shutdown && (
        <span className="stat"><Clock size={14} /> {t("Boşta {n}/{max} dk", { n: Math.floor(rt.idle_seconds / 60), max: rt.idle_minutes })}</span>
      )}
      <span className="spacer" />
      {rt && (
        <button className={`switch ${rt.auto_shutdown ? "on" : ""}`} onClick={onToggleAuto} title={t("{n} dk boşta kalınca GPU'yu kapat", { n: rt.idle_minutes })}>
          <span className="track" /> {t("Otomatik kapanma")}
        </button>
      )}
      <select className="chip lang" value={lang} onChange={(e) => setLang(e.target.value as Lang)} aria-label={t("Dil")} title={t("Dil")}>
        {LANGS.map(([k, name]) => <option key={k} value={k}>{name}</option>)}
      </select>
      <button className="icon-btn" onClick={onToggleLog} aria-label={t("Kayıtlar")} title={t("Kayıtlar")}><ScrollText size={18} /></button>
      <button className="icon-btn" onClick={onToggleSettings} aria-label={t("Ayarlar")} title={t("Ayarlar")}><SlidersHorizontal size={18} /></button>
    </div>
  );
}
