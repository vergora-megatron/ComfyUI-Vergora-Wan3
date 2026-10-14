# Changelog

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
