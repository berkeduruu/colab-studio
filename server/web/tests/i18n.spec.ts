import { expect, test } from "@playwright/test";
import { job, LONG_ERROR, MockApi, runtimeState } from "./mock";

// Dil seçilmemişse arayüz İngilizce açılır; seçim kaydedilir. Sunucunun Türkçe mesajları çevrilir.

test("varsayılan dil İngilizce, sunucu mesajları çevrilir", async ({ page }) => {
  const api = new MockApi();
  api.lang = null;
  api.rt = runtimeState({ phase: "error", detail: "GPU kapatılamadı, yeniden denenecek: komut başarısız (1): colab sessions\nboom" });
  api.thread("t1", "kedi", "image", [job({ id: "j1", status: "error", error: "Süre aşımı: iş 30 dakikada bitmedi, durduruldu", outputs: [] })]);
  await api.install(page);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Image", exact: true })).toBeVisible();
  await expect(page.getByText("History")).toBeVisible();
  await expect(page.getByText(/GPU error: Could not stop the GPU, will retry: command failed \(1\)/)).toBeVisible();
  await page.getByText("kedi").click();
  await expect(page.getByRole("alert").filter({ hasText: "Timeout: the job did not finish in 30 minutes and was stopped" })).toBeVisible();
  api.assertClean();
});

test("dil düğmesi Türkçeye geçirir ve seçimi hatırlar", async ({ page }) => {
  const api = new MockApi();
  api.lang = null;
  await api.install(page);
  await page.goto("/");
  await page.getByLabel("Language").selectOption("tr");
  await expect(page.getByRole("button", { name: "Görsel", exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText("Geçmiş")).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("studio.lang"))).toBe("tr");
  api.assertClean();
});

test("model adı ve açıklaması models.toml'dan gelir", async ({ page }) => {
  const api = new MockApi();
  api.lang = null;
  api.models.image = { label: "My Custom Model", description: { en: "A custom fine-tune.", tr: "Özel ince ayar." } };
  await api.install(page);
  await page.setViewportSize({ width: 1440, height: 900 }); // geniş ekranda ayar paneli açık başlar
  await page.goto("/");
  await expect(page.locator(".settings").getByText("My Custom Model")).toBeVisible();
  await expect(page.locator(".settings").getByText("A custom fine-tune.")).toBeAttached();
  api.assertClean();
});

test("uzun ham hata çevrilmeden ama kırpılmadan gösterilir", async ({ page }) => {
  const api = new MockApi();
  api.lang = null;
  api.rt = runtimeState({ phase: "error", detail: LONG_ERROR });
  await api.install(page);
  await page.goto("/");
  await expect(page.getByText(/GPU could not start: command failed \(1\)/)).toBeVisible();
  api.assertClean();
});
