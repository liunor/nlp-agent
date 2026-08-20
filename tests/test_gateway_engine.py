from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from core.coordinator_runtime import CoordinatorRuntime
from core.learning import ExerciseState, LearningContext, LearningProgress, TeachingMaterials
from core.session_context import SessionContext
from core.worker_events import WorkerEventBus
from gateway.contracts import GatewayEventType
from gateway.engine import LangGraphAgentEngine


class RecordingGraph:
    def __init__(self):
        self.configs = []

    async def astream_events(self, _state, *, config, version):
        self.configs.append(config)
        if False:
            yield version

    async def aget_state(self, _config):
        return SimpleNamespace(values={"messages": [AIMessage(content="done")]})


class ToolEventGraph(RecordingGraph):
    async def astream_events(self, _state, *, config, version):
        self.configs.append(config)
        yield {"event": "on_chain_start", "metadata": {"langgraph_node": "tools"}}
        yield {"event": "on_tool_start", "name": "search_docs", "run_id": "tool-1", "metadata": {"langgraph_node": "tools"}}
        yield {"event": "on_tool_end", "name": "search_docs", "run_id": "tool-1", "metadata": {"langgraph_node": "tools"}}
        yield {"event": "on_chain_end", "metadata": {"langgraph_node": "tools"}}


class StreamingGraph(RecordingGraph):
    async def astream_events(self, _state, *, config, version):
        self.configs.append(config)
        yield {
            "event": "on_chat_model_stream",
            "metadata": {"langgraph_node": "coordinator"},
            "data": {"chunk": AIMessageChunk(content="late answer")},
        }


@pytest.mark.asyncio
async def test_engine_injects_teacher_topic_and_blueprint_into_graph_config(monkeypatch):
    async def record_transcript_without_database(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "server.agent.session_storage.record_transcript",
        record_transcript_without_database,
    )
    graph = RecordingGraph()
    engine = LangGraphAgentEngine()
    engine._app = graph
    engine._runtime = CoordinatorRuntime(WorkerEventBus(), engine._invoke)
    engine._started = True
    context = SessionContext(
        session_id="learning-session",
        user_id="alice",
        workspace_id="w1",
        channel="web",
    )
    materials = TeachingMaterials(
        learning_topic={"name": "Transformer", "description": "模型结构", "knowledge_points": ["QKV"]},
        exercise_blueprint={"id": "bp", "instructions": "生成一道 QKV 题"},
        guided_session={"id": "guided-1", "objective": "理解 Attention", "stage": "awaiting_learner_response"},
        guided_blueprint={"id": "guided-bp", "guidance": "先用问题帮助学生区分 QKV。"},
    )

    result = await engine.run_turn(
        context,
        "turn-1",
        "开始练习",
        learning_context=LearningContext(topic_id="transformer", topic_name="Transformer", mode="practice"),
        learning_progress=LearningProgress(objective="掌握 Transformer"),
        exercise_state=ExerciseState(blueprint_id="bp", status="awaiting_answer"),
        teaching_materials=materials,
        model_profile="qwen",
    )

    configurable = graph.configs[0]["configurable"]
    assert result == "done"
    assert configurable["model_profile"] == "qwen"
    assert configurable["learning_topic"]["description"] == "模型结构"
    assert configurable["learning_topic"]["knowledge_points"] == ["QKV"]
    assert configurable["exercise_blueprint"]["instructions"] == "生成一道 QKV 题"
    assert configurable["review_blueprint"] == {}
    assert configurable["guided_session"]["objective"] == "理解 Attention"
    assert configurable["guided_blueprint"]["guidance"] == "先用问题帮助学生区分 QKV。"
    await engine._runtime.close()


@pytest.mark.asyncio
async def test_engine_emits_named_tool_lifecycle_events_for_the_webui():
    graph = ToolEventGraph()
    engine = LangGraphAgentEngine()
    engine._app = graph
    emitted = []

    async def sink(turn_id, session_id, event_type, payload):
        emitted.append((turn_id, session_id, event_type, payload))

    engine._event_sink = sink
    context = SessionContext(session_id="session-1", user_id="alice", workspace_id="w1", channel="web")

    await engine._invoke([], context, False, "turn-1")

    assert [(event_type, payload) for _, _, event_type, payload in emitted] == [
        (GatewayEventType.TOOL_STARTED, {"name": "search_docs"}),
        (GatewayEventType.TOOL_COMPLETED, {"name": "search_docs"}),
    ]


@pytest.mark.asyncio
async def test_detached_worker_resume_does_not_mutate_completed_chat_content():
    engine = LangGraphAgentEngine()
    engine._app = StreamingGraph()
    emitted = []

    async def sink(turn_id, session_id, event_type, payload):
        emitted.append((turn_id, session_id, event_type, payload))

    engine._event_sink = sink
    context = SessionContext(
        session_id="session-1", user_id="alice", workspace_id="w1", channel="web"
    )

    await engine._invoke([], context, True, "completed-turn")

    assert [item[2] for item in emitted] == [GatewayEventType.WORKER_UPDATE]
