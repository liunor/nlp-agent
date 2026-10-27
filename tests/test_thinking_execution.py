"""Immutable per-Turn model execution, without live Provider requests."""

import asyncio
from types import SimpleNamespace

import pytest

from core.model_runtime.factory import ModelFactory
from core.model_runtime.registry import ProviderRegistry
from core.runtime_config import load_runtime_config


@pytest.fixture
def factory(monkeypatch):
    monkeypatch.setattr(ModelFactory, "_api_key", lambda _self, _name: "test-key")
    result = ModelFactory(
        ModelFactory.config_from_raw(load_runtime_config()), registry=ProviderRegistry()
    )

    class Adapter:
        def build(self, **kwargs):
            return SimpleNamespace(parameters=kwargs)

    for adapter in ("deepseek", "qwen", "glm"):
        result.registry.register(adapter, Adapter, replace=True)
    return result


def snapshot(factory, level="light", user="a", turn="turn-a"):
    return factory.snapshot(
        profile_name="deepseek",
        level=level,
        user_id=user,
        workspace_id="default",
        session_id=f"session-{user}",
        turn_id=turn,
    )


def test_snapshot_freezes_primary_fallback_and_system_parameters(factory):
    frozen = snapshot(factory)
    assert [item.preset_name for item in frozen.worker] == [
        "worker-flash",
        "worker-pro",
    ]
    changed = factory.config.model_presets["worker-flash"].model_copy(
        update={
            "thinking": factory.config.model_presets["utility-flash"].thinking,
        }
    )
    factory.config = factory.config.model_copy(
        update={
            "model_presets": {**factory.config.model_presets, "worker-flash": changed}
        }
    )
    built = factory.build_snapshot_role(frozen, "worker")
    assert [item.preset.thinking.effort.value for item in built.candidates] == [
        "low",
        "low",
    ]
    assert (
        factory.build_snapshot_role(frozen, "utility")
        .candidates[0]
        .preset.thinking.enabled
        is False
    )
    assert (
        factory.build_snapshot_role(frozen, "system_worker")
        .candidates[0]
        .preset.thinking.effort.value
        == "high"
    )


def test_snapshot_and_model_cache_separate_levels_without_storing_user_state(factory):
    light = snapshot(factory)
    extreme = snapshot(factory, "extreme", "b", "turn-b")
    first = factory.build_snapshot_role(light, "coordinator")
    second = factory.build_snapshot_role(extreme, "coordinator")
    assert first is not second
    assert first.candidates[0].preset.thinking.effort.value == "low"
    assert second.candidates[0].preset.thinking.effort.value == "max"


def test_snapshot_does_not_persist_credentials(factory):
    provider = factory.config.providers["deepseek"].model_copy(
        update={"default_headers": {"Authorization": "Bearer private-value"}}
    )
    factory.config = factory.config.model_copy(
        update={"providers": {**factory.config.providers, "deepseek": provider}}
    )
    frozen = snapshot(factory)
    assert "private-value" not in frozen.model_dump_json()
    assert "test-key" not in frozen.model_dump_json()


@pytest.mark.asyncio
async def test_concurrent_turn_contexts_restore_after_failure_and_cancellation(factory):
    from core.model_runtime.selection import bind_model_snapshot, current_model_snapshot

    first = snapshot(factory, "light")
    second = snapshot(factory, "extreme", "b", "turn-b")

    async def run(value):
        with bind_model_snapshot(value):
            await asyncio.sleep(0)
            assert current_model_snapshot().user_id == value.user_id
            assert current_model_snapshot().level == value.level
        assert current_model_snapshot() is None

    await asyncio.gather(run(first), run(second))
    with pytest.raises(RuntimeError):
        with bind_model_snapshot(first):
            raise RuntimeError("failure")
    assert current_model_snapshot() is None

    started = asyncio.Event()
    restored = []

    async def cancelled():
        try:
            with bind_model_snapshot(first):
                started.set()
                await asyncio.Event().wait()
        finally:
            restored.append(current_model_snapshot())

    running = asyncio.create_task(cancelled())
    await started.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert restored == [None]


