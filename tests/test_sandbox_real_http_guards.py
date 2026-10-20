"""The real HTTP/runsc harness must be confined to disposable test resources."""

import json
from subprocess import CompletedProcess

import pytest

from tests.api_http.support.environment import ApiHttpEnvironmentError, ManagedHttpEnvironment


@pytest.fixture
def harness(tmp_path):
    environment = ManagedHttpEnvironment.__new__(ManagedHttpEnvironment)
    environment.env = {}
    environment.log_dir = tmp_path
    environment.sandbox_namespace = None
    return environment


def test_real_http_mode_rejects_implicit_enablement_and_mutable_images(monkeypatch, harness):
    monkeypatch.delenv("RUN_SANDBOX_DOCKER_MANAGER_INTEGRATION", raising=False)
    with pytest.raises(ApiHttpEnvironmentError, match="explicit Docker integration gate"):
        harness._configure_real_sandbox("sha256:" + "a" * 64, "a" * 12)
    monkeypatch.setenv("RUN_SANDBOX_DOCKER_MANAGER_INTEGRATION", "1")
    with pytest.raises(ApiHttpEnvironmentError, match="immutable local image ID"):
        harness._configure_real_sandbox("runtime:latest", "a" * 12)
    with pytest.raises(ApiHttpEnvironmentError, match="private run namespace"):
        harness._configure_real_sandbox("sha256:" + "a" * 64, "prod")
    assert harness.env == {} and harness.sandbox_namespace is None


@pytest.mark.parametrize("mismatch", ["namespace", "managed", "business_mount"])
def test_cleanup_validates_all_containers_before_removing_any(monkeypatch, harness, mismatch):
    from tests.api_http.support import environment as module

    namespace = "api-http-" + "a" * 12
    harness.sandbox_namespace = namespace
    first, second = "b" * 64, "c" * 64
    calls = []

    def docker(command, **kwargs):
        calls.append(command)
        if command[1] == "ps":
            return CompletedProcess(command, 0, first + "\n" + second + "\n", "")
        assert command[1] == "inspect", "cleanup must refuse the whole selection before docker rm"
        row = {"Config": {"Labels": {"nova.sandbox.managed": namespace, "nova.sandbox.namespace": namespace}},
               "Mounts": [{"Type": "tmpfs"}]}
        if command[-1] == second:
            if mismatch == "business_mount":
                row["Mounts"] = [{"Type": "volume"}]
            else:
                row["Config"]["Labels"]["nova.sandbox." + mismatch] = "prod"
        return CompletedProcess(command, 0, json.dumps([row]), "")

    monkeypatch.setattr(module.subprocess, "run", docker)
    with pytest.raises(ApiHttpEnvironmentError, match="identity/mount mismatch"):
        harness._remove_owned_sandbox_containers()
    assert all(command[1] != "rm" for command in calls)


def test_ci_manager_refuses_non_test_database_before_starting(monkeypatch):
    from tests.api_http.support import real_sandbox_manager as module

    monkeypatch.setenv("NLP_AGENT_API_HTTP_REAL_SANDBOX", "1")
    monkeypatch.setenv("RUN_SANDBOX_DOCKER_MANAGER_INTEGRATION", "1")
    monkeypatch.setattr(module.settings, "NLP_AGENT_DATABASE_URL", "mysql+aiomysql://a:b@127.0.0.1/nova_test")
    monkeypatch.setattr(module.settings, "NLP_AGENT_SANDBOX_NAMESPACE", "api-http-" + "a" * 12)
    monkeypatch.setattr(module.settings, "NLP_AGENT_SANDBOX_DOCKER_IMAGE_DIGEST", "sha256:" + "a" * 64)
    monkeypatch.setattr(module.manager_runner, "main", lambda: pytest.fail("must not start on an existing database"))
    with pytest.raises(RuntimeError, match="isolated HTTP-test resources"):
        module.main()
