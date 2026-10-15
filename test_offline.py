# -*- coding: utf-8 -*-
"""Offline self-test: run the whole job flow (submit -> poll -> download -> preview)
against a stub HTTP layer. No network, no API key, no cost.

Run it with any Python that has numpy:  python test_offline.py
"""

import base64
import os
import sys
import tempfile

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import nodes  # noqa: E402

STATE = {"polls": 0, "post_url": None, "payload": None, "headers": None,
         "get_headers": None, "fail_first": 0, "download_url": None}
OUT_DIR = tempfile.mkdtemp(prefix="vergora_test_")


class FakeFolderPaths:
    """Pretend to be ComfyUI's folder_paths so output goes to a temp directory."""

    @staticmethod
    def get_output_directory():
        return OUT_DIR


def fake_post_json(url, headers, payload, timeout=120):
    STATE["post_url"] = url
    STATE["payload"] = payload
    STATE["headers"] = headers
    return 200, '{"task_info": {"id": "test-task-1234", "status": "pending"}}'


def fake_get_json(url, headers, timeout=60):
    STATE["get_headers"] = headers
    STATE["polls"] += 1
    if STATE["fail_first"] > 0:
        STATE["fail_first"] -= 1
        return 400, ('{"error":{"message":"common.UnmarshalBodyReusable failed: '
                     'unexpected end of JSON input","type":"gateway_error"}}')
    if STATE["polls"] < 3:
        return 200, '{"task_info": {"id": "test-task-1234", "status": "processing"}}'
    return 200, ('{"task_info": {"id": "test-task-1234", "status": "completed"},'
                 ' "videos": ["https://example.invalid/result_00.mp4"]}')


def fake_download(url, dest_path, timeout=600):
    STATE["download_url"] = url
    STATE["downloads"] = STATE.get("downloads", 0) + 1
    if STATE.get("short_downloads"):
        STATE["short_downloads"] -= 1
        with open(dest_path, "wb") as handle:
            handle.write(b"")  # a stalled transfer leaves nothing behind
        return
    with open(dest_path, "wb") as handle:
        handle.write(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 9000)  # big enough to be accepted


def fake_get_failure(url, headers, timeout=60):
    return 503, '{"error": {"message": "No available channel for model xxx"}}'


def prepare():
    nodes.http_post_json = fake_post_json
    nodes.http_get_json = fake_get_json
    nodes.http_download = fake_download
    nodes.folder_paths = FakeFolderPaths()
    nodes.load_config = lambda: {
        "base_url": "https://api.vergora.ai/v1",
        "api_key": "sk-test",
        "poll_interval": 0.01,
        "timeout": 30,
        "output_dir": "",  # empty -> use folder_paths' output directory
    }
    return nodes.VergoraVideoGenerator()


def call(node, **kwargs):
    """Node returns a ComfyUI-style dict; hand back both it and the result tuple."""
    out = node.run(**kwargs)
    if isinstance(out, dict):
        return out, out.get("result")
    return None, out


def expect_error(kwargs, needle, node):
    try:
        call(node, **kwargs)
    except RuntimeError as exc:
        assert needle in str(exc), "expected %r in %r" % (needle, str(exc))
        return
    raise AssertionError("expected RuntimeError containing %r" % needle)


