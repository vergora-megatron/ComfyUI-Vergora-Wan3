# -*- coding: utf-8 -*-
"""Offline self-test against the Alibaba Cloud Model Studio Wan 3.0 contract.

Run with any Python that has numpy:
    python test_offline.py

No network, API key or paid request is used.
"""

import base64
import os
import sys
import tempfile

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import nodes  # noqa: E402

BASE_URL = "https://ws-test.ap-southeast-1.maas.aliyuncs.com"
SYNTHESIS_URL = BASE_URL + "/api/v1/services/aigc/video-generation/video-synthesis"
TASK_URL = BASE_URL + "/api/v1/tasks/test-task-1234"

STATE = {
    "polls": 0,
    "post_url": None,
    "payload": None,
    "headers": None,
    "get_headers": None,
    "fail_first": 0,
    "download_url": None,
}
OUT_DIR = tempfile.mkdtemp(prefix="wan3_test_")


class FakeFolderPaths:
    @staticmethod
    def get_output_directory():
        return OUT_DIR


def fake_post_json(url, headers, payload, timeout=120):
    STATE["post_url"] = url
    STATE["payload"] = payload
    STATE["headers"] = headers
    return 200, (
        '{"request_id":"req-test",'
        '"output":{"task_id":"test-task-1234","task_status":"PENDING"}}'
    )


def fake_get_json(url, headers, timeout=60):
    STATE["get_headers"] = headers
    STATE["polls"] += 1
    if STATE["fail_first"] > 0:
        STATE["fail_first"] -= 1
        return 429, '{"code":"Throttling","message":"request rate limited","request_id":"req-test"}'
    if STATE["polls"] < 3:
        return 200, '{"output":{"task_id":"test-task-1234","task_status":"RUNNING"}}'
    return 200, (
        '{"request_id":"req-test","output":{"task_id":"test-task-1234",'
        '"task_status":"SUCCEEDED","video_url":"https://example.invalid/result.mp4"}}'
    )


def fake_download(url, dest_path, timeout=600):
    STATE["download_url"] = url
    STATE["downloads"] = STATE.get("downloads", 0) + 1
    if STATE.get("short_downloads"):
        STATE["short_downloads"] -= 1
        with open(dest_path, "wb") as handle:
            handle.write(b"")
        return
    with open(dest_path, "wb") as handle:
        handle.write(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 9000)


def prepare():
    nodes.http_post_json = fake_post_json
    nodes.http_get_json = fake_get_json
    nodes.http_download = fake_download
    nodes.folder_paths = FakeFolderPaths()
    nodes.load_config = lambda: {
        "base_url": BASE_URL,
        "workspace_id": "ws-test",
        "region": "ap-southeast-1",
        "api_key": "sk-test",
        "poll_interval": 0.01,
        "timeout": 30,
        "output_dir": "",
    }
    return nodes.VergoraVideoGenerator()


def call(node, **kwargs):
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


def media_types(payload):
    return [item["type"] for item in payload["input"].get("media", [])]


