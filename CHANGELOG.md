# Changelog

## 0.2.1 - 2026-10-15

Robustness work driven by testing against a live platform: channel quirks, large inline images,
flaky networks and a few error messages that used to be unreadable.

Added

- The node fits the request to the channel that actually serves the model. `minimax-h3` accepts only
  `16:9`, `9:16` and `1:1`, so `adaptive` is replaced by `16:9` (the swap is logged) and any other
  unsupported ratio is refused before the request leaves the machine.
- `minimax-h3` is image-driven on that channel, so a text-only request is refused locally with an
  explanation instead of spending a queue slot on a job the upstream will reject
  (`缺少必填参数：ref_image_0`).
- The canvas progress bar follows the upstream's own percentage when the channel reports one
  (`progress` / `progress_text` in the task payload), falling back to a time-based estimate.
- Example workflows: `examples/text-to-video.json`, `examples/image-to-video.json`,
  `examples/reference-to-video.json` and `examples/minimax-h3-reference.json`.
- Downloads are verified. A stalled transfer is retried up to three times, partial data is written
  to a `.part` file and never reported as success, and a final failure carries the video URL so the
  clip can be fetched by hand.

Fixed

- Inline images are budgeted and compressed (PNG when it fits, JPEG or a smaller resolution when it
  does not) with the budget split across every frame being sent, keeping the request body inside the
  gateway's ~1 MB limit. This was the cause of `413 Request Entity Too Large`.
- Dropped TLS connections (`SSLError`, `UNEXPECTED_EOF_WHILE_READING`) are explained instead of
  surfacing a raw requests traceback. Submits are deliberately **not** retried automatically, since a
  duplicate job could be billed twice; polling is still retried five times.
- `402 Insufficient balance` reports the required and available credits.
- `400` parameter rejections and `503 No available channel` errors now state what to change.

## 0.2.0 - 2026-10-14

Breaking change: the node now exposes the full W3.0 parameter set, and the widget list grew in the
middle of the node. A workflow saved with 0.1.0 must be repaired by deleting the node and adding it
again (ComfyUI stores widget values by position).

Added

- `resolution` (1080p / 720p / 480p), `ratio` (adaptive plus five fixed ratios), `audio` and
  `prompt_extend` as first-class widgets.
- Reference mode: `reference_images` (IMAGE batch, up to 10 images), `reference_image_urls`,
  `reference_video_urls`, `reference_audio_urls`, `file_url` and `link_url`.
- Local contract validation before anything is submitted: keyframe mode and reference mode are
  mutually exclusive, `file` and `link` are mutually exclusive, array limits and the duration range
  are enforced with clear messages.
- The clip is returned as a preview result and the node registers as an output node, so ComfyUI no
  longer complains that the workflow has no output node.

Changed

- `duration` now also accepts `-1` for smart duration; the previous 1 to 30 range was wrong.
- Node display name is now `vergora Wan 3.0 视频生成（文生/图生/参考）`.
- Configuration template `config.ini.example` is fully commented.

Fixed

- Polling requests no longer send `Content-Type`, which made the gateway answer
  `400 common.UnmarshalBodyReusable failed`.
- Transient gateway errors during polling are retried up to five times instead of discarding a job
  that is already running.
- The HTTP layer falls back to the Python standard library when `requests` is not installed.

## 0.1.0 - 2026-10-14

Initial release: one node for Wan 3.0 text-to-video and first/last-frame image-to-video, with
asynchronous task polling, automatic download into the ComfyUI output directory and the API key read
from `config.ini` or the environment.
