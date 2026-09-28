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
from server.tools.vision.config import get_vision_config


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

    # Keep quota/model-runtime initialization outside the tool registration
    # import path. Sandbox contract discovery imports this module without a DB.
    from core.model_runtime.usage import BillableFeatureUsage
    from server.quota.reporting import (
        begin_billable_tool_usage,
        cancel_billable_tool_usage,
        complete_billable_tool_usage,
    )

    request = ImageAnalyzeInput(
        image=image,
        task=task,
        question=question,
        output_format=output_format,
        language=language,
        max_chars=max_chars,
    )
    billing_invocation = None

    async def reserve_ocr_usage(asset, route: str, task_executed: str) -> None:
        nonlocal billing_invocation
        if route != "ocr":
            return
        reference = asset.reference
        image_units = get_vision_config().vlm.fallback_image_units(
            width=reference.width,
            height=reference.height,
            task=task_executed,
        )
        billing_invocation = await begin_billable_tool_usage(
            tool_name="image_analyze",
            feature_usage=BillableFeatureUsage(image_units=image_units),
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
        response = await service.analyze(
            request,
            before_provider=reserve_ocr_usage,
        )
    except VisionError as error:
        await cancel_billable_tool_usage(billing_invocation)
        return error.to_response().model_dump_json()
    except BaseException:
        await cancel_billable_tool_usage(billing_invocation)
        raise
    await complete_billable_tool_usage(billing_invocation)
    return response.model_dump_json(exclude_none=True)
