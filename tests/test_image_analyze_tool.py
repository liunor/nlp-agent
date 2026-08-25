"""Service assembly and LangChain wrapper tests for ``image_analyze``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from core.session_context import SessionContext
from core.tool_runtime import ToolGrantRequest, ToolRuntime, ToolScope
from server.tools.api import image_analyze_tool as tool_module
from server.tools.vision.contracts import (
    UNTRUSTED_IMAGE_BANNER,
    BoundingBox,
    ImageAnalyzeInput,
    ImageCitation,
    OCRBlock,
    OCRResult,
    OCRTableCell,
    TableResult,
    VisionModelResult,
    VisionErrorCode,
    VisionSignals,
)
from server.tools.vision.input_resolver import ImageInputResolver
from server.tools.vision.providers import UnavailableOCRProvider
from server.tools.vision.service import ImageAnalyzeService, build_image_analyze_service
from server.tools.tool_manager import register_builtin_tools


class MockOCRProvider:
    id = "mock-ocr"

    def __init__(self, text: str = "识别文字") -> None:
        self.text = text
        self.calls = 0

    async def extract(self, image, *, language):
        self.calls += 1
        return OCRResult(
            text=self.text,
            language=language,
            confidence=0.91,
            blocks=[
                OCRBlock(
                    block_id="block-1",
                    text=self.text,
                    bbox=BoundingBox(x=0, y=0, width=60, height=20),
                    confidence=0.91,
                    language=language,
                )
            ],
        )


class MockVLMProvider:
    id = "mock-vlm"

    def __init__(self, *, summary: str = "一张测试图片") -> None:
        self.summary = summary
        self.calls: list[dict] = []

    async def analyze(
        self,
        image,
        *,
        task,
        question,
        language,
        ocr_context,
    ):
        self.calls.append(
            {
                "task": task,
                "question": question,
                "language": language,
                "ocr_context": ocr_context,
            }
        )
        table = None
        if task == "table":
            table = TableResult(
                markdown="| A |\n|---|\n| 1 |",
                cells=[
                    OCRTableCell(
                        cell_id="cell-1",
                        row=0,
                        column=0,
                        text="A",
                        bbox=BoundingBox(x=0, y=0, width=20, height=20),
                        confidence=0.9,
                    )
                ],
                confidence=0.9,
            )
        return VisionModelResult(
            summary=self.summary,
            markdown=f"## 结果\n\n{self.summary}",
            table=table,
            confidence=0.8,
        )


class StaticSignalProvider:
    id = "static-signals"

    def __init__(self, signals: VisionSignals) -> None:
        self.signals = signals

    async def detect(self, image):
        return self.signals


class UnsafeCitationVLMProvider(MockVLMProvider):
    async def analyze(self, *args, **kwargs):
        result = await super().analyze(*args, **kwargs)
        return result.model_copy(
            update={
                "citations": [
                    ImageCitation(file_name="C:/private/uploads/secret.png")
                ]
            }
        )


def _image(tmp_path: Path) -> tuple[Path, ImageInputResolver]:
    uploads = tmp_path / ".data" / "uploads"
    uploads.mkdir(parents=True)
    path = uploads / "input.png"
    Image.new("RGB", (96, 64), color=(30, 40, 50)).save(path, format="PNG")
    resolver = ImageInputResolver(uploads_root=uploads, project_root=tmp_path)
    return path, resolver


def test_question_task_requires_nonempty_question() -> None:
    with pytest.raises(ValidationError):
        ImageAnalyzeInput(image=".data/uploads/input.png", task="question")


def test_default_service_scopes_upload_root_to_full_session_identity() -> None:
    context = SessionContext(
        session_id="session-1",
        user_id="learner-1",
        workspace_id="class-1",
    )

    service = build_image_analyze_service(context=context)

    assert service.resolver.uploads_root.parts[-5:] == (
        ".data",
        "uploads",
        "class-1",
        "learner-1",
        "session-1",
    )


async def test_service_assembles_ocr_with_banner_confidence_and_safe_reference(
    tmp_path: Path,
) -> None:
    path, resolver = _image(tmp_path)
    ocr = MockOCRProvider()
    service = ImageAnalyzeService(resolver=resolver, ocr_provider=ocr)

    response = await service.analyze(
        ImageAnalyzeInput(image=str(path), task="ocr", language="zh")
    )

    assert response.route == "ocr"
    assert response.task_executed == "ocr"
    assert response.summary.startswith(UNTRUSTED_IMAGE_BANNER)
    assert response.markdown and response.markdown.startswith(UNTRUSTED_IMAGE_BANNER)
    assert response.ocr and response.ocr.text == "识别文字"
    assert response.confidence.ocr == 0.91
    assert response.confidence.semantic is None
    assert response.citations[0].file_name == "input.png"
    assert str(path.parent) not in response.model_dump_json()
    assert response.untrusted is True
    assert ocr.calls == 1


async def test_service_sends_question_only_to_vlm(tmp_path: Path) -> None:
    path, resolver = _image(tmp_path)
    ocr = MockOCRProvider()
    vlm = MockVLMProvider(summary="图片里有一个蓝色方块")
    service = ImageAnalyzeService(
        resolver=resolver,
        ocr_provider=ocr,
        vlm_provider=vlm,
    )

    response = await service.analyze(
        ImageAnalyzeInput(
            image=str(path), task="question", question="图里有什么？"
        )
    )

    assert response.route == "vlm"
    assert response.task_executed == "question"
    assert ocr.calls == 0
    assert vlm.calls[0]["question"] == "图里有什么？"
    assert vlm.calls[0]["ocr_context"] is None
    assert response.confidence.semantic == 0.8


async def test_service_replaces_provider_paths_with_safe_file_name(
    tmp_path: Path,
) -> None:
    path, resolver = _image(tmp_path)
    service = ImageAnalyzeService(
        resolver=resolver,
        vlm_provider=UnsafeCitationVLMProvider(),
    )

    response = await service.analyze(
        ImageAnalyzeInput(image=str(path), task="describe")
    )

    assert response.citations[0].file_name == "input.png"
    assert "C:/private" not in response.model_dump_json()


async def test_fusion_preserves_raw_ocr_and_passes_it_to_vlm(tmp_path: Path) -> None:
    path, resolver = _image(tmp_path)
    ocr = MockOCRProvider(text="A 1")
    vlm = MockVLMProvider(summary="一个简单表格")
    service = ImageAnalyzeService(
        resolver=resolver,
        ocr_provider=ocr,
        vlm_provider=vlm,
    )

    response = await service.analyze(
        ImageAnalyzeInput(image=str(path), task="table")
    )

    assert response.route == "fusion"
    assert response.ocr and response.ocr.text == "A 1"
    assert vlm.calls[0]["ocr_context"] is response.ocr
    assert response.table and response.table.markdown.startswith("| A |")
    assert response.confidence.ocr == 0.91
    assert response.confidence.semantic == 0.8


async def test_auto_uses_injected_signals_for_route(tmp_path: Path) -> None:
    path, resolver = _image(tmp_path)
    ocr = MockOCRProvider()
    service = ImageAnalyzeService(
        resolver=resolver,
        ocr_provider=ocr,
        signal_provider=StaticSignalProvider(VisionSignals(text_coverage=0.4)),
    )

    response = await service.analyze(ImageAnalyzeInput(image=str(path), task="auto"))

    assert (response.task_executed, response.route) == ("ocr", "ocr")
    assert ocr.calls == 1


async def test_service_truncates_text_at_effective_limit(tmp_path: Path) -> None:
    path, resolver = _image(tmp_path)
    service = ImageAnalyzeService(
        resolver=resolver,
        ocr_provider=MockOCRProvider(text="x" * 2_000),
        result_max_chars=800,
    )

    response = await service.analyze(
        ImageAnalyzeInput(image=str(path), task="ocr", max_chars=500)
    )

    assert response.truncated is True
    assert len(response.summary) == 500
    assert response.markdown and len(response.markdown) == 500
    assert response.summary.endswith("[输出已截断]")
    assert response.warnings == ["文本输出超过 500 字符，已截断"]


async def test_json_output_omits_rendered_markdown(tmp_path: Path) -> None:
    path, resolver = _image(tmp_path)
    service = ImageAnalyzeService(
        resolver=resolver,
        ocr_provider=MockOCRProvider(),
    )

    response = await service.analyze(
        ImageAnalyzeInput(image=str(path), task="ocr", output_format="json")
    )

    assert response.markdown is None
    assert response.ocr is not None
    assert response.summary.startswith(UNTRUSTED_IMAGE_BANNER)


async def test_tool_wrapper_returns_success_as_json(tmp_path: Path, monkeypatch) -> None:
    path, resolver = _image(tmp_path)
    service = ImageAnalyzeService(
        resolver=resolver,
        ocr_provider=MockOCRProvider(),
    )
    contexts = []
    monkeypatch.setattr(
        tool_module,
        "build_image_analyze_service",
        lambda *, context: contexts.append(context) or service,
    )

    output = await tool_module.image_analyze.ainvoke(
        {"image": str(path), "task": "ocr"},
        config={
            "configurable": {
                "thread_id": "session-1",
                "user_id": "learner-1",
                "workspace_id": "class-1",
                "worker_id": "vision-worker-1",
            }
        },
    )
    payload = json.loads(output)

    assert payload["route"] == "ocr"
    assert payload["input"]["file_name"] == "input.png"
    assert payload["untrusted"] is True
    assert contexts[0].session_id == "session-1"
    assert contexts[0].user_id == "learner-1"
    assert contexts[0].workspace_id == "class-1"


async def test_tool_wrapper_returns_stable_provider_error(
    tmp_path: Path, monkeypatch
) -> None:
    path, resolver = _image(tmp_path)
    service = ImageAnalyzeService(
        resolver=resolver,
        ocr_provider=UnavailableOCRProvider(),
    )
    monkeypatch.setattr(
        tool_module, "build_image_analyze_service", lambda *, context: service
    )

    output = await tool_module.image_analyze.ainvoke(
        {"image": str(path), "task": "ocr"},
        config={"configurable": {"thread_id": "session-1"}},
    )
    payload = json.loads(output)

    assert payload["code"] == "provider_unavailable"
    assert "OCR provider" in payload["error"]


async def test_tool_wrapper_returns_stable_remote_url_error(
    tmp_path: Path, monkeypatch
) -> None:
    _, resolver = _image(tmp_path)
    service = ImageAnalyzeService(resolver=resolver)
    monkeypatch.setattr(
        tool_module, "build_image_analyze_service", lambda *, context: service
    )

    output = await tool_module.image_analyze.ainvoke(
        {"image": "https://example.com/image.png", "task": "ocr"},
        config={"configurable": {"thread_id": "session-1"}},
    )
    payload = json.loads(output)

    assert payload["code"] == "remote_url_disabled"


async def test_tool_wrapper_fails_closed_without_session_context() -> None:
    output = await tool_module.image_analyze.ainvoke(
        {"image": ".data/uploads/input.png", "task": "ocr"}
    )
    payload = json.loads(output)

    assert payload["code"] == VisionErrorCode.SESSION_CONTEXT_REQUIRED.value
    assert ".data" not in payload["error"]


async def test_unified_runtime_preserves_image_error_code(
    tmp_path: Path, monkeypatch
) -> None:
    path, resolver = _image(tmp_path)
    service = ImageAnalyzeService(
        resolver=resolver,
        ocr_provider=UnavailableOCRProvider(),
    )
    monkeypatch.setattr(
        tool_module, "build_image_analyze_service", lambda *, context: service
    )
    runtime = ToolRuntime()
    register_builtin_tools(runtime.catalog)
    toolset = runtime.build_toolset(
        ToolGrantRequest(
            role=ToolScope.WORKER,
            session_id="session-1",
            allowed_tools={"image_analyze"},
        )
    )

    result = await toolset.execute(
        "image_analyze",
        {"image": str(path), "task": "ocr"},
        config={"configurable": {"thread_id": "session-1"}},
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.kind == "tool_error"
    assert result.error.code == VisionErrorCode.PROVIDER_UNAVAILABLE.value
    assert "OCR provider" in result.error.message
