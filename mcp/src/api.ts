import { exec, execFile } from "node:child_process";
import { promisify } from "node:util";
import fs from "node:fs/promises";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import os from "node:os";
import path from "node:path";

const execAsync = promisify(exec);
const execFileAsync = promisify(execFile);

/**
 * Stüdyonun .env dosyası (repo kökü, bu dosyanın iki üst dizini): port ve veri dizini oradan okunur,
 * böylece MCP ek ayar istemez. Ortam değişkeni verilmişse o geçerlidir.
 */
function studioEnv(): Record<string, string> {
  const file = process.env.STUDIO_ENV_FILE || path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../.env");
  try {
    return Object.fromEntries(
      readFileSync(file, "utf8")
        .split("\n")
        .map((l) => l.trim())
        .filter((l) => l && !l.startsWith("#") && l.includes("="))
        .map((l) => {
          const i = l.indexOf("=");
          let v = l.slice(i + 1).trim();
          if (v.length >= 2 && (v[0] === '"' || v[0] === "'") && v.endsWith(v[0])) v = v.slice(1, -1);
          return [l.slice(0, i).trim().replace(/^export\s+/, ""), v];
        }),
    );
  } catch {
    return {};
  }
}
const ENV: Record<string, string | undefined> = { ...studioEnv(), ...process.env };

/** Stüdyo API'si (server/app.py). */
export const API_BASE = (ENV.STUDIO_URL || `http://127.0.0.1:${ENV.STUDIO_PORT || "8190"}`).replace(/\/+$/, "");
/** Medya dosyalarının istemciye verilen taban adresi (stüdyo `/media` yolundan sunar). */
export const PUBLIC_MEDIA_URL_BASE = (ENV.STUDIO_PUBLIC_MEDIA_URL || `${API_BASE}/media`).replace(/\/+$/, "");
/** colab CLI (PATH'te değilse COLAB_BIN ile verilir). */
const COLAB_BIN = ENV.COLAB_BIN || "colab";

/** `server/studio.py:JOB_TIMEOUT` — bir işin 30 dakikada bitmesi beklenir. */
export const JOB_TIMEOUT_SECONDS = 1800;
/** `server/studio.py:CANCELLED` — iptal edilen iş `status=error, cancelled=true` olarak kapanır; bu metin
 * yalnızca `cancelled` alanını göndermeyen eski stüdyolar için yedek. */
export const CANCELLED_JOB_ERROR = "İptal edildi";
/** Terminal durumlar (`server/studio.py:JOB_TRANSITIONS`): backend bir işi bunlardan geri açmaz. */
export const TERMINAL_JOB_STATUSES = ["done", "error"] as const;
/** Stüdyonun modları (`server/app.py:MODES`). */
export const STUDIO_MODES = ["image", "video", "music", "speech", "transcribe", "model3d"] as const;
export type StudioMode = (typeof STUDIO_MODES)[number];
/**
 * Yönetici araçları (servisi yeniden başlatma, ayar yazma, thread silme, ham CLI çıktısı) varsayılan
 * kapalıdır: araç listesini kısaltır ve aynı stüdyoyu kullanan başka bir ajanın işini kesmesini önler.
 */
export const ADMIN_TOOLS = /^(1|true|yes|on)$/i.test((process.env.MCP_ADMIN_TOOLS || "").trim());

/** `/api/uploads` sözleşmesi: multipart alanı `file`, en fazla 50 MB (aiohttp client_max_size). */
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
/** API'nin kabul ettiği içerik tipleri (`server/app.py:UPLOAD_TYPES`) ve uzantı karşılıkları. */
export const UPLOAD_MIME_TYPES: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".mp3": "audio/mpeg",
  ".wav": "audio/wav",
  ".flac": "audio/flac",
  ".ogg": "audio/ogg",
  ".webm": "audio/webm",
  ".m4a": "audio/mp4",
};

