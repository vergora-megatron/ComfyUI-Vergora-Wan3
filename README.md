# ComfyUI Vergora Wan 3.0 Video

A ComfyUI custom node that generates video with **Wan 3.0** (`w3.0-video`) through the Vergora API.
Text-to-video, image-to-video and first/last-frame video all live in one node: the request is
submitted, polled and downloaded for you, and the finished clip shows up in the ComfyUI result panel.

- **Model:** `w3.0-video` (Wan 3.0, provider listed as `alibaba` in the Vergora model catalog).
  A second model, `minimax-h3`, is selectable from the same node, but it is **not enabled for every
  API key** — if yours lacks a channel for it the gateway answers `503 No available channel`. Keep
  `w3.0-video` unless you know your key can reach the other one.
  When `minimax-h3` is available it is served by a Seedance 2.0 channel that only accepts the
  `16:9`, `9:16` and `1:1` ratios, so `adaptive` is rejected there; `w3.0-video` accepts all six
  ratios plus `adaptive`.
  With a model whose channel has a narrowed ratio set, the node replaces `adaptive` with `16:9` and
  logs it, and refuses any other unsupported ratio locally before the request is sent.
  That channel is also image-driven: it needs a reference image, so a text-only `minimax-h3` job is
  refused locally (the upstream would answer `缺少必填参数：ref_image_0`). Use `w3.0-video` when you
  want to generate purely from text.
- **Modes:** text to video, image to video (first and/or last frame), and reference to video
  (reference images, videos, audio, documents, or a web link).
- **Full parameter set:** resolution, aspect ratio, duration, audio on/off, prompt rewriting and
  seed are all node widgets; the request contract is validated locally before anything is billed.
- **No code required:** prompts and media are widgets; wiring is optional.
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
| `README.md`, `CHANGELOG.md`, `LICENSE` | Documentation, release history and license |

## Node reference

Node id: `VergoraVideoGenerator`
Display name: `vergora Wan 3.0 视频生成（文生/图生/参考）`
Category: `vergora`

| Input | Type | Default | Mode | Notes |
| --- | --- | --- | --- | --- |
| `prompt` | STRING (multiline) | empty | all | What the clip should show, up to 20000 characters. In reference mode you can address inputs as `Image 1`, `Video 1` |
| `model` | COMBO | `w3.0-video` | all | `w3.0-video` (Wan 3.0) or `minimax-h3` |
| `resolution` | COMBO | `1080p` | all | `1080p` / `720p` / `480p`. This sets the price band |
| `ratio` | COMBO | `adaptive` | all | `adaptive`, `16:9`, `4:3`, `1:1`, `3:4`, `9:16` |
| `duration` | INT | 5 | all | `-1` for smart duration, otherwise 2 to 30 seconds. This sets the length, not the prompt |
| `audio` | BOOLEAN | true | all | Whether the clip carries an audio track. Pricing is identical either way |
| `prompt_extend` | BOOLEAN | true | all | Let upstream rewrite the prompt, which usually improves motion. Turn off to stay literal |
| `seed` | INT | -1 | all | `-1` means "do not send a seed". Any value `>= 0` is forwarded |
| `first_frame` | IMAGE (optional) | - | keyframe | Image to video, or the first frame of a pair |
| `last_frame` | IMAGE (optional) | - | keyframe | Pair it with `first_frame` for first/last-frame video |
| `first_frame_url`, `last_frame_url` | STRING (optional) | empty | keyframe | Hosted image URL, used when the IMAGE input is not connected |
| `reference_images` | IMAGE (optional) | - | reference | A batch of up to 10 reference images |
| `reference_image_urls` | STRING (multiline, optional) | empty | reference | Extra reference images, one URL or data URI per line |
| `reference_video_urls` | STRING (multiline, optional) | empty | reference | Reference videos (mp4/mov), one URL per line, up to 5, 15s in total |
| `reference_audio_urls` | STRING (multiline, optional) | empty | reference | Reference audio (wav/mp3), one URL per line, up to 5, 15s in total |
| `file_url` | STRING (optional) | empty | reference | Document URL: pdf, docx, doc, pptx, ppt, xlsx, xls, txt, md, key, pages, numbers |
| `link_url` | STRING (optional) | empty | reference | A public web page URL |
| `extra_json` | STRING (multiline, optional) | empty | all | Extra request fields, merged into the body last |

