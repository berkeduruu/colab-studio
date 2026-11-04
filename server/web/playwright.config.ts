import { defineConfig } from "@playwright/test";

// Arayüz testleri derlenmiş uygulamayı (vite preview) gerçek tarayıcıda açar; /api istekleri
// tests/mock.ts'deki sahte sunucuya gider, GPU ya da Colab gerekmez.
export default defineConfig({
  testDir: "tests",
  fullyParallel: true,
  reporter: "list",
  use: { baseURL: "http://127.0.0.1:4173", colorScheme: "dark" },
  webServer: {
    command: "npx vite preview --host 127.0.0.1 --port 4173 --strictPort",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: false,
  },
});
