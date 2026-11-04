import { Download, Maximize2, Pause, Play, RotateCcw, X } from "lucide-react";
import { useEffect, useRef, useState, type RefObject } from "react";
import { t } from "./i18n";

// <model-viewer> (three.js) büyük bir paket; yalnızca ekranda bir 3D sonuç olunca yüklenir.
let viewerModule: Promise<unknown> | null = null;
const loadViewer = () => (viewerModule ??= import("@google/model-viewer"));

declare module "react" {
  namespace JSX {
    interface IntrinsicElements {
      "model-viewer": DetailedHTMLProps<HTMLAttributes<HTMLElement>, HTMLElement> & Record<string, unknown>;
    }
  }
}

interface ViewerElement extends HTMLElement {
  cameraOrbit: string;
  fieldOfView: string;
  jumpCameraToGoal: () => void;
  loaded: boolean;
}

const BACKGROUNDS: [string, string][] = [[t("Koyu"), "#16171a"], [t("Gri"), "#5b5f66"], [t("Açık"), "#e9e9ec"]];
const HELP = t("Sürükle: döndür · Tekerlek ya da iki parmak: yakınlaştır · Sağ tık ya da iki parmakla sürükle: kaydır");

function ModelView({ src, autoRotate, exposure, viewerRef, background }: {
  src: string;
  autoRotate: boolean;
  exposure: number;
  viewerRef?: RefObject<ViewerElement | null>;
  background: string;
}) {
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    loadViewer().catch((e) => setError(t("3D görüntüleyici yüklenemedi: {e}", { e: String(e) })));
  }, []);
  if (error) return <div className="error-text">{error}</div>;
  return (
    <model-viewer
      ref={viewerRef}
      src={src}
      camera-controls=""
      touch-action="pan-y"
      environment-image="neutral"
      shadow-intensity="1"
      exposure={exposure}
      interaction-prompt="none"
      style={{ background }}
      {...(autoRotate ? { "auto-rotate": "" } : {})}
    />
  );
}

/** Sohbetteki 3D sonuç: döndürülebilir önizleme, tam ekran görüntüleyici ve indirme. */
export function Model3DTile({ src, onOpen }: { src: string; onOpen: () => void }) {
  return (
    <div className="tile model3d">
      <ModelView src={src} autoRotate exposure={1} background={BACKGROUNDS[0][1]} />
      <div className="tile-actions">
        <button title={t("Tam ekran görüntüleyici")} aria-label={t("Tam ekran görüntüleyici")} onClick={onOpen}><Maximize2 size={15} /></button>
        <a title={t("GLB indir")} href={src} download><Download size={15} /></a>
      </div>
    </div>
  );
}

/** Tam ekran 3D görüntüleyici: modelin çevresinde dön, yakınlaş, ışığı ve arka planı değiştir. */
export function Viewer3D({ src, onClose }: { src: string; onClose: () => void }) {
  const viewer = useRef<ViewerElement | null>(null);
  const [autoRotate, setAutoRotate] = useState(false);
  const [exposure, setExposure] = useState(1);
  const [background, setBackground] = useState(BACKGROUNDS[0][1]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const resetView = () => {
    const v = viewer.current;
    if (!v) return;
    v.cameraOrbit = "0deg 75deg 105%";
    v.fieldOfView = "auto";
    v.jumpCameraToGoal();
  };

  return (
    <div className="viewer3d" role="dialog" aria-label={t("3D görüntüleyici")}>
      <div className="viewer3d-bar">
        <button className={`chip ${autoRotate ? "on" : ""}`} onClick={() => setAutoRotate(!autoRotate)}>
          {autoRotate ? <Pause size={14} /> : <Play size={14} />} {t("Otomatik döndür")}
        </button>
        <button className="chip" onClick={resetView}><RotateCcw size={14} /> {t("Görünümü sıfırla")}</button>
        <label className="viewer3d-light">
          {t("Işık")}
          <input type="range" min={0.3} max={2.5} step={0.1} value={exposure} onChange={(e) => setExposure(Number(e.target.value))} />
        </label>
        <div className="row">
          {BACKGROUNDS.map(([name, color]) => (
            <button key={name} className={`chip ${background === color ? "on" : ""}`} onClick={() => setBackground(color)}>{name}</button>
          ))}
        </div>
        <span className="spacer" />
        <a className="chip" href={src} download><Download size={14} /> {t("GLB indir")}</a>
        <button className="icon-btn" onClick={onClose} aria-label={t("Görüntüleyiciyi kapat")}><X size={18} /></button>
      </div>
      <ModelView src={src} autoRotate={autoRotate} exposure={exposure} viewerRef={viewer} background={background} />
      <div className="viewer3d-help">{HELP}</div>
    </div>
  );
}
