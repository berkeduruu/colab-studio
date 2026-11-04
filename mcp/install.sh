#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

node_major=$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)
if [[ ${node_major} -lt 22 ]]; then
  echo "ERROR: the MCP server needs Node.js 22 or newer (found: $(node --version 2>/dev/null || echo none))." >&2
  echo "Install it from https://nodejs.org/ or with your package manager, then re-run ./install.sh" >&2
  exit 1
fi

echo "=== Installing dependencies for the Colab Studio MCP server (npm ci) ==="
npm ci --no-audit --no-fund

echo "=== Building dist/index.js ==="
npm run build

echo "=== Verifying MCP Server build ==="
node -e '
import { spawn } from "node:child_process";

const child = spawn("node", ["dist/index.js"], { stdio: ["pipe", "pipe", "inherit"] });

// Satır tamponlu okuyucu: parçalı gelen mesajlar birleştirilir, JSON.parse yalnızca tam satırda çalışır.
let buffer = "";
const timer = setTimeout(() => {
  console.error("MCP handshake did not complete within 10s");
  child.kill();
  process.exit(1);
}, 10000);

function finish(code) {
  clearTimeout(timer);
  child.kill();
  process.exit(code);
}

child.stdout.on("data", (chunk) => {
  buffer += chunk.toString();
  const lines = buffer.split("\n");
  buffer = lines.pop() || "";

  for (const line of lines) {
    if (!line.trim()) continue;
    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      continue; // JSON olmayan çıktı (log satırı) atlanır
    }
    if (msg.id !== 1) continue;
    const name = msg.result?.serverInfo?.name;
    if (name !== "colab-studio") {
      console.error("Unexpected server name:", name);
      finish(1);
    }
    console.log("MCP Server verified successfully! Name:", name);
    finish(0);
  }
});

child.on("exit", (code) => {
  clearTimeout(timer);
  if (code !== 0) {
    console.error("MCP server exited early with code", code);
    process.exit(1);
  }
});

child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "verify", version: "1.0" } } }) + "\n");
'

echo "=== Done ==="
echo "Add it to Claude Code on this machine:"
echo "  claude mcp add colab-studio -- node \"$SCRIPT_DIR/dist/index.js\""
echo "From another computer over SSH (replace my-server with your ssh host):"
echo "  claude mcp add colab-studio -- ssh -T my-server node \"$SCRIPT_DIR/dist/index.js\""