| Output | Type | Notes |
| --- | --- | --- |
| `video_path` | STRING | Absolute path of the downloaded `.mp4` |
| `task_id` | STRING | The upstream task id, useful for support or for polling by hand |
| `video_url` | STRING | The temporary upstream URL the file was fetched from |

The node is registered as an output node and returns the clip as a preview result, so ComfyUI will
not complain that the workflow has no output node.

## Modes and mutual exclusivity

The media fields you send decide which capability runs:

| Media fields sent | Capability |
| --- | --- |
| none (prompt only) | text to video |
| `first_frame` and/or `last_frame` | image to video (keyframe mode) |
| `reference_images` / `reference_videos` / `reference_audios` / `file` / `link` | reference to video (reference mode) |

- Keyframe mode and reference mode **cannot be mixed** in one request.
- `file` and `link` **cannot be sent together**.
- At least one of `prompt`, a keyframe field or a reference field must be present.
- Images (`first_frame`, `last_frame`, `reference_images`) accept a public URL, a `data:` URI or bare Base64; `reference_videos` and `reference_audios` are URL only.
- Limits: images up to 20 MB each with side 240–8000 px and ratio ≤ 8:1; videos mp4/mov up to 100 MB, 1–15 s each and 15 s in total; audio wav/mp3 up to 15 MB, 1–15 s each and 15 s in total. With a video input, input plus output duration must stay within 30 s.

The node enforces all of this locally, so a malformed request fails immediately instead of failing
upstream after you have waited for a queue.

## Usage

> **Upgrading from 0.1.x?** Version 0.2.0 added widgets in the middle of the node
> (`resolution`, `ratio`, `audio`, `prompt_extend`, and the reference-mode fields). ComfyUI stores
> a workflow's widget values **by position**, so a workflow saved with 0.1.x will have its values
> shifted onto the wrong widgets — for example a seed number ends up on `ratio` and an empty string
> on `seed`, which shows up as "invalid input" before the run starts. The fix is to **delete the
> node and add it again**; do not try to repair the widgets one by one, because other values may be
> silently shifted too. Widgets are only ever appended from now on, and any future breaking change
> will ship under a new node id.

**Text to video.** Fill in `prompt`, keep `duration` at 5 and press Run. The console prints the
submit call and then one status line per poll; the clip usually lands in two to four minutes.
While it waits, the node drives ComfyUI's progress bar: it uses the upstream's own percentage when
the channel reports one (`progress` in the task payload, which the newer channels do), and a
time-based estimate otherwise. That way the node never looks frozen during a long render.

**Image to video.** Feed any `IMAGE` into `first_frame` (a `LoadImage` node, or the output of an
image generation node). If your image already lives at a public URL, put the URL in
`first_frame_url` instead — that keeps the request body small.

**First and last frame.** Connect both `first_frame` and `last_frame`; the model interpolates
between them.

The downloaded file is saved into the ComfyUI output directory (`folder_paths.get_output_directory()`
unless `OUTPUT_DIR` is set) and named `vergora_<taskid>_<timestamp>.mp4`.

### Ready-made workflows

The `examples/` folder ships three workflows you can load as-is:

| File | What it wires up |
| --- | --- |
| `examples/text-to-video.json` | The node on its own, with a sample prompt |
| `examples/image-to-video.json` | A `LoadImage` node wired into `first_frame` |
| `examples/reference-to-video.json` | A `LoadImage` node wired into `reference_images` |
| `examples/minimax-h3-reference.json` | `minimax-h3` with a `LoadImage` wired into `reference_images`; that channel needs an image, and this file already sets `ratio` to `16:9` so it runs as-is |

Load one through **Workflow → Open**, or drag the `.json` straight onto the canvas. The two image
examples point at `example.png`, which does not exist on your machine — click the loader and pick any
image of your own. Keeping the image inside ComfyUI is also the most robust route: the node sends it
as Base64, so nothing has to be reachable from the internet.

