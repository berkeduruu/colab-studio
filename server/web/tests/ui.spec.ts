import { expect, test, type Page } from "@playwright/test";
import type { Job } from "../src/api";
import { job, LONG_ERROR, MockApi, overflowProblems, READY, runtimeState, STARTING } from "./mock";

const VIEWPORTS = { masaüstü: { width: 1440, height: 900 }, dizüstü: { width: 1100, height: 760 }, telefon: { width: 375, height: 812 } };
const MODE_LABELS = { image: "Görsel", video: "Video", model3d: "3D model", music: "Müzik", speech: "Seslendirme", transcribe: "Yazıya dök" } as const;

async function open(page: Page, api: MockApi) {
  await api.install(page);
  await page.goto("/");
  await expect(page.getByText("Colab Stüdyo")).toBeVisible();
}

/** Ayar panelinde etiketi verilen adla başlayan alan (bilgi baloncuklarındaki metne takılmaz). */
function field(page: Page, label: string) {
  return page.locator(".settings .field").filter({ has: page.locator("label", { hasText: new RegExp(`^${label}`) }) });
}

async function openSidebarOnPhone(page: Page) {
  if ((page.viewportSize()?.width ?? 1440) <= 900) await page.getByLabel("Menü").click();
}

/** Her iş türü ve durumundan birer örnek içeren sohbetler. */
function busyApi() {
  const api = new MockApi();
  const longTitle = "Yağmurlu İstanbul sokağı, neon tabelalar, sinematik gece fotoğrafı, çok uzun bir başlık ".repeat(3);
  api.thread("t1", longTitle, "image", [
    job({ params: { ...job().params, count: 4 }, outputs: ["a_0.png", "a_1.png", "a_2.png", "a_3.png"] }),
    job({ inputs: ["uploads/ref.png"], prompt: "Arka planı gün batımı yap" }),
    job({ status: "error", error: LONG_ERROR, outputs: [], finished_at: null }),
    job({ status: "running", progress: 0.4, stage: "Üretiliyor (10/25 adım)", live: { progress: 0.4, eta: 17, stage_seconds: 25, stage_expected: 40, loading: false }, outputs: [], finished_at: null }),
    job({ status: "queued", progress: 0, outputs: [], finished_at: null, prompt: "a".repeat(400) }),
  ]);
  api.thread("t2", "Boğaz'da vapur", "video", [
    job({ kind: "video", params: { width: 864, height: 480, seconds: 3, turbo: true, seed: 1 }, outputs: ["v_0.mp4"] }),
    job({ kind: "video", status: "waiting_gpu", progress: 0, outputs: [], finished_at: null }),
  ]);
  api.thread("t6", "Kahve fincanı", "model3d", [
    job({ kind: "model3d", inputs: ["uploads/cup.png"], prompt: "3D model", params: { resolution: 1536, texture: "pbr", faces: 200000, seed: 9 }, outputs: ["k_0.glb"] }),
    job({ kind: "model3d", inputs: ["uploads/cup.png"], prompt: "3D model", status: "running", progress: 0.55, stage: "Şekil ayrıntılandırılıyor (6/12 adım)",
      params: { resolution: 1536, texture: "pbr", faces: 200000, seed: null }, outputs: [], finished_at: null }),
  ]);
  api.thread("t3", "Lo-fi", "music", [job({ kind: "music", params: { lyrics: "[Verse]\nla", seconds: 30, seed: 2 }, outputs: ["m_0.mp3"] })]);
  api.thread("t4", "Merhaba", "speech", [
    job({ kind: "speech", params: { engine: "chatterbox", language: "Turkish", seed: 3 }, outputs: ["s_0.mp3"] }),
    job({ kind: "speech", inputs: ["uploads/voice.mp3"], params: { engine: "qwen3", language: "English", seed: 4 }, outputs: ["s_1.mp3"] }),
  ]);
  api.thread("t5", "Ses kaydı", "transcribe", [
    job({ kind: "transcribe", inputs: ["uploads/rec.mp3"], params: { language: "auto" }, outputs: [],
      text: "Merhaba, bu bir test kaydıdır. " + "https://example.com/" + "uzun-bir-baglanti-".repeat(20) }),
  ]);
  const running = api.jobs[Object.keys(api.jobs).find((id) => api.jobs[id].status === "running")!];
  api.rt = { ...READY, queue: { order: [running.id], current: { ...running, stage: running.stage } } };
  return api;
}

for (const [vpName, viewport] of Object.entries(VIEWPORTS)) {
  test.describe(`${vpName} (${viewport.width}px)`, () => {
    test.use({ viewport });

    for (const [rtName, rt] of Object.entries({
      kapalı: runtimeState(),
      açılıyor: STARTING,
      hazır: READY,
      hata: runtimeState({ phase: "error", detail: LONG_ERROR }),
      "kredi okunamadı": runtimeState({ credits: { error: "colab usage çıktısı okunamadı" }, credit_stats: null }),
    })) {
      test(`GPU ${rtName}: kenar çubuğu ve üst çubuk taşmıyor`, async ({ page }) => {
        const api = busyApi();
        api.rt = { ...rt, queue: { order: [], current: null } };
        await open(page, api);
        await openSidebarOnPhone(page);
        if (rt.phase === "ready") await expect(page.getByText("VRAM")).toBeVisible();
        if (rt.phase === "starting") await expect(page.locator(".startup")).toContainText("%15");
        if (rt.phase === "error") await expect(page.getByRole("alert")).toContainText("GPU hatası");
        expect(await overflowProblems(page)).toEqual([]);
        api.assertClean();
      });
    }

    for (const [mode, label] of Object.entries(MODE_LABELS)) {
      test(`${label}: sohbet, ayar paneli ve yazma kutusu taşmıyor`, async ({ page }) => {
        const api = busyApi();
        await open(page, api);
        await openSidebarOnPhone(page);
        await page.locator(".mode", { hasText: label }).click();
        await openSidebarOnPhone(page);
        await page.locator(".history-item").first().click();
        await expect(page.locator(".result").first()).toBeVisible();
        expect(await overflowProblems(page)).toEqual([]);
        const settings = page.locator(".settings");
        if (!(await settings.isVisible())) await page.getByLabel("Ayarlar").click();
        await expect(settings).toBeVisible();
        expect(await overflowProblems(page)).toEqual([]);
        // Her ayarın bilgi ikonu açıklamayı ekranın içinde gösterir.
        const infos = settings.locator(".info");
        expect(await infos.count()).toBeGreaterThan(0);
        for (let i = 0; i < (await infos.count()); i++) {
          const info = infos.nth(i);
          await page.mouse.move(0, 0); // önceki baloncuk kapanır
          await info.scrollIntoViewIfNeeded();
          await info.hover();
          const tip = info.locator(".tip");
          await expect(tip).toBeVisible();
          expect((await tip.textContent())!.length).toBeGreaterThan(10);
          const box = (await tip.boundingBox())!;
          expect(box.x).toBeGreaterThanOrEqual(0);
          expect(box.x + box.width).toBeLessThanOrEqual(viewport.width + 1);
        }
        expect(mode).toBeTruthy();
        api.assertClean();
      });
    }
  });
}

