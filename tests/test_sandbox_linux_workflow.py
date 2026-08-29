from __future__ import annotations

from pathlib import Path


def test_linux_smoke_uses_the_registered_gvisor_runtime() -> None:
    workflow = Path(".github/workflows/sandbox-linux.yml").read_text(encoding="utf-8")
    auth_lifecycle = workflow.split("  sandbox-auth-lifecycle:", 1)[1].split(
        "  sandbox-manager-runsc:", 1
    )[0]

    assert "docker info --format '{{json .Runtimes}}'" in workflow
    assert "--runtime runsc" in workflow
    assert "RUN_SANDBOX_DOCKER_INTEGRATION=1" in workflow
    assert "RUN_SANDBOX_REDIS_INTEGRATION=1" in workflow
    assert "NLP_AGENT_DOCKER_RUNTIME: runsc" in workflow
    assert "Install gVisor runsc for lifecycle tests" not in auth_lifecycle
    assert "NLP_AGENT_DOCKER_RUNTIME: runsc" not in auth_lifecycle
    assert "sandbox-manager-runsc" in workflow
    assert "docker run --detach --name nova-ci-mysql" in workflow
    assert "--measure-manager-claim" in workflow
    assert "sandbox-manager-claim-benchmark" in workflow
    assert 'IMAGE_ID=$(docker image inspect nova-sandbox-runtime:manager-ci --format' in workflow
    assert 'nova-sandbox-runtime:manager-ci@${IMAGE_ID}' not in workflow
    assert "contents: write" in workflow
    assert "Persist preload compatibility matrix" in workflow
    assert "git push origin HEAD:${GITHUB_REF_NAME}" in workflow
    assert "feature/sandbox-phase3-develop" in workflow
    assert "pip install -e ." not in workflow
    assert "pip install -r requirements.txt" in workflow


def test_linux_smoke_executes_the_pinned_cpu_torch_import() -> None:
    workflow = Path(".github/workflows/sandbox-linux.yml").read_text(encoding="utf-8")

    assert 'docker exec nova-ci python -c "import torch;' in workflow
    assert "torch.__version__.split('+')[0] == '2.7.1'" in workflow
    assert "not torch.cuda.is_available()" in workflow


def test_matrix_writeback_dispatches_main_ci_for_the_new_branch_head() -> None:
    workflow = Path(".github/workflows/sandbox-linux.yml").read_text(encoding="utf-8")
    persistence = workflow.split("      - name: Persist preload compatibility matrix", 1)[1]

    assert "actions: write" in workflow
    assert 'git commit -m "ci(sandbox): update preload compatibility matrix"' in persistence
    assert "[skip ci]" not in persistence
    assert 'gh workflow run CI --ref "${GITHUB_REF_NAME}"' in persistence
