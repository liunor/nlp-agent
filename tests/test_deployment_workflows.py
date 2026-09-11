import os
import platform
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATHS = (
    ROOT / ".github" / "workflows" / "publish-test-image.yml",
    ROOT / ".github" / "workflows" / "release-prod.yml",
)
RETRY_HELPER_PATH = ROOT / ".github" / "scripts" / "run-with-transient-registry-retry.sh"


def test_deployment_workflows_update_and_check_worker() -> None:
    for workflow_path in WORKFLOW_PATHS:
        workflow = workflow_path.read_text(encoding="utf-8")

        pull_services = (
            "nova-migrate nova-web nova-worker nova-monitor "
            "nova-sandbox-manager nginx"
        )
        assert f"pull {pull_services}" in workflow or f"pull --quiet {pull_services}" in workflow
        assert (
            "up -d --force-recreate --no-build --remove-orphans "
            "nova-migrate nova-web nova-worker nova-monitor nova-sandbox-manager nginx"
        ) in workflow
        assert "if ! docker compose" in workflow
        assert "logs --no-color --tail=200 nova-migrate" in workflow
        assert (
            "ps --status running --services nova-worker | grep -Fxq \"nova-worker\""
        ) in workflow
        assert (
            "ps --status running --services nova-sandbox-manager | "
            "grep -Fxq \"nova-sandbox-manager\""
        ) in workflow
        assert (
            'docker pull "$SANDBOX_CONFIGURED_REF"' in workflow
            or 'docker pull --quiet "$SANDBOX_CONFIGURED_REF"' in workflow
        )


def test_deployment_workflows_retry_transient_registry_reads() -> None:
    for workflow_path in WORKFLOW_PATHS:
        workflow = workflow_path.read_text(encoding="utf-8")

        assert (
            'bash "$GITHUB_WORKSPACE/.github/scripts/'
            'run-with-transient-registry-retry.sh"'
        ) in workflow
        assert (
            "docker pull --quiet \"$SANDBOX_CONFIGURED_REF\""
            in workflow
        )
        assert (
            "pull --quiet " + "nova-migrate nova-web nova-worker nova-monitor "
            "nova-sandbox-manager nginx mysql redis"
            in workflow
        )
        assert "export NOVA_PULL_POLICY=never" in workflow


