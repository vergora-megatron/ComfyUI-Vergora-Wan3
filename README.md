# ComfyUI Wan 3.0 Video

A ComfyUI custom node for **Wan 3.0** video generation through the official
Alibaba Cloud Model Studio API.

The repository and package keep the historical `Vergora` name for registry and workflow
compatibility, but the runtime no longer calls a Vergora gateway. Requests go directly to
Model Studio.

- Default model: `wan3.0-video`
- Faster model: `wan3.0-video-prime`
- Modes: text to video, first/last-frame image to video, and reference to video
- Includes asynchronous task submission, polling, download and ComfyUI preview

Official API reference:
<https://docs.modelstudio.console.alibabacloud.com/zh/model-studio/wan3-video-generation-api-reference>

Model market:
<https://modelstudio.console.alibabacloud.com/ap-southeast-1/model/market>

## Requirements

- ComfyUI
- An Alibaba Cloud Model Studio workspace
- A Model Studio / DashScope API key from the same region
- No extra `pip` packages are required. `requests` is used when available, with
  `urllib` as a fallback.

## Installation

1. Copy this folder into `<ComfyUI>/custom_nodes/ComfyUI-Vergora-Wan3`.
2. Copy `config.ini.example` to `config.ini`.
3. Fill in `WORKSPACE_ID`, `REGION`, and `KEY`.
4. Restart ComfyUI and add the node from the `alibaba` category.

Example:

```ini
[API]
WORKSPACE_ID = your-workspace-id
REGION = ap-southeast-1
KEY = sk-your-dashscope-key

[RUNTIME]
POLL_INTERVAL = 15
TIMEOUT = 1200
OUTPUT_DIR =
```

The endpoint is constructed as:

```text
https://{WORKSPACE_ID}.{REGION}.maas.aliyuncs.com
```

`BASE_URL` may be set instead when a full origin override is needed.

Environment variables take precedence over `config.ini`:

- `DASHSCOPE_WORKSPACE_ID`
- `DASHSCOPE_REGION`
- `DASHSCOPE_API_KEY`
- `DASHSCOPE_BASE_URL`

`config.ini` is ignored by Git. Keep API keys out of workflows and source control.

## Node Reference

- Node id: `VergoraVideoGenerator` (historical id, kept for workflow compatibility)
- Display name: `Wan 3.0 视频生成（阿里云百炼）`
- Category: `alibaba`

### Required inputs

| Input | Default | Notes |
| --- | --- | --- |
| `prompt` | empty | Up to 20,000 characters. In reference mode, use `图1`, `视频1`, `音频1`, etc. |
| `model` | `wan3.0-video` | `wan3.0-video` or `wan3.0-video-prime` |
| `resolution` | `1080p` | `1080p`, `720p`, or `480p`; sent to the API as uppercase |
| `ratio` | `adaptive` | `adaptive`, `21:9`, `16:9`, `4:3`, `1:1`, `3:4`, `9:16` |
| `duration` | `5` | `-1` for smart duration, otherwise 2-30 seconds |
| `audio` | `true` | Include an audio track in the output |
| `prompt_extend` | `false` | Send the prompt as written by default; enable to let upstream rewrite it |
| `seed` | `-1` | `-1` omits the field; otherwise 0-2147483647 |

### Optional inputs

| Input | Notes |
| --- | --- |
| `first_frame` | `IMAGE` tensor for first-frame video |
| `last_frame` | `IMAGE` tensor for first/last-frame video |
| `first_frame_url`, `last_frame_url` | URL or data URI used when the tensor input is absent |
| `reference_images` | `IMAGE` batch, up to 10 reference images |
| `reference_image_urls` | One image URL or data URI per line |
| `reference_video_urls` | One mp4/mov URL per line, up to 5 |
| `reference_audio_urls` | One wav/mp3 URL per line, up to 5 |
| `file_url` | Document URL: pdf, docx, pptx, xlsx, txt, md, key, pages, numbers |
| `link_url` | Public web page URL |
| `extra_json` | Additional request fields; plain keys merge into `parameters` |
| `watermark` | Add the Model Studio watermark; defaults to false |

`watermark` is appended at the end of the widget list so existing workflow widget positions
remain stable.

The API default for `prompt_extend` is `true`. This node intentionally preserves its
version 0.3.x default of `false`; enable it when you want the upstream rewrite behavior.

### Outputs

| Output | Meaning |
| --- | --- |
| `video_path` | Absolute path of the downloaded MP4 |
| `task_id` | Model Studio task id |
| `video_url` | Temporary result URL returned by Model Studio |

## Modes and Validation

The media types decide which capability runs:

| Media supplied | Capability |
| --- | --- |
| prompt only | text to video |
| `first_frame` and/or `last_frame` | image to video |
| `reference_image`, `reference_video`, `reference_audio`, `file`, or `link` | reference to video |

The node validates these rules before submitting a paid task:

