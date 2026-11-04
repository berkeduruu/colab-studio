import { test } from "@playwright/test";
import { MockApi, STARTING } from "./mock";

test.skip(!process.env.SHOTS, "yalnızca SHOTS=1 ile");
test("ekran görüntüleri", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const api = new MockApi();
  api.rt = STARTING;
  await api.install(page);
  await page.goto("/");
  await page.getByLabel("Menü").click();
  await page.screenshot({ path: `${process.env.SHOTS}/phone-sidebar.png` });
  await page.getByLabel("Menü").click({ force: true }).catch(() => {});
  await page.mouse.click(350, 400);
  await page.getByLabel("Ayarlar").click();
  await page.locator(".settings .info").nth(2).hover();
  await page.screenshot({ path: `${process.env.SHOTS}/phone-settings.png` });
});