def test_fallback_without_distinct_level_is_excluded(factory):
    original = factory.config.model_presets["worker-pro"]
    fallback = original.model_copy(
        update={
            "model": "qwen3.8-max",
            "generation": original.generation.model_copy(
                update={"max_output_tokens": 8000}
            ),
        }
    )
    # Validation already checks structural fallback capabilities; level support
    # additionally rejects an adapter outside this product's verified mappings.
    factory.config = factory.config.model_copy(
        update={
            "model_presets": {**factory.config.model_presets, "worker-pro": fallback}
        }
    )
    assert [item.preset_name for item in snapshot(factory).worker] == ["worker-flash"]


def task_for(frozen):
    from core.learning import TeachingMaterials
    from core.session_context import SessionContext
    from gateway.dispatch import TurnTask

    return TurnTask(
        context=SessionContext(
            user_id=frozen.user_id,
            workspace_id=frozen.workspace_id,
            session_id=frozen.session_id,
        ),
        turn_id=frozen.turn_id,
        content="hello",
        learning_context=None,
        learning_progress=None,
        exercise_state=None,
        teaching_materials=TeachingMaterials(),
        guided_session_id=None,
        exercise_session_id=None,
        model_profile=frozen.profile_name,
        model_snapshot=frozen,
        thinking_enabled=frozen.level.value != "quick",
    )


def test_transport_rehydrates_frozen_parameters_and_rejects_cross_user_snapshot(
    factory,
):
    import json
    from gateway.redis_transport import TurnTaskCodec

    task = task_for(snapshot(factory, "extreme"))
    encoded = TurnTaskCodec.dumps(task)
    assert json.loads(encoded)["version"] == 2
    restored = TurnTaskCodec.loads(encoded)
    assert restored == task
    assert (
        factory.build_snapshot_role(restored.model_snapshot, "worker")
        .candidates[0]
        .preset.thinking.effort.value
        == "max"
    )
    forged = json.loads(encoded)
    forged["context"]["user_id"] = "b"
    with pytest.raises(ValueError, match="does not belong"):
        TurnTaskCodec.loads(json.dumps(forged))


def test_v2_transport_cannot_drop_snapshot_or_change_boolean(factory):
    import json
    from gateway.redis_transport import TurnTaskCodec

    encoded = json.loads(TurnTaskCodec.dumps(task_for(snapshot(factory))))
    encoded["model_snapshot"] = None
    with pytest.raises(ValueError, match="snapshot"):
        TurnTaskCodec.loads(json.dumps(encoded))
    encoded = json.loads(TurnTaskCodec.dumps(task_for(snapshot(factory))))
    encoded["thinking_enabled"] = False
    with pytest.raises(ValueError, match="thinking"):
        TurnTaskCodec.loads(json.dumps(encoded))


def test_facade_applies_level_to_ordinary_worker_only(factory, monkeypatch):
    from core.model_runtime.selection import bind_model_snapshot
    from server.agent import llm_factory

    monkeypatch.setattr(llm_factory, "get_global_model_factory", lambda: factory)
    with bind_model_snapshot(snapshot(factory, "extreme")):
        assert (
            llm_factory.get_planner_llm().candidates[0].preset.thinking.effort.value
            == "max"
        )
        assert (
            llm_factory.get_worker_llm().candidates[0].preset.thinking.effort.value
            == "max"
        )
        assert (
            llm_factory.get_utility_llm().candidates[0].preset.thinking.enabled is False
        )
        assert (
            llm_factory.get_worker_llm(agent_name="web_reader")
            .candidates[0]
            .preset.thinking.effort.value
            == "high"
        )
        web = llm_factory.get_worker_llm(agent_name="web_researcher")
        assert web.candidates[0].preset.thinking.enabled is False
        assert web.candidates[0].preset.native_search.enabled is True
        with pytest.raises(ValueError, match="outside"):
            llm_factory.get_worker_llm(tool_specified_model="worker-qwen-web")


