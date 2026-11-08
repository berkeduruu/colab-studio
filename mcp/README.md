# Colab Studio MCP server

Exposes the studio to MCP clients such as Claude Code and Claude Desktop: start and stop the GPU,
generate images, video, music, speech and 3D models, transcribe audio, and browse results.

It talks to the studio's local API, so it runs **on the machine where the studio runs** (or is
started there over SSH). Port and data directory are read from the studio's `.env`, so no extra
configuration is needed. Requires Node.js 22+.

## Install

```bash
cd mcp
./install.sh          # npm ci + build + a protocol self-check
```

Then add it to Claude Code:

```bash
# studio on this machine
claude mcp add colab-studio -- node /path/to/colab-studio/mcp/dist/index.js

# studio on a server you reach with `ssh my-server`
claude mcp add colab-studio -- ssh -T my-server node /path/to/colab-studio/mcp/dist/index.js
```

`./test-mcp.sh` checks the protocol handshake and every tool against a running studio.

## Settings (environment variables, all optional)

| Variable | Default | Meaning |
| --- | --- | --- |
| `STUDIO_ENV_FILE` | `../.env` (the studio's) | Where `STUDIO_PORT` / `STUDIO_DATA` are read from. |
| `STUDIO_URL` | `http://127.0.0.1:$STUDIO_PORT` | Studio API address. |
| `STUDIO_DATA` | `~/.local/share/colab-studio` | Studio data directory (media lives in `media/`). |
| `STUDIO_PUBLIC_MEDIA_URL` | `$STUDIO_URL/media` | Base URL returned for media files. |
| `COLAB_BIN` | `colab` | colab CLI, for `colab_sessions_raw`. |
| `MCP_TRANSPORT` | stdio | `sse` (or `--sse`) serves HTTP/SSE instead. |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8195` | SSE listen address (`--host`, `--port`). |
| `MCP_HTTP_TOKEN` | empty | If set, every SSE request needs `Authorization: Bearer <token>`; only `GET /health` (a liveness check that reveals nothing) answers without it. |
| `MCP_ALLOWED_ORIGINS` | empty | Comma-separated CORS allowlist for browser clients. |

## Tools

- **GPU:** `colab_runtime_status`, `colab_runtime_start`, `colab_runtime_stop`, `colab_runtime_toggle_auto`,
  `colab_runtime_metrics`, `colab_runtime_logs`, `colab_sessions_raw`
- **Generation:** `generate_image`, `enhance_prompt`, `generate_video`, `generate_music`, `generate_speech`,
  `transcribe_audio`, `generate_3d` (each accepts `wait: true` to block until the job finishes)
- **Jobs:** `get_job`, `wait_for_job`, `cancel_job`
- **Media:** `list_media`, `get_media_base64` (lets Claude see an image), `upload_media`
- **Studio:** `list_threads`, `get_thread`, `delete_thread`, `list_presets`, `save_preset`, `delete_preset`,
  `get_settings`, `update_setting`, `get_system_status`, `restart_colab_studio` (only with `install.sh --service`)

Job states are the studio's own: `queued`, `waiting_gpu`, `running`, `done`, `error` (a cancelled
job ends as `error`; the tools report it as cancelled).
