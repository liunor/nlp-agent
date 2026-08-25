"""LangChain wrapper exposing image analysis through the unified runtime."""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from core.session_context import SessionContext
from server.tools.vision.contracts import (
    ImageAnalyzeInput,
    ImageLanguage,
    ImageOutputFormat,
    ImageTask,
    VisionError,
    VisionErrorCode,
)
from server.tools.vision.service import build_image_analyze_service


@tool("image_analyze", args_schema=ImageAnalyzeInput)
async def image_analyze(
    image: str,
    config: RunnableConfig,
    task: ImageTask = "auto",
    question: str | None = None,
    output_format: ImageOutputFormat = "markdown",
    language: ImageLanguage = "auto",
    max_chars: int = 20_000,
) -> str:
    """识别或理解受控上传目录中的图片，返回不可信标记的结构化 JSON。"""

    request = ImageAnalyzeInput(
        image=image,
        task=task,
        question=question,
        output_format=output_format,
        language=language,
        max_chars=max_chars,
    )
    try:
        try:
            context = SessionContext.from_config(config, require=True)
        except ValueError:
            raise VisionError(
                VisionErrorCode.SESSION_CONTEXT_REQUIRED,
                "图片分析需要有效的会话上下文",
            ) from None
        service = build_image_analyze_service(context=context)
        response = await service.analyze(request)
    except VisionError as error:
        return error.to_response().model_dump_json()
    return response.model_dump_json(exclude_none=True)
