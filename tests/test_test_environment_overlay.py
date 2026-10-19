"""Execute the actual deployment awk on disposable env files on Linux CI."""
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("existing_label", ["", 'NLP_AGENT_DEPLOYMENT_ENV="production"\n'])
def test_test_overlay_labels_all_env_file_consumers_without_changing_original(tmp_path, existing_label):
    awk = shutil.which("awk")
    if awk is None:
        pytest.skip("actual awk is exercised on Linux CI")
    workflow = yaml.safe_load((ROOT / ".github/workflows/publish-test-image.yml").read_text(encoding="utf-8"))
    deploy = next(step for step in workflow["jobs"]["deploy"]["steps"] if "AUTH_DEPLOY_SOURCE_DIR" in step.get("env", {}))
    body = deploy["run"].split("awk -v nova_image_ref=", 1)[1].split(" '\n", 1)[1].split("\n' \"$DEPLOY_DIR/.env\"", 1)[0]
    original = '# keep comment\nDATABASE_URL="synthetic-db"\nNLP_AGENT_WORKER_CONCURRENCY=1\n' + existing_label
    path = tmp_path / ".env"
    path.write_text(original, encoding="utf-8")
    result = subprocess.run([awk, "-v", "nova_image_ref=synthetic-app", "-v", "sandbox_runtime_ref=synthetic-runtime", body, str(path)],
                            check=True, capture_output=True, text=True)
    assert path.read_text(encoding="utf-8") == original
    assert result.stdout.count('NLP_AGENT_DEPLOYMENT_ENV="test"') == 1
    assert 'NLP_AGENT_DEPLOYMENT_ENV="production"' not in result.stdout
    assert 'DATABASE_URL="synthetic-db"' in result.stdout
    assert "NLP_AGENT_WORKER_CONCURRENCY=1" in result.stdout
    assert '# keep comment' in result.stdout