- At least one of `prompt` or media is required.
- `first_frame` / `last_frame` cannot be mixed with reference media, `file`, or `link`.
- `file` and `link` are mutually exclusive.
- `file` or `link` requires `prompt_extend=true`.
- At most 10 reference images, 5 reference videos, and 5 reference audios.
- `duration` must be `-1` or an integer from 2 to 30.
- Inline image tensors are compressed or resized when necessary.

When a reference video is supplied, Model Studio also requires:

```text
input video duration + output video duration <= 30 seconds
```

The node cannot inspect a remote video's duration, so this final check is performed upstream.

## Official Request Shape

The node sends requests to:

```text
POST https://{WORKSPACE_ID}.{REGION}.maas.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis
GET  https://{WORKSPACE_ID}.{REGION}.maas.aliyuncs.com/api/v1/tasks/{task_id}
```

Submit headers:

```http
Authorization: Bearer DASHSCOPE_API_KEY
Content-Type: application/json
X-DashScope-Async: enable
X-DashScope-DataInspection: {"input":"disable","output":"disable"}
```

The node explicitly disables data inspection for Wan 3.0 input and output, as requested by
the Model Studio request contract.

Request body:

```json
{
  "model": "wan3.0-video",
  "input": {
    "prompt": "A paper boat drifting down a rainy street",
    "media": [
      {
        "type": "reference_image",
        "url": "https://example.com/reference.png"
      }
    ]
  },
  "parameters": {
    "resolution": "720P",
    "ratio": "16:9",
    "duration": 5,
    "audio": true,
    "prompt_extend": false,
    "watermark": false
  }
}
```

Submission response:

```json
{
  "output": {
    "task_id": "0385dc79-5ff8-4d82-bcb6-xxxxxx",
    "task_status": "PENDING"
  },
  "request_id": "4909100c-7b5a-9f92-bfe5-xxxxxx"
}
```

Completed task response:

```json
{
  "output": {
    "task_id": "17ed7e50-00cf-4509-aea1-xxxxxx",
    "task_status": "SUCCEEDED",
    "video_url": "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/xxx/video.mp4"
  }
}
```

The node accepts the official statuses `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`,
`CANCELED`, and `UNKNOWN`.

## Media Limits

Official Model Studio limits:

- Image: JPEG, JPG, PNG without alpha, BMP, WEBP; 240-8000 px per side; ratio no more than
  8:1; at most 20 MB.
- Reference video: mp4 or mov; each 1-15 seconds; 15 seconds total; at least 16 fps;
  240-4096 px per side; ratio no more than 8:1; at most 100 MB.
- Reference audio: wav or mp3; each and total 1-15 seconds; at most 15 MB.
- File: up to 100 MB and 50 pages, depending on type.
- Link: public page that does not require authentication.

Images may be sent as public URLs or Base64 data URIs. Reference videos and audio must use
public URLs.

## Task Lifetime and Cost

- `task_id` can be queried for 24 hours.
- The generated video URL is valid for 24 hours, so the node downloads it immediately.
- Output video is MP4/H.264 at 30 fps.
- `wan3.0-video` and `wan3.0-video-prime` share the same parameter contract.
- `wan3.0-video-prime` is the high-speed variant, with capabilities aligned to the standard model.
- Availability and price are controlled by Model Studio; check the model market before long runs.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `没有配置 Workspace ID` | Fill `WORKSPACE_ID` or set `DASHSCOPE_WORKSPACE_ID` |
| `没有配置 API key` | Fill `KEY` or set `DASHSCOPE_API_KEY` |
| `检测到旧版 Vergora 网关配置` | Delete the old `BASE_URL`, add `WORKSPACE_ID`, and replace the old gateway key with a Model Studio key from the same region |
| `404` | Check `WORKSPACE_ID`, `REGION`, and endpoint; all must belong to the same region |
| `401` / `403` | Check the key, model access, quota, and region |
| `429` | Reduce request frequency or concurrency |
| `file 或 link 必须开启 prompt_extend` | Set `prompt_extend=true` |
| Failed download of reference media | Use a stable public URL or connect an image tensor instead |
| Timed out while waiting | Query `GET /api/v1/tasks/{task_id}` manually; the task may still be running |

## Migration from 0.3.x

Version 0.4.0 changes the runtime from the Vergora gateway to Model Studio:

- Model ids are now `wan3.0-video` and `wan3.0-video-prime`.
- Configuration now requires `WORKSPACE_ID` and `REGION`.
- An old `BASE_URL = https://api.vergora.ai/v1` is rejected instead of being reused.
- The old gateway API key must be replaced with a Model Studio / DashScope key.
- The request and response envelopes use the official `input`, `parameters`, and `output` fields.
- `ratio` now includes `21:9`.
- `watermark` is available.
- `file` and `link` require `prompt_extend=true`.

Old workflows that store `w3.0-video` need the model widget changed to `wan3.0-video`.

## Release Tags

Release tags use the form `vMAJOR.MINOR.PATCH` and point to the exact source commit that
was published to the Comfy Registry.

