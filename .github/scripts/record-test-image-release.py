"""Record the app/runtime digests of a healthy develop deployment."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile


def record(deploy_dir: Path, override: Path, app: str, runtime: str) -> None:
    app_pattern = r'ghcr\.io/liunor/nlp-agent@sha256:[0-9a-f]{64}'
    runtime_pattern = r'ghcr\.io/liunor/nova-sandbox-runtime@sha256:[0-9a-f]{64}'
    if not re.fullmatch(app_pattern, app) or not re.fullmatch(runtime_pattern, runtime):
        raise ValueError('UNEXPECTED_RELEASE_IMAGE_REFERENCES')
    root = (deploy_dir / 'auth-releases').absolute()
    override = override.absolute()
    if (override.name != 'compose.auth.json' or override.parent.parent != root
            or not re.fullmatch(r'[0-9a-f]{64}', override.parent.name)
            or any(p.is_symlink() for p in (override, *override.parents))):
        raise ValueError('UNEXPECTED_PREPARED_RELEASE_PATH')
    prepared_path = override.parent / 'prepared.json'
    if prepared_path.is_symlink() or not prepared_path.is_file() or prepared_path.stat().st_size > 2 * 1024**2:
        raise ValueError('EXPECTED_PREPARED_RELEASE_METADATA')
    prepared = json.loads(prepared_path.read_bytes())
    if prepared.get('project') != 'nova-test' or prepared.get('image') != app:
        raise ValueError('RELEASE_NOT_PREPARED_FOR_TEST')
    data = {'schema': 1, 'project': 'nova-test', 'app': app, 'runtime': runtime,
            'verified_at': datetime.now(timezone.utc).isoformat()}
    target = override.parent / 'image-lifecycle.json'
    temp = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=override.parent, delete=False) as handle:
            temp = Path(handle.name)
            json.dump(data, handle, sort_keys=True)
            handle.write('\n')
        os.replace(temp, target)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deploy-dir', type=Path, required=True)
    parser.add_argument('--override', type=Path, required=True)
    parser.add_argument('--app', required=True)
    parser.add_argument('--runtime', required=True)
    args = parser.parse_args()
    record(args.deploy_dir, args.override, args.app, args.runtime)