const MEDIA_MIME_TYPES: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".mp3": "audio/mpeg",
  ".wav": "audio/wav",
  ".flac": "audio/flac",
  ".ogg": "audio/ogg",
  ".mp4": "video/mp4",
  ".webm": "video/webm",
  ".m4a": "audio/mp4",
  ".glb": "model/gltf-binary",
  ".ply": "application/octet-stream",
  ".txt": "text/plain",
  ".json": "application/json",
};

/** `/api/runtime/metrics` kayıtları (bayt cinsinden VRAM/RAM/disk). */
export interface GpuMetricsSnapshot {
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

/** `runtime.snapshot()["gpu_now"]` — metrics'in son kaydının kısa hâli. */
export type GpuNow = Pick<GpuMetricsSnapshot, "t" | "gpu" | "vram_used" | "vram_total">;

export interface CreditsInfo {
  balance: number;
  rate: number;
}

export interface CreditsError {
  error: string;
}

/** Başlangıçta `null`, okunamazsa `{error}` döner: her iki durum da normaldir. */
export type RuntimeCredits = CreditsInfo | CreditsError | null;

export interface StartupProgress {
  progress: number;
  remaining_seconds: number;
  elapsed_seconds: number;
  step: number;
  steps: string[];
  step_elapsed_seconds: number;
  download?: unknown;
  pack?: string;
  setup?: string | null;
}

export interface QueueCurrent {
  id: string;
  kind: string;
  status: string;
  stage?: string | null;
  progress: number;
  prompt: string;
}

export interface CreditStats {
  today: number;
  week: number;
  per_day: number;
  tracked_days: number;
}

export interface RuntimeStatus {
  /** `server/runtime.py:PHASE_TRANSITIONS`. */
  phase: "off" | "starting" | "ready" | "stopping" | "error";
  detail: string | null;
  auto_shutdown: boolean;
  idle_seconds: number | null;
  idle_minutes: number;
  credits: RuntimeCredits;
  session_spent: number | null;
  session_seconds: number | null;
  startup: StartupProgress | null;
  gpu: string;
  packs: Record<string, unknown>;
  gpu_now: GpuNow | null;
  credit_stats: CreditStats | null;
  queue: {
    order: string[];
    current: QueueCurrent | null;
    starts_in: Record<string, number | null>;
  } | null;
}

export type JobStatus = "queued" | "waiting_gpu" | "running" | "done" | "error";

export interface JobLiveState {
  progress: number;
  eta: number | null;
  stage_seconds: number | null;
  stage_expected: number | null;
  loading: boolean;
}

export interface JobRecord {
  id: string;
  thread_id: string | null;
  mode: string;
  kind: string;
  prompt: string;
  params: Record<string, unknown>;
  inputs: string[];
  status: JobStatus;
  /** status=error ve iş iptal edildi (kullanıcı ya da elle GPU kapatma); hata değil. */
  cancelled?: boolean;
  stage?: string | null;
  live?: JobLiveState | null;
  progress: number;
  error: string | null;
  outputs: string[];
  text: string | null;
  created_at: number;
  finished_at: number | null;
}

export interface JobCreatePayload {
  kind: "image" | "video" | "music" | "speech" | "transcribe" | "model3d" | "enhance";
  prompt: string;
  inputs?: string[];
  params?: Record<string, unknown>;
  thread_id?: string;
}

export interface ThreadItem {
  id: string;
  mode: string;
  title: string;
  created_at: number;
  jobs?: JobRecord[];
}

export interface PresetItem {
  id: string;
  mode: string;
  name: string;
  params: Record<string, unknown>;
}

export interface MediaFileInfo {
  filename: string;
  sizeBytes: number;
  modifiedAt: string;
  publicUrl: string;
  isOutput: boolean;
}

export interface SystemHealthSummary {
  service: string;
  uptime: string;
  diskUsage: string;
  memoryUsage: string;
  colabSessions: string;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** İş zamanında bitmediğinde fırlatılır: çağıran taraf işi iptal etmekle yükümlüdür. */
export class JobTimeoutError extends Error {
  readonly jobId: string;
  readonly timeoutSeconds: number;
  readonly lastJob: JobRecord | null;