## What the node does under the hood

The Vergora video API is an asynchronous task API with an OpenAI-Sora-style shape:

| Step | Call |
| --- | --- |
| Submit | `POST {BASE_URL}/videos` with `{model, prompt?, resolution, ratio, duration, audio, prompt_extend, seed?, first_frame?, last_frame?, reference_images?, reference_videos?, reference_audios?, file?, link?}` → `{"task_info": {"id": "...", "status": "pending"}}` |
| Poll | `GET {BASE_URL}/videos/{id}` → `{"task_info": {"status": "pending\|processing\|completed\|failed"}, "videos": ["https://.../result_00.mp4"]}` |
| Download | `GET videos[0]` |

The request body must contain at least one of `prompt`, a keyframe field (`first_frame`,
`last_frame`) or a reference field (`reference_images`, `reference_videos`, `reference_audios`,
`file`, `link`). Additional fields are passed through with `extra_json`.

Field names, enums and limits match the upstream Alibaba W3.0 contract documented at
<https://www.mulerouter.ai/docs/api-reference/endpoint/alibaba/w3.0-video/generation>; the node adds
the local validation described above on top of it.

Two implementation details worth knowing if you fork this:

- Polling requests must **not** carry `Content-Type: application/json`. The gateway tries to parse
  the empty body as JSON and answers `400 common.UnmarshalBodyReusable failed`.
- Transient gateway errors during polling are retried (five consecutive failures are tolerated)
  so a hiccup does not throw away an already running job.

## Cost

Billing is **resolution unit price × billed seconds**, where the billed duration is the length of
any reference video input plus the length of the generated clip:

| Resolution | Unit price |
| --- | --- |
| 480p | 0.050 USD / second |
| 720p | 0.100 USD / second |
| 1080p (default) | 0.200 USD / second |

So a 5 second 1080p clip is roughly 1.00 USD, and the same clip at 720p roughly 0.50 USD. Failed
tasks are not billed, and `audio: false` does not lower the price. These are the upstream
Alibaba W3.0 rates (checked 2026-10-14); a reseller such as Vergora may quote a different rate in
your console — including a flat per-second price — so confirm what you are actually charged before
running long clips. The node itself does not meter anything.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `没有配置 API key` | `KEY` is empty in `config.ini` and `VERGORA_API_KEY` is not set |
| `503 No available channel for model xxx` | This key has no channel for that model. Switch the `model` widget back to `w3.0-video`, or ask the platform to enable the model for your key. `minimax-h3` in particular is listed by the platform but is not enabled for every key |
| `400 invalid_request` mentioning a ratio (for example `ratio 只支持 16:9、9:16、1:1`) | The backend serving your selected model accepts fewer ratios than `w3.0-video` does. `minimax-h3` is currently served by a Seedance 2.0 channel that only takes `16:9`, `9:16` and `1:1`. The node now covers this: `adaptive` is switched to `16:9` automatically (it logs the change), and an explicitly unsupported ratio such as `4:3` is refused locally instead of being sent |
| `402 Insufficient balance` | The request costs more credits than the account holds. The message states the required and available amounts; top up, or lower `resolution` / `duration` |
| Task fails with `缺少必填参数：ref_image_0` (any "ref_image" wording) | That model's channel is image-driven. `minimax-h3` needs a reference image, so a text-only job cannot work there: connect an `IMAGE` to `reference_images` (or fill `reference_image_urls`), or use `w3.0-video` for text-to-video. The node now refuses a text-only request for such a model locally instead of burning a queue slot |
| The job completes but the saved file is 0 bytes | The CDN dropped the transfer. The node verifies the download, retries three times, and only then raises an error that contains the video URL so you can fetch it by hand |
| `413 Request Entity Too Large` | The inline image pushed the request body past the gateway's limit (nginx defaults to 1 MB). The node now budgets and compresses local images automatically; if it still happens, use `reference_image_urls` with a hosted image, or ask the platform to raise `client_max_body_size` |
| `401` | Wrong or expired key |
| `400 prompt is required` | Neither a prompt nor a frame was supplied |
| `524` | The platform edge timed out. Video generation is asynchronous, so this normally means a platform-side hiccup; retry |
| Stuck on `pending` / `processing` | Upstream queue. The node waits up to `TIMEOUT` seconds (default 1200) and then tells you how to query the task by hand |
| `关键帧模式与参考模式互斥` / "mutually exclusive" | `first_frame` / `last_frame` were combined with a reference field |
| `file 与 link 不能同时使用` | Send only one of `file` and `link` |
| `reference_images 最多 10 个` | Array limits are 10 images, 5 videos, 5 audios |
| `duration 只能是 -1 ...` | `duration` must be `-1` (smart duration) or between 2 and 30 |
| `4002 Infrastructure operation failed` / `Failed to download file: HTTP 502` | The upstream could not fetch the image or video URL you supplied, usually because the link expired or is blocked for its region. Connect an `IMAGE` tensor instead (the node sends Base64) or host the file at a stable public URL |
| `403 insufficient_quota` | The account behind the API key is out of quota. Top it up in the platform console |