test.describe("davranış", () => {
  test.use({ viewport: VIEWPORTS.masaüstü });

  test("görsel üretimi doğru ayarlarla gönderilir ve sohbet açılır", async ({ page }) => {
    const api = new MockApi();
    await open(page, api);
    await page.getByTitle("Adet").selectOption("2");
    await page.locator(".composer textarea").fill("Kırmızı bir bisiklet");
    await page.locator(".composer .btn.primary", { hasText: "Üret" }).click();
    await expect(page.locator(".bubble")).toContainText("Kırmızı bir bisiklet");
    const sent = api.requests.find((r) => r.method === "POST" && r.path === "/api/jobs")!.body as Record<string, any>;
    expect(sent.kind).toBe("image");
    expect(sent.params.count).toBe(2);
    expect(sent.params.negative.length).toBeGreaterThan(10);
    await expect(page.locator(".history-item")).toHaveCount(1);
    await expect(page.locator(".result .status")).toContainText("Sırada");
    api.assertClean();
  });

  test("ayarlar sunucuya kaydedilir ve yeniden açınca geri gelir", async ({ page }) => {
    const api = new MockApi();
    await open(page, api);
    const negative = page.locator(".settings textarea.autogrow");
    await negative.fill("kötü eller, bulanık");
    await page.locator(".mode", { hasText: "Video" }).click();
    await expect.poll(() => api.settings["image.params"]).toMatchObject({ negative: "kötü eller, bulanık" });
    await expect.poll(() => api.settings["mode"]).toBe("video");
    await page.reload();
    await expect(page.locator(".mode.active")).toContainText("Video");
    await page.locator(".mode", { hasText: "Görsel" }).click();
    await expect(page.locator(".settings textarea.autogrow")).toHaveValue("kötü eller, bulanık");
    api.assertClean();
  });

  test("negatif istem kutusu yazının tamamını gösterecek kadar büyür", async ({ page }) => {
    const api = new MockApi();
    await open(page, api);
    const box = page.locator(".settings textarea.autogrow");
    const before = (await box.boundingBox())!.height;
    await box.fill("çok uzun negatif istem, ".repeat(30));
    await expect.poll(async () => (await box.boundingBox())!.height).toBeGreaterThan(before + 40);
    expect(await box.evaluate((el: HTMLTextAreaElement) => el.scrollHeight <= el.clientHeight + 2)).toBe(true);
  });

  test("seed 0 ile 2^32-1 arasında kalır; büyük, negatif ve ondalık girişler sınırlanır", async ({ page }) => {
    const api = new MockApi();
    await open(page, api);
    const seed = page.getByPlaceholder("rastgele");
    await seed.fill("-5");
    await expect(seed).toHaveValue("0");
    await seed.fill("12.7");
    await expect(seed).toHaveValue("12");
    await seed.fill("99999999999");
    await expect(seed).toHaveValue("4294967295");
    await page.locator(".composer textarea.prompt").fill("kahve");
    await page.locator(".composer .btn.primary", { hasText: "Üret" }).click();
    const sent = api.requests.find((r) => r.path === "/api/jobs")!.body as Record<string, any>;
    expect(sent.params.seed).toBe(4294967295);
    api.assertClean();
  });

  test("istem geliştirme bir yoklama 502 alsa da sonucu kutuya yazar", async ({ page }) => {
    const api = new MockApi();
    let polls = 0;
    api.onJobCreate = (j) => j;
    await open(page, api);
    await page.locator(".composer textarea").fill("Sisli çam ormanı");
    await page.route("**/api/jobs/*", async (route) => {
      polls += 1;
      if (polls === 1) return route.fulfill({ status: 502, contentType: "text/html", body: "<html>502 Bad Gateway</html>" });
      const id = route.request().url().split("/").pop()!;
      return route.fulfill({ json: { ...api.jobs[id], status: "done", text: "A misty pine forest at dawn." } });
    });
    await page.getByRole("button", { name: "İstemi geliştir" }).click();
    await expect(page.locator(".composer textarea")).toHaveValue("A misty pine forest at dawn.", { timeout: 10_000 });
    await expect(page.getByRole("alert")).toHaveCount(0);
    expect(polls).toBeGreaterThanOrEqual(2);
    const sent = api.requests.find((r) => r.path === "/api/jobs")!.body as Record<string, any>;
    expect(sent).toMatchObject({ kind: "enhance", thread_id: null, prompt: "Sisli çam ormanı" });
  });

  test("istem geliştirme hatası okunur bir mesajla gösterilir", async ({ page }) => {
    const api = new MockApi();
    await open(page, api);
    api.failNext.push({ prefix: "/api/jobs", status: 502, body: "<html><center><h1>502 Bad Gateway</h1></center></html>" });
    await page.locator(".composer textarea").fill("kahve");
    await page.getByRole("button", { name: "İstemi geliştir" }).click();
    const alert = page.getByRole("alert");
    await expect(alert).toContainText("Sunucuya şu an ulaşılamıyor (502)");
    await expect(alert).not.toContainText("<html>");
    await alert.getByLabel("Kapat").click();
    await expect(alert).toHaveCount(0);
  });

  test("sunucunun doğrulama hatası olduğu gibi gösterilir, istem kaybolmaz", async ({ page }) => {
    const api = new MockApi();
    await open(page, api);
    await page.route("**/api/jobs", (route) => route.fulfill({ status: 400, contentType: "text/plain", body: "CFG 1-10 arası olmalı" }));
    await page.locator(".composer textarea").fill("kahve");
    await page.locator(".composer .btn.primary", { hasText: "Üret" }).click();
    await expect(page.getByRole("alert")).toHaveText("CFG 1-10 arası olmalı");
    await expect(page.locator(".composer textarea")).toHaveValue("kahve");
  });

  test("sohbet yenilemesi geçici hatadan sonra sürer", async ({ page }) => {
    const api = busyApi();
    await open(page, api);
    await page.locator(".history-item").first().click();
    const running = Object.values(api.jobs).find((j) => j.status === "running")!;
    await expect(page.locator(".result .status", { hasText: /^\s*Üretiliyor \(10\/25 adım\) · %40 · ~17 sn kaldı/ })).toBeVisible();
    api.failNext.push({ prefix: "/api/threads/t1", status: 502, body: "<html>502</html>" });
    running.status = "done";
    running.outputs = ["late_0.png"];
    running.stage = null;
    await expect(page.locator(".result .status", { hasText: /^\s*Üretiliyor \(10\/25 adım\) · %40 · ~17 sn kaldı/ })).toHaveCount(0, { timeout: 15_000 });
    // Geçici yenileme hatası kullanıcıya hata bandı olarak çıkmaz (sohbetteki iş hatası satırı ayrı).
    await expect(page.locator(".banner.err")).toHaveCount(0);
  });

  test("sırada bekleyen iş önündeki iş sayısını ve o anki işi gösterir", async ({ page }) => {
    const api = busyApi();
    await open(page, api);
    await page.locator(".history-item").first().click();
    const queued = Object.values(api.jobs).find((j) => j.status === "queued")!;
    const running = Object.values(api.jobs).find((j) => j.status === "running")!;
    api.rt = { ...api.rt, queue: { order: [running.id, queued.id], current: { ...running, stage: running.stage } } };
    await expect(page.getByText(/Sırada · önünde 1 iş · şu an: Görsel — Üretiliyor \(10\/25 adım\) %40/)).toBeVisible({ timeout: 5000 });
  });

  test("sıra listesinde henüz olmayan yeni iş için 'önünde -1 iş' yazmaz", async ({ page }) => {
    const api = busyApi();
    await open(page, api);
    await page.locator(".history-item").first().click();
    // busyApi'de bekleyen iş sıra listesinde yok (sıra durumu en fazla 3 sn'de bir yenilenir): önündeki iş sayısı bilinmez.
    await expect(page.locator(".status-text", { hasText: "Sırada" })).toHaveText("Sırada · şu an: Görsel — Üretiliyor (10/25 adım) %40");
    api.assertClean();
  });

  test("GPU açılırken bekleyen iş açılış adımını ve kalan süreyi gösterir", async ({ page }) => {
    const api = busyApi();
    api.rt = { ...STARTING, queue: { order: [], current: null } };
    await open(page, api);
    await page.locator(".mode", { hasText: "Video" }).click();
    await page.locator(".history-item").first().click();
    await expect(page.getByText(/GPU açılıyor: Modeller indiriliyor, ComfyUI kuruluyor · %15 · ~3 dk 3 sn kaldı/)).toBeVisible();
  });

  test("yazıya dökme ses dosyası eklenmeden gönderilemez", async ({ page }) => {
    const api = new MockApi();
    await open(page, api);
    await page.locator(".mode", { hasText: "Yazıya dök" }).click();
    const submit = page.locator(".composer .btn.primary");
    await expect(submit).toHaveText("Yazıya dök");
    await expect(submit).toBeDisabled();
    await page.locator('input[type="file"]').setInputFiles({ name: "kayit.mp3", mimeType: "audio/mpeg", buffer: Buffer.from("ID3") });
    await expect(page.locator(".attachment.audio")).toHaveCount(1);
    await expect(submit).toBeEnabled();
    await submit.click();
    const sent = api.requests.find((r) => r.path === "/api/jobs")!.body as Record<string, any>;
    expect(sent).toMatchObject({ kind: "transcribe", params: { language: "auto" } });
    expect(sent.inputs).toHaveLength(1);
    api.assertClean();
  });

  test("seslendirmede ses eklenince klonlama gösterilir ve motor/dil gönderilir", async ({ page }) => {
    const api = new MockApi();
    await open(page, api);
    await page.locator(".mode", { hasText: "Seslendirme" }).click();
    await page.locator('input[type="file"]').setInputFiles({ name: "ses.mp3", mimeType: "audio/mpeg", buffer: Buffer.from("ID3") });
    await expect(page.locator(".chip.on", { hasText: "Ses klonlama" })).toBeVisible();
    await page.locator(".composer textarea").fill("Merhaba dünya");
    await page.locator(".composer .btn.primary", { hasText: "Seslendir" }).click();
    const sent = api.requests.find((r) => r.path === "/api/jobs")!.body as Record<string, any>;
    expect(sent).toMatchObject({ kind: "speech", params: { engine: "chatterbox", language: "Turkish" } });
    api.assertClean();
  });

  test("müzikte sözler ayrı alandan gönderilir, dosya eklenemez", async ({ page }) => {
    const api = new MockApi();
    await open(page, api);
    await page.locator(".mode", { hasText: "Müzik" }).click();
    await expect(page.locator(".composer .icon-btn[title]")).toHaveCount(0);
    await page.locator(".composer textarea.lyrics").fill("[Verse]\nDenizin üstünde");
    await page.locator(".composer textarea").last().fill("Türkçe akustik pop");
    await page.locator(".composer .btn.primary", { hasText: "Üret" }).click();
    const sent = api.requests.find((r) => r.path === "/api/jobs")!.body as Record<string, any>;
    expect(sent).toMatchObject({ kind: "music", prompt: "Türkçe akustik pop", params: { lyrics: "[Verse]\nDenizin üstünde" }, inputs: [] });
  });

  test("video için en fazla iki görsel eklenir", async ({ page }) => {
    const api = new MockApi();
    await open(page, api);
    await page.locator(".mode", { hasText: "Video" }).click();
    const files = [1, 2, 3].map((i) => ({ name: `k${i}.png`, mimeType: "image/png", buffer: Buffer.from("PNG") }));
    await page.locator('input[type="file"]').setInputFiles(files);
    await expect(page.locator(".attachment")).toHaveCount(2);
  });

  test("süre çipleri ayar kaydırıcısının her değerini gösterir (video 6 sn, müzik 200 sn)", async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.masaüstü);
    const api = new MockApi();
    await open(page, api);
    await page.locator(".mode", { hasText: "Video" }).click();
    await field(page, "Süre").locator("input[type=range]").fill("6");
    const videoChip = page.getByTitle("Süre", { exact: true });
    await expect(videoChip).toHaveValue("6");
    await expect(videoChip.locator("option:checked")).toHaveText("6 sn");
    await page.locator(".mode", { hasText: "Müzik" }).click();
    await page.locator(".settings input[type=range]").fill("200");
    const musicChip = page.getByTitle("En uzun süre");
    await expect(musicChip).toHaveValue("200");
    await expect(musicChip.locator("option:checked")).toHaveText("en çok 3 dk 20 sn");
    api.assertClean();
  });

  test("30 dk'yı aşacak ayarlarda gönder kapanır ve neden yazar; Turbo seçilince açılır", async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.masaüstü);
    const api = new MockApi();
    await open(page, api);
    await page.locator(".mode", { hasText: "Video" }).click();
    await page.getByPlaceholder(/Sahneyi/).fill("sahil");
    await page.getByTitle("Çözünürlük", { exact: true }).selectOption("768p");
    await field(page, "Süre").locator("input[type=range]").fill("10");
    await page.getByRole("button", { name: "Kaliteli (20 adım)" }).click();
    await expect(page.getByRole("alert")).toContainText("30 dk sınırını aşar");
    await expect(page.getByRole("button", { name: /Üret/ })).toBeDisabled();
    await field(page, "Süre").locator("input[type=range]").fill("9");
    await expect(page.getByRole("alert")).toContainText("30 dk sınırını aşar");
    await field(page, "Süre").locator("input[type=range]").fill("8");
    await expect(page.getByRole("alert")).toHaveCount(0);
    await field(page, "Süre").locator("input[type=range]").fill("10");
    await page.getByRole("button", { name: "Turbo (8 adım)" }).click();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(page.getByRole("button", { name: /Üret/ })).toBeEnabled();
    api.assertClean();
  });

  test("Yeniden üret yeni seed ile, Seed'i sabitle eski seed ve eski ayarlarla çalışır", async ({ page }) => {
    const api = busyApi();
    await open(page, api);
    await page.locator(".settings .field", { hasText: "Hız / kalite" }).locator(".chip", { hasText: "Turbo" }).click();
    await page.locator(".history-item").first().click();
    await page.getByRole("button", { name: "Seed'i sabitle" }).first().click();
    await expect(page.locator(".settings input[type=number]")).toHaveValue("123");
    // Turbo'dan önceki iş turbo alanı taşımıyor: o anki Turbo seçimi kalmamalı.
    await expect(page.locator(".settings .field", { hasText: "Hız / kalite" }).locator(".chip", { hasText: "Kaliteli" })).toHaveClass(/on/);
    await page.getByRole("button", { name: "Yeniden üret" }).first().click();
    const sent = api.requests.find((r) => r.path === "/api/jobs")!.body as Record<string, any>;
    expect(sent.params.seed).toBeNull();
    expect(sent.thread_id).toBe("t1");
  });

  test("ön ayar kaydedilir, uygulanır ve silinir", async ({ page }) => {
    const api = new MockApi();
    await open(page, api);
    await page.locator(".settings input[type=range]").first().fill("40");
    await page.getByPlaceholder("Ön ayar adı").fill("Detaylı");
    await page.getByTitle("Mevcut ayarları kaydet").click();
    await expect.poll(() => api.presets.map((p) => [p.name, p.params.steps])).toEqual([["Detaylı", 40]]);
    await page.getByRole("button", { name: "Tüm ayarları sıfırla" }).click();
    await page.getByTitle("Ön ayar uygula").selectOption({ label: "Detaylı" });
    await expect(field(page, "Adım").locator(".field-value")).toHaveText("40");
    api.assertClean();
  });

  test("GPU başlat ve otomatik kapanma düğmeleri doğru uca gider", async ({ page }) => {
    const api = new MockApi();
    await open(page, api);
    await page.getByRole("button", { name: "L4 başlat" }).click();
    await expect(page.locator(".pill")).toHaveText("GPU açılıyor");
    await page.getByRole("button", { name: "Otomatik kapanma" }).click();
    // Tıklama, istek sahte sunucuya ulaşmadan döner: liste beklenerek denetlenir.
    await expect.poll(() => api.requests.map((r) => `${r.method} ${r.path}`)).toEqual(expect.arrayContaining(["POST /api/runtime/start", "POST /api/runtime/toggle-auto"]));
    page.once("dialog", (d) => d.accept());
    await page.getByRole("button", { name: "Kapat" }).first().click();
    await expect(page.locator(".pill")).toHaveText("GPU kapalı");
    api.assertClean();
  });

  test("GPU kapanırken Kapat düğmesi beklemede: ikinci basış onay penceresi açmaz", async ({ page }) => {
    const api = new MockApi();
    api.rt = runtimeState({ phase: "stopping", detail: "elle kapatıldı" });
    await open(page, api);
    await expect(page.locator(".pill")).toHaveText("Kapanıyor");
    await expect(page.getByRole("button", { name: "Kapat" }).first()).toBeDisabled();
    api.assertClean();
  });

  test("sohbet silinir; açık sohbet silinirse boş ekrana dönülür", async ({ page }) => {
    const api = busyApi();
    await open(page, api);
    await page.locator(".history-item").first().click();
    page.once("dialog", (d) => d.accept());
    await page.locator(".history-item").first().hover();
    await page.locator(".history-item .del").first().click();
    await expect(page.locator(".history-item")).toHaveCount(0);
    await expect(page.getByText("Ne üretmek istiyorsun?")).toBeVisible();
    api.assertClean();
  });

  test("sohbet satırı klavyeyle açılır (Tab ile odaklanıp Enter)", async ({ page }) => {
    const api = new MockApi();
    api.thread("t1", "Kahve fincanı", "image", [job()]);
    await open(page, api);
    await page.getByLabel("Yeni sohbet").focus();
    await page.keyboard.press("Tab");
    const row = page.getByRole("button", { name: "Kahve fincanı", exact: true });
    await expect(row).toBeFocused();
    await expect(row).toHaveCSS("outline-style", /^(auto|solid)$/);
    await page.keyboard.press("Enter");
    await expect(page.locator(".history-item.active")).toHaveCount(1);
    await expect(page.locator(".bubble")).toContainText("Ahşap masada seramik kahve fincanı");
    api.assertClean();
  });

  test("kayıtlar penceresi açılıp kapanır", async ({ page }) => {
    const api = new MockApi();
    await open(page, api);
    await page.getByLabel("Kayıtlar").click();
    await expect(page.locator(".log pre")).toContainText("GPU ayrılıyor");
    await page.locator(".log").getByLabel("Kapat").click();
    await expect(page.locator(".log")).toHaveCount(0);
    api.assertClean();
  });

  test("yazıya dökülen metin kopyalanabilir ve indirilebilir", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    const api = busyApi();
    await open(page, api);
    await page.locator(".mode", { hasText: "Yazıya dök" }).click();
    await page.locator(".history-item").first().click();
    await page.getByRole("button", { name: "Metni kopyala" }).click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("Merhaba, bu bir test kaydıdır.");
    await expect(page.getByRole("link", { name: ".txt indir" })).toHaveAttribute("download", /\.txt$/);
  });
});

