"""Deterministic teacher-analysis model used only by the HTTP test server."""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage


class DeterministicTeacherAiModel:
    """Return success, timeout, and error outcomes in a fixed sequence."""

    model_name = "api-http-deterministic-stub"

    def __init__(self) -> None:
        self._call_index = 0

    async def ainvoke(self, messages: list[object]) -> AIMessage:
        modes = ("success", "timeout", "error")
        mode = modes[min(self._call_index, len(modes) - 1)]
        self._call_index += 1
        if mode == "timeout":
            raise TimeoutError("deterministic teacher AI timeout")
        if mode == "error":
            raise RuntimeError("deterministic teacher AI provider error")

        prompt = str(getattr(messages[-1], "content", ""))
        material = json.loads(prompt.split("数据：", 1)[1])
        source = next(
            item
            for item in material["contents"]
            if item.get("data_sufficiency") == "sufficient"
        )
        response = {
            "summary": "Deterministic Phase 4 analysis",
            "diagnoses": [
                {
                    "knowledge_point_id": source["knowledge_point_id"],
                    "level": "medium",
                    "problem": "Synthetic evidence indicates a practice gap.",
                    "cause": "Synthetic deterministic evidence.",
                    "evidence": ["Synthetic evidence from isolated MySQL."],
                    "suggestions": ["Review the knowledge point and retry."],
                    "confidence": "medium",
                    "data_gaps": [],
                    "error_type": "方法不熟",
                }
            ],
        }
        return AIMessage(content=json.dumps(response, ensure_ascii=False))