For the last four cases the node raises its own message instead of dumping the raw gateway body, so
the error you see in ComfyUI already tells you what to change.

## Notes and limitations

- The returned `video_url` points at a temporary CDN object. The node downloads it immediately;
  do not store the URL as a permanent link.
- `seed` is forwarded, but whether the upstream model honours it is not guaranteed. Use ComfyUI's
  `control_after_generate = fixed` with a saved seed if you want to try to reproduce a clip.
- **Host your input media somewhere stable.** Image URLs are fetched by the upstream service, not
  by this node, and temporary CDN links expire quickly — a link that returned 200 when you made it
  can be gone minutes later, and the task then fails with `4002 Failed to download file: HTTP 502`.
  Images supplied as `IMAGE` tensors are sent as Base64 `data:` URIs and never need to be fetched,
  which is the most robust path; use `first_frame_url` / `reference_image_urls` only with links you
  control. `reference_videos` and `reference_audios` are URL-only, so those always need a stable
  public URL.
  Inline images are re-encoded to stay inside the gateway's request body limit (PNG when it fits,
  JPEG or a smaller resolution when it does not), with the budget split across however many frames
  the node is sending.
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
在 ComfyUI 里生成视频。一个节点覆盖三类用法——文生视频、图生视频（关键帧模式）、参考生视频
（参考模式）；提交、轮询、下载全自动，出片后直接显示在结果面板里。参数与官方 W3.0 契约一致，
并且提交前会在本地把不合法的请求拦下来，不会白等一次失败任务。同一个节点也能选 `minimax-h3`。

**安装**：把整个文件夹放进 ComfyUI 的 `custom_nodes` 目录 → 把 `config.ini.example` 复制一份
改名为 `config.ini`（Windows：`copy config.ini.example config.ini`），在 `[API]` 的 `KEY` 填上你的
密钥（`BASE_URL` 默认已经指向平台）→ 重启 ComfyUI → 在 `vergora` 分类下添加节点。
`config.ini.example` 里每一项都有注释说明；也可以用环境变量 `VERGORA_BASE_URL` / `VERGORA_API_KEY`，
优先级高于配置文件。`config.ini` 已被 `.gitignore` 忽略，密钥不会被提交。

> **从 0.1.x 升级的注意**：0.2.0 在节点中间新增了 `resolution`、`ratio`、`audio`、`prompt_extend`
> 以及参考模式那一批控件。ComfyUI 是按**位置**保存工作流控件值的，所以用 0.1.x 存过的工作流会出现
> 参数错位——比如 seed 的数字跑到 `ratio` 上、空字符串跑到 `seed` 上，运行前就会报"无效输入"。
> 处理办法是**把该节点删掉重新添加**，不要逐个手改（可能还有别的值被静默错位）。以后控件只会往末尾
> 追加，不会插在中间；如果将来必须做破坏性改动，会用一个**新的节点 id** 发布，旧版本保持可用。

**三种模式**（发哪些媒体字段，决定用哪种能力）