def main():
    node = prepare()
    image = np.zeros((64, 96, 3), dtype=np.float32)
    image[:, :, 0] = 1.0
    batch = np.zeros((2, 64, 96, 3), dtype=np.float32)
    batch[1, :, :, 1] = 1.0

    # ---- keyframe mode: first + last frame, full parameter set ----
    out, result = call(
        node, prompt="a red paper boat on a calm lake", model="w3.0-video",
        resolution="720p", ratio="16:9", duration=5, audio=False, prompt_extend=False,
        seed=42, first_frame=image, last_frame=image,
    )
    path, task_id, video_url = result
    payload = STATE["payload"]
    assert STATE["post_url"] == "https://api.vergora.ai/v1/videos", STATE["post_url"]
    assert STATE["headers"]["Authorization"] == "Bearer sk-test", STATE["headers"]
    assert STATE["headers"]["Content-Type"] == "application/json", STATE["headers"]
    # polling must not carry Content-Type (the gateway 400s on an empty JSON body)
    assert "Content-Type" not in STATE["get_headers"], STATE["get_headers"]
    assert payload["model"] == "w3.0-video" and payload["duration"] == 5, payload
    assert payload["resolution"] == "720p" and payload["ratio"] == "16:9", payload
    assert payload["audio"] is False and payload["prompt_extend"] is False, payload
    assert payload["seed"] == 42, payload
    assert payload["first_frame"].startswith("data:image/png;base64,"), payload
    assert payload["last_frame"].startswith("data:image/png;base64,"), payload
    assert task_id == "test-task-1234" and video_url.endswith("result_00.mp4")
    assert STATE["polls"] == 3, STATE["polls"]
    assert os.path.exists(path) and os.path.getsize(path) > 0, path

    # it must register as an output node and return a renderable video preview
    assert nodes.VergoraVideoGenerator.OUTPUT_NODE is True
    assert out is not None and "ui" in out, out
    item = out["ui"]["images"][0]
    assert item == {"filename": os.path.basename(path), "subfolder": "",
                    "type": "output", "format": "video/mp4"}, item
    assert out["ui"]["animated"] == (True,), out["ui"]

    # ---- text to video: defaults, smart duration -1, no seed sent ----
    STATE["polls"] = 0
    _, result2 = call(node, prompt="just text", model="w3.0-video", resolution="1080p",
                      ratio="adaptive", duration=-1, audio=True, prompt_extend=True, seed=-1)
    p2 = STATE["payload"]
    assert "first_frame" not in p2 and "last_frame" not in p2, p2
    assert "seed" not in p2, p2
    assert p2["duration"] == -1 and p2["resolution"] == "1080p" and p2["ratio"] == "adaptive", p2
    assert os.path.exists(result2[0])

    # ---- reference mode: batch images + URL lists + file ----
    STATE["polls"] = 0
    call(node, prompt="Image 1 holds Image 2", model="w3.0-video", resolution="720p",
         ratio="adaptive", duration=10, audio=True, prompt_extend=True, seed=-1,
         reference_images=batch,
         reference_image_urls="https://example.com/a.png\n# comment\nhttps://example.com/b.png\n",
         reference_video_urls="https://example.com/role.mp4",
         reference_audio_urls="https://example.com/song.mp3")
    p3 = STATE["payload"]
    assert len(p3["reference_images"]) == 4, p3["reference_images"]
    assert p3["reference_images"][0].startswith("data:image/png;base64,")
    assert p3["reference_images"][2] == "https://example.com/a.png"
    assert p3["reference_videos"] == ["https://example.com/role.mp4"], p3
    assert p3["reference_audios"] == ["https://example.com/song.mp3"], p3
    assert "first_frame" not in p3, p3

    # ---- extras pass through ----
    STATE["polls"] = 0
    call(node, prompt="hi", model="w3.0-video", resolution="480p", ratio="9:16",
         duration=2, audio=True, prompt_extend=True, seed=-1,
         extra_json='{"vendor_flag": true}')
    assert STATE["payload"]["vendor_flag"] is True, STATE["payload"]

    # ---- gateway hiccups must heal themselves ----
    STATE["polls"] = 0
    STATE["fail_first"] = 2
    _, result_r = call(node, prompt="retry me", model="w3.0-video", resolution="1080p",
                       ratio="adaptive", duration=5, audio=True, prompt_extend=True, seed=-1)
    assert os.path.exists(result_r[0]), result_r

    # ---- a stalled download must be retried instead of written as a 0-byte file ----
    STATE["polls"] = 0
    STATE["short_downloads"] = 1
    _, result_dl = call(node, prompt="retry download", model="w3.0-video", resolution="480p",
                        ratio="adaptive", duration=5, audio=True, prompt_extend=True, seed=-1)
    assert os.path.getsize(result_dl[0]) > 8192, result_dl
    assert not os.path.exists(result_dl[0] + ".part"), "partial file should be cleaned up"

    # ---- contract validation, caught before anything is billed ----
    base = dict(model="w3.0-video", resolution="1080p", ratio="adaptive",
                duration=5, audio=True, prompt_extend=True, seed=-1)
    # models with a narrowed ratio set: adaptive falls back, an unsupported fixed ratio is refused
    STATE["polls"] = 0
    call(node, model="minimax-h3", prompt="x", resolution="480p", ratio="adaptive",
         duration=5, audio=True, prompt_extend=True, seed=-1, reference_images=batch)
    assert STATE["payload"]["ratio"] == "16:9", STATE["payload"]
    call(node, model="minimax-h3", prompt="x", resolution="480p", ratio="9:16",
         duration=5, audio=True, prompt_extend=True, seed=-1, reference_images=batch)
    assert STATE["payload"]["ratio"] == "9:16", STATE["payload"]
    expect_error(dict(base, model="minimax-h3", prompt="x", ratio="4:3"), "只支持", node)
    # this channel is image-driven: text only must be refused locally
    expect_error(dict(base, model="minimax-h3", prompt="x", ratio="16:9"), "必须给一张图", node)
    # an explicitly rejected resolution is stopped before submitting, not after a 40s failure
    expect_error(dict(base, model="minimax-h3", prompt="x", ratio="16:9", resolution="720p",
                      reference_images=batch), "minimax-h3 不支持 720p，请改用 1080p", node)
    STATE["polls"] = 0
    call(node, model="minimax-h3", prompt="x", resolution="1080p", ratio="16:9",
         duration=5, audio=True, prompt_extend=True, seed=-1, reference_images=batch)
    assert STATE["payload"]["resolution"] == "1080p", STATE["payload"]
    expect_error(dict(base, prompt=""), "至少要给", node)
    expect_error(dict(base, prompt="x", first_frame=image,
                      reference_video_urls="https://example.com/v.mp4"),
                 "互斥", node)
    expect_error(dict(base, prompt="x", file_url="https://example.com/a.pdf",
                      link_url="https://example.com/page"), "不能同时", node)
    expect_error(dict(base, prompt="x",
                      reference_image_urls="\n".join("https://e.com/%d.png" % i for i in range(11))),
                 "最多", node)
    expect_error(dict(base, prompt="x", duration=45), "duration", node)
    expect_error(dict(base, prompt="x", extra_json="{not json"), "JSON", node)

    # ---- upstream failures surface with the response body ----
    nodes.http_get_json = fake_get_failure
    try:
        cfg = nodes.load_config()
        nodes.submit_task(cfg, {"model": "w3.0-video", "prompt": "x"})
        nodes.wait_for_video(cfg, "test-task-1234")
    except RuntimeError as exc:
        # the raw body is preserved and a hint is added, so match what survives both
        assert "No available channel" in str(exc), exc
        assert "5 consecutive poll failures" in str(exc), exc
    else:
        raise AssertionError("an upstream error should raise")

    # ---- known gateway errors get an actionable hint ----
    nodes.http_post_json = lambda url, headers, payload, timeout=120: (
        503, '{"error":{"code":"model_not_found","message":"No available channel for model '
             'minimax-h3 under group minimax-h3 (distributor)"}}')
    try:
        nodes.submit_task(nodes.load_config(), {"model": "minimax-h3", "prompt": "x"})
    except RuntimeError as exc:
        assert "w3.0-video" in str(exc) and "minimax-h3" in str(exc), exc
    else:
        raise AssertionError("a missing channel should raise")

    nodes.http_post_json = lambda url, headers, payload, timeout=120: (
        403, '{"error":{"message":"user quota is not enough","code":"insufficient_quota"}}')
    try:
        nodes.submit_task(nodes.load_config(), {"model": "w3.0-video", "prompt": "x"})
    except RuntimeError as exc:
        assert "额度" in str(exc), exc
    else:
        raise AssertionError("a quota error should raise")

    nodes.http_post_json = lambda url, headers, payload, timeout=120: (
        402, '{"status":402,"title":"Insufficient balance","detail":"Insufficient balance to complete '
             'this request. Required: 1000000 amount (100.0 credits), Available: 750000 amount '
             '(75.0 credits)"}')
    try:
        nodes.submit_task(nodes.load_config(), {"model": "w3.0-video", "prompt": "x"})
    except RuntimeError as exc:
        assert "100.0" in str(exc) and "75.0" in str(exc) and "720p" in str(exc), exc
    else:
        raise AssertionError("an insufficient balance should raise")

    nodes.http_post_json = lambda url, headers, payload, timeout=120: (
        400, '{"code":"invalid_request","message":"seedance2.0批发通道二 ratio 只支持 16:9、9:16、1:1",'
             '"data":null}')
    try:
        nodes.submit_task(nodes.load_config(), {"model": "minimax-h3", "prompt": "x"})
    except RuntimeError as exc:
        assert "16:9" in str(exc) and "adaptive" in str(exc), exc
    else:
        raise AssertionError("a rejected parameter should raise")

    failed = {"task_info": {"status": "failed", "error": {
        "code": 4002, "title": "Infrastructure operation failed",
        "detail": "Failed to download file: HTTP 502"}}}
    message = nodes.explain_task_failure(failed, "")
    assert "素材" in message and "Base64" in message, message

    # nested error shape + the reference-image requirement
    nested = {"task_info": {"status": "failed"},
              "data": {"error": {"code": "task_failed", "message": "缺少必填参数：ref_image_0"}}}
    message = nodes.explain_task_failure(nested, "")
    assert "参考图" in message and "w3.0-video" in message, message

    # the other failure shape: fail_reason carries the reason as a JSON string
    upstream_fail = {
        "task_info": {"status": "failed"},
        "data": {
            "status": "failed", "progress": 100,
            "fail_reason": '{"code":"invalid_resolution","data":null,"message":'
                           '"model minimax-h3-f does not support resolution 720p"}',
        },
    }
    code, detail = nodes.extract_failure_reason(upstream_fail)
    assert code == "invalid_resolution" and "720p" in detail, (code, detail)
    message = nodes.explain_task_failure(upstream_fail, "")
    assert "1080p" in message and "480p" in message, message

    # the upstream's own progress must drive the canvas bar
    class FakeProgress:
        def __init__(self):
            self.values = []

        def update_absolute(self, value, total=100):
            self.values.append(value)

    def poll_with_progress(url, headers, timeout=60):
        STATE["polls"] += 1
        if STATE["polls"] < 2:
            return 200, ('{"task_info":{"status":"processing"},'
                         '"data":{"progress":42,"progress_text":"encoding\\nsecond line"}}')
        return 200, '{"task_info":{"status":"completed"},"videos":["https://e.invalid/r.mp4"]}'

    nodes.http_get_json = poll_with_progress
    STATE["polls"] = 0
    bar = FakeProgress()
    url = nodes.wait_for_video(nodes.load_config(), "task-under-test", bar)
    assert url.endswith("r.mp4"), url
    assert 42 in bar.values, bar.values

    # a big local image must be shrunk to fit the gateway's request body limit
    noisy = np.random.RandomState(0).rand(1024, 1024, 3).astype(np.float32)
    uri = nodes.tensor_to_data_uri(noisy, max_bytes=200 * 1024)
    raw = base64.b64decode(uri.split(",", 1)[1])
    assert len(raw) <= 200 * 1024, len(raw)
    assert uri.startswith("data:image/"), uri[:30]

    # 413 gets its own explanation
    nodes.http_post_json = lambda url, headers, payload, timeout=120: (
        413, "<html><head><title>413 Request Entity Too Large</title></head></html>")
    try:
        nodes.submit_task(nodes.load_config(), {"model": "w3.0-video", "prompt": "x"})
    except RuntimeError as exc:
        assert "413" in str(exc) and "client_max_body_size" in str(exc), exc
    else:
        raise AssertionError("a 413 should raise")

    # a dropped TLS connection must be explained, and must not be retried silently
    calls = {"n": 0}

    def mock_post_json(url, headers, payload, timeout=120):
        calls["n"] += 1
        raise RuntimeError("SSLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING]'))")

    nodes.http_post_json = mock_post_json
    try:
        nodes.submit_task(nodes.load_config(), {"model": "w3.0-video", "prompt": "x"})
    except RuntimeError as exc:
        assert "网络中断" in str(exc) and "没有自动重试" in str(exc), exc
    else:
        raise AssertionError("a network error should raise")
    assert calls["n"] == 1, "a submit must not be retried automatically (could double-bill)"

    # the same rules are surfaced through input validation, which ComfyUI shows in the
    # node's own error panel instead of the generic "node failed" toast
    check = nodes.VergoraVideoGenerator.VALIDATE_INPUTS
    assert check(model="minimax-h3", resolution="720p", reference_images=["x"]) == (
        "minimax-h3 不支持 720p，请改用 1080p。 / "
        "minimax-h3 does not support 720p, please use 1080p.")
    assert check(model="minimax-h3", resolution="1080p", reference_images=["x"]) is True
    assert "需要一张参考图" in check(model="minimax-h3", resolution="1080p")
    assert check(model="minimax-h3", resolution="1080p", reference_image_urls=" https://e.invalid/a.png ") is True
    assert check(model="w3.0-video", resolution="720p") is True
    assert check(model="w3.0-video", resolution="1080p", reference_image_urls=["link"]) is True

    print("OK 全部通过 / all checks passed")
    print("  submit url :", STATE["post_url"])
    print("  payload    :", sorted(payload.keys()))
    print("  output node:", nodes.VergoraVideoGenerator.OUTPUT_NODE)
    print("  preview    :", item)
    print("  saved file :", path, os.path.getsize(path), "bytes")


if __name__ == "__main__":
    main()
