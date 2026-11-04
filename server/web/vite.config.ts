import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // 3D görüntüleyici (model-viewer + three.js, ~1 MB) ayrı parça; yalnızca 3D sonuç açılınca iner.
  build: { chunkSizeWarningLimit: 1100 },
});