test.describe("hız düğmeleri ve telefon menüsü", () => {
  test("hız / kalite düğmelerinden yalnızca biri seçili olur", async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.masaüstü);
    const api = new MockApi();
    await open(page, api);
    const chips = page.locator(".settings .field", { hasText: "Hız / kalite" }).locator(".chip");
    for (const [name, steps, sampler] of [["Turbo", "8", "euler"], ["Taslak", "12", "uni_pc"], ["Kaliteli", "25", "euler"], ["Hızlı", "25", "euler"]]) {
      await chips.filter({ hasText: name }).click();
      await expect(chips.filter({ hasText: name })).toHaveClass(/on/);
      await expect(page.locator(".settings .field", { hasText: "Hız / kalite" }).locator(".chip.on")).toHaveCount(1);
      await expect(field(page, "Adım").locator(".field-value")).toHaveText(steps);
      await expect(field(page, "Sampler").locator("select")).toHaveValue(sampler);
    }
  });

  test("Turbo sonrası adım elle değişince turbo LoRA kapanır", async ({ page }) => {
    const api = new MockApi();
    await open(page, api);
    await page.locator(".settings .field", { hasText: "Hız / kalite" }).locator(".chip", { hasText: "Turbo" }).click();
    await field(page, "Adım").locator("input[type=range]").fill("20");
    await page.locator(".composer textarea").fill("kahve");
    await page.locator(".composer .btn.primary", { hasText: "Üret" }).click();
    await expect.poll(() => api.requests.some((r) => r.path === "/api/jobs")).toBe(true);
    const sent = api.requests.find((r) => r.path === "/api/jobs")!.body as Record<string, any>;
    expect(sent.params).toMatchObject({ steps: 20, turbo: false });
  });

  test("telefonda menü kenar çubuğunu ekranın içinde açar", async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.telefon);
    const api = new MockApi();
    api.rt = STARTING;
    await open(page, api);
    const sidebar = page.locator(".sidebar");
    await expect.poll(async () => (await sidebar.boundingBox())!.x).toBeLessThan(-100);
    await page.getByLabel("Menü").click();
    await expect.poll(async () => (await sidebar.boundingBox())!.x).toBe(0);
    await expect(page.locator(".startup")).toBeInViewport();
    await page.locator(".mode", { hasText: "Video" }).click();
    await expect.poll(async () => (await sidebar.boundingBox())!.x).toBeLessThan(-100);
  });
});

