import { expect, type Page } from "@playwright/test";
import type { Job, Preset, RuntimeState, Thread } from "../src/api";

// 1x1 PNG
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==", "base64");

/** Tek üçgenlik geçerli bir GLB: 3D görüntüleyicinin gerçekten yükleyip çizdiği en küçük model. */
function triangleGlb() {
  const bin = Buffer.from(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]).buffer);
  const gltf = {
    asset: { version: "2.0" }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 } }] }],
    buffers: [{ byteLength: bin.length }], bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: bin.length }],
    accessors: [{ bufferView: 0, componentType: 5126, count: 3, type: "VEC3", min: [0, 0, 0], max: [1, 1, 0] }],
  };
  let json = Buffer.from(JSON.stringify(gltf));
  json = Buffer.concat([json, Buffer.alloc((4 - (json.length % 4)) % 4, " ")]);
  const header = Buffer.alloc(12);
  header.writeUInt32LE(0x46546c67, 0);
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(12 + 8 + json.length + 8 + bin.length, 8);
  const chunk = (data: Buffer, type: number) => {
    const h = Buffer.alloc(8);
    h.writeUInt32LE(data.length, 0);
    h.writeUInt32LE(type, 4);
    return Buffer.concat([h, data]);
  };
  return Buffer.concat([header, chunk(json, 0x4e4f534a), chunk(bin, 0x004e4942)]);
}
const GLB = triangleGlb();

export const LONG_ERROR =
  "GPU açılamadı: komut başarısız (1): ssh -F ~/.local/share/colab-studio/ssh/config colab bash /content/station/runtime_setup.sh\n…\n" +
  "  File \"/content/hfenv/lib/python3.13/site-packages/huggingface_hub/file_download.py\", line 589, in xet_get_very_long_function_name_without_spaces_" +
  "x".repeat(120) +
  "\nRuntimeError: Data processing error: CAS service error : Reqwest Error: HTTP status client error (416 Range Not Satisfiable)";

export function runtimeState(over: Partial<RuntimeState> = {}): RuntimeState {
  return {
    phase: "off",
    detail: "",
    auto_shutdown: true,
    idle_seconds: null,
    idle_minutes: 15,
    credits: { balance: 193.31, rate: 0.08 },
    session_spent: null,
    session_seconds: null,
    startup: null,
    credit_stats: { today: 3.45, week: 3.45, per_day: 18.19, tracked_days: 0.2 },
    queue: { order: [], current: null },
    gpu: "L4",
    ...over,
  };
}

export const STARTING = runtimeState({
  phase: "starting",
  detail: "Modeller indiriliyor, ComfyUI kuruluyor",
  session_spent: 0,
  startup: {
    progress: 0.15, remaining_seconds: 183, elapsed_seconds: 34, step: 2,
    steps: ["GPU ayrılıyor", "Bağlanılıyor", "Modeller indiriliyor, ComfyUI kuruluyor", "ComfyUI başlatılıyor"],
  },
});

export const READY = runtimeState({ phase: "ready", detail: "GPU hazır", idle_seconds: 125, session_spent: 1.234, session_seconds: 3725 });

let seq = 0;
export function job(over: Partial<Job> = {}): Job {
  seq += 1;
  return {
    id: `job${seq}`,
    thread_id: "t1",
    kind: "image",
    prompt: "Ahşap masada seramik kahve fincanı, sabah ışığı",
    params: { ratio: "2:3", size: "1.5K", width: 1024, height: 1536, count: 1, steps: 25, cfg: 3, sampler: "euler", scheduler: "simple", seed: 123, negative: "" },
    inputs: [],
    status: "done",
    progress: 1,
    error: null,
    stage: null,
    outputs: [`job${seq}_0.png`],
    text: null,
    created_at: 1_790_000_000 + seq,
    finished_at: 1_790_000_060 + seq,
    ...over,
  };
}

/** Uygulamanın kullandığı tüm uçların sahte karşılığı; istekleri kaydeder ve bilinmeyen bir uca
 * ya da 4xx/5xx dönen bir isteğe testin sonunda takılır. */
export class MockApi {
  rt: RuntimeState = runtimeState();
  threads: Record<string, Thread & { jobs: Job[] }> = {};
  jobs: Record<string, Job> = {};
  settings: Record<string, unknown> = {};
  presets: Preset[] = [];
  requests: { method: string; path: string; body: unknown; at: number }[] = [];
  unexpected: string[] = [];
  /** Sıradaki isteklere zorla verilecek hata yanıtları: yol öneki -> durum kodu (bir kez). */
  failNext: { prefix: string; status: number; body: string }[] = [];
  onJobCreate: (j: Job) => Job = (j) => j;
  /** Arayüz dili: testlerin çoğu Türkçe metinleri denetler; null = hiç seçilmemiş (varsayılan İngilizce). */
  lang: "tr" | "en" | null = "tr";
  models = {
    image: { label: "Noct-Q V4", description: { en: "Qwen-Image 2.1 based fine-tune.", tr: "Qwen-Image 2.1 tabanlı ince ayar." } },
    video: { label: "MiniMax H3 (w6a8)", description: { en: "Generates video and audio together.", tr: "Görüntü ve sesi birlikte üretir." } },
    model3d: { label: "TRELLIS.2", description: { en: "A textured 3D model from one image.", tr: "Tek görselden dokulu 3D model." } },
    music: { label: "YuE2", description: { en: "Songs with vocals from a style and lyrics.", tr: "Tarz ve sözlerden vokalli şarkı üretir." } },
  };
  logLines = ["20:39:45 [starting] GPU ayrılıyor"];

