import { t, tServer } from "./i18n";
export type Phase = "off" | "starting" | "ready" | "stopping" | "error";

export interface Credits {
  balance?: number;
  rate?: number;
  error?: string;
}

/** Bir model paketinin inme durumu (bayt); eta: öndeki paketlerle birlikte kalan sn. */
export interface PackStatus {
  label?: string;
  total: number;
  done: number;
  fraction: number;
  rate: number | null;
  eta: number | null;
}

export interface Startup {
  progress: number;
  remaining_seconds: number;
  elapsed_seconds: number;
  step: number;
  steps: string[];
  step_elapsed_seconds?: number;
  download?: PackStatus | null;
  /** Açılışı tetikleyen işin model paketi (ilk o iner); yoksa image. */
  pack?: string;
  /** Kurulum betiğinin aşaması, ör. "3/5 ComfyUI ve ses paketleri kuruluyor". */
  setup?: string | null;
}

export interface CreditStats {
  today: number;
  week: number;
  per_day: number;
  tracked_days: number;
}

export interface QueueInfo {
  order: string[];
  current: { id: string; kind: string; status: string; stage: string | null; progress: number; prompt: string } | null;
  /** Sıradaki işlerin tahmini başlama süresi (sn); süre henüz öğrenilmediyse null. */
  starts_in?: Record<string, number | null>;
}

export interface RuntimeState {
  phase: Phase;
  detail: string;
  auto_shutdown: boolean;
  idle_seconds: number | null;
  idle_minutes: number;
  credits: Credits | null;
  session_spent: number | null;
  session_seconds: number | null;
  startup: Startup | null;
  credit_stats: CreditStats | null;
  queue: QueueInfo;
  gpu: string;
  packs?: Record<string, PackStatus>;
  gpu_now?: { t: number; gpu: number; vram_used: number; vram_total: number } | null;
}

/** Çalışan işin canlı durumu: eta kalan sn (bu tür/ayar ilk kez çalışıyorsa null); stage_*
 * çalışan aşamanın süresi ve genelde ne kadar sürdüğü; loading: model GPU'ya yükleniyor. */
export interface JobLive {
  progress: number;
  eta: number | null;
  stage_seconds: number | null;
  stage_expected: number | null;
  loading: boolean;
}

export interface Metric {
  t: number;
  gpu: number;
  temp: number;
  vram_used: number;
  vram_total: number;
  ram_used: number;
  ram_total: number;
  disk_used: number;
  disk_total: number;
}

export interface ImageParams {
  ratio: string;
  size: string;
  count: number;
  steps: number;
  cfg: number;
  turbo?: boolean;
  sampler: string;
  scheduler: string;
  seed: number | null;
  negative: string;
  edit_resolution: string;
}

export interface VideoParams {
  ratio: string;
  quality: string;
  seconds: number;
  turbo: boolean;
  seed: number | null;
}

export interface MusicParams {
  lyrics: string;
  seconds: number;
  seed: number | null;
}

export interface SpeechParams {
  engine: "chatterbox" | "qwen3";
  language: string;
  voice_preset: string;
  instruct: string;
  reference_text: string;
  exaggeration: number;
  cfg_weight: number;
  temperature: number;
  seed: number | null;
}

export interface TranscribeParams {
  language: string;
}

export interface Model3DParams {
  resolution: number;
  texture: "pbr" | "vertex";
  faces: number;
  seed: number | null;
}

export type Mode = "image" | "video" | "model3d" | "music" | "speech" | "transcribe";
export const MODE_LIST: readonly Mode[] = ["image", "video", "model3d", "music", "speech", "transcribe"];

export type JobStatus = "queued" | "waiting_gpu" | "running" | "done" | "error";

// Job-status decisions live in one place: adding a status only changes this block. done and error
// are final (server/studio.py:JOB_TRANSITIONS). A cancelled job arrives as "error" with cancelled=true.
export const isPending = (j: { status: JobStatus }) => j.status !== "done" && j.status !== "error";
export const isDone = (j: { status: JobStatus }) => j.status === "done";
export const isFailed = (j: { status: JobStatus; cancelled?: boolean }) => j.status === "error" && !j.cancelled;
export const isCancelled = (j: { status: JobStatus; cancelled?: boolean }) => j.status === "error" && !!j.cancelled;

export interface Job {
  id: string;
  thread_id: string | null;
  kind: Mode | "enhance";
  prompt: string;
  params: Partial<ImageParams> & Partial<VideoParams> & Partial<MusicParams> & Partial<SpeechParams> & Partial<Model3DParams> & { width?: number; height?: number };
  inputs: string[];
  status: JobStatus;
  /** status=error and the job was cancelled (by the user or a manual GPU stop), not a failure. */
  cancelled?: boolean;
  progress: number;
  error: string | null;
  stage: string | null;
  outputs: string[];
  text: string | null;
  created_at: number;
  finished_at: number | null;
  live?: JobLive | null;
}

