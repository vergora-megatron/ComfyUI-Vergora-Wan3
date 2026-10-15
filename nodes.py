# -*- coding: utf-8 -*-
"""ComfyUI node: Wan 3.0 video generation through the Vergora API.

Field set and constraints follow the official W3.0 contract documented at
https://www.mulerouter.ai/docs/api-reference/endpoint/alibaba/w3.0-video/generation
(Vergora's gateway exposes the same fields through the asynchronous
`/v1/videos` route).

Flow (verified 2026-10-14):
    submit  POST {base}/videos       -> {"task_info": {"id": "...", "status": "pending"}}
    poll    GET  {base}/videos/{id}  -> {"task_info": {"status": "pending|processing|completed|failed"},
                                         "videos": ["https://.../result_00.mp4"]}
    fetch   GET  videos[0]

Three capability sets are selected by the media fields you send:
    prompt only                              -> text to video
    first_frame / last_frame                 -> image to video (keyframe mode)
    reference_images / videos / audios,
    file, link                               -> reference to video (reference mode)

Keyframe mode and reference mode are mutually exclusive, and `file` cannot be
combined with `link`.

The HTTP layer uses `requests` when available and falls back to the standard
library `urllib`, so the node loads in any ComfyUI environment. The API key is
read from config.ini or the environment; it is never a node widget, so workflow
files never carry your key.
"""

import base64
import configparser
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.request
from io import BytesIO

import numpy as np

try:
    import requests
except ImportError:  # ComfyUI's environment does not always ship requests
    requests = None

try:  # present inside ComfyUI; falls back to a local output folder otherwise
    import folder_paths
except Exception:  # pragma: no cover
    folder_paths = None

try:
    from comfy.model_management import throw_exception_if_processing_interrupted
except Exception:  # pragma: no cover
    def throw_exception_if_processing_interrupted():
        return None

try:  # lets the node report progress on the canvas instead of an idle "executing"
    from comfy.utils import ProgressBar
except Exception:  # pragma: no cover
    ProgressBar = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.ini")

DEFAULT_BASE_URL = "https://api.vergora.ai/v1"

# w3.0-video is Alibaba Wan 3.0. w3.0-video-pro is the sibling that adds 2k/4k.
VIDEO_MODELS = ["w3.0-video", "minimax-h3"]
RESOLUTIONS = ["1080p", "720p", "480p"]
RATIOS = ["adaptive", "16:9", "4:3", "1:1", "3:4", "9:16"]

MAX_REFERENCE_IMAGES = 10
MAX_REFERENCE_VIDEOS = 5
MAX_REFERENCE_AUDIOS = 5

# Some models are served by an upstream channel that accepts fewer ratios than the
# W3.0 contract does. Observed 2026-10-15: minimax-h3 is routed through a Seedance
# 2.0 channel that rejects "adaptive" and only takes these three.
MODEL_RATIO_LIMITS = {
    "minimax-h3": ["16:9", "9:16", "1:1"],
}

# Channels that need an image input. Observed 2026-10-15: the minimax-h3 route is a
# reference-to-video channel and answers "缺少必填参数：ref_image_0" for text-only jobs.
MODEL_REQUIRES_IMAGE = {
    "minimax-h3": "reference_images",
}

CATEGORY = "vergora"


def log(message):
    """Print without exploding on consoles that cannot encode CJK."""
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        print(message.encode("ascii", "replace").decode("ascii"), flush=True)


def make_progress(total=100):
    if ProgressBar is None:
        return None
    try:
        return ProgressBar(total)
    except Exception:  # pragma: no cover
        return None


def tick_progress(progress, value, total=100):
    """Advance the canvas progress bar; never let it break a run."""
    if progress is None:
        return
    try:
        progress.update_absolute(int(value), total)
    except Exception:  # pragma: no cover
        pass


# --------------------------------------------------------------------------
# HTTP layer: requests when installed, standard library otherwise
# --------------------------------------------------------------------------

