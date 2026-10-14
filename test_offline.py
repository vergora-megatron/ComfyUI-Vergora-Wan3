# -*- coding: utf-8 -*-
"""Offline self-test: run the whole job flow (submit -> poll -> download -> preview)
against a stub HTTP layer. No network, no API key, no cost.

Run it with any Python that has numpy:  python test_offline.py
"""

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
    with open(dest_path, "wb") as handle:
        handle.write(b"\x00\x00\x00\x18ftypmp42 fake-mp4-bytes")


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

    # ---- contract validation, caught before anything is billed ----
    base = dict(model="w3.0-video", resolution="1080p", ratio="adaptive",
                duration=5, audio=True, prompt_extend=True, seed=-1)
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

    failed = {"task_info": {"status": "failed", "error": {
        "code": 4002, "title": "Infrastructure operation failed",
        "detail": "Failed to download file: HTTP 502"}}}
    message = nodes.explain_task_failure(failed, "")
    assert "素材" in message and "Base64" in message, message

    print("OK 全部通过 / all checks passed")
    print("  submit url :", STATE["post_url"])
    print("  payload    :", sorted(payload.keys()))
    print("  output node:", nodes.VergoraVideoGenerator.OUTPUT_NODE)
    print("  preview    :", item)
    print("  saved file :", path, os.path.getsize(path), "bytes")


if __name__ == "__main__":
    main()