test("yazma kutusu uzun istemde büyür, ekranın yarısını geçmez", async ({ page }) => {
  await page.setViewportSize(VIEWPORTS.masaüstü);
  const api = new MockApi();
  await open(page, api);
  const box = page.locator(".composer textarea.prompt");
  const before = (await box.boundingBox())!.height;
  await box.fill("Kalabalık bir futbol stadyumu, kırmızı yeşil bayraklar, sinematik ışık. ".repeat(6));
  await expect.poll(async () => (await box.boundingBox())!.height).toBeGreaterThan(before + 20);
  expect(await box.evaluate((el: HTMLTextAreaElement) => el.scrollHeight <= el.clientHeight + 2)).toBe(true);
  await box.fill("çok uzun satır\n".repeat(200));
  await expect.poll(async () => (await box.boundingBox())!.height).toBeLessThanOrEqual(VIEWPORTS.masaüstü.height / 2 + 1);
  await box.fill("");
  await expect.poll(async () => (await box.boundingBox())!.height).toBeLessThan(before + 5);
});

test("bekleyen iş iptal edilir ve sohbette 'İptal edildi' görünür", async ({ page }) => {
  await page.setViewportSize(VIEWPORTS.masaüstü);
  const api = busyApi();
  await open(page, api);
  await page.locator(".history-item").first().click();
  const running = page.locator(".result", { has: page.locator(".status-text", { hasText: /^Üretiliyor/ }) });
  page.once("dialog", (d) => d.accept());
  await running.getByRole("button", { name: "İptal" }).click();
  await expect(page.locator(".cancelled-text", { hasText: "İptal edildi" })).toBeVisible();
  await expect(page.locator(".error-text", { hasText: "İptal edildi" })).toHaveCount(0); // iptal bir hata gibi görünmez
  expect(api.requests.some((r) => r.method === "POST" && /\/api\/jobs\/job\d+\/cancel$/.test(r.path))).toBe(true);
  api.assertClean();
});