def http_post_json(url, headers, payload, timeout=120):
    """Return (status_code, response_text)."""
    body = json.dumps(payload).encode("utf-8")
    if requests is not None:
        response = requests.post(url, headers=headers, data=body, timeout=timeout)
        return response.status_code, response.text
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.getcode(), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def http_get_json(url, headers, timeout=60):
    """Return (status_code, response_text).

    Never attach Content-Type here: the gateway tries to parse an empty body as
    JSON and answers 400 (common.UnmarshalBodyReusable failed) if you do.
    """
    if requests is not None:
        response = requests.get(url, headers=headers, timeout=timeout)
        return response.status_code, response.text
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.getcode(), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def http_download(url, dest_path, timeout=600):
    if requests is not None:
        with requests.get(url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            with open(dest_path, "wb") as handle:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    if chunk:
                        handle.write(chunk)
        return
    request = urllib.request.Request(url, headers={"User-Agent": "vergora-comfyui"})
    with urllib.request.urlopen(request, timeout=timeout) as response, open(dest_path, "wb") as handle:
        shutil.copyfileobj(response, handle, length=1 << 20)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

def load_config():
    """Read config.ini; environment variables win over the file."""
    cfg = {
        "base_url": DEFAULT_BASE_URL,
        "api_key": "",
        "poll_interval": 10.0,
        "timeout": 1200.0,
        "output_dir": "",
    }
    if os.path.exists(CONFIG_PATH):
        parser = configparser.ConfigParser()
        parser.read(CONFIG_PATH, encoding="utf-8")
        if parser.has_section("API"):
            base = parser.get("API", "BASE_URL", fallback="").strip()
            key = parser.get("API", "KEY", fallback="").strip()
            if base:
                cfg["base_url"] = base
            if key:
                cfg["api_key"] = key
        if parser.has_section("RUNTIME"):
            cfg["poll_interval"] = parser.getfloat("RUNTIME", "POLL_INTERVAL", fallback=cfg["poll_interval"])
            cfg["timeout"] = parser.getfloat("RUNTIME", "TIMEOUT", fallback=cfg["timeout"])
            out_dir = parser.get("RUNTIME", "OUTPUT_DIR", fallback="").strip()
            if out_dir:
                cfg["output_dir"] = out_dir

    env_base = os.environ.get("VERGORA_BASE_URL", "").strip()
    env_key = os.environ.get("VERGORA_API_KEY", "").strip()
    if env_base:
        cfg["base_url"] = env_base
    if env_key:
        cfg["api_key"] = env_key

    cfg["base_url"] = cfg["base_url"].rstrip("/")
    return cfg


def auth_headers(cfg, with_json=True):
    """with_json=False for GET requests (see http_get_json)."""
    if not cfg["api_key"]:
        raise RuntimeError(
            "没有配置 API key / no API key configured: put it in %s under [API] KEY, "
            "or set the environment variable VERGORA_API_KEY" % CONFIG_PATH
        )
    headers = {"Authorization": "Bearer " + cfg["api_key"]}
    if with_json:
        headers["Content-Type"] = "application/json"
    return headers


# --------------------------------------------------------------------------
# Image helpers
# --------------------------------------------------------------------------

def array_to_data_uri(frame):
    if Image is None:
        raise RuntimeError("需要 Pillow 才能把 IMAGE 转成图片 / Pillow is required to encode IMAGE tensors")
    arr = np.asarray(frame)
    if arr.ndim == 4:
        arr = arr[0]
    arr = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
    buffer = BytesIO()
    Image.fromarray(arr).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def tensor_to_data_uri(image):
    """First frame of a single IMAGE, or None."""
    if image is None:
        return None
    arr = image
    if hasattr(arr, "detach"):
        arr = arr.detach().cpu().numpy()
    arr = np.asarray(arr)
    if arr.ndim == 3:
        arr = arr[None, ...]
    return array_to_data_uri(arr[0])


def tensor_batch_to_data_uris(images, limit):
    """Every frame of a batch (or a single image) as data URIs, capped at limit."""
    if images is None:
        return []
    arr = images
    if hasattr(arr, "detach"):
        arr = arr.detach().cpu().numpy()
    arr = np.asarray(arr)
    if arr.ndim == 3:
        arr = arr[None, ...]
    return [array_to_data_uri(frame) for frame in arr[:limit]]


def split_uri_lines(text):
    """One URI per line; blank lines and comments are ignored."""
    if not text:
        return []
    out = []
    for line in str(text).splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


# --------------------------------------------------------------------------
# Request building and validation
# --------------------------------------------------------------------------

def validate_request(prompt, first_frame, last_frame, reference_images,
                     reference_videos, reference_audios, file_url, link_url, duration):
    """Check the contract locally so a bad request is caught before it is billed."""
    has_prompt = bool(prompt and prompt.strip())
    has_keyframe = bool(first_frame or last_frame)
    has_reference = bool(reference_images or reference_videos or reference_audios
                         or file_url or link_url)

    if not (has_prompt or has_keyframe or has_reference):
        raise RuntimeError(
            "至少要给 prompt、首帧/尾帧，或参考素材其中之一 / provide at least one of "
            "prompt, a keyframe (first_frame/last_frame) or a reference input "
            "(reference_images/reference_videos/reference_audios/file/link)"
        )
    if has_keyframe and has_reference:
        raise RuntimeError(
            "关键帧模式与参考模式互斥：首帧/尾帧不能和 reference_*/file/link 同时使用 / "
            "keyframe mode (first_frame, last_frame) and reference mode "
            "(reference_images, reference_videos, reference_audios, file, link) are mutually exclusive"
        )
    if file_url and link_url:
        raise RuntimeError(
            "file 与 link 不能同时使用 / file and link are mutually exclusive"
        )
    if len(reference_images) > MAX_REFERENCE_IMAGES:
        raise RuntimeError("reference_images 最多 %d 个 / at most %d reference images"
                           % (MAX_REFERENCE_IMAGES, MAX_REFERENCE_IMAGES))
    if len(reference_videos) > MAX_REFERENCE_VIDEOS:
        raise RuntimeError("reference_videos 最多 %d 个 / at most %d reference videos"
                           % (MAX_REFERENCE_VIDEOS, MAX_REFERENCE_VIDEOS))
    if len(reference_audios) > MAX_REFERENCE_AUDIOS:
        raise RuntimeError("reference_audios 最多 %d 个 / at most %d reference audios"
                           % (MAX_REFERENCE_AUDIOS, MAX_REFERENCE_AUDIOS))
    if int(duration) != -1 and not 2 <= int(duration) <= 30:
        raise RuntimeError("duration 只能是 -1（智能时长）或 2 到 30 秒 / duration must be -1 "
                           "(smart duration) or 2 to 30 seconds")


def reconcile_ratio(model, ratio):
    """Fit the requested ratio to what the model's channel actually accepts."""
    allowed = MODEL_RATIO_LIMITS.get(str(model))
    if not allowed or ratio in allowed:
        return ratio
    if ratio == "adaptive":
        log("[vergora] %s 不支持 adaptive，已自动改用 %s / %s does not accept 'adaptive', "
            "switched to %s" % (model, allowed[0], model, allowed[0]))
        return allowed[0]
    raise RuntimeError(
        "%s 只支持 %s，不支持 %s / %s only supports %s"
        % (model, " / ".join(allowed), ratio, model, " / ".join(allowed))
    )


def build_payload(prompt, model, duration, seed, resolution, ratio, audio, prompt_extend,
                  first_frame=None, last_frame=None,
                  reference_images=None, reference_videos=None, reference_audios=None,
                  file_url="", link_url="", extra_json=""):
    reference_images = list(reference_images or [])
    reference_videos = list(reference_videos or [])
    reference_audios = list(reference_audios or [])
    file_url = (file_url or "").strip()
    link_url = (link_url or "").strip()

    validate_request(prompt, first_frame, last_frame, reference_images,
                     reference_videos, reference_audios, file_url, link_url, duration)
    ratio = reconcile_ratio(model, ratio)
    # `reference_images` here is already the merged list from the IMAGE batch and the URL lines
    if MODEL_REQUIRES_IMAGE.get(str(model)) and not (reference_images or first_frame or last_frame):
        raise RuntimeError(
            "%s 的通道目前只支持参考图/首帧生视频，必须给一张图 / %s currently needs an image input: "
            "把图片接到 reference_images（或 reference_image_urls、first_frame），"
            "纯文生视频请改用 w3.0-video。" % (model, model)
        )

    payload = {
        "model": model,
        "duration": int(duration),
        "resolution": resolution,
        "ratio": ratio,
        "audio": bool(audio),
        "prompt_extend": bool(prompt_extend),
    }
    if prompt and prompt.strip():
        payload["prompt"] = prompt.strip()
    if seed is not None and int(seed) >= 0:
        payload["seed"] = int(seed)
    if first_frame:
        payload["first_frame"] = first_frame
    if last_frame:
        payload["last_frame"] = last_frame
    if reference_images:
        payload["reference_images"] = reference_images
    if reference_videos:
        payload["reference_videos"] = reference_videos
    if reference_audios:
        payload["reference_audios"] = reference_audios
    if file_url:
        payload["file"] = file_url
    if link_url:
        payload["link"] = link_url

    if extra_json and extra_json.strip():
        try:
            extra = json.loads(extra_json)
        except Exception as exc:
            raise RuntimeError("extra_json 不是合法 JSON / not valid JSON: %s" % exc)
        if not isinstance(extra, dict):
            raise RuntimeError('extra_json 必须是 JSON 对象，例如 {"duration": 10} / '
                               'extra_json must be a JSON object')
        payload.update(extra)
    return payload


def extract_video_url(data):
    """Upstreams differ slightly in shape; try the likely keys in order."""
    videos = data.get("videos")
    if isinstance(videos, list) and videos:
        first = videos[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            for key in ("url", "video_url", "file_url", "content_url"):
                if first.get(key):
                    return first[key]
    for key in ("video_url", "url"):
        if isinstance(data.get(key), str) and data[key]:
            return data[key]
    for holder in ("data", "output", "result"):
        node = data.get(holder)
        if isinstance(node, list) and node and isinstance(node[0], dict):
            for key in ("url", "video_url"):
                if node[0].get(key):
                    return node[0][key]
        if isinstance(node, dict):
            found = extract_video_url(node)
            if found:
                return found
    return None


def resolve_output_dir(cfg):
    if cfg["output_dir"]:
        return cfg["output_dir"]
    if folder_paths is not None:
        try:
            return folder_paths.get_output_directory()
        except Exception:
            pass
    return os.path.join(HERE, "output")


def explain_http_error(status, text, model=None):
    """Turn the gateway's terse errors into something a user can act on."""
    lower = (text or "").lower()
    snippet = (text or "")[:200]
    if "no available channel for model" in lower:
        return (
            "模型 %s 在当前账号的渠道分组里没有可用通道 / no available channel for model %s with this "
            "key. 把 model 换回 w3.0-video，或到平台控制台确认这个模型对你这把 key 是否开放。"
            "Upstream said: %s" % (model or "?", model or "?", snippet)
        )
    if "quota" in lower:
        return ("额度不足 / insufficient quota: 这把 key 背后的账户余额已用尽，去平台控制台充值。"
                "Upstream said: %s" % snippet)
    if status == 402 or "insufficient balance" in lower:
        need = re.search(r"required:\s*\d+\s*amount\s*\(([0-9.]+)\s*credits?\)", lower)
        have = re.search(r"available:\s*\d+\s*amount\s*\(([0-9.]+)\s*credits?\)", lower)
        return ("余额不足 / insufficient balance (HTTP 402): 这次请求需要 %s credits，账户可用 %s credits。"
                "去控制台充值，或把 resolution 降到 720p/480p、把 duration 调短后再试。"
                "Upstream said: %s"
                % (need.group(1) if need else "?", have.group(1) if have else "?", snippet))
    if status == 400:
        return ("上游拒绝了这次请求的参数 / the upstream rejected the request parameters (HTTP 400): %s "
                "—— 按它给出的限制调整对应控件后重试（例如它只支持 16:9 / 9:16 / 1:1 时，"
                "就把 ratio 从 adaptive 改成其中之一）。" % snippet)
    if status in (401, 403):
        return ("鉴权失败 / authentication failed (HTTP %s): 检查 config.ini 里的 KEY。"
                "Upstream said: %s" % (status, snippet))
    if status == 524:
        return ("平台边缘节点超时 (HTTP 524) / edge timeout: 上游这次没在超时时间内返回，重试一次通常就好。"
                "Upstream said: %s" % snippet)
    return None


def explain_task_failure(data, text):
    """Add a hint to an upstream task failure when the reason is a known trap."""
    error = {}
    for holder in (data.get("task_info"), data.get("data")):
        if isinstance(holder, dict) and isinstance(holder.get("error"), dict):
            error = holder["error"]
            break
    detail = str(error.get("message") or error.get("detail") or error.get("title") or "")
    lower = detail.lower()
    if "ref_image" in lower or "缺少必填参数" in detail:
        return ("任务失败：这个模型的通道要求提供参考图 / this channel requires a reference image "
                "（上游原话：“%s”）。把图片接到 reference_images，或把图片链接填进 "
                "reference_image_urls；只想用文字生成视频请改用 w3.0-video。" % detail)
    if "failed to download file" in lower:
        return ("任务失败：上游下载不到你提供的素材链接 / the upstream could not fetch your media URL. "
                "多半是链接已过期或对它不可达——把图片接到 IMAGE 输入上（节点会转成 Base64 发送），"
                "或换成一个长期有效的公开链接。Upstream said: %s" % (detail or text[:200]))
    if "cannot mix" in lower:
        return ("任务失败：关键帧模式与参考模式不能混用 / keyframe mode and reference mode cannot be mixed. "
                "Upstream said: %s" % (detail or text[:200]))
    return text[:400]


# --------------------------------------------------------------------------
# Job lifecycle
# --------------------------------------------------------------------------

def submit_task(cfg, payload):
    url = cfg["base_url"] + "/videos"
    preview = json.dumps(
        {k: (v[:60] + "..." if isinstance(v, str) and len(v) > 60 else v) for k, v in payload.items()},
        ensure_ascii=False,
    )
    log("[vergora] 提交任务 / submit -> %s  %s" % (url, preview))
    status, text = http_post_json(url, auth_headers(cfg), payload, timeout=120)
    if status >= 400:
        hint = explain_http_error(status, text, payload.get("model"))
        raise RuntimeError(hint or ("提交失败 / submit failed HTTP %s: %s" % (status, text[:400])))
    try:
        data = json.loads(text)
    except Exception as exc:
        raise RuntimeError("提交返回不是 JSON（HTTP %s）/ not JSON: %s" % (status, text[:400])) from exc
    task_info = data.get("task_info") or {}
    task_id = task_info.get("id") or data.get("id")
    if not task_id:
        raise RuntimeError("返回里没有任务 id / no task id in response: %s" % text[:400])
    log("[vergora] 任务已创建 / task created id=%s status=%s" % (task_id, task_info.get("status")))
    return task_id


def wait_for_video(cfg, task_id, progress=None):
    started = time.time()
    consecutive_errors = 0
    while True:
        throw_exception_if_processing_interrupted()
        elapsed = time.time() - started
        # video jobs usually land in 2-6 minutes; show progress on the canvas so the
        # node does not look frozen while the upstream works
        tick_progress(progress, min(95, int(elapsed / 300.0 * 100)))
        if elapsed > cfg["timeout"]:
            raise RuntimeError(
                "等待超时 / timed out after %.0f s. Task %s may still be running; query it with "
                "GET %s/videos/%s" % (cfg["timeout"], task_id, cfg["base_url"], task_id)
            )
        try:
            status, text = http_get_json(
                cfg["base_url"] + "/videos/" + task_id, auth_headers(cfg, with_json=False), timeout=60
            )
            if status >= 400:
                raise RuntimeError(explain_http_error(status, text)
                                   or ("HTTP %s: %s" % (status, text[:300])))
            data = json.loads(text)
        except Exception as exc:
            # A gateway hiccup should not throw away a job that is already running.
            consecutive_errors += 1
            log("[vergora] 查询出错第 %d 次 / poll error %d: %s" % (consecutive_errors, consecutive_errors, exc))
            if consecutive_errors >= 5:
                raise RuntimeError("连续 %d 次查询失败 / %d consecutive poll failures, last: %s"
                                   % (consecutive_errors, consecutive_errors, exc))
            time.sleep(cfg["poll_interval"])
            continue
        consecutive_errors = 0
        task_info = data.get("task_info") or {}
        state = str(task_info.get("status") or data.get("status") or "").lower()
        # the upstream reports real progress (and a progress label) on its own channel
        reported = None
        label = ""
        for holder in (data, data.get("data")):
            if isinstance(holder, dict):
                value = holder.get("progress")
                if reported is None and isinstance(value, (int, float)):
                    reported = int(value)
                if not label and isinstance(holder.get("progress_text"), str):
                    label = holder["progress_text"].strip().splitlines()[0][:40]
        if reported is not None:
            tick_progress(progress, max(0, min(99, reported)))
        log("[vergora] 已等待 / waited %4.0f s  状态 / status=%s%s"
            % (elapsed, state or "unknown", ("  进度 / progress=%d%%" % reported) if reported is not None else ""))
        if label:
            log("[vergora]   上游进度说明 / upstream says: %s" % label)
        if state in ("completed", "succeeded", "success", "done"):
            video_url = extract_video_url(data)
            if video_url:
                return video_url
            raise RuntimeError("任务已完成但没有视频地址 / completed without a video url: %s" % text[:400])
        if state in ("failed", "error", "canceled", "cancelled"):
            raise RuntimeError("任务失败 / task failed: %s" % explain_task_failure(data, text))
        time.sleep(cfg["poll_interval"])


def download_video(cfg, video_url, task_id, attempts=3):
    """Download the clip, verify it actually arrived, and retry a stalled transfer.

    The CDN occasionally drops the connection mid-transfer and leaves a 0-byte file,
    so an unverified write would look like success but produce an unplayable clip.
    """
    out_dir = resolve_output_dir(cfg)
    os.makedirs(out_dir, exist_ok=True)
    filename = "vergora_%s_%d.mp4" % (str(task_id).replace("-", "")[:8], int(time.time()))
    path = os.path.join(out_dir, filename)
    part = path + ".part"
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            http_download(video_url, part, timeout=600)
            size = os.path.getsize(part) if os.path.exists(part) else 0
            if size > 8192:
                os.replace(part, path)
                log("[vergora] 已保存 / saved %s (%.1f MB)" % (path, size / 1024.0 / 1024.0))
                return path
            last_error = "只拿到 %d 字节 / got only %d bytes" % (size, size)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        log("[vergora] 下载不完整（%s），第 %d/%d 次重试 / incomplete download, retry %d/%d"
            % (last_error, attempt, attempts, attempt, attempts))
        if os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass
        time.sleep(2 * attempt)
    raise RuntimeError(
        "下载失败（试了 %d 次）/ download failed after %d attempts: %s —— 任务本身已完成，"
        "视频在 %s，可以手动下载或稍后重试。" % (attempts, attempts, last_error, video_url)
    )


def build_preview_ui(path):
    """Return a UI entry the ComfyUI frontend renders as a video, when possible.

    Only returned when the file really sits inside ComfyUI's output directory,
    otherwise the frontend cannot fetch it.
    """
    if folder_paths is None:
        return None
    try:
        out_root = folder_paths.get_output_directory()
    except Exception:
        return None
    path = os.path.abspath(path)
    out_root = os.path.abspath(out_root)
    if not path.startswith(out_root + os.sep):
        return None
    relative = os.path.relpath(path, out_root)
    subfolder = os.path.dirname(relative).replace(os.sep, "/")
    return {
        "filename": os.path.basename(relative),
        "subfolder": "" if subfolder == "." else subfolder,
        "type": "output",
        "format": "video/mp4",
    }


def generate(prompt, model, duration, seed, resolution, ratio, audio, prompt_extend,
             first_frame=None, last_frame=None, first_frame_url="", last_frame_url="",
             reference_images=None, reference_image_urls="",
             reference_video_urls="", reference_audio_urls="",
             file_url="", link_url="", extra_json=""):
    cfg = load_config()
    progress = make_progress()
    frame = tensor_to_data_uri(first_frame) or (first_frame_url.strip() or None)
    tail = tensor_to_data_uri(last_frame) or (last_frame_url.strip() or None)

    images = tensor_batch_to_data_uris(reference_images, MAX_REFERENCE_IMAGES)
    images += split_uri_lines(reference_image_urls)
    videos = split_uri_lines(reference_video_urls)
    audios = split_uri_lines(reference_audio_urls)

    payload = build_payload(
        prompt, model, duration, seed, resolution, ratio, audio, prompt_extend,
        frame, tail, images, videos, audios, file_url, link_url, extra_json,
    )
    tick_progress(progress, 3)
    task_id = submit_task(cfg, payload)
    video_url = wait_for_video(cfg, task_id, progress)
    path = download_video(cfg, video_url, task_id)
    tick_progress(progress, 100)
    return path, task_id, video_url


# --------------------------------------------------------------------------
# Node
# --------------------------------------------------------------------------

class VergoraVideoGenerator:
    """Wan 3.0 video generation: text to video, keyframe (image) to video, or
    reference to video, all through one node."""

    CATEGORY = CATEGORY
    OUTPUT_NODE = True
    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("video_path", "task_id", "video_url")
    FUNCTION = "run"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "model": (VIDEO_MODELS, {"default": VIDEO_MODELS[0]}),
                "resolution": (RESOLUTIONS, {"default": RESOLUTIONS[0]}),
                "ratio": (RATIOS, {"default": RATIOS[0]}),
                "duration": ("INT", {"default": 5, "min": -1, "max": 30, "step": 1,
                                     "tooltip": "-1 = smart duration, otherwise 2 to 30 seconds"}),
                "audio": ("BOOLEAN", {"default": True}),
                "prompt_extend": ("BOOLEAN", {"default": True,
                                              "tooltip": "Let upstream rewrite the prompt (usually better motion)"}),
                "seed": ("INT", {"default": -1, "min": -1, "max": 2147483647,
                                 "tooltip": "-1 = let the service pick a seed"}),
            },
            "optional": {
                "first_frame": ("IMAGE", {"tooltip": "Keyframe mode: the first frame of the video"}),
                "last_frame": ("IMAGE", {"tooltip": "Keyframe mode: the last frame of the video"}),
                "first_frame_url": ("STRING", {"default": "", "tooltip": "Used when first_frame is not connected"}),
                "last_frame_url": ("STRING", {"default": "", "tooltip": "Used when last_frame is not connected"}),
                "reference_images": ("IMAGE", {"tooltip": "Reference mode: a batch of up to 10 reference images"}),
                "reference_image_urls": ("STRING", {"multiline": True, "default": "",
                                                    "tooltip": "Reference mode: image URLs, one per line"}),
                "reference_video_urls": ("STRING", {"multiline": True, "default": "",
                                                    "tooltip": "Reference mode: video URLs (mp4/mov), one per line, up to 5"}),
                "reference_audio_urls": ("STRING", {"multiline": True, "default": "",
                                                    "tooltip": "Reference mode: audio URLs (wav/mp3), one per line, up to 5"}),
                "file_url": ("STRING", {"default": "", "tooltip": "Reference mode: document URL (pdf, docx, pptx, ...)"}),
                "link_url": ("STRING", {"default": "", "tooltip": "Reference mode: public web page URL"}),
                "extra_json": ("STRING", {"multiline": True, "default": "",
                                          "tooltip": "Extra request fields, e.g. {\"duration\": 10}"}),
            },
        }

    def run(self, prompt, model, resolution, ratio, duration, audio, prompt_extend, seed,
            first_frame=None, last_frame=None, first_frame_url="", last_frame_url="",
            reference_images=None, reference_image_urls="",
            reference_video_urls="", reference_audio_urls="",
            file_url="", link_url="", extra_json=""):
        path, task_id, video_url = generate(
            prompt, model, duration, seed, resolution, ratio, audio, prompt_extend,
            first_frame, last_frame, first_frame_url, last_frame_url,
            reference_images, reference_image_urls,
            reference_video_urls, reference_audio_urls,
            file_url, link_url, extra_json,
        )
        result = (path, task_id, video_url)
        preview = build_preview_ui(path)
        if preview is None:
            return {"result": result}
        return {"ui": {"images": [preview], "animated": (True,)}, "result": result}


NODE_CLASS_MAPPINGS = {
    "VergoraVideoGenerator": VergoraVideoGenerator,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "VergoraVideoGenerator": "vergora Wan 3.0 视频生成（文生/图生/参考）",
}