@pytest.mark.parametrize(
    "level,effort", [("light", "low"), ("deep", "high"), ("extreme", "max")]
)
def test_glm_actual_wire_parameters(factory, level, effort):
    from core.model_runtime.adapters.glm import GLMAdapter
    from langchain_core.messages import HumanMessage

    frozen = factory.snapshot(
        profile_name="glm",
        level=level,
        user_id="a",
        workspace_id="default",
        session_id="s",
        turn_id="t",
    )
    preset = frozen.coordinator[0].preset
    model = GLMAdapter().build(
        provider_name="glm",
        provider=factory.config.providers["glm"],
        model_name="glm-5.3",
        model=factory.config.models["glm-5.3"],
        preset_name="coordinator-glm",
        preset=preset,
        api_key="test-key",
    )
    payload = model._get_request_payload([HumanMessage(content="test")])
    assert payload["reasoning_effort"] == effort
    assert payload["extra_body"]["thinking"] == {"type": "enabled"}
    assert payload["model"] == "glm-5.3"
    with pytest.raises(ValueError):
        factory.snapshot(
            profile_name="glm",
            level="quick",
            user_id="a",
            workspace_id="default",
            session_id="s",
            turn_id="t",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["quick", "light", "deep", "extreme"])
async def test_websocket_freezes_message_level_instead_of_other_tab_preference(
    factory, tmp_path, monkeypatch, level
):
    from unittest.mock import AsyncMock

    from core.identity import AuthenticatedPrincipal
    from gateway.core import BackendGateway
    from gateway.repository import GatewayRepository
    from server.web.contracts import CommandEnvelope
    from server.web.websocket import _dispatch_command
    from test_gateway_core import FakeEngine, FakeSessions, RecordingTurnDispatcher

    monkeypatch.setattr(
        "core.model_runtime.factory.get_global_model_factory", lambda: factory
    )
    repository = GatewayRepository(tmp_path / "websocket.sqlite3")
    dispatcher = RecordingTurnDispatcher()
    gateway = BackendGateway(
        engine=FakeEngine(), sessions=FakeSessions(), repository=repository,
        dispatcher=dispatcher,
    )
    principal = AuthenticatedPrincipal(
        user_id="a", workspace_ids=frozenset({"default"}), roles=frozenset({"student"})
    )
    await gateway.start()
    try:
        session = await gateway.create_session(principal)
        # Another tab has saved a different preference since this tab loaded.
        other_level = "extreme" if level == "quick" else "quick"
        repository.update_user_settings("a", {"thinking_level": other_level})
        connection = SimpleNamespace(
            principal=principal, gateway=gateway, subscribe=AsyncMock(),
            send=AsyncMock(), auth_session_id=None,
        )
        await _dispatch_command(connection, CommandEnvelope(
            type="chat.send", request_id="message-request", payload={
                "session_id": session.session_id, "content": "hello",
                "model_profile": "deepseek", "thinking_level": level,
                "idempotency_key": "message-request",
            },
        ))
        assert len(dispatcher.submissions) == 1
        task = dispatcher.submissions[0]
        assert task.model_snapshot.level.value == level
        assert repository.get_user_settings("a")["settings"]["thinking_level"] == other_level
    finally:
        await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False, None])