export interface Thread {
  id: string;
  mode: string;
  title: string;
  created_at: number;
}

export interface ThreadDetail extends Thread {
  jobs: Job[];
}

export interface Preset {
  id: string;
  mode: string;
  name: string;
  params: ImageParams;
}

/** models.toml'daki ad ve dile göre açıklama (server/app.py: /api/models). */
export interface ModelInfo {
  label: string;
  description: Record<string, string>;
}

export interface Upload {
  name: string;
  url: string;
}

/** İsteği gönderir; ağ hatası (sunucuya hiç ulaşılamadı) da ApiError olur (status 0) ki
 * çağıranlar tek biçimde ele alsın. */
async function send(method: string, path: string, body?: unknown, form?: FormData): Promise<Response> {
  try {
    return await fetch(path, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: form ?? (body === undefined ? undefined : JSON.stringify(body)),
    });
  } catch (e) {
    throw new ApiError(0, t("Sunucuya ulaşılamadı; bağlantını kontrol edip tekrar dene. ({detail})", { detail: e instanceof Error ? e.message : String(e) }));
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await send(method, path, body);
  if (!r.ok) throw new ApiError(r.status, await errorText(r));
  return r.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// Uygulamanın kendi hataları düz metin ya da JSON'dur; nginx'in 502 sayfası gibi HTML yanıtlar
// (servis yeniden başlarken) ham gösterilmez. Karar gövdenin türüne göre verilir, durum koduna değil.
async function errorText(r: Response) {
  if (r.status === 413) return t("Dosya çok büyük (en fazla 50 MB).");
  if (r.status >= 502) return t("Sunucuya şu an ulaşılamıyor ({status}); servis yeniden başlıyor olabilir, birazdan tekrar dene.", { status: r.status });
  const type = (r.headers.get("content-type") ?? "").toLowerCase();
  const text = (await r.text()).trim();
  if (type.includes("text/html") || text.startsWith("<")) return t("Sunucu isteği işleyemedi ({status}); birazdan tekrar dene.", { status: r.status });
  if (type.includes("json")) {
    try {
      const j = JSON.parse(text) as Record<string, unknown>;
      const msg = [j.error, j.detail, j.message].find((m): m is string => typeof m === "string" && m.trim() !== "");
      if (msg) return tServer(msg);
    } catch {
      // JSON çözülemediyse gövdenin kısa hâli aşağıda gösterilir.
    }
  }
  return tServer(text.slice(0, 300)) || `${r.status} ${r.statusText}`;
}

export const api = {
  runtime: () => call<RuntimeState>("GET", "/api/runtime"),
  runtimeAction: (action: "start" | "stop" | "toggle-auto") =>
    call<RuntimeState>("POST", `/api/runtime/${action}`),
  metrics: () => call<Metric[]>("GET", "/api/runtime/metrics"),
  runtimeLog: () => call<{ lines: string[] }>("GET", "/api/runtime/log"),
  threads: (mode: string) => call<Thread[]>("GET", `/api/threads?mode=${mode}`),
  thread: (id: string) => call<ThreadDetail>("GET", `/api/threads/${id}`),
  deleteThread: (id: string) => call<{ ok: boolean }>("DELETE", `/api/threads/${id}`),
  createJob: (body: { thread_id: string | null; kind: Job["kind"]; prompt: string; params?: ImageParams | VideoParams | Model3DParams | MusicParams | SpeechParams | TranscribeParams; inputs: string[] }) =>
    call<Job>("POST", "/api/jobs", body),
  job: (id: string): Promise<Job> => call<Job>("GET", `/api/jobs/${id}`),
  // İptal ucu iptal edilen işin son hâlini döndürür ({ok} değil).
  cancelJob: (id: string): Promise<Job> => call<Job>("POST", `/api/jobs/${id}/cancel`),
  models: () => call<Record<string, ModelInfo>>("GET", "/api/models"),
  settings: () => call<Record<string, unknown>>("GET", "/api/settings"),
  saveSetting: (key: string, value: unknown) => call<{ ok: boolean }>("PUT", `/api/settings/${key}`, value),
  presets: (mode: string) => call<Preset[]>("GET", `/api/presets?mode=${mode}`),
  savePreset: (mode: string, name: string, params: ImageParams) =>
    call<{ id: string }>("POST", "/api/presets", { mode, name, params }),
  deletePreset: (id: string) => call<{ ok: boolean }>("DELETE", `/api/presets/${id}`),
  upload: async (file: File): Promise<Upload> => {
    const form = new FormData();
    form.append("file", file);
    const r = await send("POST", "/api/uploads", undefined, form);
    if (!r.ok) throw new ApiError(r.status, await errorText(r));
    return r.json();
  },
};

export const mediaUrl = (name: string) => `/media/${name}`;