def main():
    node = prepare()

    required = nodes.VergoraVideoGenerator.INPUT_TYPES()["required"]
    optional = nodes.VergoraVideoGenerator.INPUT_TYPES()["optional"]
    assert nodes.VIDEO_MODELS == ["wan3.0-video", "wan3.0-video-prime"]
    assert required["model"][1]["default"] == "wan3.0-video"
    assert required["prompt_extend"][1]["default"] is False
    assert required["audio"][1]["default"] is True
    assert required["seed"][1]["default"] == -1
    assert optional["watermark"][1]["default"] is False
    assert "21:9" in nodes.RATIOS

    # Both official models share the same parameter contract.
    standard = nodes.build_payload(
        "same input", "wan3.0-video", 5, 7, "720p", "16:9", True, True,
        watermark=False,
    )
    prime = nodes.build_payload(
        "same input", "wan3.0-video-prime", 5, 7, "720p", "16:9", True, True,
        watermark=False,
    )
    assert standard["parameters"] == prime["parameters"], (standard, prime)
    assert standard["input"] == prime["input"], (standard, prime)
    assert (standard["model"], prime["model"]) == ("wan3.0-video", "wan3.0-video-prime")

    # ---- legacy gateway configuration is rejected before any request ----
    try:
        nodes.resolve_base_url({
            "base_url": "",
            "workspace_id": "",
            "region": "ap-southeast-1",
            "legacy_base_url": "https://api.vergora.ai/v1",
        })
    except RuntimeError as exc:
        assert "旧版 Vergora" in str(exc) and "WORKSPACE_ID" in str(exc), exc
    else:
        raise AssertionError("a legacy Vergora BASE_URL should be rejected")

    image = np.zeros((64, 96, 3), dtype=np.float32)
    image[:, :, 0] = 1.0
    batch = np.zeros((2, 64, 96, 3), dtype=np.float32)
    batch[1, :, :, 1] = 1.0

    # ---- keyframes, official envelope and async headers ----
    out, result = call(
        node,
        prompt="a red paper boat on a calm lake",
        model="wan3.0-video",
        resolution="720p",
        ratio="16:9",
        duration=5,
        audio=False,
        prompt_extend=False,
        seed=42,
        first_frame=image,
        last_frame=image,
        watermark=True,
    )
    path, task_id, video_url = result
    payload = STATE["payload"]
    assert STATE["post_url"] == SYNTHESIS_URL, STATE["post_url"]
    assert STATE["headers"]["Authorization"] == "Bearer sk-test", STATE["headers"]
    assert STATE["headers"]["Content-Type"] == "application/json", STATE["headers"]
    assert STATE["headers"]["X-DashScope-Async"] == "enable", STATE["headers"]
    assert STATE["headers"]["X-DashScope-DataInspection"] == (
        '{"input":"disable","output":"disable"}'
    ), STATE["headers"]
    assert "Content-Type" not in STATE["get_headers"], STATE["get_headers"]
    assert STATE["get_headers"]["X-DashScope-DataInspection"] == (
        '{"input":"disable","output":"disable"}'
    ), STATE["get_headers"]

    assert payload["model"] == "wan3.0-video", payload
    assert payload["input"]["prompt"] == "a red paper boat on a calm lake", payload
    assert "prompt" not in payload and "duration" not in payload, payload
    parameters = payload["parameters"]
    assert parameters["resolution"] == "720P", parameters
    assert parameters["ratio"] == "16:9" and parameters["duration"] == 5, parameters
    assert parameters["audio"] is False and parameters["prompt_extend"] is False, parameters
    assert parameters["seed"] == 42 and parameters["watermark"] is True, parameters
    assert media_types(payload) == ["first_frame", "last_frame"], payload
    assert all(item["url"].startswith("data:image/png;base64,") for item in payload["input"]["media"])
    assert task_id == "test-task-1234" and video_url.endswith("result.mp4")
    assert STATE["polls"] == 3, STATE["polls"]
    assert STATE["download_url"] == video_url
    assert os.path.exists(path) and os.path.getsize(path) > 0, path

    # output-node preview contract
    assert nodes.VergoraVideoGenerator.OUTPUT_NODE is True
    assert out is not None and "ui" in out, out
    item = out["ui"]["images"][0]
    assert item == {
        "filename": os.path.basename(path),
        "subfolder": "",
        "type": "output",
        "format": "video/mp4",
    }, item
    assert out["ui"]["animated"] == (True,), out["ui"]

    # ---- text to video on prime, 21:9 and smart duration ----
    STATE["polls"] = 0
    _, result2 = call(
        node,
        prompt="just text",
        model="wan3.0-video-prime",
        resolution="1080p",
        ratio="21:9",
        duration=-1,
        audio=True,
        prompt_extend=True,
        seed=-1,
    )
    p2 = STATE["payload"]
    assert p2["model"] == "wan3.0-video-prime", p2
    assert "media" not in p2["input"], p2
    assert p2["parameters"]["resolution"] == "1080P", p2
    assert p2["parameters"]["ratio"] == "21:9", p2
    assert p2["parameters"]["duration"] == -1, p2
    assert "seed" not in p2["parameters"], p2
    assert os.path.exists(result2[0])

    # ---- reference mode: media array order and types ----
    STATE["polls"] = 0
    call(
        node,
        prompt="Image 1 holds Image 2",
        model="wan3.0-video",
        resolution="720p",
        ratio="adaptive",
        duration=10,
        audio=True,
        prompt_extend=True,
        seed=-1,
        reference_images=batch,
        reference_image_urls="https://example.com/a.png\n# comment\nhttps://example.com/b.png\n",
        reference_video_urls="https://example.com/role.mp4",
        reference_audio_urls="https://example.com/song.mp3",
    )
    p3 = STATE["payload"]
    assert media_types(p3) == [
        "reference_image",
        "reference_image",
        "reference_image",
        "reference_image",
        "reference_video",
        "reference_audio",
    ], p3["input"]["media"]
    assert p3["input"]["media"][0]["url"].startswith("data:image/png;base64,")
    assert p3["input"]["media"][2]["url"] == "https://example.com/a.png"
    assert p3["input"]["media"][-2]["url"] == "https://example.com/role.mp4"
    assert p3["input"]["media"][-1]["url"] == "https://example.com/song.mp3"

    # ---- extra_json: plain keys go to parameters; nested objects target sections ----
    STATE["polls"] = 0
    call(
        node,
        prompt="hi",
        model="wan3.0-video",
        resolution="480p",
        ratio="9:16",
        duration=2,
        audio=True,
        prompt_extend=True,
        seed=-1,
        extra_json='{"vendor_flag":true,"parameters":{"watermark":true},'
                   '"input":{"prompt":"overridden"}}',
    )
    p4 = STATE["payload"]
    assert p4["parameters"]["vendor_flag"] is True, p4
    assert p4["parameters"]["watermark"] is True, p4
    assert p4["input"]["prompt"] == "overridden", p4

    # ---- transient polling errors heal themselves ----
    STATE["polls"] = 0
    STATE["fail_first"] = 2
    _, result_r = call(
        node,
        prompt="retry me",
        model="wan3.0-video",
        resolution="1080p",
        ratio="adaptive",
        duration=5,
        audio=True,
        prompt_extend=True,
        seed=-1,
    )
    assert os.path.exists(result_r[0]), result_r

    # ---- stalled downloads are retried ----
    STATE["polls"] = 0
    STATE["short_downloads"] = 1
    _, result_dl = call(
        node,
        prompt="retry download",
        model="wan3.0-video",
        resolution="480p",
        ratio="adaptive",
        duration=5,
        audio=True,
        prompt_extend=True,
        seed=-1,
    )
    assert os.path.getsize(result_dl[0]) > 8192, result_dl
    assert not os.path.exists(result_dl[0] + ".part")

    # ---- local contract validation happens before submit ----
    base = dict(
        model="wan3.0-video",
        resolution="1080p",
        ratio="adaptive",
        duration=5,
        audio=True,
        prompt_extend=True,
        seed=-1,
    )
    expect_error(dict(base, prompt=""), "至少要给", node)
    expect_error(
        dict(base, prompt="x", first_frame=image,
             reference_video_urls="https://example.com/v.mp4"),
        "互斥",
        node,
    )
    expect_error(
        dict(base, prompt="x", file_url="https://example.com/a.pdf",
             link_url="https://example.com/page"),
        "不能同时",
        node,
    )
    expect_error(
        dict(base, prompt="x", prompt_extend=False,
             file_url="https://example.com/a.pdf"),
        "prompt_extend",
        node,
    )
    expect_error(
        dict(base, prompt="x",
             reference_image_urls="\n".join("https://e.com/%d.png" % i for i in range(11))),
        "最多",
        node,
    )
    expect_error(dict(base, prompt="x", duration=45), "duration", node)
    expect_error(dict(base, prompt="x", extra_json="{not json"), "JSON", node)
    expect_error(dict(base, prompt="x", model="unknown-model"), "不支持的模型", node)
    expect_error(dict(base, prompt="x", ratio="2:1"), "不支持的 ratio", node)

    # ---- Model Studio task failures expose output code/message ----
    def failed_task(url, headers, timeout=60):
        return 200, (
            '{"request_id":"req-fail","output":{"task_id":"test-task-1234",'
            '"task_status":"FAILED","code":"InvalidParameter",'
            '"message":"Failed to download file: HTTP 502"}}'
        )

    nodes.http_get_json = failed_task
    try:
        nodes.wait_for_video(nodes.load_config(), "test-task-1234")
    except RuntimeError as exc:
        assert "InvalidParameter" in str(exc) or "素材" in str(exc), exc
    else:
        raise AssertionError("a failed task should raise")
    nodes.http_get_json = fake_get_json

    # ---- known HTTP failures get actionable hints ----
    nodes.http_post_json = lambda url, headers, payload, timeout=120: (
        403,
        '{"code":"AccessDenied","message":"user quota is not enough","request_id":"req-test"}',
    )
    try:
        nodes.submit_task(nodes.load_config(), {"model": "wan3.0-video", "input": {}})
    except RuntimeError as exc:
        assert "额度" in str(exc), exc
    else:
        raise AssertionError("a quota error should raise")

    nodes.http_post_json = lambda url, headers, payload, timeout=120: (
        400,
        '{"code":"InvalidParameter","message":"ratio must be one of adaptive, 21:9, 16:9, '
        '4:3, 1:1, 3:4, 9:16","request_id":"req-test"}',
    )
    try:
        nodes.submit_task(nodes.load_config(), {"model": "wan3.0-video", "input": {}})
    except RuntimeError as exc:
        assert "21:9" in str(exc), exc
    else:
        raise AssertionError("a rejected parameter should raise")

    hint = nodes.explain_http_error(404, '{"code":"InvalidEndpoint","message":"not found"}')
    assert "WORKSPACE_ID" in hint and "REGION" in hint, hint

    # ---- upstream progress drives the canvas bar ----
    class FakeProgress:
        def __init__(self):
            self.values = []

        def update_absolute(self, value, total=100):
            self.values.append(value)

    def poll_with_progress(url, headers, timeout=60):
        STATE["polls"] += 1
        if STATE["polls"] < 2:
            return 200, (
                '{"output":{"task_id":"test-task-1234","task_status":"RUNNING",'
                '"progress":42,"message":"encoding"}}'
            )
        return 200, (
            '{"output":{"task_id":"test-task-1234","task_status":"SUCCEEDED",'
            '"video_url":"https://e.invalid/r.mp4"}}'
        )

    nodes.http_get_json = poll_with_progress
    STATE["polls"] = 0
    bar = FakeProgress()
    url = nodes.wait_for_video(nodes.load_config(), "test-task-1234", bar)
    assert url.endswith("r.mp4"), url
    assert 42 in bar.values, bar.values

    # ---- local images are only compressed when they exceed the budget ----
    noisy = np.random.RandomState(0).rand(1024, 1024, 3).astype(np.float32)
    uri = nodes.tensor_to_data_uri(noisy, max_bytes=200 * 1024)
    raw = base64.b64decode(uri.split(",", 1)[1])
    assert len(raw) <= 200 * 1024, len(raw)
    assert uri.startswith("data:image/"), uri[:30]

    # ---- a dropped TLS connection is not retried silently ----
    calls = {"n": 0}

    def mock_post_json(url, headers, payload, timeout=120):
        calls["n"] += 1
        raise RuntimeError("SSLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING]'))")

    nodes.http_post_json = mock_post_json
    try:
        nodes.submit_task(nodes.load_config(), {"model": "wan3.0-video", "input": {}})
    except RuntimeError as exc:
        assert "网络中断" in str(exc) and "没有自动重试" in str(exc), exc
    else:
        raise AssertionError("a network error should raise")
    assert calls["n"] == 1, "a submit must not be retried automatically"

    print("OK 全部通过 / all checks passed")
    print("  submit url :", STATE["post_url"])
    print("  payload    :", sorted(payload.keys()))
    print("  output node:", nodes.VergoraVideoGenerator.OUTPUT_NODE)
    print("  preview    :", item)
    print("  saved file :", path, os.path.getsize(path), "bytes")


if __name__ == "__main__":
    main()
