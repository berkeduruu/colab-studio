import { test, type Page } from "@playwright/test";
import { job, MockApi, READY, runtimeState } from "./mock";

// README ekran görüntüleri (docs/screenshots). Sahte API ile, gerçek GPU gerekmez:
//   README_SHOTS=../../docs/screenshots npx playwright test readme-shots
test.skip(!process.env.README_SHOTS, "yalnızca README_SHOTS=<klasör> ile");
const DIR = process.env.README_SHOTS;

/** 1.2 sn'lik yumuşak bir ton (WAV): ses oynatıcıları boş görünmesin. */
function toneWav() {
  const rate = 16000;
  const n = Math.round(rate * 1.2);
  const buf = Buffer.alloc(44 + n * 2);
  buf.write("RIFF", 0);
  buf.writeUInt32LE(36 + n * 2, 4);
  buf.write("WAVEfmt ", 8);
  buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(1, 20);
  buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(rate, 24);
  buf.writeUInt32LE(rate * 2, 28);
  buf.writeUInt16LE(2, 32);
  buf.writeUInt16LE(16, 34);
  buf.write("data", 36);
  buf.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) buf.writeInt16LE(Math.round(Math.sin((i / rate) * 2 * Math.PI * 440) * 3000), 44 + i * 2);
  return buf;
}

async function withAudio(page: Page) {
  const wav = toneWav();
  await page.route(/\/media\/.*\.(mp3|wav)$/, (route) => route.fulfill({ status: 200, contentType: "audio/wav", body: wav }));
}

const GB = 1024 ** 3;

test.use({ viewport: { width: 1440, height: 860 }, deviceScaleFactor: 1 });

test("hero: image mode with the settings panel", async ({ page }) => {
  const api = new MockApi();
  api.lang = null;
  api.rt = { ...READY, gpu_now: { t: 1, gpu: 3, vram_used: 14.2 * GB, vram_total: 22.5 * GB },
    packs: { image: { label: "Görsel", total: 17.3 * GB, done: 17.3 * GB, fraction: 1, rate: null, eta: 0 } } };
  api.thread("h1", "Rainy Istanbul street, neon signs", "image", []);
  api.thread("h2", "Ceramic coffee cup, morning light", "image", []);
  api.thread("h3", "Logo for a mountain café", "image", []);
  await api.install(page);
  await page.goto("/");
  await page.getByText("What do you want to create?").first().waitFor();
  await page.screenshot({ path: `${DIR}/hero.png` });
});

test("startup: the GPU is starting and models are downloading", async ({ page }) => {
  const api = new MockApi();
  api.lang = null;
  const waiting = job({ id: "w1", kind: "video", thread_id: "v1", status: "waiting_gpu", progress: 0, outputs: [], finished_at: null,
    prompt: "A ferry crossing the Bosphorus at sunset, seagull sounds, slow camera pan",
    params: { quality: "480p", ratio: "16:9", seconds: 5, turbo: true, seed: null } });
  api.thread("v1", "Ferry at sunset", "video", [waiting]);
  api.rt = runtimeState({
    phase: "starting", detail: "Modeller indiriliyor, ComfyUI kuruluyor", session_spent: 0,
    startup: {
      progress: 0.58, remaining_seconds: 74, elapsed_seconds: 103, step: 2, step_elapsed_seconds: 71, pack: "video",
      steps: ["GPU ayrılıyor", "Bağlanılıyor", "Modeller indiriliyor, ComfyUI kuruluyor", "ComfyUI başlatılıyor"],
      setup: "3/5 ComfyUI ve ses paketleri kuruluyor",
      download: { total: 31.4 * GB, done: 19.8 * GB, fraction: 0.63, rate: 412 * 2 ** 20, eta: 29 },
    },
    packs: {
      video: { label: "Video", total: 31.4 * GB, done: 19.8 * GB, fraction: 0.63, rate: 412 * 2 ** 20, eta: 29 },
      image: { label: "Görsel", total: 17.3 * GB, done: 0, fraction: 0, rate: 412 * 2 ** 20, eta: 71 },
    },
    queue: { order: ["w1"], current: { id: "w1", kind: "video", status: "waiting_gpu", stage: null, progress: 0, prompt: waiting.prompt } },
  });
  await api.install(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Video", exact: true }).click();
  await page.getByText("Ferry at sunset").click();
  await page.getByText(/GPU starting:/).waitFor();
  await page.screenshot({ path: `${DIR}/startup.png` });
});

test("speech: results, voice cloning and a running job", async ({ page }) => {
  const api = new MockApi();
  api.lang = null;
  await withAudio(page);
  const running = job({ id: "r1", kind: "speech", thread_id: "s1", status: "running", progress: 0.62, stage: "Seslendiriliyor",
    prompt: "Thanks for watching! New episodes every Friday.", outputs: [], finished_at: null,
    params: { engine: "qwen3", language: "English", voice_preset: "Ryan", seed: null },
    live: { progress: 0.62, eta: 14, stage_seconds: 22, stage_expected: 36, loading: false } });
  api.thread("s1", "Podcast intro", "speech", [
    job({ kind: "speech", prompt: "Welcome back to the channel. Today we are building something special.",
      params: { engine: "chatterbox", language: "English", seed: 7 }, outputs: ["s_0.wav"] }),
    job({ kind: "speech", inputs: ["uploads/voice.wav"], prompt: "This is my own voice, cloned from a ten second recording.",
      params: { engine: "qwen3", language: "English", seed: 11 }, outputs: ["s_1.wav"] }),
    running,
  ]);
  api.rt = { ...READY, gpu_now: { t: 1, gpu: 87, vram_used: 9.6 * GB, vram_total: 22.5 * GB },
    queue: { order: ["r1"], current: { id: "r1", kind: "speech", status: "running", stage: "Seslendiriliyor", progress: 0.62, prompt: running.prompt } } };
  await api.install(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Speech", exact: true }).click();
  await page.getByText("Podcast intro").click();
  await page.getByText(/Synthesizing speech/).first().waitFor();
  await page.screenshot({ path: `${DIR}/speech.png` });
});

test("mobile, Turkish UI", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const api = new MockApi();
  api.lang = "tr";
  api.rt = READY;
  await api.install(page);
  await page.goto("/");
  await page.getByText("Ne üretmek istiyorsun?").first().waitFor();
  await page.screenshot({ path: `${DIR}/mobile-tr.png` });
});
