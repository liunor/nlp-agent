from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATHS = (
    ROOT / ".github" / "workflows" / "publish-test-image.yml",
    ROOT / ".github" / "workflows" / "release-prod.yml",
)


def test_deployment_workflows_update_and_check_worker() -> None:
    for workflow_path in WORKFLOW_PATHS:
        workflow = workflow_path.read_text(encoding="utf-8")

        assert "pull nova-migrate nova-web nova-worker nova-monitor nginx" in workflow
        assert (
            "up -d --force-recreate --no-build --remove-orphans "
            "nova-migrate nova-web nova-worker nova-monitor nginx"
        ) in workflow
        assert (
            "ps --status running --services nova-worker | grep -Fxq \"nova-worker\""
        ) in workflow