async def test_websocket_preserves_legacy_thinking_without_inventing_a_level(enabled):
    from unittest.mock import AsyncMock

    from core.identity import AuthenticatedPrincipal
    from gateway.contracts import TurnAccepted, TurnStatus
    from server.web.contracts import CommandEnvelope
    from server.web.websocket import _dispatch_command

    gateway = SimpleNamespace(submit_turn=AsyncMock(return_value=TurnAccepted(
        turn_id="t", session_id="s", status=TurnStatus.ACCEPTED,
    )))
    connection = SimpleNamespace(
        principal=AuthenticatedPrincipal(user_id="a", roles=frozenset({"student"})),
        gateway=gateway, subscribe=AsyncMock(), send=AsyncMock(), auth_session_id=None,
    )
    await _dispatch_command(connection, CommandEnvelope(
        type="chat.send", request_id="request", payload={
            "session_id": "s", "content": "hello", "thinking_enabled": enabled,
        },
    ))
    request = gateway.submit_turn.await_args.args[1]
    assert request.thinking_level is None
    assert request.thinking_enabled is enabled


@pytest.mark.asyncio
async def test_gateway_persists_user_level_and_dispatch_retry_never_reresolves(
    factory, tmp_path, monkeypatch
):
    from test_gateway_core import FakeEngine, FakeSessions, FlakyTurnDispatcher
    from core.identity import AuthenticatedPrincipal
    from gateway.contracts import SubmitTurnRequest
    from gateway.core import BackendGateway
    from gateway.repository import GatewayRepository
    from gateway.redis_transport import TurnTaskCodec

    monkeypatch.setattr(
        "core.model_runtime.factory.get_global_model_factory", lambda: factory
    )
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    dispatcher = FlakyTurnDispatcher()
    gateway = BackendGateway(
        engine=FakeEngine(),
        sessions=FakeSessions(),
        repository=repository,
        dispatcher=dispatcher,
    )
    user = AuthenticatedPrincipal(
        user_id="a", workspace_ids=frozenset({"default"}), roles=frozenset({"student"})
    )
    await gateway.start()
    try:
        session = await gateway.create_session(user)
        repository.update_user_settings(
            "a", {"thinking_level": "deep", "show_reasoning": False}
        )
        request = SubmitTurnRequest(
            session_id=session.session_id, content="hello", idempotency_key="one"
        )
        with pytest.raises(ConnectionError):
            await gateway.submit_turn(user, request)
        turn = repository.list_turns(session.session_id)[0]
        frozen = TurnTaskCodec.loads(
            repository.get_turn_task(turn.turn_id)
        ).model_snapshot
        assert frozen.level.value == "deep"
        assert frozen.user_id == "a"
        repository.update_user_settings("a", {"thinking_level": "quick"})
        monkeypatch.setattr(
            factory,
            "snapshot",
            lambda **_kwargs: pytest.fail("retry attempted to resolve new parameters"),
        )
        retried = await gateway.submit_turn(
            user, request.model_copy(update={"thinking_level": "extreme"})
        )
        assert retried.duplicate is True
        assert dispatcher.submissions[0].model_snapshot == frozen
        assert retried.turn_id == frozen.turn_id
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_executor_rejects_redis_parameters_different_from_durable_snapshot(
    factory, tmp_path
):
    from dataclasses import replace
    from test_gateway_core import FakeEngine
    from gateway.repository import GatewayRepository
    from gateway.redis_transport import TurnTaskCodec
    from gateway.turn_execution import InProcessTurnExecutor

    task = task_for(snapshot(factory))
    repository = GatewayRepository(tmp_path / "executor.sqlite3")
    repository.create_turn(
        turn_id=task.turn_id,
        session_id=task.context.session_id,
        user_id=task.context.user_id,
        workspace_id=task.context.workspace_id,
        input_text=task.content,
        idempotency_key=None,
        persisted_task=TurnTaskCodec.dumps(task),
    )
    engine = FakeEngine()

    async def emit(*_args):
        pass

    forged = replace(task, model_snapshot=snapshot(factory, "extreme"))
    with pytest.raises(ValueError, match="persisted"):
        await InProcessTurnExecutor(engine, repository, emit).run(forged)
    assert not engine.contexts