  thread(id: string, title: string, mode: string, jobs: Job[]) {
    this.threads[id] = { id, title, mode, created_at: 1_790_000_000, jobs: jobs.map((j) => ({ ...j, thread_id: id })) };
    for (const j of this.threads[id].jobs) this.jobs[j.id] = j;
    return this.threads[id];
  }

  async install(page: Page) {
    await page.addInitScript((lang) => {
      if (lang && !sessionStorage.getItem("lang-set")) {
        // Yalnızca ilk yüklemede: dil düğmesiyle yapılan seçim yeniden yüklemede ezilmesin.
        localStorage.setItem("studio.lang", lang);
        sessionStorage.setItem("lang-set", "1");
      }
    }, this.lang);
    await page.addInitScript(() => {
      for (const c of [window.WebGL2RenderingContext, window.WebGLRenderingContext]) {
        // WebGL çizim çağrıları sahte: başsız tarayıcıda GPU olmadan da çizim yapılmış gibi görünür.
        const proto = (c?.prototype ?? null) as unknown as Record<string, unknown> | null;
        for (const n of ["drawElements", "drawArrays", "drawElementsInstanced", "drawArraysInstanced", "drawRangeElements"] as const)
          if (proto?.[n]) proto[n] = function () {};
      }
    });
    page.on("pageerror", (e) => this.unexpected.push(`sayfa hatası: ${e.message}`));
    page.on("console", (m) => {
      // Başarısız isteklerin tarayıcı kaydı ayrıca sayılmaz; onları sahte sunucu kendisi kaydeder.
      if (m.type() === "error" && !m.text().includes("Failed to load resource"))
        this.unexpected.push(`konsol: ${m.text()}`);
    });
    await page.route("**/media/**", (route) => {
      const url = route.request().url();
      if (url.endsWith(".glb")) return route.fulfill({ status: 200, contentType: "model/gltf-binary", body: GLB });
      const type = url.endsWith(".png") ? "image/png" : url.endsWith(".mp4") ? "video/mp4" : "audio/mpeg";
      return route.fulfill({ status: 200, contentType: type, body: url.endsWith(".png") ? PNG : Buffer.alloc(0) });
    });
    await page.route("**/api/**", async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      const path = url.pathname;
      const method = req.method();
      let body: unknown = null;
      if (method === "POST" || method === "PUT") {
        try {
          body = req.postDataJSON();
        } catch {
          body = req.postData();
        }
      }
      this.requests.push({ method, path, body, at: Date.now() });
      const forced = this.failNext.findIndex((f) => path.startsWith(f.prefix));
      if (forced >= 0) {
        const f = this.failNext.splice(forced, 1)[0];
        return route.fulfill({ status: f.status, contentType: "text/html", body: f.body });
      }
      const json = (data: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
      const notFound = () => {
        this.unexpected.push(`404: ${method} ${path}`);
        return route.fulfill({ status: 404, body: "yok" });
      };

      if (path === "/api/runtime" && method === "GET") return json(this.rt);
      const action = path.match(/^\/api\/runtime\/(start|stop|toggle-auto)$/);
      if (action && method === "POST") {
        if (action[1] === "start") this.rt = { ...STARTING };
        if (action[1] === "stop") this.rt = runtimeState({ detail: "elle kapatıldı" });
        if (action[1] === "toggle-auto") this.rt = { ...this.rt, auto_shutdown: !this.rt.auto_shutdown };
        return json(this.rt);
      }
      if (path === "/api/runtime/metrics") {
        const m = (i: number) => ({ t: i, gpu: 40 + i, temp: 61, vram_used: 18.2 * 2 ** 30, vram_total: 22.5 * 2 ** 30,
          ram_used: 30.1 * 2 ** 30, ram_total: 53 * 2 ** 30, disk_used: 60 * 2 ** 30, disk_total: 235 * 2 ** 30 });
        return json(this.rt.phase === "ready" ? [m(1), m(2), m(3)] : []);
      }
      if (path === "/api/runtime/log") return json({ lines: this.logLines });
      if (path === "/api/threads" && method === "GET") {
        const mode = url.searchParams.get("mode");
        return json(Object.values(this.threads).filter((t) => t.mode === mode).map(({ jobs: _j, ...t }) => t));
      }
      const th = path.match(/^\/api\/threads\/([^/]+)$/);
      if (th) {
        const t = this.threads[th[1]];
        if (!t) return notFound();
        if (method === "DELETE") {
          delete this.threads[th[1]];
          return json({ ok: true });
        }
        return json({ ...t, jobs: t.jobs.map((j) => this.jobs[j.id] ?? j) });
      }
      if (path === "/api/uploads" && method === "POST") {
        const name = `uploads/u${this.requests.length}${(req.postData() ?? "").includes("audio/") ? ".mp3" : ".png"}`;
        return json({ name, url: `/media/${name}` });
      }
      if (path === "/api/jobs" && method === "POST") {
        const b = body as { thread_id: string | null; kind: Job["kind"]; prompt: string; params?: Job["params"]; inputs: string[] };
        const kindMode = b.kind === "enhance" ? "image" : b.kind;
        let tid = b.thread_id;
        if (b.kind !== "enhance" && !tid) tid = this.thread(`t${Object.keys(this.threads).length + 100}`, b.prompt, kindMode, []).id;
        const j = this.onJobCreate(job({ thread_id: b.kind === "enhance" ? null : tid, kind: b.kind, prompt: b.prompt,
          params: b.params ?? {}, inputs: b.inputs, status: "queued", progress: 0, outputs: [], finished_at: null }));
        this.jobs[j.id] = j;
        if (tid) this.threads[tid].jobs.push(j);
        return json(j);
      }
      const cancel = path.match(/^\/api\/jobs\/([^/]+)\/cancel$/);
      if (cancel && method === "POST") {
        const j = this.jobs[cancel[1]];
        if (!j) return notFound();
        Object.assign(j, { status: "error", error: "İptal edildi", cancelled: true, finished_at: 1_790_000_999 });
        this.rt = { ...this.rt, queue: { order: this.rt.queue.order.filter((id) => id !== j.id), current: null } };
        return json(j);
      }
      const jb = path.match(/^\/api\/jobs\/([^/]+)$/);
      if (jb) return this.jobs[jb[1]] ? json(this.jobs[jb[1]]) : notFound();
      if (path === "/api/models" && method === "GET") return json(this.models);
      if (path === "/api/settings" && method === "GET") return json(this.settings);
      const st = path.match(/^\/api\/settings\/(.+)$/);
      if (st && method === "PUT") {
        this.settings[decodeURIComponent(st[1])] = body;
        return json({ ok: true });
      }
      if (path === "/api/presets" && method === "GET") return json(this.presets.filter((p) => p.mode === url.searchParams.get("mode")));
      if (path === "/api/presets" && method === "POST") {
        const b = body as Preset;
        const p = { ...b, id: `p${this.presets.length}` };
        this.presets.push(p);
        return json({ id: p.id });
      }
      const pr = path.match(/^\/api\/presets\/(.+)$/);
      if (pr && method === "DELETE") {
        this.presets = this.presets.filter((p) => p.id !== pr[1]);
        return json({ ok: true });
      }
      return notFound();
    });
  }