test("görsel eklenince düzenleme çözünürlüğü seçilir ve gönderilir", async ({ page }) => {
  await page.setViewportSize(VIEWPORTS.masaüstü);
  const api = new MockApi();
  await open(page, api);
  await expect(field(page, "Çözünürlük")).toBeVisible();
  await expect(field(page, "Düzenleme çözünürlüğü")).toHaveCount(0);
  await page.locator('input[type="file"]').setInputFiles({ name: "foto.jpg", mimeType: "image/jpeg", buffer: Buffer.from("JPG") });
  const editField = field(page, "Düzenleme çözünürlüğü");
  await expect(editField).toBeVisible();
  await expect(field(page, "Çözünürlük")).toHaveCount(0);
  await expect(editField.locator(".chip.on")).toHaveText("1080p");
  await expect(editField.locator(".tip")).toContainText("1080p 1x, ~7.8 sn/adım");
  await expect(page.getByTitle(/Düzenleme çözünürlüğü/)).toHaveValue("1080p");
  await editField.getByRole("button", { name: "720p" }).click();
  await expect(page.getByTitle(/Düzenleme çözünürlüğü/)).toHaveValue("720p");
  await page.locator(".composer textarea.prompt").fill("Arka planı gün batımı yap");
  await page.locator(".composer .btn.primary", { hasText: "Düzenle" }).click();
  const sent = api.requests.find((r) => r.path === "/api/jobs")!.body as Record<string, any>;
  expect(sent.params.edit_resolution).toBe("720p");
  expect(sent.inputs).toHaveLength(1);
  api.assertClean();
});


test("panodan yapıştırılan dosya odak nerede olursa olsun eklenir", async ({ page }) => {
  await page.setViewportSize(VIEWPORTS.masaüstü);
  const api = new MockApi();
  await open(page, api);
  const uploads = () => api.requests.filter((r) => r.path === "/api/uploads").length;
  // where: "body" odak yokken, "prompt" yazma kutusunda; itemsOnly: dosya yalnızca items'ta (Firefox)
  const paste = (type: string, name: string, where = "body", itemsOnly = false) =>
    page.evaluate(([type, name, where, itemsOnly]) => {
      const target = where === "body" ? document.body : document.querySelector(".composer textarea.prompt")!;
      const ev = new Event("paste", { bubbles: true, cancelable: true });
      const file = new File([new Uint8Array([1, 2, 3])], name, { type });
      const data = type === "text"
        ? { files: [], items: [{ kind: "string", getAsFile: () => null }], getData: () => name }
        : { files: itemsOnly ? [] : [file], items: [{ kind: "file", getAsFile: () => file }] };
      Object.defineProperty(ev, "clipboardData", { value: data });
      target.dispatchEvent(ev);
    }, [type, name, where, itemsOnly] as const);
  await paste("image/png", "ekran.png");
  await expect(page.locator(".attachment img")).toHaveCount(1);
  await paste("image/png", "firefox.png", "prompt", true);
  await expect(page.locator(".attachment img")).toHaveCount(2);
  expect(uploads()).toBe(2);
  await paste("image/gif", "hareketli.gif");
  await expect(page.getByRole("alert")).toContainText("hareketli.gif eklenemedi");
  expect(uploads()).toBe(2);
  await page.getByRole("alert").getByLabel("Kapat").click();
  // Seslendirmede ses dosyası eklenir.
  await page.locator(".mode", { hasText: "Seslendirme" }).click();
  await paste("audio/mpeg", "ses.mp3");
  await expect(page.locator(".attachment.audio")).toHaveCount(1);
  // Müzikte dosya eklenmez, uyarı çıkar.
  await page.locator(".mode", { hasText: "Müzik" }).click();
  await paste("image/png", "ekran.png");
  await expect(page.getByRole("alert")).toContainText("Bu modda dosya eklenemez");
  expect(uploads()).toBe(3);
  api.assertClean();
});

test("art arda bırakılan dosyalar birbirini ezmez", async ({ page }) => {
  await page.setViewportSize(VIEWPORTS.masaüstü);
  const api = new MockApi();
  await open(page, api);
  // İlk yükleme sona kadar tutulur; ikinci dosya o bitmeden bırakılır.
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  let uploads = 0;
  await page.route("**/api/uploads", async (route) => {
    uploads += 1;
    if (uploads === 1) await gate;
    await route.fallback();
  });
  const input = page.locator('input[type="file"]');
  await input.setInputFiles({ name: "a.png", mimeType: "image/png", buffer: Buffer.from("A") });
  await input.setInputFiles({ name: "b.png", mimeType: "image/png", buffer: Buffer.from("B") });
  await expect(page.locator(".attachment img")).toHaveCount(1);
  release();
  await expect(page.locator(".attachment img")).toHaveCount(2);
  api.assertClean();
});

test("ek sınırı dolunca fazla dosyalar için uyarı çıkar, sığanlar eklenir", async ({ page }) => {
  await page.setViewportSize(VIEWPORTS.masaüstü);
  const api = new MockApi();
  await open(page, api);
  await page.locator(".mode", { hasText: "Video" }).click();
  // Video en fazla 2 ek alır. İlk dosya sona kadar tutulur (yer kaplar), sonra iki dosya bırakılır.
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  let uploads = 0;
  await page.route("**/api/uploads", async (route) => {
    uploads += 1;
    if (uploads === 1) await gate;
    await route.fallback();
  });
  const input = page.locator('input[type="file"]');
  const image = (name: string) => ({ name, mimeType: "image/png", buffer: Buffer.from("PNG") });
  await input.setInputFiles(image("k1.png"));
  await input.setInputFiles([image("k2.png"), image("k3.png")]);
  await expect(page.getByRole("alert")).toContainText("En fazla 2 ek eklenebilir; 1 dosya eklenmedi.");
  await expect(page.locator(".attachment img")).toHaveCount(1);
  release();
  await expect(page.locator(".attachment img")).toHaveCount(2);
  expect(uploads).toBe(2);
  api.assertClean();
});

test("gerçek Ctrl+V: panoya kopyalanan görsel eklenir", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.setViewportSize(VIEWPORTS.masaüstü);
  const api = new MockApi();
  await open(page, api);
  await page.evaluate(async () => {
    const c = document.createElement("canvas");
    c.width = c.height = 8;
    const blob = await new Promise<Blob>((r) => c.toBlob((b) => r(b!), "image/png"));
    await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
  });
  await page.locator(".empty h2").click(); // odak yazma kutusunda değil
  await page.keyboard.press("Control+V");
  await expect(page.locator(".attachment img")).toHaveCount(1);
  await page.locator(".composer textarea.prompt").click();
  await page.keyboard.press("Control+V");
  await expect(page.locator(".attachment img")).toHaveCount(2);
  api.assertClean();
});

test("sohbette son görsel bir sonraki mesaja bağlam olarak eklenir", async ({ page }) => {
  await page.setViewportSize(VIEWPORTS.masaüstü);
  const api = new MockApi();
  const first = job({ outputs: ["ilk_0.png"] });
  api.thread("t1", "Kahve fincanı", "image", [first]);
  await open(page, api);
  // Yeni sohbette bağlam yok.
  await expect(page.locator(".attachment")).toHaveCount(0);
  await page.locator(".history-item").first().click();
  await expect(page.locator(".attachment img")).toHaveAttribute("src", "/media/ilk_0.png");
  await expect(page.locator(".composer .btn.primary")).toHaveText("Düzenle");
  await page.locator(".composer textarea.prompt").fill("Fincanı kırmızı yap");
  await page.locator(".composer .btn.primary").click();
  await expect.poll(() => api.requests.filter((r) => r.path === "/api/jobs").length).toBe(1);
  const sent = api.requests.find((r) => r.path === "/api/jobs")!.body as Record<string, any>;
  expect(sent).toMatchObject({ kind: "image", thread_id: "t1", inputs: ["ilk_0.png"], prompt: "Fincanı kırmızı yap" });
  // Yeni sonuç bitince o eklenir.
  const second = Object.values(api.jobs).find((j) => j.prompt === "Fincanı kırmızı yap")!;
  Object.assign(second, { status: "done", outputs: ["ikinci_0.png"] });
  await expect(page.locator(".attachment img")).toHaveAttribute("src", "/media/ikinci_0.png", { timeout: 15_000 });
  // Kaldırılınca yeni görsel üretilir ve aynı sonuç tekrar eklenmez.
  await page.locator(".attachment").getByLabel("Kaldır").click();
  await expect(page.locator(".composer .btn.primary")).toHaveText("Üret");
  await page.waitForTimeout(2000);
  await expect(page.locator(".attachment")).toHaveCount(0);
  // Başka sohbete geçince bağlam taşınmaz.
  await page.getByLabel("Yeni sohbet").click();
  await expect(page.locator(".attachment")).toHaveCount(0);
  api.assertClean();
});