- 文生视频：只填 `prompt`
- 图生视频（关键帧模式）：图片接到 `first_frame`，只给尾帧也可以；两个都接就是首尾帧
- 参考生视频（参考模式）：`reference_images`（一批图，最多 10 张，可直接接图像节点）或
  `reference_image_urls`（一行一个链接）、`reference_video_urls`（最多 5 个，合计不超过 15 秒）、
  `reference_audio_urls`（最多 5 个，合计不超过 15 秒）、`file_url`（pdf/docx/pptx 等文档）、
  `link_url`（公开网页）。参考模式下提示词里可以用 `Image 1`、`Video 1` 指代输入素材

**关键约束**：关键帧模式和参考模式**不能混用**；`file` 与 `link` 也**不能同时发**。这些节点会在
本地直接报错拦下，不会带着错请求去排队。

**开箱可用的工作流**：`examples/` 目录里放了三份可以直接加载的文件（`text-to-video.json` 单独一个
节点、`image-to-video.json` 用 `LoadImage` 接 `first_frame`、`reference-to-video.json` 接
`reference_images`）。用法是「工作流 → 打开」选中文件，或把 json 直接拖到画布上；两份带图的里面
加载器指向 `example.png`，你机器上没有，点一下换成自己的图即可。图片在 ComfyUI 内部传递时节点会
转成 Base64 发送，不依赖外网可访问性，这是最稳的一条路。

**参数要点**

- `resolution`：`1080p`（默认）/ `720p` / `480p`，这一项直接决定单价
- `ratio`：`adaptive`（默认，由内容和素材推断）或 `16:9`、`4:3`、`1:1`、`3:4`、`9:16`
- `duration`：`-1` 表示智能时长，否则 2 到 30 秒；片子长短只由它决定，提示词里写"30 秒"不会改变时长
- `audio`：是否输出音轨，开或关价格一样
- `prompt_extend`：是否让上游改写（扩写）提示词，默认开启，通常动作和运镜更好；关掉更贴近原话
- `seed`：填 `-1` 表示不传种子、让上游随机；填具体数字（0 到 2147483647）会原样传给上游
- 其它字段可以用 `extra_json` 透传，格式是 JSON 对象
- 输出：`video_path`（本地 mp4 路径）、`task_id`、`video_url`；文件存到 ComfyUI 的输出目录

**费用**：按"分辨率单价 × 计费秒数"计费，计费秒数 = 参考视频输入时长 + 输出视频时长。

| 分辨率 | 单价 |
| --- | --- |
| 480p | 0.050 美元/秒 |
| 720p | 0.100 美元/秒 |
| 1080p（默认） | 0.200 美元/秒 |

5 秒 1080p 约 1.00 美元，同样 5 秒的 720p 约 0.50 美元。失败任务不计费，`audio: false` 也不会降价。
以上是上游阿里 W3.0 的官方价（核对于 2026-10-14）；中转平台（如 Vergora）在你的控制台里可能显示
另一套口径，甚至是一个按时长统一的单价（例如 0.08 美元/秒），**以你实际账单为准**，跑长片前先确认。

**已知行为**：轮询请求不能带 `Content-Type`（否则网关会解析空 body 并报 400）；轮询期间的偶发
网关错误会自动重试，不会把已经在跑的任务丢掉。返回的视频链接是临时 CDN 地址，节点会立刻下载，
不要把它当长期链接存起来。

**素材一定要放在稳定的地方**：图片链接是**由上游去下载**的，不是节点下载的；临时 CDN 链接很快会
失效（我们实测中生成出来几分钟后就变成 404 了），上游下载失败时任务会以
`4002 Infrastructure operation failed / Failed to download file: HTTP 502` 结束（失败任务不计费）。
所以最稳的做法是把图片接到 `IMAGE` 输入上——节点会转成 Base64 内嵌发送，完全不依赖外部可访问性；
只有在你确信链接长期有效时才用 `first_frame_url` / `reference_image_urls`。参考视频和参考音频
官方只接受 URL，必须用稳定可公开访问的地址。

**额度**：额度不足时提交会直接返回 `403 insufficient_quota`（不会产生任务和费用）。API 本身查不到
真实余额，需要到平台控制台确认。

**自测**：`python test_offline.py` 用假的 HTTP 层跑通全流程，不联网、不花钱、不需要密钥。