  constructor(jobId: string, timeoutSeconds: number, lastJob: JobRecord | null) {
    super(`Timeout waiting for job ${jobId} after ${timeoutSeconds} seconds`);
    this.name = "JobTimeoutError";
    this.jobId = jobId;
    this.timeoutSeconds = timeoutSeconds;
    this.lastJob = lastJob;
  }
}

export function isTerminalStatus(status: string): boolean {
  return (TERMINAL_JOB_STATUSES as readonly string[]).includes(status);
}

/** İptal edilen iş terminal `error` durumundadır; stüdyo bunu `cancelled` alanıyla bildirir. */
export function isCancelledJob(job: Pick<JobRecord, "error" | "cancelled"> | null | undefined): boolean {
  if (typeof job?.cancelled === "boolean") return job.cancelled;
  return typeof job?.error === "string" && job.error.trim() === CANCELLED_JOB_ERROR;
}

/** Göreli medya adını (`uploads/x.png`) genel URL'ye çevirir. */
export function mediaPublicUrl(relativeName: string): string {
  const clean = relativeName.replace(/^\/+/, "");
  return `${PUBLIC_MEDIA_URL_BASE}/${clean}`;
}

function abortError(signal: AbortSignal): Error {
  const reason: unknown = signal.reason;
  if (reason instanceof Error) {
    return reason;
  }
  const error = new Error("Aborted");
  error.name = "AbortError";
  return error;
}

export function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError(signal));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, Math.max(0, ms));
    function onAbort(): void {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      reject(abortError(signal as AbortSignal));
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/** Stüdyoya istek: ağ hatası (stüdyo kapalı, yanlış port) ajana çıplak "fetch failed" yerine stüdyonun
 * beklendiği adresi ve nasıl başlatılacağını söyler. Zaman aşımı ve iptal olduğu gibi geçer. */
async function studioFetch(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch (error: unknown) {
    const name = (error as { name?: string })?.name;
    if (name === "AbortError" || name === "TimeoutError") throw error;
    throw new Error(
      `Colab Studio is not reachable at ${API_BASE} (${error instanceof Error ? error.message : String(error)}). ` +
        "Is it running? Start it with 'systemctl --user start colab-studio', './run.sh' or 'docker compose up -d'; " +
        "set STUDIO_URL if it listens on another address.",
      { cause: error }
    );
  }
}

export async function fetchJson<T>(apiPath: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE}${apiPath.startsWith("/") ? "" : "/"}${apiPath}`;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (init?.body !== undefined && init.body !== null) {
    headers["Content-Type"] = "application/json";
  }
  const response = await studioFetch(url, {
    ...init,
    headers: {
      ...headers,
      ...((init?.headers as Record<string, string> | undefined) || {}),
    },
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new ApiError(
      `Colab Studio API Error (${response.status} ${response.statusText}): ${errorText}`,
      response.status
    );
  }

  return response.json() as Promise<T>;
}

/** Geçici hata mı: 5xx/429 ya da ağ katmanı hatası (DNS, bağlantı reddi, reset). */
function isTransientError(error: unknown): boolean {
  if (error instanceof ApiError) {
    return error.status >= 500 || error.status === 429;
  }
  if (error && typeof error === "object" && (error as { name?: string }).name === "AbortError") {
    return false;
  }
  return true;
}

const JOB_POLL_RETRIES = 3;

export async function getRuntimeStatus(): Promise<RuntimeStatus> {
  return fetchJson<RuntimeStatus>("/api/runtime");
}

export async function startRuntime(): Promise<RuntimeStatus> {
  return fetchJson<RuntimeStatus>("/api/runtime/start", { method: "POST" });
}

export async function stopRuntime(): Promise<RuntimeStatus> {
  return fetchJson<RuntimeStatus>("/api/runtime/stop", { method: "POST" });
}

export async function toggleAutoShutdown(): Promise<RuntimeStatus> {
  return fetchJson<RuntimeStatus>("/api/runtime/toggle-auto", { method: "POST" });
}

export async function getRuntimeMetrics(): Promise<GpuMetricsSnapshot[]> {
  return fetchJson<GpuMetricsSnapshot[]>("/api/runtime/metrics");
}

export async function getRuntimeLogs(): Promise<{ lines: string[] }> {
  return fetchJson<{ lines: string[] }>("/api/runtime/log");
}

export async function createJob(payload: JobCreatePayload): Promise<JobRecord> {
  return fetchJson<JobRecord>("/api/jobs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** Yoklama sırasında geçici ağ hatası tek bir turu boşa harcamasın: 3 kez denenir. */
export async function getJob(jobId: string, signal?: AbortSignal): Promise<JobRecord> {
  const apiPath = `/api/jobs/${encodeURIComponent(jobId)}`;
  let lastError: unknown = null;

  for (let attempt = 1; attempt <= JOB_POLL_RETRIES; attempt++) {
    try {
      return await fetchJson<JobRecord>(apiPath, { signal });
    } catch (error: unknown) {
      lastError = error;
      if (!isTransientError(error) || attempt === JOB_POLL_RETRIES) {
        throw error;
      }
      await delay(400 * attempt, signal);
    }
  }

  throw lastError instanceof Error ? lastError : new Error(String(lastError));
}

/** İptal uç noktası iş kaydının tamamını döner (`{ok}` değil). */
export async function cancelJob(jobId: string): Promise<JobRecord> {
  return fetchJson<JobRecord>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
  });
}

/**
 * İş terminal duruma (`done|error`) gelene kadar yoklar.
 * Zaman aşımında `JobTimeoutError` fırlatır; `signal` iptalinde abort hatası.
 */
export async function waitForJob(
  jobId: string,
  timeoutSeconds: number = JOB_TIMEOUT_SECONDS,
  pollIntervalSeconds: number = 3,
  signal?: AbortSignal,
  onPoll?: (job: JobRecord) => void
): Promise<JobRecord> {
  const controller = new AbortController();
  let timedOut = false;
  let lastJob: JobRecord | null = null;

  const forwardAbort = (): void => controller.abort(signal?.reason ?? new Error("Aborted"));
  if (signal) {
    if (signal.aborted) {
      forwardAbort();
    } else {
      signal.addEventListener("abort", forwardAbort, { once: true });
    }
  }

  const timeoutMs = Math.max(1, timeoutSeconds) * 1000;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  try {
    for (;;) {
      lastJob = await getJob(jobId, controller.signal);
      onPoll?.(lastJob);
      if (isTerminalStatus(lastJob.status)) {
        return lastJob;
      }
      await delay(pollIntervalSeconds * 1000, controller.signal);
    }
  } catch (error: unknown) {
    if (timedOut) {
      throw new JobTimeoutError(jobId, timeoutSeconds, lastJob);
    }
    throw error;
  } finally {
    clearTimeout(timer);
    if (signal) {
      signal.removeEventListener("abort", forwardAbort);
    }
  }
}

/**
 * Medya kitaplığı (`GET /api/media`): kökteki üretim çıktıları ve `uploads/` altındaki yüklemeler, en yeni
 * önce. Stüdyonun diskine bakılmaz: stüdyo Docker'da ya da başka bir makinede olabilir.
 */
export async function listMediaFiles(limit: number = 30, extensionFilter?: string): Promise<MediaFileInfo[]> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (extensionFilter) query.set("ext", extensionFilter);
  const { files } = await fetchJson<{ files: Array<{ name: string; size: number; mtime: number; is_output: boolean }> }>(`/api/media?${query}`);
  return files.map((f) => ({
    filename: f.name,
    sizeBytes: f.size,
    modifiedAt: new Date(f.mtime * 1000).toISOString(),
    publicUrl: mediaPublicUrl(f.name),
    isOutput: f.is_output,
  }));
}

/**
 * Dosyayı API üzerinden yükler (`POST /api/uploads`, multipart alanı `file`).
 * Böylece içerik tipi ve 50 MB sınırı backend tarafından doğrulanır; stüdyonun diskine doğrudan yazılmaz.
 */
export async function uploadMedia(
  buffer: Buffer,
  filename: string
): Promise<{ uploadPath: string; publicUrl: string; sizeBytes: number; serverUrl: string | null }> {
  const extension = path.extname(filename).toLowerCase();
  const mimeType = UPLOAD_MIME_TYPES[extension];
  if (!mimeType) {
    throw new Error(
      `Unsupported file extension: ${extension || "(none)"}. Supported:PNG, JPG, JPEG, WebP, MP3, WAV, FLAC, OGG, WebM, M4A`
    );
  }
  if (buffer.length === 0) {
    throw new Error("Uploaded file is empty");
  }
  if (buffer.length > MAX_UPLOAD_BYTES) {
    throw new Error(
      `File is ${(buffer.length / (1024 * 1024)).toFixed(1)} MB, the API accepts at most ${MAX_UPLOAD_BYTES / (1024 * 1024)} MB`
    );
  }

  const form = new FormData();
  form.append("file", new Blob([new Uint8Array(buffer)], { type: mimeType }), path.basename(filename) || `upload${extension}`);

  const response = await studioFetch(`${API_BASE}/api/uploads`, {
    method: "POST",
    body: form,
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new ApiError(
      `Upload failed (${response.status} ${response.statusText}): ${errorText}`,
      response.status
    );
  }

  const payload = (await response.json()) as { name?: unknown; url?: unknown };
  const name = typeof payload.name === "string" ? payload.name : "";
  if (!name) {
    throw new Error(`Upload response did not contain a file name: ${JSON.stringify(payload)}`);
  }

  return {
    uploadPath: name,
    publicUrl: mediaPublicUrl(name),
    sizeBytes: buffer.length,
    serverUrl: typeof payload.url === "string" ? `${API_BASE}${payload.url}` : null,
  };
}

/** Medya adını doğrular (`abc_0.png`, `uploads/x.jpg`); dizin dışına çıkan ya da mutlak yol reddedilir. */
function mediaName(filenameOrRelative: string): string {
  const name = path.posix.normalize(filenameOrRelative.replace(/\\/g, "/")).replace(/^\.\/+/, "");
  if (!name || name === "." || name.startsWith("/") || name.split("/").includes("..")) {
    throw new Error(`Path escapes the media directory: ${filenameOrRelative}`);
  }
  return name;
}

/** Medya dosyasını stüdyonun `/media` adresinden okur (stüdyo nerede çalışırsa çalışsın aynı yol). */
async function fetchMedia(filenameOrRelative: string, maxBytes: number): Promise<{ name: string; data: Buffer }> {
  const name = mediaName(filenameOrRelative);
  const response = await studioFetch(`${API_BASE}/media/${name.split("/").map(encodeURIComponent).join("/")}`, {
    signal: AbortSignal.timeout(120_000),
  });
  if (response.status === 404) {
    await response.body?.cancel();
    throw new Error(`Media file not found: ${name} (list_media shows the available files)`);
  }
  if (!response.ok) {
    await response.body?.cancel();
    throw new ApiError(`Colab Studio media error (${response.status} ${response.statusText}) for ${name}`, response.status);
  }
  const declared = Number(response.headers.get("content-length") || 0);
  if (declared > maxBytes) {
    await response.body?.cancel();
    throw new Error(`File size (${Math.round(declared / 1024)} KB) exceeds maximum allowed size of ${Math.round(maxBytes / 1024)} KB`);
  }
  const data = Buffer.from(await response.arrayBuffer());
  if (data.length > maxBytes) {
    throw new Error(`File size (${Math.round(data.length / 1024)} KB) exceeds maximum allowed size of ${Math.round(maxBytes / 1024)} KB`);
  }
  return { name, data };
}

export async function readMediaBase64(
  filenameOrRelative: string,
  maxSizeKb: number = 4096
): Promise<{ mimeType: string; base64: string; sizeBytes: number; filename: string }> {
  const { name, data } = await fetchMedia(filenameOrRelative, maxSizeKb * 1024);
  return {
    mimeType: MEDIA_MIME_TYPES[path.extname(name).toLowerCase()] || "application/octet-stream",
    base64: data.toString("base64"),
    sizeBytes: data.length,
    filename: name,
  };
}

/** Uzantıdan içerik tipine; yüklemede URL'nin adı yoksa ters yönde kullanılır. */
function extensionForMime(mime: string): string | undefined {
  const clean = mime.split(";")[0].trim().toLowerCase();
  return Object.entries(UPLOAD_MIME_TYPES).find(([, value]) => value === clean)?.[0];
}

/**
 * `http(s)` adresindeki dosyayı indirir (en fazla 50 MB, akış sırasında da sayılır).
 * Ajanın dosyayı base64 olarak yazması gerekmesin diye: büyük dosyalar token harcamadan yüklenir.
 */
export async function downloadForUpload(
  sourceUrl: string,
  filenameHint?: string
): Promise<{ buffer: Buffer; filename: string }> {
  let url: URL;
  try {
    url = new URL(sourceUrl);
  } catch {
    throw new Error(`source_url is not a valid URL: ${sourceUrl}`);
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error(`source_url must be http(s), got ${url.protocol}`);
  }

  const response = await fetch(url, { redirect: "follow", signal: AbortSignal.timeout(120_000) });
  if (!response.ok || !response.body) {
    throw new Error(`Download failed (${response.status} ${response.statusText}) for ${url.href}`);
  }
  const declared = Number(response.headers.get("content-length") || 0);
  if (declared > MAX_UPLOAD_BYTES) {
    throw new Error(`Remote file is ${(declared / (1024 * 1024)).toFixed(1)} MB, the API accepts at most ${MAX_UPLOAD_BYTES / (1024 * 1024)} MB`);
  }

  const chunks: Buffer[] = [];
  let total = 0;
  for await (const chunk of response.body as unknown as AsyncIterable<Uint8Array>) {
    total += chunk.length;
    if (total > MAX_UPLOAD_BYTES) {
      throw new Error(`Remote file exceeds ${MAX_UPLOAD_BYTES / (1024 * 1024)} MB`);
    }
    chunks.push(Buffer.from(chunk));
  }

  let filename = (filenameHint || path.basename(decodeURIComponent(url.pathname)) || "").trim();
  if (!(path.extname(filename).toLowerCase() in UPLOAD_MIME_TYPES)) {
    const ext = extensionForMime(response.headers.get("content-type") || "");
    if (ext) {
      filename = `${path.parse(filename || "upload").name || "upload"}${ext}`;
    }
  }
  return { buffer: Buffer.concat(chunks), filename: filename || "upload" };
}

let ffmpegAvailable: boolean | null = null;

async function hasFfmpeg(): Promise<boolean> {
  if (ffmpegAvailable === null) {
    try {
      await execFileAsync("ffmpeg", ["-version"], { timeout: 10_000 });
      await execFileAsync("ffprobe", ["-version"], { timeout: 10_000 });
      ffmpegAvailable = true;
    } catch {
      ffmpegAvailable = false;
    }
  }
  return ffmpegAvailable;
}

export const VIDEO_EXTENSIONS = new Set([".mp4", ".webm", ".mov", ".mkv"]);

/**
 * Görseli en uzun kenarı `maxDim` olacak şekilde JPEG önizlemeye, videoyu 2x2 kare tablosuna çevirir
 * (ffmpeg). Claude görselleri zaten küçültür: tam PNG göndermek yalnızca aktarımı yavaşlatır.
 * ffmpeg yoksa ya da dönüştürme başarısızsa `null` döner; çağıran taraf özgün dosyaya düşer.
 */
/** Kaynak dosya önizleme için en fazla bu kadar indirilir (uzun videolar dahil). */
// ponytail: dosya bellekte tutulup geçici dosyaya yazılır; çok büyük videolar gerekirse akışla diske yazılır.
const MAX_PREVIEW_SOURCE_BYTES = 512 * 1024 * 1024;

export async function readMediaPreview(
  filenameOrRelative: string,
  maxDim: number
): Promise<{ base64: string; mimeType: string; sizeBytes: number; originalBytes: number; filename: string; kind: "image" | "video" } | null> {
  const relative = mediaName(filenameOrRelative);
  const ext = path.extname(relative).toLowerCase();
  const isImage = (MEDIA_MIME_TYPES[ext] || "").startsWith("image/");
  const isVideo = VIDEO_EXTENSIONS.has(ext);
  if ((!isImage && !isVideo) || !(await hasFfmpeg())) {
    return null;
  }
  // ffmpeg/ffprobe dosyada çalışır (mp4'ün moov kutusu sonda olabilir, akıştan okunamaz): geçici kopya.
  const { data } = await fetchMedia(relative, MAX_PREVIEW_SOURCE_BYTES);
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "colab-studio-mcp-"));
  const resolved = path.join(tmp, `source${ext}`);
  try {
    await fs.writeFile(resolved, data);
    return await renderPreview(resolved, isVideo, maxDim, data.length, relative);
  } finally {
    await fs.rm(tmp, { recursive: true, force: true });
  }
}


async function renderPreview(
  resolved: string,
  isVideo: boolean,
  maxDim: number,
  originalBytes: number,
  relative: string
): Promise<{ base64: string; mimeType: string; sizeBytes: number; originalBytes: number; filename: string; kind: "image" | "video" } | null> {
  const fit = `scale='min(${maxDim},iw)':'min(${maxDim},ih)':force_original_aspect_ratio=decrease`;
  let filter = fit;
  if (isVideo) {
    let duration = 0;
    try {
      const { stdout } = await execFileAsync(
        "ffprobe",
        ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", resolved],
        { timeout: 30_000 }
      );
      duration = parseFloat(stdout.trim());
    } catch {
      return null;
    }
    if (!Number.isFinite(duration) || duration <= 0) {
      return null;
    }
    // 4 kare, süreye eşit aralıklı; her kare tablonun yarı genişliğine sığar.
    const half = Math.max(64, Math.round(maxDim / 2));
    filter = `fps=4/${duration.toFixed(3)},scale='min(${half},iw)':'min(${half},ih)':force_original_aspect_ratio=decrease,tile=2x2`;
  }

  try {
    const { stdout } = await execFileAsync(
      "ffmpeg",
      ["-v", "error", "-i", resolved, "-vf", filter, "-frames:v", "1", "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
      { encoding: "buffer", maxBuffer: 32 * 1024 * 1024, timeout: 60_000 }
    );
    const out = stdout as unknown as Buffer;
    if (!out.length) {
      return null;
    }
    return {
      base64: out.toString("base64"),
      mimeType: "image/jpeg",
      sizeBytes: out.length,
      originalBytes,
      filename: relative,
      kind: isVideo ? "video" : "image",
    };
  } catch {
    return null;
  }
}

/** `mode` verilirse sorguya eklenir; verilmezse istek modsuz gider (backend tüm modları döner). */
function modeQuery(mode?: string | null): string {
  const clean = typeof mode === "string" ? mode.trim() : "";
  return clean ? `?mode=${encodeURIComponent(clean)}` : "";
}

export async function listThreads(mode?: string): Promise<ThreadItem[]> {
  return fetchJson<ThreadItem[]>(`/api/threads${modeQuery(mode)}`);
}

export async function getThread(threadId: string): Promise<ThreadItem> {
  return fetchJson<ThreadItem>(`/api/threads/${encodeURIComponent(threadId)}`);
}

export async function deleteThread(threadId: string): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/threads/${encodeURIComponent(threadId)}`, {
    method: "DELETE",
  });
}

