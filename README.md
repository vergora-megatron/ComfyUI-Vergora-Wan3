# ComfyUI Vergora Wan 3.0 Video

A ComfyUI custom node that generates video with **Wan 3.0** (`w3.0-video`) through the Vergora API.
Text-to-video, image-to-video and first/last-frame video all live in one node: the request is
submitted, polled and downloaded for you, and the finished clip shows up in the ComfyUI result panel.

- **Model:** `w3.0-video` (Wan 3.0, provider listed as `alibaba` in the Vergora model catalog).
  A second video model, `minimax-h3`, is available from the same node.
- **Modes:** text → video, image → video (first frame), first frame + last frame.
- **No code required:** prompts, duration and frames are node widgets; wiring is optional.
- **Verified on:** ComfyUI 0.40.0 (frontend 1.55.17), Python 3.13, Windows 11, RTX 5070.

## Requirements

- ComfyUI (recent version; developed against 0.40.0)
- A Vergora API key
- No extra pip packages are required. The request layer uses `requests` when it is installed and
  falls back to the Python standard library `urllib` otherwise, so the node loads in any ComfyUI
  installation.

## Installation

1. Copy this folder into your ComfyUI `custom_nodes` directory:
   - portable / manual install: `<ComfyUI>/custom_nodes/ComfyUI-Vergora-Wan3`
   - ComfyUI Desktop: the `custom_nodes` folder is inside the installation base path
2. Create your own configuration from the shipped template and fill in your key
   (`config.ini` itself is gitignored and does not exist in a fresh checkout):
   - Windows: `copy config.ini.example config.ini`
   - macOS / Linux: `cp config.ini.example config.ini`

   Then edit the new `config.ini`:
   ```ini
   [API]
   BASE_URL = https://api.vergora.ai/v1
   KEY = sk-your-key-here
   ```
   Every setting is documented inside `config.ini.example`: `BASE_URL`, `KEY`,
   `POLL_INTERVAL`, `TIMEOUT` and `OUTPUT_DIR`.
3. Restart ComfyUI, then add the node from the `vergora` category
   (search for `vergora`, or for `wan3` / `video`).

`config.ini` is listed in `.gitignore`; keep your key out of version control. As an alternative you
can set the environment variables `VERGORA_BASE_URL` and `VERGORA_API_KEY`, which take precedence
over the file.

## What is in this repository

| File | Purpose |
| --- | --- |
| `nodes.py`, `__init__.py` | The custom node itself |
| `config.ini.example` | Configuration template, copy it to `config.ini` |
| `test_offline.py` | Offline self-test, no network or key needed |
| `pyproject.toml` | Package metadata for the ComfyUI node registry |
| `README.md`, `LICENSE` | Documentation and license |

## Node reference

Node id: `VergoraVideoGenerator`
Display name: `vergora 视频生成（文生/图生/首尾帧）`
Category: `vergora`

| Input | Type | Default | Notes |
| --- | --- | --- | --- |
| `prompt` | STRING (multiline) | empty | What the clip should show. Required unless a frame is supplied |
| `model` | COMBO | `w3.0-video` | `w3.0-video` (Wan 3.0) or `minimax-h3` |
| `duration` | INT | 5 | Clip length in seconds, 1 to 30. This is what controls length, not the prompt |
| `seed` | INT | -1 | `-1` means "do not send a seed" and the upstream picks one. Any value `>= 0` is sent as-is |
| `first_frame` | IMAGE (optional) | not connected | Connect an image to get image-to-video |
| `last_frame` | IMAGE (optional) | not connected | Connect together with `first_frame` for first/last-frame video |
| `first_frame_url` | STRING (optional) | empty | A public image URL, used when `first_frame` is not connected |
| `last_frame_url` | STRING (optional) | empty | A public image URL, used when `last_frame` is not connected |
| `extra_json` | STRING (optional) | empty | Extra request fields, for example `{"resolution": "720p"}` |

| Output | Type | Notes |
| --- | --- | --- |
| `video_path` | STRING | Absolute path of the downloaded `.mp4` |
| `task_id` | STRING | The upstream task id, useful for support or for polling by hand |
| `video_url` | STRING | The temporary upstream URL the file was fetched from |

The node is registered as an output node and returns the clip as a preview result, so ComfyUI will
not complain that the workflow has no output node.

## Usage

**Text to video.** Fill in `prompt`, keep `duration` at 5 and press Run. The console prints the
submit call and then one status line per poll; the clip usually lands in two to four minutes.

**Image to video.** Feed any `IMAGE` into `first_frame` (a `LoadImage` node, or the output of an
image generation node). If your image already lives at a public URL, put the URL in
`first_frame_url` instead — that keeps the request body small.

**First and last frame.** Connect both `first_frame` and `last_frame`; the model interpolates
between them.

The downloaded file is saved into the ComfyUI output directory (`folder_paths.get_output_directory()`
unless `OUTPUT_DIR` is set) and named `vergora_<taskid>_<timestamp>.mp4`.

## What the node does under the hood

The Vergora video API is an asynchronous task API with an OpenAI-Sora-style shape:

