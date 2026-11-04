import { Box, FileText, Film, Image, Mic, Music, Plus, Trash2 } from "lucide-react";
import type { Mode, RuntimeState, Thread } from "./api";
import { SystemPanel } from "./SystemPanel";
import { t } from "./i18n";

const MODES = [
  { id: "image", label: t("Görsel"), icon: Image, ready: true },
  { id: "video", label: t("Video"), icon: Film, ready: true },
  { id: "model3d", label: t("3D model"), icon: Box, ready: true },
  { id: "music", label: t("Müzik"), icon: Music, ready: true },
  { id: "speech", label: t("Seslendirme"), icon: Mic, ready: true },
  { id: "transcribe", label: t("Yazıya dök"), icon: FileText, ready: true },
];

interface Props {
  open: boolean;
  rt: RuntimeState | null;
  mode: Mode;
  onMode: (m: Mode) => void;
  threads: Thread[];
  activeId: string | null;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}

export function Sidebar({ open, rt, mode, onMode, threads, activeId, onNew, onSelect, onDelete }: Props) {
  return (
    <aside className={`sidebar ${open ? "open" : ""}`}>
      <div className="brand">{t("Colab Stüdyo")}</div>
      <nav className="modes">
        {MODES.map((m) => (
          <button key={m.id} className={`mode ${m.id === mode ? "active" : ""} ${m.ready ? "" : "soon"}`} disabled={!m.ready} onClick={() => onMode(m.id as Mode)}>
            <m.icon size={16} /> {m.label} {!m.ready && <small>{t("yakında")}</small>}
          </button>
        ))}
      </nav>
      <div className="history-head">
        <span>{t("Geçmiş")}</span>
        <button className="icon-btn" onClick={onNew} aria-label={t("Yeni sohbet")} title={t("Yeni sohbet")}><Plus size={16} /></button>
      </div>
      <div className="history">
        {threads.map((th) => (
          <div key={th.id} className={`history-item ${th.id === activeId ? "active" : ""}`}>
            <button className="history-open" onClick={() => onSelect(th.id)}>
              <span title={th.title}>{th.title}</span>
            </button>
            <button
              className="icon-btn del"
              aria-label={t("Sil")}
              onClick={() => {
                if (confirm(t("Bu sohbet ve sonuçları silinsin mi?"))) onDelete(th.id);
              }}
            >
              <Trash2 size={14} />
            </button>
          </div>
        ))}
        {threads.length === 0 && <div className="muted" style={{ padding: "4px 10px", fontSize: 13 }}>{t("Henüz üretim yok.")}</div>}
      </div>
      <SystemPanel rt={rt} />
    </aside>
  );
}