export async function listPresets(mode?: string): Promise<PresetItem[]> {
  return fetchJson<PresetItem[]>(`/api/presets${modeQuery(mode)}`);
}

export async function savePreset(mode: string, name: string, params: Record<string, unknown>): Promise<{ id: string }> {
  return fetchJson<{ id: string }>("/api/presets", {
    method: "POST",
    body: JSON.stringify({ mode, name, params }),
  });
}

export async function deletePreset(presetId: string): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/presets/${encodeURIComponent(presetId)}`, {
    method: "DELETE",
  });
}

export async function getSettings(): Promise<Record<string, unknown>> {
  return fetchJson<Record<string, unknown>>("/api/settings");
}

export async function updateSetting(key: string, value: unknown): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/settings/${encodeURIComponent(key)}`, {
    method: "PUT",
    body: JSON.stringify(value),
  });
}

export async function runColabSessions(): Promise<string> {
  try {
    const { stdout, stderr } = await execFileAsync(COLAB_BIN, ["sessions"]);
    return stdout || stderr || "No output returned.";
  } catch (error: unknown) {
    if ((error as { code?: string }).code === "ENOENT") {
      return `colab CLI (${COLAB_BIN}) is not on this machine. With Docker it lives in the container: docker compose exec studio colab sessions. colab_runtime_status shows the GPU state.`;
    }
    if (error && typeof error === "object" && "message" in error) {
      const err = error as { message: string; stdout?: string; stderr?: string };
      return `Error executing colab sessions: ${err.message}\n${err.stdout || ""} ${err.stderr || ""}`;
    }
    return `Error executing colab sessions: ${String(error)}`;
  }
}

