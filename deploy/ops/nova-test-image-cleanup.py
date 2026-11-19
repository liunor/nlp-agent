"""Deferred cleanup of superseded, explicitly recorded test deployments."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.error import URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


COLLECTOR = Path('/usr/local/lib/nova-image-cleanup/cleanup.py')
COLLECTOR_SHA = '56818b464537f057f416028f9701c19b89b09ab408d7c7a9ed179e071d26acc1'
RELEASES = Path('/opt/nova-test/auth-releases')
APP = 'ghcr.io/liunor/nlp-agent'
RUNTIME = 'ghcr.io/liunor/nova-sandbox-runtime'
SCOPE_LABEL = 'io.nova.image.lifecycle.scope'
AGE = 3 * 86400
DELAY = 15 * 60
LIMIT = 3


class Stop(ValueError):
    pass


def check(ok, reason):
    if not ok:
        raise Stop(reason)


def valid_ref(ref, repository):
    return isinstance(ref, str) and re.fullmatch(re.escape(repository) + r'@sha256:[0-9a-f]{64}', ref)


def load_collector():
    for part in (COLLECTOR, *COLLECTOR.parents):
        meta = part.lstat()
        check(not part.is_symlink() and meta.st_uid == 0 and not meta.st_mode & 0o022,
              'COLLECTOR_PATH_NOT_ROOT_CONTROLLED')
    check(COLLECTOR.is_file() and COLLECTOR.stat().st_size < 100_000, 'COLLECTOR_FILE_INVALID')
    raw = COLLECTOR.read_bytes()
    check(hashlib.sha256(raw).hexdigest() == COLLECTOR_SHA, 'INSTALLED_COLLECTOR_CHANGED')
    functions = {'__name__': 'verified_image_collector', '__file__': str(COLLECTOR)}
    exec(compile(raw, str(COLLECTOR), 'exec'), functions)
    return functions


def release_records(worker, now):
    check(not any(p.is_symlink() for p in (RELEASES, *RELEASES.parents)), 'UNSAFE_RELEASE_ROOT')
    if not RELEASES.is_dir():
        return [], ()
    entries = list(RELEASES.iterdir())
    check(len(entries) <= 1000, 'RELEASE_HISTORY_LIMIT')
    records, fingerprints = [], []
    for entry in entries:
        check(not entry.is_symlink(), 'RELEASE_HISTORY_SYMLINK')
        path = entry / 'image-lifecycle.json'
        if not path.exists() and not path.is_symlink():
            continue
        check(re.fullmatch('[0-9a-f]{64}', entry.name), 'INVALID_RELEASE_DIRECTORY')
        raw = worker['secure_read'](path, 4096)
        data = json.loads(raw)
        prepared = json.loads(worker['secure_read'](entry / 'prepared.json'))
        check(data.get('schema') == 1 and data.get('project') == prepared.get('project') == 'nova-test'
              and data.get('app') == prepared.get('image') and valid_ref(data.get('app'), APP)
              and valid_ref(data.get('runtime'), RUNTIME), 'RELEASE_PAIR_UNVERIFIED')
        reported = datetime.fromisoformat(data['verified_at'])
        check(reported.tzinfo is not None, 'RELEASE_TIMESTAMP_NOT_UTC_AWARE')
        effective = max(reported.timestamp(), path.stat().st_mtime)
        check(effective <= now + 60, 'RELEASE_TIMESTAMP_IN_FUTURE')
        records.append((effective, data['app'], data['runtime']))
        fingerprints.append((entry.name, hashlib.sha256(raw).hexdigest(), effective))
    return sorted(records, reverse=True), tuple(sorted(fingerprints))


def configured_pair(api):
    manager = api.get('/containers/nova-test-nova-sandbox-manager-1/json')
    config = manager['Config']
    labels = config.get('Labels') or {}
    check(manager['State']['Running'] and labels.get('com.docker.compose.project') == 'nova-test'
          and labels.get('com.docker.compose.service') == 'nova-sandbox-manager', 'TEST_MANAGER_NOT_VERIFIED')
    key = 'NLP_AGENT_SANDBOX_DOCKER_IMAGE_DIGEST='
    refs = [env[len(key):] for env in config.get('Env') or [] if env.startswith(key)]
    check(valid_ref(config.get('Image'), APP) and len(refs) == 1 and valid_ref(refs[0], RUNTIME),
          'TEST_MANAGER_IMAGE_CONFIGURATION_UNVERIFIED')
    return config['Image'], refs[0]


def choose(state, records, current, now, worker):
    if not records or tuple(records[0][1:]) != current:
        return [], 'WAITING_FOR_CURRENT_VERIFIED_DEPLOYMENT'
    if now - records[0][0] < DELAY:
        return [], 'DEPLOYMENT_GRACE_PERIOD'
    _, old_policy_pins, _ = worker['plan'](state, now)
    protected = set(state['pins']) | old_policy_pins
    candidates = {}
    for index, repo in ((1, APP), (2, RUNTIME)):
        latest_use = {}
        for row in records:
            latest_use.setdefault(row[index], row[0])
        refs = list(latest_use)
        protected.update(ref.split('@')[1] for ref in refs[:3])
        for ref in refs[3:]:
            if now - latest_use[ref] >= AGE:
                candidates[ref] = (repo, latest_use[ref])
    picked = []
    for image_id, image in state['images'].items():
        labels = (image.get('Config') or {}).get('Labels') or {}
        if (image_id != image['Id'] or not worker['IMAGE_ID'].fullmatch(image_id)
                or labels.get(SCOPE_LABEL) != 'nova-test'
                or labels.get('nova.cleanup.keep', '').lower() == 'true'
                or labels.get('org.opencontainers.image.source', '').rstrip('/') != worker['APP_SOURCE']
                or worker['image_digests'](image) & protected or now - worker['image_time'](image) < AGE):
            continue
        matches = set(image.get('RepoDigests') or []) & candidates.keys()
        if len(matches) != 1:
            continue
        ref = matches.pop()
        repo, since = candidates[ref]
        refs = (image.get('RepoTags') or []) + (image.get('RepoDigests') or [])
        if all(item.startswith((repo + ':', repo + '@')) for item in refs):
            picked.append((since, image_id, ref))
    return sorted(picked), None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def manifest_available(ref):
    opener = build_opener(NoRedirect())
    repo, digest = ref.removeprefix('ghcr.io/').split('@')
    query = urlencode({'service': 'ghcr.io', 'scope': f'repository:{repo}:pull'})
    accept = ', '.join(('application/vnd.oci.image.index.v1+json', 'application/vnd.oci.image.manifest.v1+json',
                        'application/vnd.docker.distribution.manifest.list.v2+json',
                        'application/vnd.docker.distribution.manifest.v2+json'))
    try:
        with opener.open('https://ghcr.io/token?' + query, timeout=5) as response:
            token = json.loads(response.read(65536))['token']
        request = Request(f'https://ghcr.io/v2/{repo}/manifests/{quote(digest, safe=":")}', method='HEAD',
                          headers={'Authorization': 'Bearer ' + token, 'Accept': accept})
        with opener.open(request, timeout=5) as response:
            return response.status == 200 and response.headers.get('Docker-Content-Digest') == digest
    except (URLError, TimeoutError, ValueError, KeyError):
        return False


def execute(worker, api, apply):
    report = {'status': 'PREVIEW', 'scope': 'nova-test', 'apply': apply, 'deleted': [], 'pending': None,
              'registry_unavailable': [], 'min_age_days': AGE // 86400, 'maximum_deletions': LIMIT}
    try:
        worker['no_ci']()
        worker['site_health']()
        now = time.time()
        records, receipt_fingerprint = release_records(worker, now)
        if not records:
            return {**report, 'status': 'SKIPPED', 'reason': 'WAITING_FOR_FIRST_VERIFIED_DEPLOYMENT'}
        current = configured_pair(api)
        inventory = worker['snapshot'](api)
        plan, reason = choose(inventory, records, current, now, worker)
        report.update(eligible=len(plan), candidates=[identifier for _, identifier, _ in plan[:LIMIT]])
        if reason:
            return {**report, 'status': 'SKIPPED', 'reason': reason}
        if not apply:
            return report
        before = worker['filesystem_available']()
        for candidate in plan[:LIMIT]:
            _, image_id, ref = candidate
            if not manifest_available(ref):
                report['registry_unavailable'].append(image_id)
                continue
            worker['no_ci']()
            worker['site_health']()
            _, latest_fingerprint = release_records(worker, time.time())
            latest = worker['snapshot'](api)
            check(configured_pair(api) == current and latest_fingerprint == receipt_fingerprint
                  and inventory['boundary'] == latest['boundary'], 'DEPLOYMENT_CHANGED')
            expected = {key: image for key, image in inventory['images'].items() if key not in report['deleted']}
            check(expected == latest['images'], 'IMAGE_INVENTORY_CHANGED')
            latest_plan, _ = choose(latest, records, current, time.time(), worker)
            check(candidate in latest_plan, 'CANDIDATE_NO_LONGER_ELIGIBLE')
            report['pending'] = image_id
            api.delete(image_id)
            report['pending'] = None
            report['deleted'].append(image_id)
            worker['site_health']()
        report.update(status='COMPLETED', available_change_mib=round((worker['filesystem_available']() - before) / 1024**2, 2))
    except Exception as error:
        reason = str(error)
        report.update(status='STOPPED', reason=reason if re.fullmatch('[A-Z][A-Z0-9_]{1,100}', reason) else type(error).__name__)
    return report


if __name__ == '__main__':
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('--apply', action='store_true')
    options = args.parse_args()
    check(os.geteuid() == 0, 'ROOT_REQUIRED')
    collector = load_collector()
    if options.apply:
        with collector['cleanup_lock']():
            result = execute(collector, collector['Docker'](), True)
    else:
        result = execute(collector, collector['Docker'](), False)
    print(json.dumps(result))
    raise SystemExit(int(result['status'] == 'STOPPED' and result.get('reason') != 'CI_JOB_ACTIVE_SKIP'))
