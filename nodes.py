# -*- coding: utf-8 -*-
"""ComfyUI 节点：调 vergora 网关的 /v1/videos 生成视频。

接口流程（2026-10-14 实测）：
    提交  POST {base}/videos        -> {"task_info": {"id": "...", "status": "pending"}}
    查询  GET  {base}/videos/{id}   -> {"task_info": {"status": "pending|processing|completed|failed"},
                                        "videos": ["https://.../result_00.mp4"]}
    下载  GET  videos[0]

请求体至少要给 prompt / first_frame / last_frame / reference_images /
reference_videos / reference_audios / file / link 之一（接口的校验要求）。

HTTP 层优先用 requests，没有就退回标准库 urllib，保证节点在任何 ComfyUI
环境里都能加载。密钥只从 config.ini 或环境变量读，不做成节点参数，
这样工作流 JSON 里不会带 key。
"""

import base64
import configparser
import json
import os
import shutil
import time
import urllib.error
import urllib.request
from io import BytesIO

import numpy as np

try:
    import requests
except ImportError:  # ComfyUI 自带环境不一定装 requests
    requests = None

try:  # ComfyUI 环境里存在；脱离 ComfyUI 单跑时退化成节点目录下的 output
    import folder_paths
except Exception:  # pragma: no cover
    folder_paths = None

try:
    from comfy.model_management import throw_exception_if_processing_interrupted
except Exception:  # pragma: no cover
    def throw_exception_if_processing_interrupted():
        return None

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.ini")

DEFAULT_BASE_URL = "https://api.vergora.ai/v1"
VIDEO_MODELS = ["w3.0-video", "minimax-h3"]
CATEGORY = "vergora"


def log(message):
    """日志兜底：控制台不是 UTF-8 时也不至于因为中文报错。"""
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        print(message.encode("ascii", "replace").decode("ascii"), flush=True)


# --------------------------------------------------------------------------
# HTTP 层：有 requests 用 requests，没有就用标准库
# --------------------------------------------------------------------------

def http_post_json(url, headers, payload, timeout=120):
    """返回 (状态码, 响应文本)。"""
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
    """返回 (状态码, 响应文本)。"""
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
# 配置与工具
# --------------------------------------------------------------------------

def load_config():
    """读 config.ini，环境变量优先级更高。"""
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
    """with_json=False 用于 GET：带 Content-Type 但没有请求体时，
    网关会尝试解析空 body 并返回 400（实测报 UnmarshalBodyReusable failed）。"""
    if not cfg["api_key"]:
        raise RuntimeError(
            "没有配置 API key：请在 %s 的 [API] 段填 KEY，或设置环境变量 VERGORA_API_KEY" % CONFIG_PATH
        )
    headers = {"Authorization": "Bearer " + cfg["api_key"]}
    if with_json:
        headers["Content-Type"] = "application/json"
    return headers


def tensor_to_data_uri(image):
    """把 ComfyUI 的 IMAGE 张量转成 data URI（PNG）。"""
    if image is None:
        return None
    if Image is None:
        raise RuntimeError("需要 Pillow 才能把 IMAGE 转成图片")
    arr = image
    if hasattr(arr, "detach"):
        arr = arr.detach().cpu().numpy()
    arr = np.asarray(arr)
    if arr.ndim == 4:
        arr = arr[0]
    arr = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
    buffer = BytesIO()
    Image.fromarray(arr).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def build_payload(prompt, model, duration, seed, first_frame=None, last_frame=None, extra_json=""):
    payload = {"model": model}
    if prompt and prompt.strip():
        payload["prompt"] = prompt.strip()
    if duration and int(duration) > 0:
        payload["duration"] = int(duration)
    if seed is not None and int(seed) >= 0:
        payload["seed"] = int(seed)
    if first_frame:
        payload["first_frame"] = first_frame
    if last_frame:
        payload["last_frame"] = last_frame
    if extra_json and extra_json.strip():
        try:
            extra = json.loads(extra_json)
        except Exception as exc:
            raise RuntimeError("extra_json 不是合法 JSON：%s" % exc)
        if not isinstance(extra, dict):
            raise RuntimeError('extra_json 必须是 JSON 对象，例如 {"resolution": "720p"}')
        payload.update(extra)
    if not any(k in payload for k in ("prompt", "first_frame", "last_frame")):
        raise RuntimeError("至少要给 prompt、首帧或尾帧其中之一")
    return payload


