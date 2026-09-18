from __future__ import annotations

import base64
import json
import time

import httpx
import pytest
from pygent.llm import ModelConfig, OpenAICompatibleClient

from lora.runtime import model_configuration
from lora.runtime.service import LoraRuntimeService
from lora.sessions import SessionManager
from tests.unit.test_model_configuration import native_runtime_config


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["image", "large_image", "video", "text"])
async def test_default_read_reaches_provider_with_native_content(
    tmp_path, monkeypatch, kind,
):
    from PIL import Image
    import av

    if kind in ("image", "large_image"):
        filename, wire_type, mime = "sample.png", "image_url", "image/png"
        size = (32, 32) if kind == "image" else (2600, 1300)
        Image.new("RGB", size, "blue").save(tmp_path / filename)
    elif kind == "video":
        filename, wire_type, mime = "sample.mp4", "video_url", "video/mp4"
        with av.open(str(tmp_path / filename), "w") as container:
            stream = container.add_stream("libx264", rate=5)
            stream.width = stream.height = 64
            stream.pix_fmt = "yuv420p"
            for color in ("red",) * 5 + ("blue",) * 5:
                frame = av.VideoFrame.from_image(Image.new("RGB", (64, 64), color))
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
    else:
        filename, wire_type, mime = "sample.txt", None, None
        (tmp_path / filename).write_text("native text read evidence", encoding="utf-8")
    media = (tmp_path / filename).read_bytes()
    tool_name = "read"
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            names = {item["function"]["name"] for item in payload["tools"]}
            assert "read" in names
            assert not {"read_image", "read_video"} & names
            delta = {"role": "assistant", "tool_calls": [{
                "index": 0, "id": "media-call", "type": "function",
                "function": {"name": tool_name, "arguments": json.dumps({"file_path": filename})},
            }]}
            finish = "tool_calls"
        else:
            delta = {"role": "assistant", "content": "media received"}
            finish = "stop"
        chunk = {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                             text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setitem(model_configuration.CLIENT_FACTORIES, "openai_chat_completions",
                            lambda **kwargs: OpenAICompatibleClient(
                                base_url=kwargs["base_url"], api_key=kwargs["api_key"], client=client))
        config = native_runtime_config(tmp_path, group=("main",))
        config.model_config_mapping["models"]["main"]["capabilities"]["modalities"]["input"] = [
            "text", "image", "video",
        ]
        config.model_config = ModelConfig.from_mapping(config.model_config_mapping)
        manager = SessionManager(config)
        session = manager.create(case_id="media", mode="chat")
        run = manager.start_case_run(session.session_id, "media", run_config=config)
        service = LoraRuntimeService(config)
        try:
            handle = await service.start_turn(
                manager=manager, message=f"Read {filename}", run_ref=run,
                turn_id="media-turn", interactive_approvals=False,
                deadline=time.monotonic() + 60,
            )
            answer, _ = await handle.result()
            assert answer.content == "media received"
        finally:
            manager.finish_case_run(run, "passed")
            await service.close()

    assert len(requests) == 2
    result = next(item for item in requests[1]["messages"] if item["role"] == "tool")
    assert result["tool_call_id"] == "media-call"
    if kind == "text":
        assert isinstance(result["content"], str)
        assert "native text read evidence" in result["content"]
        return
    assert isinstance(result["content"], str)
    assert filename in result["content"]
    media_message = next(
        item
        for item in requests[1]["messages"]
        if item["role"] == "user" and isinstance(item["content"], list)
    )
    block = next(item for item in media_message["content"] if item["type"] == wire_type)
    url = block[wire_type]["url"]
    if kind == "large_image":
        import io
        delivered = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
        assert max(delivered.size) <= 2048
        assert delivered.size[0] == delivered.size[1] * 2
    else:
        assert url == f"data:{mime};base64,{base64.b64encode(media).decode()}"