@pytest.mark.asyncio
async def test_fallback_execution_retains_the_same_effort(factory):
    from langchain_core.messages import AIMessage
    from test_model_runtime import FakeModel, StatusError

    for name in ("worker-flash", "worker-pro"):
        preset = factory.config.model_presets[name]
        factory.config.model_presets[name] = preset.model_copy(
            update={
                "retry": preset.retry.model_copy(update={"max_attempts": 1}),
            }
        )
    model = factory.build_snapshot_role(snapshot(factory, "extreme"), "worker")
    model.candidates[0].model = FakeModel([StatusError(503)])
    model.candidates[1].model = FakeModel([AIMessage(content="fallback answer")])
    assert (await model.ainvoke("hello")).content == "fallback answer"
    assert [item.preset.thinking.effort.value for item in model.candidates] == [
        "max",
        "max",
    ]


@pytest.mark.asyncio
async def test_background_resume_loads_its_own_turn_instead_of_latest_selection(
    factory,
):
    from dataclasses import replace
    from core.model_runtime.selection import bind_model_snapshot, current_model_snapshot
    from gateway.engine import LangGraphAgentEngine
    from gateway.redis_transport import TurnTaskCodec

    original = snapshot(factory, "light")
    newer = snapshot(factory, "extreme", turn="turn-new")
    observed = []

    class Graph:
        async def astream_events(self, _state, *, config, version):
            observed.append(current_model_snapshot())
            if False:
                yield None

    engine = LangGraphAgentEngine()
    engine._app = Graph()
    task = task_for(original)
    encoded = TurnTaskCodec.dumps(task)
    engine.set_model_task_loader(lambda _turn: encoded)
    with bind_model_snapshot(newer):
        await engine._invoke([], task.context, True, original.turn_id)
        assert current_model_snapshot() == newer
    assert observed == [original]
    forged = replace(
        task,
        context=task.context.model_copy(update={"user_id": "b"}),
        model_snapshot=None,
    )
    with pytest.raises(ValueError, match="does not belong"):
        await engine._invoke([], forged.context, True, original.turn_id)


@pytest.mark.asyncio
async def test_dispatch_retry_cannot_start_while_another_turn_is_active(
    factory, tmp_path, monkeypatch
):
    from test_gateway_core import FakeEngine, FakeSessions, FlakyTurnDispatcher
    from core.identity import AuthenticatedPrincipal
    from gateway.contracts import SubmitTurnRequest, TurnConflictError, TurnStatus
    from gateway.core import BackendGateway
    from gateway.repository import GatewayRepository

    monkeypatch.setattr(
        "core.model_runtime.factory.get_global_model_factory", lambda: factory
    )
    repository = GatewayRepository(tmp_path / "busy.sqlite3")
    gateway = BackendGateway(
        engine=FakeEngine(),
        sessions=FakeSessions(),
        repository=repository,
        dispatcher=FlakyTurnDispatcher(),
    )
    user = AuthenticatedPrincipal(
        user_id="a", workspace_ids=frozenset({"default"}), roles=frozenset({"student"})
    )
    await gateway.start()
    try:
        session = await gateway.create_session(user)
        request = SubmitTurnRequest(
            session_id=session.session_id, content="hello", idempotency_key="one"
        )
        with pytest.raises(ConnectionError):
            await gateway.submit_turn(user, request)
        first = repository.list_turns(session.session_id)[0]
        await gateway.submit_turn(
            user, SubmitTurnRequest(session_id=session.session_id, content="next")
        )
        with pytest.raises(TurnConflictError):
            await gateway.submit_turn(user, request)
        assert repository.get_turn(first.turn_id).status == TurnStatus.FAILED
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_two_users_execute_distinct_levels_concurrently(
    factory, tmp_path, monkeypatch
):
    from test_gateway_core import FakeEngine, FakeSessions
    from core.identity import AuthenticatedPrincipal
    from core.model_runtime.selection import current_model_snapshot
    from gateway.contracts import SubmitTurnRequest
    from gateway.core import BackendGateway
    from gateway.repository import GatewayRepository

    monkeypatch.setattr(
        "core.model_runtime.factory.get_global_model_factory", lambda: factory
    )
    entered = []
    observed = {}
    both_entered = asyncio.Event()
    both_finished = asyncio.Event()

    class Engine(FakeEngine):
        async def run_turn(self, context, turn_id, content):
            frozen = current_model_snapshot()
            entered.append(context.user_id)
            if len(entered) == 2:
                both_entered.set()
            await asyncio.wait_for(both_entered.wait(), 3)
            assert current_model_snapshot() == frozen
            model = factory.build_snapshot_role(frozen, "coordinator")
            observed[context.user_id] = model.candidates[0].preset.thinking.effort.value
            if len(observed) == 2:
                both_finished.set()
            return "done"

    repository = GatewayRepository(tmp_path / "users.sqlite3")
    gateway = BackendGateway(
        engine=Engine(), sessions=FakeSessions(), repository=repository
    )
    await gateway.start()
    try:
        for name, level in (("a", "extreme"), ("b", "quick")):
            user = AuthenticatedPrincipal(
                user_id=name,
                workspace_ids=frozenset({"default"}),
                roles=frozenset({"student"}),
            )
            repository.update_user_settings(
                name, {"thinking_level": level, "show_reasoning": False}
            )
            session = await gateway.create_session(user)
            await gateway.submit_turn(
                user, SubmitTurnRequest(session_id=session.session_id, content="hello")
            )
        await asyncio.wait_for(both_finished.wait(), 5)
        assert observed == {"a": "max", "b": "none"}
        assert current_model_snapshot() is None
    finally:
        await gateway.close()


