import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessageChunk

from core.session_context import SessionContext
from gateway.contracts import GatewayEventType as Event
from gateway.delta_batcher import DeltaBatcher
from gateway.engine import LangGraphAgentEngine


@pytest.mark.asyncio
async def test_first_text_is_immediate_and_subsequent_fragments_reduce_durable_writes():
    writes = []

    async def persist(kind, payload):
        writes.append((kind, payload))

    buffer = DeltaBatcher(persist, clock=lambda: 0, max_chars=20)
    await buffer.emit(Event.MESSAGE_DELTA, {"delta": ""})
    await buffer.emit(Event.MESSAGE_DELTA, {"delta": "first", "background": False})
    assert [p['delta'] for _, p in writes] == ['first']
    for _ in range(100):
        await buffer.emit(Event.MESSAGE_DELTA, {"delta": "x", "background": False})
    await buffer.flush()
    assert len(writes) == 6
    assert ''.join(p['delta'] for _, p in writes) == 'first' + 'x'*100


@pytest.mark.asyncio
async def test_whitespace_does_not_delay_the_first_visible_fragment():
    writes = []

    async def persist(kind, payload):
        writes.append(payload['delta'])

    buffer = DeltaBatcher(persist, clock=lambda: 0)
    await buffer.emit(Event.MESSAGE_DELTA, {'delta': ' '})
    await buffer.emit(Event.MESSAGE_DELTA, {'delta': 'visible'})
    assert writes == [' ', 'visible']


@pytest.mark.asyncio
async def test_channel_and_tool_boundaries_preserve_order_and_metadata():
    writes = []

    async def persist(kind, payload):
        writes.append((kind, payload))

    buffer = DeltaBatcher(persist, clock=lambda: 0)
    for text in ('reason-one', 'reason-two'):
        await buffer.emit(Event.MESSAGE_DELTA, {'delta': text, 'channel': 'reasoning', 'background': False})
    await buffer.emit(Event.MESSAGE_DELTA, {'delta': 'answer-one', 'background': False})
    await buffer.emit(Event.MESSAGE_DELTA, {'delta': 'answer-two', 'background': False})
    await buffer.emit(Event.TOOL_STARTED, {'name': 'fixture-tool'})
    assert [p.get('delta', p.get('name')) for _, p in writes] == [
        'reason-one', 'reason-two', 'answer-one', 'answer-two', 'fixture-tool']
    assert all(p.get('channel') == 'reasoning' for _, p in writes[:2])


@pytest.mark.asyncio
async def test_elapsed_time_flushes_on_the_next_fragment_and_unknown_payloads_are_preserved():
    writes, now = [], [0.0]

    async def persist(kind, payload):
        writes.append((kind, payload))

    buffer = DeltaBatcher(persist, clock=lambda: now[0])
    await buffer.emit(Event.MESSAGE_DELTA, {'delta': 'first'})
    await buffer.emit(Event.MESSAGE_DELTA, {'delta': 'pending'})
    assert len(writes) == 1
    now[0] = .2
    await buffer.emit(Event.MESSAGE_DELTA, {'delta': 'more'})
    assert writes[-1][1]['delta'] == 'pendingmore'
    unusual = {'delta': [{'type': 'text', 'text': 'structured'}], 'citations': ['fixture']}
    await buffer.emit(Event.MESSAGE_DELTA, unusual)
    assert writes[-1][1] == unusual


@pytest.mark.asyncio
@pytest.mark.parametrize('cancelled', [False, True])
async def test_engine_drains_on_completion_without_delayed_writes_after_cancellation(cancelled):
    writes = []

    class Graph:
        async def astream_events(self, *args, **kwargs):
            for delta in ('first', 'second', 'third'):
                yield {'event': 'on_chat_model_stream', 'metadata': {'langgraph_node': 'coordinator'},
                       'data': {'chunk': AIMessageChunk(content=delta)}}
            if cancelled:
                raise asyncio.CancelledError

    async def persist(turn, session, kind, payload):
        writes.append((turn, session, kind, payload))

    engine = LangGraphAgentEngine()
    engine._app = Graph()
    engine._event_sink = persist
    engine._runtime = SimpleNamespace(telemetry_context=lambda session: None)
    context = SessionContext(session_id='session', user_id='user')
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await engine._invoke([], context, False, 'turn')
        assert ''.join(p['delta'] for _, _, _, p in writes) == 'first'
    else:
        await engine._invoke([], context, False, 'turn')
        assert ''.join(p['delta'] for _, _, _, p in writes) == 'firstsecondthird'
    count = len(writes)
    await asyncio.sleep(0)
    assert len(writes) == count
