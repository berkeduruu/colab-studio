# Colab Studio MCP server

Exposes the studio to MCP clients such as Claude Code and Claude Desktop: start and stop the GPU,
generate images, video, music, speech and 3D models, transcribe audio, and browse results.

It talks only to the studio's HTTP API (media included), so it works with the script install and
with Docker. Run it where it can reach the studio, usually the same machine (or start it there over
SSH). The port is read from the studio's `.env`, so no extra configuration is needed. Requires
Node.js 22+.

## Install

```bash
cd mcp
./install.sh          # npm ci + build + a protocol self-check
```

Then add it to Claude Code (`install.sh` prints these commands with your real paths):

```bash
# studio on this machine
claude mcp add -s user colab-studio -- /path/to/node /path/to/colab-studio/mcp/dist/index.js

# studio on a server you reach with `ssh my-server`
claude mcp add -s user colab-studio -- ssh -T my-server /path/to/node /path/to/colab-studio/mcp/dist/index.js
```

`-s user` makes it available in every project (without it, only in the current folder). Use the full
path to node (`command -v node` on the studio machine): a non-interactive ssh shell does not load
nvm or fnm, so a bare `node` is often not found there.

`./test-mcp.sh` checks the protocol handshake and that every tool is listed, then calls
`colab_runtime_status` and `get_system_status` and lists the resources against a running studio.

## Settings (environment variables, all optional)

| Variable | Default | Meaning |
| --- | --- | --- |
| `STUDIO_ENV_FILE` | `../.env` (the studio's) | Where `STUDIO_PORT` is read from. |
| `STUDIO_URL` | `http://127.0.0.1:$STUDIO_PORT` | Studio API address. |
| `STUDIO_PUBLIC_MEDIA_URL` | `$STUDIO_URL/media` | Base URL returned for media files. |
| `COLAB_BIN` | `colab` | colab CLI, for `get_system_status` and `colab_sessions_raw` (with Docker it is inside the container; these tools say so). |
| `MCP_ADMIN_TOOLS` | off | `1` also lists the admin tools `delete_thread`, `update_setting`, `restart_colab_studio`, `colab_sessions_raw`. Off keeps the tool list short and stops one agent from interrupting another agent's work on the same studio. |
| `MCP_TRANSPORT` | stdio | `sse` (or `--sse`) serves HTTP/SSE instead. |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8195` | SSE listen address (`--host`, `--port`). |
| `MCP_HTTP_TOKEN` | empty | If set, every SSE request needs `Authorization: Bearer <token>`; only `GET /health` (a liveness check that reveals nothing) answers without it. |
| `MCP_ALLOWED_ORIGINS` | empty | Comma-separated CORS allowlist for browser clients. |

## Tools

- **GPU:** `colab_runtime_status`, `colab_runtime_start`, `colab_runtime_stop`, `colab_runtime_toggle_auto`,
  `colab_runtime_metrics`, `colab_runtime_logs`. A job starts the GPU by itself and downloads that job's
  models first, so the status and start tools are not needed before generating.
- **Generation:** `generate_image`, `enhance_prompt`, `generate_video`, `generate_music`, `generate_speech`,
  `transcribe_audio`, `generate_3d`
- **Jobs:** `get_job`, `wait_for_job`, `cancel_job`
- **Media:** `list_media`, `get_media_base64` (a downscaled JPEG preview of an image, or a 2x2 frame sheet of
  a video, so Claude can look at a result), `upload_media` (from a `source_url` the server downloads, or
  base64 for small files)
- **Studio:** `list_threads`, `get_thread`, `list_presets`, `save_preset`, `delete_preset`, `get_settings`,
  `get_system_status`
- **Admin** (only with `MCP_ADMIN_TOOLS=1`): `delete_thread`, `update_setting`, `colab_sessions_raw`,
  `restart_colab_studio` (only with `install.sh --service`)

Job states are the studio's own: `queued`, `waiting_gpu`, `running`, `done`, `error`. `done` and
`error` are final; a job never leaves them. A cancelled job ends as `error` with `cancelled: true`
(the tools report it as cancelled). Stopping the GPU by hand (`colab_runtime_stop`) cancels running
and queued jobs, so the queue does not start the GPU again a few seconds later.

### Waiting for results

Submit a job, then call `wait_for_job` once: it blocks until the job is done (no tokens are spent while it
waits) and returns the output names and URLs. The generation tools also take `wait: true` to do both in one
call. When `timeout_seconds` runs out the job **keeps running** and the tool says so; call `wait_for_job`
again. Pass `cancel_on_timeout: true` to cancel it instead. If the client sends a progress token, the server
reports each stage and percentage change as an MCP progress notification.

### Keeping token use low

Replies are short summaries; `verbose: true` adds the raw JSON (`get_job`, `colab_runtime_status`,
`colab_runtime_metrics`, `list_threads`, `get_thread`). The server also sends clients a short usage guide
(MCP `instructions`). Media previews need `ffmpeg` on the machine that runs the MCP server; without it the original image is
sent. To copy a file to your computer, download its URL rather than reading it through the MCP.