@pytest.mark.parametrize(
    "level,effort",
    [("quick", None), ("light", "low"), ("deep", "high"), ("extreme", "max")],
)
def test_deepseek_actual_wire_parameters(factory, level, effort):
    from core.model_runtime.adapters.deepseek import DeepSeekAdapter
    from langchain_core.messages import HumanMessage

    frozen = snapshot(factory, level)
    preset = frozen.coordinator[0].preset
    model = DeepSeekAdapter().build(
        provider_name="deepseek",
        provider=factory.config.providers["deepseek"],
        model_name="deepseek-v4-pro",
        model=factory.config.models["deepseek-v4-pro"],
        preset_name="coordinator-pro",
        preset=preset,
        api_key="test-key",
    )
    payload = model._get_request_payload([HumanMessage(content="test")])
    assert payload.get("reasoning_effort") == effort
    assert payload["extra_body"]["thinking"] == {
        "type": "disabled" if level == "quick" else "enabled"
    }


def test_global_worker_override_is_frozen_and_keeps_non_thinking_parameters(
    factory, monkeypatch
):
    from configs.settings import settings
    from core.model_runtime.selection import bind_model_snapshot
    from server.agent import llm_factory

    monkeypatch.setattr(llm_factory, "get_global_model_factory", lambda: factory)
    monkeypatch.setattr(settings, "NLP_AGENT_WORKER_MODEL", "worker-pro")
    frozen = snapshot(factory, "light")
    assert [item.preset_name for item in frozen.worker] == ["worker-pro"]
    monkeypatch.setattr(settings, "NLP_AGENT_WORKER_MODEL", "worker-flash")
    with bind_model_snapshot(frozen):
        assert (
            llm_factory.resolve_worker_model_name(requested_model="worker-flash")
            == "worker-pro"
        )
        model = llm_factory.get_worker_llm(tool_specified_model="worker-flash")
    assert model.candidates[0].preset.generation.max_output_tokens == 32000
    assert model.candidates[0].preset.thinking.effort.value == "low"


