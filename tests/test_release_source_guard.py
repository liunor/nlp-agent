"""Exercise latest-source decisions without a registry or deployment."""
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
REVISION = "a" * 40


@pytest.fixture
def guard():
    spec = importlib.util.spec_from_file_location("release_source", ROOT / ".github/scripts/current-release-source.py")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.mark.parametrize("branch", ["develop", "main"])
@pytest.mark.parametrize("latest", [REVISION, "b" * 40])
def test_only_current_checkout_is_eligible(guard, monkeypatch, branch, latest):
    responses = iter([REVISION, latest+"\trefs/heads/"+branch])
    monkeypatch.setattr(guard, "git", lambda *args: next(responses))
    assert guard.current_source(branch, REVISION) is (latest == REVISION)


@pytest.mark.parametrize("remote", ["", "a"*40+" refs/heads/main", "not-a-sha refs/heads/develop",
                                    REVISION+" refs/heads/develop\n"+REVISION+" refs/heads/develop"])
def test_missing_or_ambiguous_ref_fails_closed(guard, monkeypatch, remote):
    responses = iter([REVISION, remote])
    monkeypatch.setattr(guard, "git", lambda *args: next(responses))
    with pytest.raises(ValueError):
        guard.current_source("develop", REVISION)


def test_wrong_checkout_cannot_publish_or_deploy(guard, monkeypatch):
    monkeypatch.setattr(guard, "git", lambda *args: "b"*40)
    with pytest.raises(ValueError, match="CHECKOUT_REVISION_MISMATCH"):
        guard.current_source("develop", REVISION)


@pytest.mark.parametrize("branch,revision", [("feature/untrusted", REVISION), ("develop", "bad\ncurrent=true")])
def test_arguments_cannot_inject_refs_or_outputs(guard, branch, revision):
    with pytest.raises(ValueError, match="INVALID_RELEASE_SOURCE"):
        guard.current_source(branch, revision)


def test_unreadable_remote_never_becomes_successful_skip(guard, monkeypatch):
    monkeypatch.setattr(guard.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 128, "", "private remote error"))
    with pytest.raises(ValueError, match="RELEASE_SOURCE_READ_FAILED"):
        guard.git("ls-remote", "origin")


def test_actual_git_guard_skips_an_obsolete_checkout_and_does_not_move_it(tmp_path):
    remote, checkout = tmp_path / "remote.git", tmp_path / "checkout"
    remote.mkdir()
    checkout.mkdir()

    def run(*args, cwd=checkout):
        return subprocess.run(["git", "-c", "safe.directory="+cwd.as_posix(),
                               "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
                              cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    run("init", "--bare", cwd=remote)
    run("init", "-b", "develop")
    (checkout / "fixture.txt").write_text("first", encoding="utf-8")
    run("add", "fixture.txt")
    run("commit", "-m", "first fixture")
    first = run("rev-parse", "HEAD")
    run("remote", "add", "origin", str(remote))
    run("push", "origin", "develop")
    script = ROOT / ".github/scripts/current-release-source.py"
    # Run from a user-owned disposable repository; no network remote is used.
    command = [sys.executable, str(script), "--branch", "develop", "--revision", first]
    assert subprocess.run(command, cwd=checkout, check=True, capture_output=True, text=True).stdout.strip() == "true"
    (checkout / "fixture.txt").write_text("second", encoding="utf-8")
    run("add", "fixture.txt")
    run("commit", "-m", "second fixture")
    run("push", "origin", "develop")
    run("checkout", "--detach", first)
    output = tmp_path / "job-output"
    result = subprocess.run([*command, "--github-output", str(output)], cwd=checkout,
                            check=True, capture_output=True, text=True)
    assert result.stdout.strip() == "false"
    assert "current=false" in output.read_text(encoding="utf-8")
    assert run("rev-parse", "HEAD") == first