test.describe("3D model", () => {
  test.use({ viewport: VIEWPORTS.masaüstü });

  test("görsel eklenmeden gönderilemez; eklenince seçilen ayarlarla 3D işi gider", async ({ page }) => {
    const api = new MockApi();
    await open(page, api);
    await page.locator(".mode", { hasText: "3D model" }).click();
    const submit = page.locator(".composer .btn.primary", { hasText: "3D'ye çevir" });
    await expect(submit).toBeDisabled();
    await page.getByTitle("Şekil çözünürlüğü").selectOption("1024");
    await page.getByTitle("Doku").selectOption("vertex");
    await page.getByTitle("Üçgen sayısı").selectOption("50000");
    await page.locator('input[type="file"]').setInputFiles({ name: "kupa.png", mimeType: "image/png", buffer: Buffer.from("png") });
    await expect(page.locator(".attachment img")).toHaveCount(1);
    await expect(submit).toBeEnabled();
    await submit.click();
    const sent = api.requests.find((r) => r.method === "POST" && r.path === "/api/jobs")!.body as Record<string, any>;
    expect(sent).toMatchObject({ kind: "model3d", params: { resolution: 1024, texture: "vertex", faces: 50000, seed: null } });
    expect(sent.inputs).toHaveLength(1);
    await expect.poll(() => api.settings["model3d.params"]).toMatchObject({ texture: "vertex" });
    api.assertClean();
  });

  test("sonuç döndürülebilir önizlemede yüklenir, tam ekran görüntüleyici açılır ve kapanır", async ({ page }) => {
    const api = busyApi();
    await open(page, api);
    await page.locator(".mode", { hasText: "3D model" }).click();
    await page.locator(".history-item").first().click();
    const preview = page.locator(".tile.model3d model-viewer");
    await expect(preview).toHaveAttribute("src", "/media/k_0.glb");
    await expect.poll(() => preview.evaluate((el) => (el as HTMLElement & { loaded: boolean }).loaded), { timeout: 15_000 }).toBe(true);
    await expect(page.locator(".meta").first()).toContainText("TRELLIS.2 · şekil 1536 · PBR doku · 200K üçgen · seed 9");
    await expect(page.locator(".status-text")).toContainText("Şekil ayrıntılandırılıyor");

    await page.getByLabel("Tam ekran görüntüleyici").click();
    const viewer = page.getByRole("dialog", { name: "3D görüntüleyici" });
    const big = viewer.locator("model-viewer");
    await expect.poll(() => big.evaluate((el) => (el as HTMLElement & { loaded: boolean }).loaded), { timeout: 15_000 }).toBe(true);
    await expect(big).toHaveAttribute("camera-controls", "");
    await expect(big).not.toHaveAttribute("auto-rotate");
    await viewer.getByRole("button", { name: "Otomatik döndür" }).click();
    await expect(big).toHaveAttribute("auto-rotate", "");
    await viewer.getByRole("button", { name: "Açık" }).click();
    await expect(big).toHaveCSS("background-color", "rgb(233, 233, 236)");
    await viewer.locator("input[type=range]").fill("1.8");
    await expect.poll(() => big.evaluate((el) => (el as HTMLElement & { exposure: number }).exposure)).toBe(1.8);
    await viewer.getByRole("button", { name: "Görünümü sıfırla" }).click();
    await expect(viewer.getByRole("link", { name: "GLB indir" })).toHaveAttribute("href", "/media/k_0.glb");
    expect(await overflowProblems(page)).toEqual([]);
    // Fareyle sürükleyince model döner (kamera açısı değişir). Tam ortada model-viewer'ın kaydırma
    // göstergesi durur; sürükleme ondan uzakta başlar.
    const before = await big.evaluate((el) => (el as HTMLElement & { getCameraOrbit: () => { theta: number } }).getCameraOrbit().theta);
    const box = (await big.boundingBox())!;
    await page.mouse.move(box.x + box.width / 3, box.y + box.height / 3);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 3 + 200, box.y + box.height / 3, { steps: 10 });
    await page.mouse.up();
    await expect.poll(() => big.evaluate((el) => (el as HTMLElement & { getCameraOrbit: () => { theta: number } }).getCameraOrbit().theta)).not.toBe(before);
    await page.keyboard.press("Escape");
    await expect(viewer).toHaveCount(0);
    api.assertClean();
  });

  test("telefonda görüntüleyici taşmıyor", async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.telefon);
    const api = busyApi();
    await open(page, api);
    await openSidebarOnPhone(page);
    await page.locator(".mode", { hasText: "3D model" }).click();
    await openSidebarOnPhone(page);
    await page.locator(".history-item").first().click();
    await page.getByLabel("Tam ekran görüntüleyici").click();
    await expect(page.getByRole("dialog", { name: "3D görüntüleyici" }).locator("model-viewer")).toBeVisible();
    expect(await overflowProblems(page)).toEqual([]);
    await page.getByLabel("Görüntüleyiciyi kapat").click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    api.assertClean();
  });

  test("görsel sonucundaki küp düğmesi görseli 3D moduna ekler", async ({ page }) => {
    const api = busyApi();
    await open(page, api);
    await page.locator(".history-item").first().click();
    await page.locator(".tile").first().hover();
    await page.getByLabel("Bu görselden 3D model yap").first().click();
    await expect(page.locator(".mode.active")).toContainText("3D model");
    await expect(page.locator(".attachment img")).toHaveAttribute("src", "/media/a_0.png");
    await expect(page.locator(".composer .btn.primary", { hasText: "3D'ye çevir" })).toBeEnabled();
    await page.locator(".composer .btn.primary").click();
    const sent = api.requests.find((r) => r.method === "POST" && r.path === "/api/jobs")!.body as Record<string, any>;
    expect(sent.kind).toBe("model3d");
    expect(sent.inputs).toEqual(["a_0.png"]);
    api.assertClean();
  });
});

