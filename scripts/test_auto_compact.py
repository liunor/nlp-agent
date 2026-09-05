"""
测试 Auto-Compact (Layer 5)
"""
import os
import sys
import pytest
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uuid
import asyncio
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from server.agent.compression.auto_compact import (
    AUTOCOMPACT_THRESHOLD,
    autocompact_if_needed,
)

import server.agent.compression.auto_compact as ac_module


async def mock_generate_global_summary(msgs):
    return "This is a mock global summary."

def make_human(content: str):
    return HumanMessage(content=content, id=str(uuid.uuid4()))

def make_ai(content: str):
    return AIMessage(content=content, id=str(uuid.uuid4()))

def make_system(content: str):
    return SystemMessage(content=content, id=str(uuid.uuid4()))

def make_tool_ai(file_path: str):
    tc = {"name": "read_local_file", "args": {"file_path": file_path}, "id": "tc123"}
    return AIMessage(content="I read a file", tool_calls=[tc], id=str(uuid.uuid4()))

@pytest.mark.asyncio
async def test_auto_compact_flow(monkeypatch):
    # Keep module-level mocks and circuit state isolated from the rest of the
    # test session. The complete CI suite imports this file before unit tests.
    monkeypatch.setattr(
        ac_module, "_generate_global_summary", mock_generate_global_summary
    )
    monkeypatch.setattr(ac_module, "_consecutive_failures", {})

    # 构造一批消息，刚好超过 167K tokens
    msgs = [make_system("Sys prompt")]
    for i in range(16):
        # 40,000 chars ~ 10K tokens
        msgs.append(make_human(f"H{i}" * 40_000))
        if i == 5:
            msgs.append(make_tool_ai("/important/file.py"))
        else:
            msgs.append(make_ai(f"A{i}" * 10))
            
    print("Testing Auto-Compact...")
    result = await autocompact_if_needed(msgs, threshold=AUTOCOMPACT_THRESHOLD)
    
    assert result.was_compacted is True, "由于消息超过 167K 且未折叠，应当触发全量压缩"
    assert len(result.messages) < len(msgs), "压缩后消息总数应减少"
    
    has_summary = any(
        (getattr(m, "additional_kwargs", {}) or {}).get("compression_kind")
        == "auto_compact"
        for m in result.messages
    )
    assert has_summary, "应包含内部全局摘要"
    
    has_restoration = any("/important/file.py" in str(m.content) for m in result.messages)
    assert has_restoration, "应包含后置恢复的文件上下文"
    
    # 模拟失败熔断
    async def failing_summary(msgs):
        raise Exception("Mock failure")
    monkeypatch.setattr(ac_module, "_generate_global_summary", failing_summary)
    
    for _ in range(3):
        res = await autocompact_if_needed(msgs, threshold=AUTOCOMPACT_THRESHOLD)
        assert res.was_compacted is False
        
    # 第 4 次应触发 circuit breaker
    res_breaker = await autocompact_if_needed(msgs, threshold=AUTOCOMPACT_THRESHOLD)
    assert res_breaker.error == "Circuit broken"
    assert res_breaker.was_compacted is False

if __name__ == "__main__":
    asyncio.run(test_auto_compact_flow())
    print("All Auto-Compact tests passed!")