  assertClean() {
    expect(this.unexpected, "beklenmeyen istek ya da hata").toEqual([]);
  }
}

/** Görünür her öğe ebeveyninin sınırları içinde mi (yatay taşma) ve sayfa yatay kaymıyor mu? */
export async function overflowProblems(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const problems: string[] = [];
    const doc = document.documentElement;
    if (doc.scrollWidth > doc.clientWidth + 1) problems.push(`sayfa yatay kayıyor: ${doc.scrollWidth} > ${doc.clientWidth}`);
    const describe = (el: Element) =>
      `${el.tagName.toLowerCase()}${el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\s+/).join(".") : ""} "${(el.textContent ?? "").trim().slice(0, 40)}"`;
    for (const el of Array.from(document.querySelectorAll("body *"))) {
      if (el.closest("svg") && el.tagName.toLowerCase() !== "svg") continue;
      const cs = getComputedStyle(el);
      if (cs.display === "none" || cs.visibility === "hidden" || cs.position === "fixed" || cs.position === "absolute") continue;
      if (cs.animationName !== "none") continue; // dönen yükleniyor ikonunun kutusu dönerken büyür
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      const parent = el.parentElement;
      if (!parent || parent === document.body) continue;
      const pcs = getComputedStyle(parent);
      // Yatay kayan kapsayıcıların içi taşabilir; gizleyen (hidden) kapsayıcı da taşmayı keser ama
      // içerik kaybolur, o yüzden yine sayılır.
      if (pcs.overflowX === "auto" || pcs.overflowX === "scroll") continue;
      const p = parent.getBoundingClientRect();
      if (r.right > p.right + 1 || r.left < p.left - 1) problems.push(`${describe(el)} ebeveyninden taşıyor (${Math.round(r.left)}-${Math.round(r.right)} / ${Math.round(p.left)}-${Math.round(p.right)})`);
      if (r.right > window.innerWidth + 1) problems.push(`${describe(el)} ekrandan taşıyor`);
    }
    return problems;
  });
}