| Step | Call |
| --- | --- |
| Submit | `POST {BASE_URL}/videos` with `{model, prompt, duration, seed?, first_frame?, last_frame?}` → `{"task_info": {"id": "...", "status": "pending"}}` |
| Poll | `GET {BASE_URL}/videos/{id}` → `{"task_info": {"status": "pending\|processing\|completed\|failed"}, "videos": ["https://.../result_00.mp4"]}` |
| Download | `GET videos[0]` |

The request body must contain at least one of `prompt`, a keyframe field (`first_frame`,
`last_frame`) or a reference field (`reference_images`, `reference_videos`, `reference_audios`,
`file`, `link`). Additional fields are passed through with `extra_json`.

Two implementation details worth knowing if you fork this:

- Polling requests must **not** carry `Content-Type: application/json`. The gateway tries to parse
  the empty body as JSON and answers `400 common.UnmarshalBodyReusable failed`.
- Transient gateway errors during polling are retried (five consecutive failures are tolerated)
  so a hiccup does not throw away an already running job.

## Cost

Video is billed by duration. When this node was written (2026-10-14) the Vergora catalog listed
`w3.0-video` at **0.08 USD per second** (0.10 USD official price), so a 5 second clip costs about
0.40 USD and a 30 second clip about 2.40 USD. Check your platform's current pricing before running
long clips — the node itself does not meter anything.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `没有配置 API key` | `KEY` is empty in `config.ini` and `VERGORA_API_KEY` is not set |
| `503 No available channel for model xxx` | The model name is not available on the platform |
| `401` | Wrong or expired key |
| `400 prompt is required` | Neither a prompt nor a frame was supplied |
| `524` | The platform edge timed out. Video generation is asynchronous, so this normally means a platform-side hiccup; retry |
| Stuck on `pending` / `processing` | Upstream queue. The node waits up to `TIMEOUT` seconds (default 1200) and then tells you how to query the task by hand |

## Notes and limitations

- The returned `video_url` points at a temporary CDN object. The node downloads it immediately;
  do not store the URL as a permanent link.
- `seed` is forwarded, but whether the upstream model honours it is not guaranteed. Use ComfyUI's
  `control_after_generate = fixed` with a saved seed if you want to try to reproduce a clip.
- Frame images supplied as `IMAGE` tensors are sent as base64 `data:` URIs. If an upstream rejects
  data URIs, use `first_frame_url` with a hosted image instead.
- Pricing, availability and the exact set of accepted parameters are controlled by the platform,
  not by this node.

## Development

`test_offline.py` exercises the whole flow (submit → poll → download → preview result) against a
stub HTTP layer. It needs no network, no API key and spends nothing:

```bash
python test_offline.py
```

## License

MIT. See `LICENSE`.

---

# 中文说明

**ComfyUI Vergora Wan 3.0 视频节点**：通过 Vergora API 调用 **Wan 3.0**（模型名 `w3.0-video`）
在 ComfyUI 里生成视频。一个节点覆盖三种用法——文生视频、图生视频（首帧）、首尾帧视频；提交、
轮询、下载全自动，出片后直接显示在结果面板里。同一个节点也能选 `minimax-h3`。

**安装**：把整个文件夹放进 ComfyUI 的 `custom_nodes` 目录 → 把 `config.ini.example` 复制一份
改名为 `config.ini`（Windows：`copy config.ini.example config.ini`），在 `[API]` 的 `KEY` 填上你的
密钥（`BASE_URL` 默认已经指向平台）→ 重启 ComfyUI → 在 `vergora` 分类下添加节点。
`config.ini.example` 里每一项都有注释说明；也可以用环境变量 `VERGORA_BASE_URL` / `VERGORA_API_KEY`，
优先级高于配置文件。`config.ini` 已被 `.gitignore` 忽略，密钥不会被提交。

**三种接法**

- 文生视频：只填 `prompt`，`duration` 是时长（秒，1 到 30），点运行
- 图生视频：把图片接到 `first_frame`（或者用 `first_frame_url` 直接给图片链接）
- 首尾帧：`first_frame` 和 `last_frame` 都接上

**参数要点**

- 片子长短只由 `duration` 决定，提示词里写"30 秒"不会改变时长
- `seed` 填 `-1` 表示不传种子、让上游随机；填具体数字会原样传给上游
- 其它参数（例如分辨率）可以用 `extra_json` 透传，格式是 JSON 对象
- 输出：`video_path`（本地 mp4 路径）、`task_id`、`video_url`；文件存到 ComfyUI 的输出目录

**费用**：视频按时长计费。撰写本节点时（2026-10-14）平台价目表里 `w3.0-video` 是
**0.08 美元/秒**（官方价 0.10），5 秒约 0.40 美元，30 秒约 2.40 美元。跑长片段前请自行确认当日价格。

**已知行为**：轮询请求不能带 `Content-Type`（否则网关会解析空 body 并报 400）；轮询期间的偶发
网关错误会自动重试，不会把已经在跑的任务丢掉。返回的视频链接是临时 CDN 地址，节点会立刻下载，
不要把它当长期链接存起来。

**自测**：`python test_offline.py` 用假的 HTTP 层跑通全流程，不联网、不花钱、不需要密钥。