export async function getSystemStatus(): Promise<SystemHealthSummary> {
  let serviceStatus = "unknown";
  try {
    const { stdout } = await execAsync("systemctl --user is-active colab-studio || true");
    serviceStatus = stdout.trim();
  } catch {}

  let uptime = "";
  try {
    const { stdout } = await execAsync("uptime -p");
    uptime = stdout.trim();
  } catch {}

  // Disk, stüdyonun kendi veri dizini için stüdyodan okunur (Docker'da bu makinenin yolu değildir).
  let diskUsage = "";
  try {
    const { disk } = await fetchJson<{ disk: { total: number; used: number; free: number } }>("/api/media?limit=1");
    const gb = (n: number) => `${(n / 1024 ** 3).toFixed(1)} GB`;
    diskUsage = `studio data: ${gb(disk.used)} used of ${gb(disk.total)}, ${gb(disk.free)} free`;
  } catch (error: unknown) {
    diskUsage = `unavailable: ${error instanceof Error ? error.message : String(error)}`;
  }

  let memoryUsage = "";
  try {
    const { stdout } = await execAsync("free -h");
    memoryUsage = stdout.trim();
  } catch {}

  const colabSessions = await runColabSessions();

  return {
    service: serviceStatus,
    uptime,
    diskUsage,
    memoryUsage,
    colabSessions,
  };
}

export async function restartService(): Promise<string> {
  try {
    const { stdout, stderr } = await execAsync("systemctl --user restart colab-studio");
    return stdout || stderr || "Service restarted successfully.";
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`Failed to restart colab-studio (installed as a systemd user service by install.sh --service): ${message}`);
  }
}