def test_registry_retry_helper_retries_eof_and_preserves_success() -> None:
    if platform.system() != "Linux" or shutil.which("bash") is None:
        return

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        counter_path = temp_path / "attempts"
        command_path = temp_path / "fake-registry-command"
        command_path.write_text(
            textwrap.dedent(
                """
                #!/usr/bin/env python3
                import os
                import pathlib
                import sys

                counter = pathlib.Path(os.environ["FAKE_COUNTER"])
                attempt = int(counter.read_text()) if counter.exists() else 0
                counter.write_text(str(attempt + 1))
                if attempt < 2:
                    print("failed to read manifest: EOF", file=sys.stderr)
                    raise SystemExit(23)
                print("pulled")
                """
            ).lstrip(),
            encoding="utf-8",
        )
        command_path.chmod(0o755)
        environment = os.environ.copy()
        environment.update(
            {
                "FAKE_COUNTER": str(counter_path),
                "TRANSIENT_REGISTRY_MAX_ATTEMPTS": "3",
                "TRANSIENT_REGISTRY_RETRY_DELAY_SECONDS": "0",
            }
        )

        result = subprocess.run(
            ["bash", str(RETRY_HELPER_PATH), str(command_path)],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert counter_path.read_text(encoding="utf-8") == "3"
        assert "retrying" in result.stderr


def test_registry_retry_helper_fails_fast_for_non_transient_errors() -> None:
    if platform.system() != "Linux" or shutil.which("bash") is None:
        return

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        counter_path = temp_path / "attempts"
        command_path = temp_path / "fake-registry-command"
        command_path.write_text(
            textwrap.dedent(
                """
                #!/usr/bin/env python3
                import os
                import pathlib
                import sys

                counter = pathlib.Path(os.environ["FAKE_COUNTER"])
                attempt = int(counter.read_text()) if counter.exists() else 0
                counter.write_text(str(attempt + 1))
                print("manifest unknown", file=sys.stderr)
                raise SystemExit(37)
                """
            ).lstrip(),
            encoding="utf-8",
        )
        command_path.chmod(0o755)
        environment = os.environ.copy()
        environment.update(
            {
                "FAKE_COUNTER": str(counter_path),
                "TRANSIENT_REGISTRY_MAX_ATTEMPTS": "3",
                "TRANSIENT_REGISTRY_RETRY_DELAY_SECONDS": "0",
            }
        )

        result = subprocess.run(
            ["bash", str(RETRY_HELPER_PATH), str(command_path)],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )

        assert result.returncode == 37
        assert counter_path.read_text(encoding="utf-8") == "1"


def test_registry_retry_helper_preserves_exhausted_transient_exit_code() -> None:
    if platform.system() != "Linux" or shutil.which("bash") is None:
        return

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        counter_path = temp_path / "attempts"
        command_path = temp_path / "fake-registry-command"
        command_path.write_text(
            textwrap.dedent(
                """
                #!/usr/bin/env python3
                import os
                import pathlib
                import sys

                counter = pathlib.Path(os.environ["FAKE_COUNTER"])
                attempt = int(counter.read_text()) if counter.exists() else 0
                counter.write_text(str(attempt + 1))
                print("failed to read manifest: EOF", file=sys.stderr)
                raise SystemExit(41)
                """
            ).lstrip(),
            encoding="utf-8",
        )
        command_path.chmod(0o755)
        environment = os.environ.copy()
        environment.update(
            {
                "FAKE_COUNTER": str(counter_path),
                "TRANSIENT_REGISTRY_MAX_ATTEMPTS": "2",
                "TRANSIENT_REGISTRY_RETRY_DELAY_SECONDS": "0",
            }
        )

        result = subprocess.run(
            ["bash", str(RETRY_HELPER_PATH), str(command_path)],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )

        assert result.returncode == 41
        assert counter_path.read_text(encoding="utf-8") == "2"


def test_deployment_workflows_preflight_container_proxy_reachability() -> None:
    for workflow_path in WORKFLOW_PATHS:
        workflow = workflow_path.read_text(encoding="utf-8")

        assert "Proxy connectivity preflight" in workflow
        assert 'NOVA_OUTBOUND_PROXY_URL' in workflow
        assert "socket.create_connection" in workflow
        assert "host.docker.internal" in workflow


def test_publish_workflow_builds_and_publishes_the_runtime_image() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish-test-image.yml").read_text(
        encoding="utf-8"
    )
    assert "RUNTIME_IMAGE_NAME: ghcr.io/${{ github.repository_owner }}/nova-sandbox-runtime" in workflow
    assert "context: sandbox-runtime" in workflow
    assert "id: build_runtime" in workflow
    assert "runtime_digest" in workflow


def test_publish_workflow_recovers_squash_merges_and_supports_manual_replay() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish-test-image.yml").read_text(
        encoding="utf-8"
    )

    # Squash merge messages can contain a historical ``[skip ci]`` marker,
    # which suppresses the subsequent develop push event.  The deployment
    # workflow must therefore have a merged-PR fallback and an operator
    # replay path for the already-merged commit.
    assert "  workflow_dispatch:" in workflow
    assert "  pull_request_target:" in workflow
    assert "    types: [closed]" in workflow
    assert "github.event.pull_request.merged == true" in workflow


def test_publish_workflow_uses_trusted_skip_ci_fallback_without_duplicate_pr_publish() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish-test-image.yml").read_text(
        encoding="utf-8"
    )

    # A pull_request token cannot publish to GHCR reliably for collaborator merges.
    # The fallback must run in the base-repository context and only publish when
    # the merged commit really contains a skip-CI marker; ordinary merges use the
    # single develop push run and must not publish twice.
    assert "  pull_request_target:" in workflow
    assert "    types: [closed]" in workflow
    assert "fallback_check:" in workflow
    assert "github.event.pull_request.merge_commit_sha" in workflow
    assert "skip ci" in workflow.lower()
    assert "ci skip" in workflow.lower()
    assert "pull_request:" not in workflow
    assert "needs: fallback_check" in workflow
    assert "always()" in workflow
    assert "      packages: write" in workflow
    assert "      packages: read" in workflow


