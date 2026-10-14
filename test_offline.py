# -*- coding: utf-8 -*-
"""离线自测：把 HTTP 层换成假的，跑通「提交 -> 轮询 -> 下载 -> 返回预览结果」。
不联网、不花钱、不需要 requests。"""

import os
import sys
import tempfile

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import nodes  # noqa: E402

STATE = {"polls": 0, "post_url": None, "payload": None, "headers": None, "download_url": None,
         "get_headers": None, "fail_first": 0}
OUT_DIR = tempfile.mkdtemp(prefix="vergora_test_")


class FakeFolderPaths:
    """假装是 ComfyUI 的 folder_paths，让 output 目录指到临时目录。"""

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


def call(node, *args, **kwargs):
    """节点返回 dict（ComfyUI 风格）或元组，这里统一取出结果。"""
    out = node.run(*args, **kwargs)
    if isinstance(out, dict):
        return out, out.get("result")
    return None, out


def main():
    nodes.http_post_json = fake_post_json
    nodes.http_get_json = fake_get_json
    nodes.http_download = fake_download
    nodes.folder_paths = FakeFolderPaths()
    nodes.load_config = lambda: {
        "base_url": "https://api.vergora.ai/v1",
        "api_key": "sk-test",
        "poll_interval": 0.01,
        "timeout": 30,
        "output_dir": "",  # 留空 -> 用 folder_paths 的 output 目录
    }

    node = nodes.VergoraVideoGenerator()
    image = np.zeros((64, 96, 3), dtype=np.float32)
    image[:, :, 0] = 1.0

    out, result = call(node, "a red paper boat on a calm lake", "w3.0-video", 5, -1,
                       first_frame=image, last_frame=image)
    path, task_id, video_url = result
    assert STATE["post_url"] == "https://api.vergora.ai/v1/videos", STATE["post_url"]
    assert STATE["headers"]["Authorization"] == "Bearer sk-test", STATE["headers"]
    assert STATE["headers"]["Content-Type"] == "application/json", STATE["headers"]
    # 轮询必须不带 Content-Type，否则网关会把空 body 当 JSON 解析并返回 400
    assert "Content-Type" not in STATE["get_headers"], STATE["get_headers"]
    assert STATE["get_headers"]["Authorization"] == "Bearer sk-test", STATE["get_headers"]
    payload = STATE["payload"]
    assert payload["model"] == "w3.0-video" and payload["duration"] == 5, payload
    assert payload["prompt"].startswith("a red paper boat")
    assert payload["first_frame"].startswith("data:image/png;base64,")
    assert payload["last_frame"].startswith("data:image/png;base64,")
    assert "seed" not in payload, payload
    assert task_id == "test-task-1234" and video_url.endswith("result_00.mp4")
    assert STATE["polls"] == 3, STATE["polls"]
    assert os.path.exists(path) and os.path.getsize(path) > 0, path

    # 关键点：必须被当成输出节点，并且带上前端能识别的视频预览结果
    assert nodes.VergoraVideoGenerator.OUTPUT_NODE is True
    assert out is not None and "ui" in out, out
    item = out["ui"]["images"][0]
    assert item["filename"] == os.path.basename(path), item
    assert item["subfolder"] == "", item
    assert item["type"] == "output" and item["format"] == "video/mp4", item
    assert out["ui"]["animated"] == (True,), out["ui"]

    # 文生视频：只给 prompt
    STATE["polls"] = 0
    _, result2 = call(node, "just text", "minimax-h3", 3, 42)
    assert "first_frame" not in STATE["payload"] and "last_frame" not in STATE["payload"]
    assert STATE["payload"]["seed"] == 42
    assert os.path.exists(result2[0])

    # extra_json 透传
    STATE["polls"] = 0
    call(node, "hi", "w3.0-video", 2, -1, extra_json='{"resolution": "720p"}')
    assert STATE["payload"]["resolution"] == "720p", STATE["payload"]

    # 网关偶发 400 要能自愈，不能把整个任务拖死
    STATE["polls"] = 0
    STATE["fail_first"] = 2
    out_r, result_r = call(node, "retry me", "w3.0-video", 2, -1)
    assert os.path.exists(result_r[0]), result_r

    # 空输入报错
    try:
        call(node, "", "w3.0-video", 5, -1)
    except RuntimeError as exc:
        assert "至少要给" in str(exc), exc
    else:
        raise AssertionError("空输入应当报错")

    # 平台报错要原样抛出
    nodes.http_get_json = fake_get_failure
    try:
        cfg = nodes.load_config()
        nodes.submit_task(cfg, {"model": "w3.0-video", "prompt": "x"})
        nodes.wait_for_video(cfg, "test-task-1234")
    except RuntimeError as exc:
        assert "503" in str(exc) and "No available channel" in str(exc), exc
    else:
        raise AssertionError("上游报错应当抛出")

    print("OK 全部通过")
    print("  提交 URL   :", STATE["post_url"])
    print("  提交体字段 :", sorted(STATE["payload"].keys()))
    print("  输出节点   :", nodes.VergoraVideoGenerator.OUTPUT_NODE)
    print("  预览条目   :", item)
    print("  落盘文件   :", path, os.path.getsize(path), "bytes")


if __name__ == "__main__":
    main()
