import esbuild from "esbuild";
import { chmodSync } from "node:fs";

try {
  await esbuild.build({
    entryPoints: ["src/index.ts"],
    bundle: true,
    platform: "node",
    target: "node22",
    format: "esm",
    outfile: "dist/index.js",
    banner: {
      js: `#!/usr/bin/env node
import { createRequire as __createRequire } from "node:module";
import { fileURLToPath as __fileURLToPath } from "node:url";
import { dirname as __dirnameFunction } from "node:path";
const require = __createRequire(import.meta.url);
const __filename = __fileURLToPath(import.meta.url);
const __dirname = __dirnameFunction(__filename);
`,
    },
    packages: "bundle",
    sourcemap: true,
  });

  chmodSync("dist/index.js", 0o755);
  console.log("Build successful: dist/index.js created and marked executable.");
} catch (error) {
  console.error("Build failed:", error);
  process.exit(1);
}