def extract_video_url(data):
    """不同上游返回字段不完全一致，按可能性依次取。"""
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


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def submit_task(cfg, payload):
    url = cfg["base_url"] + "/videos"
    preview = json.dumps(
        {k: (v[:60] + "..." if isinstance(v, str) and len(v) > 60 else v) for k, v in payload.items()},
        ensure_ascii=False,
    )
    log("[vergora] 提交任务 -> %s  %s" % (url, preview))
    status, text = http_post_json(url, auth_headers(cfg), payload, timeout=120)
    if status >= 400:
        raise RuntimeError("提交失败 HTTP %s: %s" % (status, text[:400]))
    try:
        data = json.loads(text)
    except Exception as exc:
        raise RuntimeError("提交返回不是 JSON（HTTP %s）：%s" % (status, text[:400])) from exc
    task_info = data.get("task_info") or {}
    task_id = task_info.get("id") or data.get("id")
    if not task_id:
        raise RuntimeError("返回里没有任务 id: %s" % text[:400])
    log("[vergora] 任务已创建 id=%s status=%s" % (task_id, task_info.get("status")))
    return task_id


def wait_for_video(cfg, task_id):
    started = time.time()
    consecutive_errors = 0
    while True:
        throw_exception_if_processing_interrupted()
        elapsed = time.time() - started
        if elapsed > cfg["timeout"]:
            raise RuntimeError(
                "等待超时（%.0f 秒），任务 %s 可能仍在处理，可手动查询 GET %s/videos/%s"
                % (cfg["timeout"], task_id, cfg["base_url"], task_id)
            )
        try:
            status, text = http_get_json(
                cfg["base_url"] + "/videos/" + task_id, auth_headers(cfg, with_json=False), timeout=60
            )
            if status >= 400:
                raise RuntimeError("HTTP %s: %s" % (status, text[:300]))
            data = json.loads(text)
        except Exception as exc:
            # 网关偶发抽风不该让整个任务失败，连续出错够多次才放弃
            consecutive_errors += 1
            log("[vergora] 查询出错第 %d 次：%s" % (consecutive_errors, exc))
            if consecutive_errors >= 5:
                raise RuntimeError("连续 %d 次查询失败，最后一次：%s" % (consecutive_errors, exc))
            time.sleep(cfg["poll_interval"])
            continue
        consecutive_errors = 0
        task_info = data.get("task_info") or {}
        state = str(task_info.get("status") or data.get("status") or "").lower()
        log("[vergora] 已等待 %4.0f 秒  状态=%s" % (elapsed, state or "未知"))
        if state in ("completed", "succeeded", "success", "done"):
            video_url = extract_video_url(data)
            if video_url:
                return video_url
            raise RuntimeError("任务已完成但没找到视频地址: %s" % text[:400])
        if state in ("failed", "error", "canceled", "cancelled"):
            raise RuntimeError("任务失败: %s" % text[:400])
        time.sleep(cfg["poll_interval"])


def download_video(cfg, video_url, task_id):
    out_dir = resolve_output_dir(cfg)
    os.makedirs(out_dir, exist_ok=True)
    filename = "vergora_%s_%d.mp4" % (str(task_id).replace("-", "")[:8], int(time.time()))
    path = os.path.join(out_dir, filename)
    http_download(video_url, path, timeout=600)
    log("[vergora] 已保存 %s（%.1f MB）" % (path, os.path.getsize(path) / 1024.0 / 1024.0))
    return path


def build_preview_ui(path):
    """按 ComfyUI 前端认识的格式返回预览结果，让视频直接显示在结果面板里。

    只有文件确实位于 ComfyUI 的 output 目录时才返回，否则前端取不到文件。
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


def generate(prompt, model, duration, seed, first_frame=None, last_frame=None,
             first_frame_url="", last_frame_url="", extra_json=""):
    cfg = load_config()
    frame = tensor_to_data_uri(first_frame) or (first_frame_url.strip() or None)
    tail = tensor_to_data_uri(last_frame) or (last_frame_url.strip() or None)
    payload = build_payload(prompt, model, duration, seed, frame, tail, extra_json)
    task_id = submit_task(cfg, payload)
    video_url = wait_for_video(cfg, task_id)
    path = download_video(cfg, video_url, task_id)
    return path, task_id, video_url


class VergoraVideoGenerator:
    """一个节点覆盖三种用法：只填 prompt 是文生视频；接首帧是图生视频；首尾帧都接是首尾帧视频。"""

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
                "duration": ("INT", {"default": 5, "min": 1, "max": 30, "step": 1}),
                "seed": ("INT", {"default": -1, "min": -1, "max": 2147483647}),
            },
            "optional": {
                "first_frame": ("IMAGE",),
                "last_frame": ("IMAGE",),
                "first_frame_url": ("STRING", {"default": ""}),
                "last_frame_url": ("STRING", {"default": ""}),
                "extra_json": ("STRING", {"multiline": True, "default": ""}),
            },
        }

    def run(self, prompt, model, duration, seed,
            first_frame=None, last_frame=None,
            first_frame_url="", last_frame_url="", extra_json=""):
        path, task_id, video_url = generate(
            prompt, model, duration, seed,
            first_frame, last_frame, first_frame_url, last_frame_url, extra_json,
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
    "VergoraVideoGenerator": "vergora 视频生成（文生/图生/首尾帧）",
}
