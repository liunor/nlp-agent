"""Provision disposable MySQL/Redis + 50 students; execute a genuine load run."""

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
import threading

from tests.api_http.support.environment import ManagedHttpEnvironment, ROOT, TEST_PASSWORD
from tests.load.fake_model_provider import FakeProviderServer, FakeProviderState


def classroom_user_data(profile):
    """Validate the same identities the load client will use, before DB setup."""
    from server.user.schemas import UserCreate

    return [
        UserCreate(
            username=profile["username_format"].format(index=index),
            display_name=f"T07 student {index}", password=TEST_PASSWORD,
        )
        for index in range(1, int(profile["user_count"]) + 1)
    ]


def database_environment(profile, *, worker=False):
    """Start Web/Worker with the pool values recorded in the load profile."""
    prefix = "worker_" if worker else ""
    sync_prefix = "worker_sync_" if worker else "database_sync_"
    return {
        "NLP_AGENT_DB_POOL_SIZE": str(int(profile[f"{prefix}database_pool_size"])),
        "NLP_AGENT_DB_MAX_OVERFLOW": str(int(profile[f"{prefix}database_max_overflow"])),
        "NLP_AGENT_DB_SYNC_POOL_SIZE": str(int(profile[f"{sync_prefix}pool_size"])),
        "NLP_AGENT_DB_SYNC_MAX_OVERFLOW": str(int(profile[f"{sync_prefix}max_overflow"])),
        "NLP_AGENT_DB_POOL_TIMEOUT_S": str(int(profile["database_pool_timeout_seconds"])),
    }


async def seed_classroom(environment, profile):
    from sqlalchemy import select
    from server.infrastructure.mysql import MySQLRuntime
    from server.infrastructure.mysql.models import ClassroomMemberModel
    from server.rbac.service import rbac_service
    from server.user.service import UserService

    students = classroom_user_data(profile)
    runtime = MySQLRuntime.from_runtime({"url": environment.env["NLP_AGENT_DATABASE_URL"], "pool_size": 2, "max_overflow": 0})
    await runtime.start()
    try:
        async with runtime.session_factory.begin() as session:
            teacher = environment.users["teacher"]
            classroom = await rbac_service.create_classroom(session, workspace_id=teacher.workspace_id, name="T07 50-student load classroom", actor_user_id=teacher.user_id)
            service = UserService(session)
            for data in students:
                user = await service.create_user(data, initial_role_code="student")
                session.add(ClassroomMemberModel(classroom_id=classroom.id, user_id=user.id, member_role="student", status="active"))
            await session.flush()
            assert len((await session.scalars(select(ClassroomMemberModel).where(ClassroomMemberModel.classroom_id == classroom.id, ClassroomMemberModel.member_role == "student"))).all()) == 50
    finally:
        await runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-kind", choices=("baseline", "regression"), required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--concurrency", type=int, choices=(1, 4), default=1)
    parser.add_argument("--replicas", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    if args.run_kind == "baseline" and (args.concurrency, args.replicas) != (1, 1):
        parser.error("frozen baseline must use its actual 1x1 execution topology")
    from tests.load.t07_classroom_load import validate_output_path, validate_run_kind, BASELINE_PRODUCT_COMMIT
    output = validate_output_path(args.output)
    validate_run_kind(argparse.Namespace(run_kind=args.run_kind, baseline_ref=BASELINE_PRODUCT_COMMIT), output)
    profile = json.loads((ROOT / "tests/load/profiles/t07-50-users.json").read_text(encoding="utf-8"))
    classroom_user_data(profile)
    web_database_env = database_environment(profile)
    worker_database_env = database_environment(profile, worker=True)
    token = secrets.token_urlsafe(24)
    fake = FakeProviderServer(("127.0.0.1", 0), state=FakeProviderState(delay_s=20, timeout_delay_s=70), control_token=token)
    thread = threading.Thread(target=fake.serve_forever, daemon=True)
    thread.start()

    class LoadEnvironment(ManagedHttpEnvironment):
        def _start_server(self, *, module, port, log_name):
            if log_name == "web.log":
                self.env.update(T07_LOAD_RUN_KIND=args.run_kind, T07_FAKE_PROVIDER_CONTROL_URL=f"http://127.0.0.1:{fake.server_port}", T07_FAKE_PROVIDER_API_KEY="isolated-test-key", NLP_AGENT_WORKER_CONCURRENCY=str(args.concurrency))
                self.env.update(web_database_env)
                self.worker_module = "tests.load.worker_entrypoint"
                asyncio.run(seed_classroom(self, profile))
                module = "tests.load.deployment_entrypoint:app"
            super()._start_server(module=module, port=port, log_name=log_name)

        def _start_worker(self):
            web_environment = self.env
            self.env = {**web_environment, **worker_database_env}
            try:
                super()._start_worker()
            finally:
                self.env = web_environment

    environment = None
    previous_artifact_dir = os.environ.get("API_HTTP_ARTIFACT_DIR")
    # start() cleans up on a boot failure before returning an environment.
    # Select diagnostics now so that failure preserves the real Web error.
    os.environ["API_HTTP_ARTIFACT_DIR"] = str(output.parent / (output.stem + "-logs"))
    try:
        environment = LoadEnvironment.start()
        for _index in range(1, args.replicas):
            environment._start_worker()
        profile.update(worker_replicas=args.replicas, worker_concurrency=args.concurrency, turn_timeout_seconds=700)
        profile_path = environment.log_dir / "load-profile.json"
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        command = [sys.executable, "-m", "tests.load.t07_classroom_load", "--base-url", environment.web_base_url, "--profile", str(profile_path), "--run-kind", args.run_kind, "--output", str(output)]
        if args.run_kind == "baseline":
            command.extend(["--baseline-ref", BASELINE_PRODUCT_COMMIT])
        result = subprocess.run(command, cwd=ROOT, env={**os.environ, "T07_TEST_PASSWORD": TEST_PASSWORD, "T07_FAKE_PROVIDER_CONTROL_URL": f"http://127.0.0.1:{fake.server_port}", "T07_FAKE_PROVIDER_CONTROL_TOKEN": token}, timeout=1100)
        return result.returncode
    finally:
        if environment is not None:
            # Logs are copied under the requested report directory with secrets redacted.
            os.environ["API_HTTP_ARTIFACT_DIR"] = str(output.parent / (output.stem + "-logs"))
            environment.close()
        if previous_artifact_dir is None:
            os.environ.pop("API_HTTP_ARTIFACT_DIR", None)
        else:
            os.environ["API_HTTP_ARTIFACT_DIR"] = previous_artifact_dir
        fake.shutdown()
        fake.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
