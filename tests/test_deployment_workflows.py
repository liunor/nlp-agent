from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATHS = (
    ROOT / ".github" / "workflows" / "publish-test-image.yml",
    ROOT / ".github" / "workflows" / "release-prod.yml",
)


def test_deployment_workflows_update_and_check_worker() -> None:
    for workflow_path in WORKFLOW_PATHS:
        workflow = workflow_path.read_text(encoding="utf-8")

        assert (
            "pull nova-migrate nova-web nova-worker nova-monitor "
            "nova-sandbox-manager nginx"
        ) in workflow
        assert (
            "up -d --force-recreate --no-build --remove-orphans "
            "nova-migrate nova-web nova-worker nova-monitor nova-sandbox-manager nginx"
        ) in workflow
        assert (
            "ps --status running --services nova-worker | grep -Fxq \"nova-worker\""
        ) in workflow
        assert (
            "ps --status running --services nova-sandbox-manager | "
            "grep -Fxq \"nova-sandbox-manager\""
        ) in workflow
        assert 'docker pull "$SANDBOX_CONFIGURED_REF"' in workflow


def test_publish_workflow_builds_and_publishes_the_runtime_image() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish-test-image.yml").read_text(
        encoding="utf-8"
    )
    assert "RUNTIME_IMAGE_NAME: ghcr.io/${{ github.repository_owner }}/nova-sandbox-runtime" in workflow
    assert "context: sandbox-runtime" in workflow
    assert "id: build_runtime" in workflow
    assert "runtime_digest" in workflow


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


def test_ci_workflow_can_be_dispatched_after_a_skip_ci_metadata_commit() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "  workflow_dispatch:" in workflow
