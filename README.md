# Grok Bot Video MCP

[简体中文](README.zh-CN.md) · [MIT License](LICENSE)

A local MCP server that turns an existing **Grok Bot with video-generation capability** into a text/image-to-video workflow. Submit a prompt and local reference images, follow the job, and retrieve the generated MP4 without manually relaying messages or downloading files from the Bot's chat.

This is an independent community integration, not an official xAI or Cursor SDK. It uses a Grok Bot webhook routine, not the separately billed xAI video API. Your Bot must have the required tools and allowance.

## What it does

- Exposes five tools over standard **stdio MCP**, using the official Python MCP SDK.
- Sends text and up to five local PNG, JPEG or WebP references through per-job HTTPS links.
- Uses a webhook to wake your existing Bot, which downloads the references and invokes its own video-generation tool.
- Receives MP4 uploads, checks that they decode, records actual resolution/duration, and optionally removes audio.
- Stores jobs locally and refuses to automatically resubmit ambiguous webhook requests.

The flow is: MCP client → local bridge → Grok Bot webhook → video generation → authenticated MP4 callback → local download.

## Requirements

- macOS or Linux, Python **3.12+**. Native Windows is not supported because service coordination uses `fcntl`.
- [uv](https://docs.astral.sh/uv/) or a Python virtual environment with pip.
- FFmpeg and ffprobe on `PATH`.
- Cloudflare's `cloudflared` executable, installed below or already available on `PATH`.
- A Grok Bot account with a video-capable Bot and webhook routines. See [Grok Bot routines](https://cursor.com/help/grok-bot/routines).

The Mac and Linux CI jobs run local transfer and MCP protocol tests. Live Bot generation was exercised on macOS; CI does not use account credentials or consume generation allowance.

## Quick start

### 1. Install

```bash
git clone https://github.com/sujian666666com-glitch/grokbot-video-mcp.git
cd grokbot-video-mcp
uv venv --python 3.12
uv pip sync --python .venv/bin/python requirements.lock

# macOS; Linux users can install ffmpeg with their package manager.
brew install ffmpeg

# Downloads an official release and verifies its published SHA-256 digest.
.venv/bin/python scripts/install_cloudflared.py
```

If `cloudflared` is already on `PATH`, the last step is optional. The installer supports macOS/Linux on arm64 and x86_64. To pin a release, pass `--version RELEASE_TAG`.

With pip instead of uv: create `.venv` using `python3.12 -m venv .venv`, then run `.venv/bin/python -m pip install -r requirements.lock`.

### 2. Create the Bot routine

Give your video-capable Bot the instructions in [docs/grok-routine.md](docs/grok-routine.md). Create a single **webhook-only** routine named `Codex Video Bridge`, with no schedule.

Open the Bot's details → Routines → the routine's **Webhook** panel (or its context menu → Webhook). Copy the `POST to` URL and sender key from that panel. A webhook HTTP 200 only means that a run was accepted, not that a video is complete.

### 3. Configure credentials locally

```bash
.venv/bin/python setup_form.py
```

Open the printed loopback URL in your browser, enter the webhook URL and key, and save. The one-use form exits after saving, and the configuration is written as a `0600` file under `data/`. The key is never printed. Do not put your key in chat, a Git commit, or a shell command argument.

For noninteractive setup, `bridge.py configure` also accepts `--webhook-url` and `--key-file`; alternatively it reads the key from standard input. An example shape is in [examples/config.example.json](examples/config.example.json).

### 4. Connect an MCP client

For Codex, from the checkout directory:

```bash
codex mcp add grokbot-video -- "$(pwd)/.venv/bin/python" "$(pwd)/mcp_server.py"
```

Set `tool_timeout_sec = 180` for this server in Codex's MCP configuration; first use may start an HTTPS tunnel. For other stdio MCP clients, use [examples/mcp.json](examples/mcp.json) and replace both placeholder paths with absolute paths. Reload the client if it does not discover the new server immediately.

The included client exercises the same MCP protocol directly:

```bash
.venv/bin/python call_tool.py list-tools
.venv/bin/python call_tool.py generate_video --arguments \
  '{"prompt":"Animate the reference with a slow camera move; preserve the subject","image_paths":["/absolute/path/reference.png"],"duration_seconds":6,"aspect_ratio":"16:9","silent":true}'

.venv/bin/python call_tool.py wait_video --arguments \
  '{"job_id":"JOB_ID_FROM_SUBMISSION","timeout_seconds":30}'
.venv/bin/python call_tool.py download_video --arguments \
  '{"job_id":"JOB_ID_FROM_SUBMISSION"}'
```

`generate_video` spends your Bot/video allowance. Listing tools, querying local jobs, and running tests do not generate videos.

## MCP tools

| Tool | Purpose |
| --- | --- |
| `generate_video` | Submit `prompt`, optional `image_paths`, `duration_seconds`, `aspect_ratio`, `silent`; returns a job ID. |
| `get_video_status` | Read callbacks, transfer evidence, errors and verified video metadata. |
| `wait_video` | Wait for up to 45 seconds; return current state if the job is still running. |
| `download_video` | Copy a completed, checksum-verified MP4 to `~/Downloads/grokbot-videos/` or `output_dir`. |
| `list_video_jobs` | List the most recent local jobs, without capability tokens. |

Defaults: 6 seconds, `16:9`, silent. The bridge accepts 1–15 seconds and ratios `16:9`, `9:16`, `1:1`, `4:3`, `3:4`. These are **generation preferences**: actual dimensions and duration depend on the Bot's video tool and are returned with the result. Multi-reference support also depends on that tool. Silent output is enforced locally by removing an audio track when present.

## Job states and limits

- `prepared` → `submitting` → `submitted` → `running` → `succeeded` or `failed`.
- `submission_unknown` means the webhook connection was interrupted and the Bot might already be generating. Inspect the existing job instead of submitting it again.
- `reference_downloaded` records a real reference transfer. `video_received_and_verified` records a decodable uploaded video. Neither validates artistic quality.
- Up to 5 references, at most 20 MiB each; MP4 uploads are limited to 90 MiB.
- Read/upload links expire after 24 hours. Local files are retained until you remove them.

## Runtime configuration

| Variable | Purpose |
| --- | --- |
| `GROKBOT_BRIDGE_DATA` | State, credentials, logs and media directory; defaults to `<checkout>/data`. Use the same value for setup and the MCP server. |
| `GROKBOT_CLOUDFLARED` | Optional executable override. Otherwise checks `bin/cloudflared`, then `PATH`. |
| `GROKBOT_FFMPEG` / `GROKBOT_FFPROBE` | Optional executable overrides; defaults to `PATH`. |

The local HTTP server binds only to loopback. A [Cloudflare Quick Tunnel](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/) makes the capability-protected transfer endpoints reachable by the cloud Bot. Quick Tunnels have temporary URLs and are intended for development/personal use, not a production availability guarantee. Keep this machine online while jobs run. Do not restart its tunnel during an active job.

```bash
.venv/bin/python bridge.py health
.venv/bin/python bridge.py list
.venv/bin/python bridge.py stop  # Refuses to invalidate unexpired in-flight jobs.
```

See [SECURITY.md](SECURITY.md) for the trust boundary and [docs/protocol.md](docs/protocol.md) for callback details.

## Development and testing

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Tests use temporary state, generated fixture images/videos, a loopback HTTP server, and a real stdio MCP client. They cover authorization, successful transfer, invalid uploads, token redaction and ambiguous webhook submission. They do not contact Grok Bot. No real webhook credentials, user media, local environment or downloaded third-party binaries are included in this repository.

## License

MIT. Third-party dependencies and services retain their own licenses and terms. Grok and Cursor are trademarks of their respective owners.