def test_allowed_explicit_worker_preset_is_actually_selected(factory, monkeypatch):
    from core.model_runtime.selection import bind_model_snapshot
    from server.agent import llm_factory

    monkeypatch.setattr(llm_factory, "get_global_model_factory", lambda: factory)
    with bind_model_snapshot(snapshot(factory, "extreme")):
        assert (
            llm_factory.resolve_worker_model_name(requested_model="worker-pro")
            == "worker-pro"
        )
        model = llm_factory.get_worker_llm(tool_specified_model="worker-pro")
    assert [item.preset_name for item in model.candidates] == ["worker-pro"]
    assert model.candidates[0].preset.thinking.effort.value == "max"


def test_global_worker_model_alias_uses_frozen_base_and_filters_supported_levels(
    factory, monkeypatch
):
    from configs.settings import settings
    from core.model_runtime.selection import bind_model_snapshot
    from server.agent import llm_factory

    monkeypatch.setattr(llm_factory, "get_global_model_factory", lambda: factory)
    monkeypatch.setattr(settings, "NLP_AGENT_WORKER_MODEL", "deepseek-v4-pro")
    frozen = snapshot(factory, "light")
    monkeypatch.setattr(settings, "NLP_AGENT_WORKER_MODEL", "worker-flash")
    with bind_model_snapshot(frozen):
        model = llm_factory.get_worker_llm()
    assert model.candidates[0].definition.model_id == "deepseek-v4-pro"
    assert model.candidates[0].preset.generation.max_output_tokens == 24000
    assert model.candidates[0].preset.thinking.effort.value == "low"
    monkeypatch.setattr(settings, "NLP_AGENT_WORKER_MODEL", "worker-glm")
    assert factory.public_profiles()["deepseek"]["supported_levels"] == [
        "light",
        "deep",
        "extreme",
    ]
    with pytest.raises(ValueError):
        snapshot(factory, "quick")


def test_sandbox_inherit_keeps_fallback_while_explicit_preset_selects_it(
    factory, monkeypatch
):
    from core.model_runtime.selection import bind_model_snapshot
    from server.agent import llm_factory
    from server.tools import worker_tool

    monkeypatch.setattr(llm_factory, "get_global_model_factory", lambda: factory)
    with bind_model_snapshot(snapshot(factory, "extreme")):
        role_token = worker_tool._worker_model_role.set("nlp_calculator")
        request_token = worker_tool._worker_model_request.set(None)
        try:
            inherited = worker_tool._sandbox_worker_llm("worker-flash")
            assert [item.preset_name for item in inherited.candidates] == [
                "worker-flash",
                "worker-pro",
            ]
            worker_tool._worker_model_request.set("worker-flash")
            primary = worker_tool._sandbox_worker_llm("worker-flash")
            assert [item.preset_name for item in primary.candidates] == ["worker-flash", "worker-pro"]
            worker_tool._worker_model_request.set("worker-pro")
            explicit = worker_tool._sandbox_worker_llm("worker-pro")
            assert [item.preset_name for item in explicit.candidates] == ["worker-pro"]
        finally:
            worker_tool._worker_model_request.reset(request_token)
            worker_tool._worker_model_role.reset(role_token)


def test_supported_levels_replace_original_worker_when_global_override_is_active(
    factory, monkeypatch
):
    from configs.settings import settings

    definition = factory.config.models["glm-5.3"].model_copy(
        update={"model_id": "glm-5.2"}
    )
    preset = factory.config.model_presets["worker-glm"].model_copy(
        update={"model": "glm-5.2"}
    )
    factory.config = factory.config.model_copy(
        update={
            "models": {**factory.config.models, "glm-5.2": definition},
            "model_presets": {**factory.config.model_presets, "worker-glm": preset},
        }
    )
    assert factory.public_profiles()["glm"]["supported_levels"] == ["deep", "extreme"]
    monkeypatch.setattr(settings, "NLP_AGENT_WORKER_MODEL", "worker-pro")
    assert factory.public_profiles()["glm"]["supported_levels"] == [
        "light",
        "deep",
        "extreme",
    ]