def test_deploy_workflows_overlay_published_digests_without_mutating_server_env() -> None:
    for workflow_path in WORKFLOW_PATHS:
        workflow = workflow_path.read_text(encoding="utf-8")

        assert 'DEPLOY_ENV_FILE="$(mktemp' in workflow
        assert 'export NOVA_ENV_FILE="$DEPLOY_ENV_FILE"' in workflow
        assert 'if [ ! -r "$DEPLOY_DIR/.env" ]' in workflow
        assert 'awk -v nova_image_ref="$NOVA_IMAGE_REF"' in workflow
        assert '-v sandbox_runtime_ref="$SANDBOX_RUNTIME_REF"' in workflow
        assert 'print "NOVA_IMAGE_REF=\\\"" nova_image_ref' in workflow
        assert (
            'print "NLP_AGENT_SANDBOX_DOCKER_IMAGE_DIGEST=\\\"" '
            'sandbox_runtime_ref'
        ) in workflow
        assert 'rm -f "$DEPLOY_ENV_FILE"' in workflow
        assert "The deployment directory" in workflow


def test_test_deploy_cleans_before_and_after_pull_without_removing_volumes() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "publish-test-image.yml").read_text(
            encoding="utf-8"
        )
    )
    steps = workflow["jobs"]["deploy"]["steps"]
    named_steps = {
        step.get("name"): (index, step)
        for index, step in enumerate(steps)
        if step.get("name")
    }
    cleanup_index, cleanup = named_steps["Cleanup unused Docker resources"]
    deploy_index, deploy = named_steps["Deploy the published GHCR image"]
    post_cleanup_index, post_cleanup = named_steps["Cleanup Docker resources after deployment"]

    # The runner keeps the database/Redis/application volumes, but old image
    # layers, networks, containers, and builder cache have no deployment value.
    assert cleanup_index < deploy_index
    assert deploy_index < post_cleanup_index
    assert cleanup.get("if") != "always()"
    assert post_cleanup.get("if") == "always()"
    for step in (cleanup, post_cleanup):
        assert "docker system prune -af" in step["run"]
        assert "docker builder prune -af" in step["run"]
        assert "--volumes" not in step["run"]
        assert "docker volume prune" not in step["run"]
    assert "docker system df" in post_cleanup["run"]
    assert 'docker pull --quiet "$SANDBOX_CONFIGURED_REF"' in deploy["run"]
    assert "pull --quiet nova-migrate nova-web nova-worker nova-monitor nova-sandbox-manager nginx" in deploy["run"]
    assert 'docker image inspect "$SANDBOX_CONFIGURED_REF"' in deploy["run"]
    assert "timeout-minutes" not in workflow["jobs"]["deploy"]


def test_compose_limits_container_stdout_log_growth() -> None:
    compose = yaml.safe_load(
        (ROOT / "compose.yaml").read_text(encoding="utf-8")
    )
    expected_logging = {
        "driver": "json-file",
        "options": {"max-size": "20m", "max-file": "3"},
    }

    assert compose["x-default-logging"] == expected_logging
    for service in (
        "nginx",
        "mysql",
        "redis",
        "nova-migrate",
        "nova-web",
        "nova-worker",
        "nova-sandbox-manager",
        "nova-monitor",
    ):
        assert compose["services"][service]["logging"] == expected_logging


def test_ci_workflow_can_be_dispatched_after_a_skip_ci_metadata_commit() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "  workflow_dispatch:" in workflow


def test_test_deploy_workflow_exposes_the_monitor_on_the_test_host_port() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish-test-image.yml").read_text(
        encoding="utf-8"
    )
    test_env = (ROOT / "deploy" / "env" / "test.env.example").read_text(
        encoding="utf-8"
    )

    assert 'NOVA_MONITOR_BIND_ADDRESS=\\"0.0.0.0\\"' in workflow
    assert 'NOVA_MONITOR_BIND_ADDRESS="0.0.0.0"' in test_env