test.describe("ilerleme ve kalan süre", () => {
  const GB = 1024 ** 3;
  const packs = {
    image: { label: "Görsel", total: 17 * GB, done: 17 * GB, fraction: 1, rate: 95 * 2 ** 20, eta: 0 },
    model3d: { label: "3D", total: 9 * GB, done: 4 * GB, fraction: 4 / 9, rate: 95 * 2 ** 20, eta: 54 },
  };

  function progressApi(live: Job["live"], gpu = 97) {
    const api = new MockApi();
    const running = job({ kind: "model3d", inputs: ["uploads/cup.png"], prompt: "3D model", status: "running", progress: live?.progress ?? 0.4,
      stage: "UV açılıyor (%65)", params: { resolution: 1024, texture: "pbr", faces: 200000, seed: null }, outputs: [], finished_at: null, live });
    const queued = job({ kind: "model3d", inputs: ["uploads/cup.png"], prompt: "3D model", status: "queued", progress: 0,
      params: { resolution: 1024, texture: "pbr", faces: 200000, seed: null }, outputs: [], finished_at: null });
    api.thread("t1", "Kahve", "model3d", [running, queued]);
    api.settings["mode"] = "model3d";
    api.rt = { ...READY, packs, gpu_now: { t: 0, gpu, vram_used: 18 * GB, vram_total: 23 * GB },
      queue: { order: [running.id, queued.id], current: { ...running }, starts_in: { [queued.id]: 245 } } };
    return api;
  }

  for (const [vpName, viewport] of Object.entries(VIEWPORTS)) {
    test(`${vpName}: çalışan iş kalan süreyi, aşama süresini ve GPU kullanımını gösterir`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const api = progressApi({ progress: 0.62, eta: 125, stage_seconds: 40, stage_expected: 90, loading: false });
      await open(page, api);
      await expect(page.locator(".result .progress.indeterminate")).toHaveCount(0);
      await openSidebarOnPhone(page);
      await page.locator(".history-item").first().click();
      const status = page.locator(".status-text").first();
      await expect(status).toHaveText("UV açılıyor (%65) · %62 · ~2 dk 5 sn kaldı");
      await expect(page.locator(".status-sub").first()).toHaveText("bu aşamada 40 sn (genelde ~1 dk 30 sn) · GPU %97 · VRAM 18.0/23 GB");
      await expect(page.locator(".result .progress div").first()).toHaveAttribute("style", /width: 62%/);
      await expect(page.locator(".status-text").nth(1)).toContainText("~4 dk 5 sn sonra başlar");
      await openSidebarOnPhone(page);
      await expect(page.getByLabel("Model ayrıntıları")).toContainText("1/2 hazır");
      await expect(page.getByLabel("Model ayrıntıları")).toContainText("3D %44");
      await page.getByLabel("Model ayrıntıları").click();
      const dialog = page.getByRole("dialog", { name: "Modeller" });
      const pack = dialog.locator(".pack", { hasText: "3D" });
      await expect(pack).toContainText("%44");
      await expect(pack).toContainText("4.0/9.0 GB · 95 MB/s · ~54 sn");
      await expect(dialog.locator(".pack", { hasText: "Görsel" })).toContainText("hazır ✓");
      await page.keyboard.press("Escape");
      await expect(dialog).toHaveCount(0);
      expect(await overflowProblems(page)).toEqual([]);
      api.assertClean();
    });
  }

  test("ilk kez çalışan iş türünde süre ölçülüyor yazar, çubuk %0'da durmaz", async ({ page }) => {
    const api = progressApi({ progress: 0, eta: null, stage_seconds: 12, stage_expected: null, loading: true });
    await open(page, api);
    await page.locator(".history-item").first().click();
    await expect(page.locator(".status-text").first()).toContainText("süre ilk kez ölçülüyor");
    await expect(page.locator(".result .progress").first()).toHaveClass(/indeterminate/);
    await expect(page.locator(".status-sub").first()).toHaveText("bu aşamada 12 sn · GPU %97 · VRAM 18.0/23 GB");
    api.assertClean();
  });

  test("aşama olağandan çok uzun sürer ve GPU boştaysa uyarır", async ({ page }) => {
    const api = progressApi({ progress: 0.5, eta: 0, stage_seconds: 400, stage_expected: 90, loading: false }, 2);
    await open(page, api);
    await page.locator(".history-item").first().click();
    await expect(page.locator(".status-text").first()).toContainText("bitmek üzere");
    const sub = page.locator(".status-sub.warn").first();
    await expect(sub).toContainText("takılmış olabilir, iptal edip yeniden dene");
    api.assertClean();
  });

  test("GPU açılırken model indirme ilerlemesi görünür", async ({ page }) => {
    const api = new MockApi();
    api.thread("t1", "Kahve", "image", [job({ status: "waiting_gpu", progress: 0, outputs: [], finished_at: null })]);
    const download = { total: 16 * GB, done: 6 * GB, fraction: 6 / 16, rate: 100 * 2 ** 20, eta: 102 };
    api.rt = { ...STARTING, startup: { ...STARTING.startup!, step_elapsed_seconds: 65, download, setup: "3/5 ComfyUI ve ses paketleri kuruluyor" },
      packs: { image: { ...download, label: "Görsel" } } };
    await open(page, api);
    await page.locator(".history-item").first().click();
    await expect(page.locator(".status-sub").first()).toHaveText("bu adımda 1 dk 5 sn · kurulum 3/5 ComfyUI ve ses paketleri kuruluyor · görsel modelleri 6.0/16.0 GB · 100 MB/s · ~1 dk 42 sn");
    await page.getByLabel("Açılış ayrıntıları").click();
    const dialog = page.getByRole("dialog", { name: "Açılış ve modeller" });
    await expect(dialog).toContainText("Kurulum 3/5 ComfyUI ve ses paketleri kuruluyor");
    await expect(dialog).toContainText("Görsel modelleri: 6.0/16.0 GB");
    await expect(dialog.locator(".pack", { hasText: "Görsel" })).toContainText("%37");
    await dialog.getByLabel("Ayrıntıyı kapat").click();
    await expect(dialog).toHaveCount(0);
    expect(await overflowProblems(page)).toEqual([]);
    api.assertClean();
  });
});

test.describe("kenar çubuğu özet kutuları", () => {
  test.use({ viewport: VIEWPORTS.dizüstü });

  test("kutucuklar kısa özet gösterir ve az yer kaplar; ayrıntı pencerede açılır", async ({ page }) => {
    const api = new MockApi();
    api.rt = { ...READY, packs: { image: { label: "Görsel", total: 10, done: 10, fraction: 1, rate: null, eta: 0 } } };
    await open(page, api);
    const system = page.locator(".system");
    expect(((await system.boundingBox())!).height).toBeLessThan(180);
    await expect(page.getByLabel("GPU ve sistem ayrıntıları")).toContainText("%43");
    await expect(page.getByLabel("Model ayrıntıları")).toContainText("Hepsi hazır ✓");
    await expect(page.getByLabel("Kredi ayrıntıları")).toContainText("193.3 CU");
    await expect(page.getByLabel("Kredi ayrıntıları")).toContainText("0.08 CU/sa");

    await page.getByLabel("Kredi ayrıntıları").click();
    const credits = page.getByRole("dialog", { name: "Kredi" });
    await expect(credits).toContainText("Saatlik harcama");
    await expect(credits).toContainText("Bugün");
    await page.keyboard.press("Escape");
    await expect(credits).toHaveCount(0);

    await page.getByLabel("GPU ve sistem ayrıntıları").click();
    const gpu = page.getByRole("dialog", { name: "GPU ve sistem" });
    await expect(gpu).toContainText("RAM");
    await expect(gpu).toContainText("Disk");
    await gpu.getByLabel("Ayrıntıyı kapat").click();
    await expect(gpu).toHaveCount(0);
    expect(await overflowProblems(page)).toEqual([]);
    api.assertClean();
  });

  test("ayrıntı ortada değil, tıklanan kutucuğun yanında balon olarak durur; dışarı ya da aynı kutucuğa tıklayınca kapanır", async ({ page }) => {
    const api = new MockApi();
    api.rt = READY;
    await open(page, api);
    const tile = page.getByLabel("Kredi ayrıntıları");
    await tile.click();
    const balloon = page.getByRole("dialog", { name: "Kredi" });
    await expect(balloon).toBeVisible();
    const t = (await tile.boundingBox())!;
    const b = (await balloon.boundingBox())!;
    expect(b.x).toBeGreaterThanOrEqual(t.x + t.width); // kutucuğun sağında, üstünü örtmez
    expect(b.x).toBeLessThan(t.x + t.width + 30);
    expect(Math.abs(b.y + b.height - (t.y + t.height))).toBeLessThan(2); // alt kenarları hizalı
    await expect(page.locator(".detail-backdrop")).toHaveCount(0); // ekran kararmaz
    await page.getByLabel("GPU ve sistem ayrıntıları").click(); // başka kutucuk balonu değiştirir
    await expect(page.getByRole("dialog")).toHaveCount(1);
    await expect(page.getByRole("dialog", { name: "GPU ve sistem" })).toBeVisible();
    await page.getByLabel("GPU ve sistem ayrıntıları").click(); // aynı kutucuk kapatır
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await tile.click();
    await page.mouse.click(700, 300); // dışarı
    await expect(page.getByRole("dialog")).toHaveCount(0);
  });

  test("balon açıkken pencere boyutu değişirse kapanır, eski yerinde kalmaz", async ({ page }) => {
    const api = new MockApi();
    api.rt = READY;
    await open(page, api);
    await page.getByLabel("Kredi ayrıntıları").click();
    await expect(page.getByRole("dialog", { name: "Kredi" })).toBeVisible();
    // Telefon genişliğinde kenar çubuğu ekranın dışına çekilir; balonun eski konumu artık geçersiz.
    await page.setViewportSize(VIEWPORTS.telefon);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    api.assertClean();
  });
});

