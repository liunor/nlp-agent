from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import runpy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
CLEAN = runpy.run_path(ROOT / 'deploy/ops/nova-test-image-cleanup.py')
RECORD = runpy.run_path(ROOT / '.github/scripts/record-test-image-release.py')['record']
NOW = 1_795_000_000
DAY = 86400


def digest(n):
    return f'sha256:{n:064x}'


def image(repo, n, *, scope='nova-test'):
    return {'Id': digest(100 + n), 'RepoDigests': [f'{repo}@{digest(n)}'], 'RepoTags': [],
            'Created': NOW - 20 * DAY,
            'Config': {'Labels': {CLEAN['SCOPE_LABEL']: scope, 'org.opencontainers.image.source': 'nova-source'}}}


@pytest.fixture
def deployment():
    app = [image(CLEAN['APP'], n) for n in range(1, 9)]
    runtime = [image(CLEAN['RUNTIME'], n) for n in range(11, 17)]
    def ref(item):
        return item['RepoDigests'][0]
    # Five app releases frequently share the very same runtime. Protect three
    # distinct digests per family, regardless of repeated uses by app releases.
    records = [(NOW - age * DAY, ref(app[a]), ref(runtime[r])) for age, a, r in (
        (1, 0, 0), (2, 1, 0), (3, 2, 0), (4, 3, 1), (5, 4, 2), (6, 5, 3), (7, 6, 4), (8, 6, 5),
    )]
    current = records[0][1:]
    state = {'images': {item['Id']: item for item in app + runtime}, 'pins': {digest(16)}, 'boundary': 'stable'}
    worker = {'plan': lambda *_: ([], {app[4]['Id']}, 0),
              'image_digests': lambda img: {img['Id'], *(ref.split('@')[1] for ref in img['RepoDigests'])},
              'image_time': lambda img: img['Created'], 'IMAGE_ID': CLEAN['re'].compile(r'sha256:[0-9a-f]{64}'),
              'APP_SOURCE': 'nova-source', 'snapshot': lambda _: deepcopy(state),
              'no_ci': Mock(), 'site_health': Mock(), 'filesystem_available': lambda: 1}
    return SimpleNamespace(app=app, runtime=runtime, records=records, current=current, state=state, worker=worker)


def test_rollbacks_with_shared_runtimes_and_legacy_images(deployment):
    d = deployment
    legacy = d.app[3]
    legacy['Config']['Labels'][CLEAN['SCOPE_LABEL']] = 'unmanaged'
    plan, reason = CLEAN['choose'](d.state, d.records, d.current, NOW, d.worker)
    assert reason is None
    assert {identifier for _, identifier, _ in plan} == {
        d.app[5]['Id'], d.app[6]['Id'], d.runtime[3]['Id'], d.runtime[4]['Id'],
    }


def test_waits_for_current_release_and_delay(deployment):
    d = deployment
    assert CLEAN['choose'](d.state, d.records, d.records[1][1:], NOW, d.worker)[1] == 'WAITING_FOR_CURRENT_VERIFIED_DEPLOYMENT'
    assert CLEAN['choose'](d.state, d.records, d.current, d.records[0][0] + 50, d.worker)[1] == 'DEPLOYMENT_GRACE_PERIOD'


def test_registry_missing_image_never_deleted_and_batch_bounded(deployment, monkeypatch):
    d = deployment
    functions = CLEAN['execute'].__globals__
    monkeypatch.setitem(functions, 'time', SimpleNamespace(time=lambda: NOW))
    monkeypatch.setitem(functions, 'release_records', lambda *_: (d.records, 'receipt'))
    monkeypatch.setitem(functions, 'configured_pair', lambda *_: d.current)
    unknown = d.runtime[4]
    monkeypatch.setitem(functions, 'manifest_available', lambda ref: ref != unknown['RepoDigests'][0])
    api = SimpleNamespace(delete=Mock(side_effect=lambda identifier: d.state['images'].pop(identifier)))
    report = CLEAN['execute'](d.worker, api, True)
    assert report['status'] == 'COMPLETED'
    assert report['registry_unavailable'] == [unknown['Id']]
    assert len(report['deleted']) == 2
    assert unknown['Id'] in d.state['images']


def test_changed_release_receipt_stops_before_delete(deployment, monkeypatch):
    d = deployment
    functions = CLEAN['execute'].__globals__
    monkeypatch.setitem(functions, 'time', SimpleNamespace(time=lambda: NOW))
    monkeypatch.setitem(functions, 'release_records', Mock(side_effect=[(d.records, 'first'), (d.records, 'changed')]))
    monkeypatch.setitem(functions, 'configured_pair', lambda *_: d.current)
    monkeypatch.setitem(functions, 'manifest_available', lambda _: True)
    api = SimpleNamespace(delete=Mock())
    report = CLEAN['execute'](d.worker, api, True)
    assert report['reason'] == 'DEPLOYMENT_CHANGED'
    api.delete.assert_not_called()


def test_writer_records_paired_digests_only_for_prepared_test(tmp_path):
    import json

    app, runtime = (f'{CLEAN[repo]}@{digest(index)}' for index, repo in enumerate(('APP', 'RUNTIME'), 1))
    root = tmp_path / 'auth-releases' / ('b' * 64)
    root.mkdir(parents=True)
    override = root / 'compose.auth.json'
    override.write_text('{}', encoding='utf-8')
    prepared = root / 'prepared.json'
    prepared.write_text(json.dumps({'project': 'nova-prod', 'image': app}), encoding='utf-8')
    with pytest.raises(ValueError, match='RELEASE_NOT_PREPARED_FOR_TEST'):
        RECORD(tmp_path, override, app, runtime)
    prepared.write_text(json.dumps({'project': 'nova-test', 'image': app}), encoding='utf-8')
    RECORD(tmp_path, override, app, runtime)
    result = json.loads((root / 'image-lifecycle.json').read_bytes())
    assert (result['app'], result['runtime'], result['project']) == (app, runtime, 'nova-test')
    assert datetime.fromisoformat(result['verified_at']).tzinfo == timezone.utc
