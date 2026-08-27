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


def test_ci_workflow_can_be_dispatched_after_a_skip_ci_metadata_commit() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "  workflow_dispatch:" in workflow