test.describe("dayanıklılık ve erişilebilirlik", () => {
  test.use({ viewport: VIEWPORTS.masaüstü });

  test("iptal isteği düşerse hata bandı çıkar, iş satırı eski hâlinde kalır", async ({ page }) => {
    const api = busyApi();
    await open(page, api);
    await page.locator(".history-item").first().click();
    const running = page.locator(".result", { has: page.locator(".status-text", { hasText: /^Üretiliyor/ }) });
    api.failNext.push({ prefix: "/api/jobs/", status: 502, body: "<html><h1>502 Bad Gateway</h1></html>" });
    page.once("dialog", (d) => d.accept());
    await running.getByRole("button", { name: "İptal" }).click();
    // Yakalanmayan söz (unhandled rejection) sayfa hatası sayılır; assertClean onu da yakalar.
    await expect(page.locator(".banner.err")).toContainText("Sunucuya şu an ulaşılamıyor (502)");
    await expect(running.getByRole("button", { name: "İptal" })).toBeVisible();
    await expect(page.locator(".cancelled-text", { hasText: "İptal edildi" })).toHaveCount(0);
    api.assertClean();
  });

  test("iptal başarılı ama liste yenilenemezse iş satırı hemen iptal görünür", async ({ page }) => {
    const api = busyApi();
    await open(page, api);
    await page.locator(".history-item").first().click();
    await expect(page.locator(".result").first()).toBeVisible();
    // Bundan sonraki sohbet yenilemeleri düşer (servis yeniden başlıyor).
    await page.route("**/api/threads/*", (route) => route.fulfill({ status: 502, contentType: "text/html", body: "<html>502</html>" }));
    page.once("dialog", (d) => d.accept());
    const running = page.locator(".result", { has: page.locator(".status-text", { hasText: /^Üretiliyor/ }) });
    await running.getByRole("button", { name: "İptal" }).click();
    await expect(page.locator(".banner.err")).toContainText("Sunucuya şu an ulaşılamıyor (502)");
    // Sunucunun döndürdüğü iptal hâli yerelde yazıldı: satır eski hâlinde kalmadı.
    await expect(page.locator(".cancelled-text", { hasText: "İptal edildi" })).toBeVisible();
    api.assertClean();
  });

  test("görsel büyütme düğmeyle açılır, Esc ile kapanır ve odak geri döner", async ({ page }) => {
    const api = busyApi();
    await open(page, api);
    await page.locator(".history-item").first().click();
    const thumb = page.getByRole("button", { name: "Görseli büyüt" }).first();
    await thumb.focus();
    await page.keyboard.press("Enter");
    const box = page.getByRole("dialog", { name: "Görsel önizleme" });
    await expect(box).toBeVisible();
    await expect(box).toHaveAttribute("aria-modal", "true");
    await expect(box).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(box).toHaveCount(0);
    await expect(thumb).toBeFocused();
    api.assertClean();
  });

  test("kayıtlar paneli Esc ile kapanır ve odağı geri verir", async ({ page }) => {
    const api = new MockApi();
    await open(page, api);
    const opener = page.getByLabel("Kayıtlar");
    await opener.click();
    const panel = page.getByRole("dialog", { name: "Kayıtlar" });
    await expect(panel.locator("pre")).toContainText("GPU ayrılıyor");
    await expect(panel).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.locator(".log")).toHaveCount(0);
    await expect(opener).toBeFocused();
    api.assertClean();
  });

  test("sohbet silme düğmesi klavyeyle odaklanınca görünür ve çalışır", async ({ page }) => {
    const api = new MockApi();
    api.thread("t1", "Kahve fincanı", "image", [job()]);
    await open(page, api);
    await page.getByRole("button", { name: "Kahve fincanı", exact: true }).focus();
    await page.keyboard.press("Tab");
    const del = page.getByRole("button", { name: "Sil" });
    await expect(del).toBeFocused();
    await expect(del).toHaveCSS("opacity", "1");
    page.once("dialog", (d) => d.accept());
    await page.keyboard.press("Enter");
    await expect(page.locator(".history-item")).toHaveCount(0);
    api.assertClean();
  });

  test("durum satırı rol=status, hata satırı rol=alert", async ({ page }) => {
    const api = busyApi();
    await open(page, api);
    await page.locator(".history-item").first().click();
    await expect(page.getByRole("status").first()).toContainText("Üretiliyor");
    await expect(page.locator(".error-text[role=alert]").first()).toHaveText(LONG_ERROR);
    api.assertClean();
  });

  test("3D süre açıklamaları ölçümle uyumlu (~30 sn soğuk fark, köşe boyama ~1 dk'ya kadar)", async ({ page }) => {
    const api = new MockApi();
    await open(page, api);
    await page.locator(".mode", { hasText: "3D model" }).click();
    if (!(await page.locator(".settings").isVisible())) await page.getByLabel("Ayarlar").click();
    const shape = field(page, "Şekil çözünürlüğü");
    await shape.locator(".info").hover();
    const tip = shape.locator(".tip");
    await expect(tip).toContainText("~30 sn daha uzun sürer");
    await expect(tip).toContainText("194.3 sn soğuk, 162.2 sn sıcak");
    await expect(tip).not.toContainText("3-4 dk");
    const texture = field(page, "Doku");
    await page.mouse.move(0, 0);
    await texture.locator(".info").hover();
    await expect(texture.locator(".tip")).toContainText("~1 dk'ya kadar kısa");
    await expect(texture.locator(".tip")).not.toContainText("~1 dk ekler");
    api.assertClean();
  });

  test("sekme kapanırken bekleyen ayar kaydı 400 ms beklenmeden gönderilir", async ({ page }) => {
    const api = new MockApi();
    await open(page, api);
    // Ayarı değiştir ve aynı anda sayfayı kapat: debounce dilimi atlanmalı.
    const t0 = await page.evaluate(() => {
      const box = document.querySelector<HTMLTextAreaElement>(".settings textarea.autogrow")!;
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(box, "kapanışta kaydedildi");
      box.dispatchEvent(new Event("input", { bubbles: true }));
      const t = Date.now();
      window.dispatchEvent(new Event("pagehide"));
      return t;
    });
    await expect.poll(() => api.requests.some((r) => r.method === "PUT" && r.path === "/api/settings/image.params")).toBe(true);
    const saved = api.requests.find((r) => r.method === "PUT" && r.path === "/api/settings/image.params")!;
    expect(saved.body).toMatchObject({ negative: "kapanışta kaydedildi" });
    expect(saved.at - t0).toBeLessThan(300);
    api.assertClean();
  });
});

test("kayıtlı mod bilinmiyorsa arayüz boş kalmaz, görsel moduyla açılır", async ({ page }) => {
  const api = new MockApi();
  api.settings["mode"] = "nonsense-mode";
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await open(page, api);
  await expect(page.locator(".composer textarea")).toBeVisible();
  expect(errors).toEqual([]);
});