| Tag | Meaning |
| --- | --- |
| `v0.3.2` | Last Vergora gateway release. Uses the legacy `w3.0-video` model id and `/v1/videos` route |
| `v0.4.0` | First official Alibaba Cloud Model Studio release. Defaults to `wan3.0-video` |
| `v0.4.1` | Adds the disabled data-inspection request header required by Wan 3.0 |

### Parameter differences

| Contract item | `v0.3.2` | `v0.4.0` |
| --- | --- | --- |
| Runtime target | Vergora gateway | Alibaba Cloud Model Studio directly |
| Model widget | `w3.0-video` | `wan3.0-video` (default), `wan3.0-video-prime` |
| Endpoint | `POST /v1/videos`, `GET /v1/videos/{id}` | Official `/api/v1/services/aigc/video-generation/video-synthesis` and `/api/v1/tasks/{task_id}` |
| Async header | Not required by the node | `X-DashScope-Async: enable` |
| Request shape | Flat top-level fields | `model`, `input`, and `parameters` sections |
| Media shape | `first_frame`, `reference_images`, `reference_videos`, etc. | `input.media[{type, url}]` |
| Aspect ratios | `adaptive`, `16:9`, `4:3`, `1:1`, `3:4`, `9:16` | Adds `21:9` |
| Watermark | Not exposed | `watermark` widget, default `false` |
| File/web input | No `prompt_extend` requirement | `file` or `link` requires `prompt_extend=true` |
| Result envelope | `task_info.status` and `videos[]` | `output.task_status` and `output.video_url` |
| Configuration | `BASE_URL` and a Vergora key | `WORKSPACE_ID`, `REGION`, and a Model Studio key |
| Resolution, duration, audio, seed | `1080p`/`720p`/`480p`, `-1` or 2-30 s, audio toggle, seed | Same meanings and defaults |

`v0.4.1` additionally sends
`X-DashScope-DataInspection: {"input":"disable","output":"disable"}` on Model Studio requests.
The `v0.4.0` tag predates this header.

Tags are annotated release markers. Before a tag is pushed, it can be moved or deleted
locally. After it has been pushed or used for a Registry release, do not move it; publish a
new patch version instead.

## Comfy Registry

| Item | Value |
| --- | --- |
| Registry page | <https://registry.comfy.org/nodes/comfyui-vergora-wan3> |
| Registry node id | `comfyui-vergora-wan3` (permanent identity) |
| Display name | `ComfyUI-Vergora` |
| Publisher id | `levi-li` |
| Current package version | `0.4.1` |

The node id remains `comfyui-vergora-wan3` because it is the existing Registry identity and
preserves install history. The display name identifies the package to users; it does not
change the install id.

After publication, search for `ComfyUI-Vergora` in ComfyUI-Manager, or install by node id:

```bash
comfy node install comfyui-vergora-wan3
```

## Development

Run the offline test with any Python that has `numpy`:

```bash
python test_offline.py
```

The test covers submission, official request shape, polling, download retries, validation,
failure parsing and preview output without network access or cost.

## License

MIT. See `LICENSE`.

---

## 中文摘要

这个节点现在直接调用**阿里云百炼 Wan 3.0 原厂接口**，不再经过 Vergora 网关。
仓库和节点 id 中的 `Vergora` 是为了兼容已有工作流和注册表而保留的历史名称。

- 默认模型：`wan3.0-video`
- 高速模型：`wan3.0-video-prime`
- 请求体：`model + input + parameters`
- 媒体：`input.media[{type, url}]`
- 提交：`POST /api/v1/services/aigc/video-generation/video-synthesis`
- 查询：`GET /api/v1/tasks/{task_id}`
- 必须配置：`WORKSPACE_ID`、`REGION`、`KEY`
- `ratio` 新增 `21:9`
- `watermark` 默认关闭
- `prompt_extend` 保留节点原有的默认值 `false`；原厂接口默认值为 `true`
- 使用 `file` 或 `link` 时必须开启 `prompt_extend`
- 旧版 `BASE_URL = https://api.vergora.ai/v1` 会被拦截，需要删除后改填 `WORKSPACE_ID` 和 `REGION`
- 旧网关 API Key 不能继续使用，需要换成同一地域的百炼 API Key

旧工作流里的 `w3.0-video` 需要改成 `wan3.0-video`。两个新模型参数一致，只有标准版和
高速版档位区别；可用地域和计费以阿里云百炼模型广场为准，不需要拆成两个项目。

发布 tag 使用 `v主版本.次版本.修订版本`：

- `v0.3.2`：旧 Vergora 网关版，模型为 `w3.0-video`，请求是扁平结构
- `v0.4.0`：阿里云百炼原厂版，默认模型为 `wan3.0-video`，请求改为
  `input + parameters + media`
- `v0.4.1`：为 Wan 3.0 请求增加关闭输入、输出数据检查的请求头

完整的字段差异见上方的 “Release Tags / Parameter differences”。Comfy Registry 的节点
ID 保持 `comfyui-vergora-wan3`，市场显示名为 `ComfyUI-Vergora`。Registry 页面：
<https://registry.comfy.org/nodes/comfyui-vergora-wan3>。
