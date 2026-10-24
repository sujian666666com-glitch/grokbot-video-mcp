# Grok Bot Video MCP

[English](README.md) · [MIT 许可证](LICENSE)

把已有的官方 **Grok Bot 视频能力**接成 MCP：传入文字与本地参考图片，自动提交任务、查询进度、接收 MP4 并下载。完成一次账号配置后，生成视频时不需要用户向 Bot 传话或手工搬运文件。

这是独立的社区集成，不是 xAI 或 Cursor 官方 SDK。使用你现有 Bot 的工具与额度，不改走独立计费的 xAI 视频 API。账号必须具备视频生成和 Webhook 例程能力。

## 安装

需要 macOS 或 Linux、Python 3.12+、FFmpeg/ffprobe。原生 Windows 暂不支持。

```bash
git clone https://github.com/sujian666666com-glitch/grokbot-video-mcp.git
cd grokbot-video-mcp
uv venv --python 3.12
uv pip sync --python .venv/bin/python requirements.lock

# macOS；Linux 使用对应包管理器安装 ffmpeg
brew install ffmpeg

# 从 Cloudflare 官方发布下载，并校验 SHA-256
.venv/bin/python scripts/install_cloudflared.py
```

已有 `cloudflared` 且位于 PATH 时可跳过最后一步。也可用 `python3.12 -m venv .venv` 和 `.venv/bin/python -m pip install -r requirements.lock` 代替 uv。

## 一次性接入

1. 让有视频能力的 Grok Bot 按 [例程模板](docs/grok-routine.md)创建名为 `Codex Video Bridge` 的例程，只设置 Webhook，不设定时。
2. 在 Bot 详情的 Routines 中打开该例程的 Webhook 面板，取得 URL 和 sender key；部分客户端在例程的右键菜单中提供 Webhook 入口。
3. 运行下面的本地配置页，将 URL 与 key 填入页面。页面仅监听本机，保存后退出，凭据文件权限为 `0600`。不要把 key 粘贴到聊天、提交到 Git 或写进命令参数。

```bash
.venv/bin/python setup_form.py
```

Grok Bot 的 Webhook 返回 HTTP 200，只表示已接受并启动任务，不代表视频已经生成。参见[官方说明](https://cursor.com/help/grok-bot/routines)。

## 注册 MCP

在项目目录执行：

```bash
codex mcp add grokbot-video -- "$(pwd)/.venv/bin/python" "$(pwd)/mcp_server.py"
```

建议为该服务器配置 `tool_timeout_sec = 180`，首次提交可能需要启动 HTTPS 隧道。其他客户端使用 [examples/mcp.json](examples/mcp.json)，替换绝对路径即可。客户端未自动发现新工具时重新加载 MCP 配置。

| 工具 | 作用 |
| --- | --- |
| `generate_video` | 提交文字、参考图片和生成偏好，返回任务 ID |
| `get_video_status` | 查询真实回调、图片下载记录、错误和视频元数据 |
| `wait_video` | 等待最多 45 秒；尚未完成时返回当前状态 |
| `download_video` | 校验后保存到下载目录或指定目录，不覆盖不同内容的文件 |
| `list_video_jobs` | 列出最近的本地任务，不返回凭据令牌 |

默认偏好是 6 秒、16:9、静音。接口接受 1–15 秒，以及最多 5 张 PNG/JPEG/WebP；每张不超过 20 MiB。实际时长、分辨率、比例和多图能力取决于 Bot 当前的视频工具，以返回的实测元数据为准。静音选项由接收端移除音轨实现。

## 直接测试 MCP

配套客户端使用真实 stdio MCP 协议，适合尚未刷新工具列表的聊天和接口调试：

```bash
.venv/bin/python call_tool.py list-tools
.venv/bin/python call_tool.py generate_video --arguments \
  '{"prompt":"让参考图中的主体轻轻转动，保持外观一致","image_paths":["/absolute/path/reference.png"],"duration_seconds":6,"aspect_ratio":"16:9","silent":true}'

.venv/bin/python call_tool.py wait_video --arguments \
  '{"job_id":"替换为提交返回的任务ID","timeout_seconds":30}'
.venv/bin/python call_tool.py download_video --arguments \
  '{"job_id":"替换为提交返回的任务ID"}'
```

生成会消耗账号的视频额度。查询任务、列出工具和运行本地测试不会生成视频。

## 状态与运行边界

- `submitted`：Webhook 已接受。`running`：Bot 已回报处理开始。
- `reference_downloaded`：参考图确实被下载。`video_received_and_verified`：MP4 已回传且可解码。
- `succeeded` 才能下载结果；解码通过不代表创作效果符合要求，仍应检查画面。
- `submission_unknown` 表示请求途中连接中断，Bot 可能已开始生成，不能直接重复提交。
- 使用临时 Cloudflare HTTPS 隧道，本机在任务期间需要保持联网。运行中不要重启隧道，否则旧任务的回传地址会失效。
- 链接 24 小时后过期；本地图片、视频和记录不会自动删除。视频上传上限为 90 MiB。
- 原生图生视频链路已在 macOS 实测。CI 验证 macOS/Linux 的本地传输和 MCP 协议，不使用真实账号。

可通过 `GROKBOT_BRIDGE_DATA` 指定数据目录；设置页面和 MCP 进程必须使用同一个值。`GROKBOT_CLOUDFLARED`、`GROKBOT_FFMPEG`、`GROKBOT_FFPROBE` 可覆盖相应可执行文件位置。

```bash
.venv/bin/python bridge.py health
.venv/bin/python bridge.py list
.venv/bin/python bridge.py stop
.venv/bin/python -m unittest discover -s tests -v
```

`stop` 会拒绝打断尚未过期的运行中任务。更多细节见[回调协议](docs/protocol.md)和[数据安全说明](SECURITY.md)。

## 开源范围

仓库包含源码、安装脚本、例程模板、MCP 配置示例和测试。不包含个人 Webhook 密钥、任务数据库、用户图片、生成视频、完整账号配置或下载的第三方二进制文件。

代码使用 MIT 许可证；第三方软件和服务仍适用各自的许可证与服务条款。
