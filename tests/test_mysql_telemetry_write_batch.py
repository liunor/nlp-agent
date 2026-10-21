from contextlib import contextmanager
import json
import os
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy.dialects import mysql

from core.observability.mysql_repository import MySQLTelemetryRepository


def repository_fixture():
    state = {'locked': False, 'transactions': 0, 'statements': []}

    class Connection:
        def execute(self, statement):
            assert state['locked']
            state['statements'].append(statement.compile(dialect=mysql.dialect()))

    @contextmanager
    def transaction():
        state['transactions'] += 1
        state['locked'] = True
        try:
            yield Connection()
        finally:
            state['locked'] = False

    repository = object.__new__(MySQLTelemetryRepository)
    repository._runtime_begin = transaction
    return repository, state


def envelope(index, state, *, padding=''):
    def dump(**kwargs):
        assert not state['locked'], 'conversion must happen before taking the reset fence'
        return {'kind': 'event', 'payload': {'event_id': 'event-'+str(index), 'trace_id': 'trace',
            'wire_padding': padding, 'payload': {'api_key': 'private-fixture-secret'}}}
    return SimpleNamespace(kind='event', model_dump=dump)


def test_hundred_records_use_few_round_trips_in_one_reset_fenced_transaction():
    repository, state = repository_fixture()
    repository.write_batch(envelope(i, state) for i in range(100))
    assert state['transactions'] == 1
    assert len(state['statements']) == 7
    records = []
    for compiled in state['statements']:
        assert 'ON DUPLICATE KEY UPDATE' in str(compiled)
        assert 'VALUES(payload_json)' in str(compiled)
        assert 'VALUES(status)' in str(compiled)
        records.extend(value for key, value in compiled.params.items() if key.startswith('payload_json_m'))
    assert len(records) == 100
    assert len({r['payload']['event_id'] for r in records}) == 100
    assert 'private-fixture-secret' not in json.dumps(records)


def test_large_payloads_are_not_combined_into_an_unbounded_insert():
    repository, state = repository_fixture()
    repository.write_batch(envelope(i, state, padding='x'*160_000) for i in range(2))
    assert state['transactions'] == 1
    assert len(state['statements']) == 2


def test_empty_or_invalid_input_does_not_take_a_connection_or_partially_write():
    repository, state = repository_fixture()
    repository.write_batch([])
    assert state['transactions'] == 0
    invalid = SimpleNamespace(kind='event', model_dump=lambda **kwargs: {'kind': 'event', 'payload': {}})
    with pytest.raises(ValueError, match='missing event_id'):
        repository.write_batch([envelope(1, state), invalid])
    assert state['transactions'] == 0 and state['statements'] == []


@pytest.mark.skipif(os.environ.get('GITHUB_ACTIONS') != 'true', reason='Requires the isolated CI MySQL service')
def test_real_mysql_bulk_upsert_last_value_and_atomic_rollback(monkeypatch):
    from sqlalchemy import CheckConstraint, MetaData, create_engine, func, select, text
    from sqlalchemy.exc import IntegrityError
    import core.observability.mysql_repository as module

    url = os.environ['NLP_AGENT_DATABASE_URL'].replace('mysql+aiomysql://', 'mysql+pymysql://')
    engine = create_engine(url)
    table = module.ObservabilityRecordModel.__table__.to_metadata(
        MetaData(), name='nlp_observability_batch_probe_'+uuid.uuid4().hex[:12])
    table.append_constraint(CheckConstraint("status IS NULL OR status IN ('ok','error')"))
    table.create(engine)
    try:
        monkeypatch.setattr(module, 'ObservabilityRecordModel', table)
        repository = object.__new__(MySQLTelemetryRepository)
        repository._engine = engine
        repository._runtime_lock_enabled = True
        state = {'locked': False}
        updated = SimpleNamespace(kind='event', model_dump=lambda **kwargs: {
            'kind': 'event', 'payload': {'event_id': 'event-0', 'status': 'ok', 'payload': {'label': 'updated'}}})
        repository.write_batch([*(envelope(i, state) for i in range(32)), updated])
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(table)) == 32
            row = connection.execute(select(table).where(table.c.record_key == 'event-0')).mappings().one()
            assert row['status'] == 'ok' and row['payload_json']['payload']['payload']['label'] == 'updated'
        invalid = SimpleNamespace(kind='event', model_dump=lambda **kwargs: {
            'kind': 'event', 'payload': {'event_id': 'invalid-last-record', 'status': 'disallowed'}})
        with pytest.raises(IntegrityError):
            repository.write_batch([*(envelope(1000+i, state) for i in range(32)), invalid])
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(table)) == 32
            assert connection.scalar(text("SELECT IS_FREE_LOCK('nlp_agent_monitor_runtime_reset')")) == 1
    finally:
        table.drop(engine)
        engine.dispose()
