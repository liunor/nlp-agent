"""Provision disposable MySQL/Redis + 50 students; execute a genuine load run."""

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
import threading
import time

from tests.api_http.support.environment import ManagedHttpEnvironment, ROOT, TEST_PASSWORD
from tests.load.fake_model_provider import FakeProviderServer, FakeProviderState

WEB_POOLS = {"NLP_AGENT_DB_POOL_SIZE": "5", "NLP_AGENT_DB_MAX_OVERFLOW": "5",
             "NLP_AGENT_DB_SYNC_POOL_SIZE": "5", "NLP_AGENT_DB_SYNC_MAX_OVERFLOW": "5",
             "NLP_AGENT_DB_POOL_TIMEOUT_S": "5"}
WORKER_POOLS = {"NLP_AGENT_DB_POOL_SIZE": "2", "NLP_AGENT_DB_MAX_OVERFLOW": "1",
                "NLP_AGENT_DB_SYNC_POOL_SIZE": "4", "NLP_AGENT_DB_SYNC_MAX_OVERFLOW": "2",
                "NLP_AGENT_DB_POOL_TIMEOUT_S": "5"}


def student_username(index):
    return f"t07student{index:02d}"


def measured_profile(profile, web_env, worker_env, *, replicas, concurrency):
    """Describe the exact env supplied to the isolated child processes."""
    result = dict(profile, worker_replicas=replicas, worker_concurrency=concurrency,
                  turn_timeout_seconds=700)
    for key, variable, env in (
        ("database_pool_size", "NLP_AGENT_DB_POOL_SIZE", web_env),
        ("database_max_overflow", "NLP_AGENT_DB_MAX_OVERFLOW", web_env),
        ("database_sync_pool_size", "NLP_AGENT_DB_SYNC_POOL_SIZE", web_env),
        ("database_sync_max_overflow", "NLP_AGENT_DB_SYNC_MAX_OVERFLOW", web_env),
        ("worker_database_pool_size", "NLP_AGENT_DB_POOL_SIZE", worker_env),
        ("worker_database_max_overflow", "NLP_AGENT_DB_MAX_OVERFLOW", worker_env),
        ("worker_sync_pool_size", "NLP_AGENT_DB_SYNC_POOL_SIZE", worker_env),
        ("worker_sync_max_overflow", "NLP_AGENT_DB_SYNC_MAX_OVERFLOW", worker_env),
        ("database_pool_timeout_seconds", "NLP_AGENT_DB_POOL_TIMEOUT_S", worker_env),
    ):
        result[key] = int(env.get(variable, "5"))
    return result


def verify_worker_topology(environment, replicas, concurrency):
    """Observe actual live heartbeats in the disposable Redis before claiming replicas."""
    from redis import Redis
    client = Redis(host="127.0.0.1", port=environment.redis_port, db=environment.redis_db,
                   decode_responses=True, socket_timeout=3, socket_connect_timeout=3)
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if any(process.poll() is not None for process in environment.capacity_workers):
                raise RuntimeError("ISOLATED_CAPACITY_WORKER_EXITED")
            keys = list(client.scan_iter(match="nlp-agent:workers:heartbeat:*", count=100))
            gauges = [json.loads(value) for key in keys if (value := client.get(key))]
            if len(gauges) == replicas and all(item.get("concurrency") == concurrency for item in gauges):
                return {"observed_replicas": len(gauges), "concurrency_per_replica": concurrency}
            time.sleep(.5)
        raise RuntimeError("ISOLATED_WORKER_TOPOLOGY_NOT_OBSERVED")
    finally:
        client.close()


async def seed_classroom(environment):
    from sqlalchemy import select
    from server.infrastructure.mysql import MySQLRuntime
    from server.infrastructure.mysql.models import ClassroomMemberModel
    from server.rbac.service import rbac_service
    from server.user.schemas import UserCreate
    from server.user.service import UserService

    runtime = MySQLRuntime.from_runtime({"url": environment.env["NLP_AGENT_DATABASE_URL"], "pool_size": 2, "max_overflow": 0})
    await runtime.start()
    try:
        async with runtime.session_factory.begin() as session:
            teacher = environment.users["teacher"]
            classroom = await rbac_service.create_classroom(session, workspace_id=teacher.workspace_id, name="T07 50-student load classroom", actor_user_id=teacher.user_id)
            service = UserService(session)
            for index in range(1, 51):
                user = await service.create_user(UserCreate(username=student_username(index), display_name=f"T07 student {index}", password=TEST_PASSWORD), initial_role_code="student")
                session.add(ClassroomMemberModel(classroom_id=classroom.id, user_id=user.id, member_role="student", status="active"))
            await session.flush()
            assert len((await session.scalars(select(ClassroomMemberModel).where(ClassroomMemberModel.classroom_id == classroom.id, ClassroomMemberModel.member_role == "student"))).all()) == 50
    finally:
        await runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-kind", choices=("baseline", "regression"), required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--concurrency", type=int, choices=(1, 2, 4), default=1)
    parser.add_argument("--replicas", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    if args.run_kind == "baseline" and (args.concurrency, args.replicas) != (1, 1):
        parser.error("frozen baseline must use its actual 1x1 execution topology")
    from tests.load.t07_classroom_load import validate_output_path, validate_run_kind, BASELINE_PRODUCT_COMMIT
    output = validate_output_path(args.output)
    validate_run_kind(argparse.Namespace(run_kind=args.run_kind, baseline_ref=BASELINE_PRODUCT_COMMIT), output)
    token = secrets.token_urlsafe(24)
    fake = FakeProviderServer(("127.0.0.1", 0), state=FakeProviderState(delay_s=20, timeout_delay_s=70), control_token=token)
    thread = threading.Thread(target=fake.serve_forever, daemon=True)
    thread.start()

    class LoadEnvironment(ManagedHttpEnvironment):
        def _start_server(self, *, module, port, log_name):
            if log_name == "web.log":
                if args.run_kind == "regression":
                    self.env.update(WEB_POOLS)
                self.env.update(T07_LOAD_RUN_KIND=args.run_kind, T07_FAKE_PROVIDER_CONTROL_URL=f"http://127.0.0.1:{fake.server_port}", T07_FAKE_PROVIDER_API_KEY="isolated-test-key", NLP_AGENT_WORKER_CONCURRENCY=str(args.concurrency))
                self.worker_module = "tests.load.worker_entrypoint"
                asyncio.run(seed_classroom(self))
                module = "tests.load.deployment_entrypoint:app"
            super()._start_server(module=module, port=port, log_name=log_name)

        def _start_worker(self):
            self.worker_env = dict(self.env)
            if args.run_kind == "regression":
                self.worker_env.update(WORKER_POOLS)
            # Appending preserves the first replica's diagnostics when adding a second.
            handle = (self.log_dir / "worker.log").open("a", encoding="utf-8")
            self.log_handles.append(handle)
            process = subprocess.Popen([sys.executable, "-m", self.worker_module], cwd=ROOT,
                                       env=self.worker_env, stdout=handle, stderr=subprocess.STDOUT,
                                       text=True, creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            self.processes.append(process)
            self.worker_process = process
            if not hasattr(self, "capacity_workers"):
                self.capacity_workers = []
            self.capacity_workers.append(process)

    environment = None
    try:
        environment = LoadEnvironment.start()
        for _index in range(1, args.replicas):
            environment._start_worker()
        # Frozen product baselines may predate the heartbeat contract.
        topology = verify_worker_topology(environment, args.replicas, args.concurrency) if args.run_kind == "regression" else None
        profile = json.loads((ROOT / "tests/load/profiles/t07-50-users.json").read_text(encoding="utf-8"))
        profile = measured_profile(profile, environment.env, environment.worker_env,
                                   replicas=args.replicas, concurrency=args.concurrency)
        profile_path = environment.log_dir / "load-profile.json"
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        command = [sys.executable, "-m", "tests.load.t07_classroom_load", "--base-url", environment.web_base_url, "--profile", str(profile_path), "--run-kind", args.run_kind, "--output", str(output)]
        if args.run_kind == "baseline":
            command.extend(["--baseline-ref", BASELINE_PRODUCT_COMMIT])
        result = subprocess.run(command, cwd=ROOT, env={**os.environ, "T07_TEST_PASSWORD": TEST_PASSWORD, "T07_FAKE_PROVIDER_CONTROL_URL": f"http://127.0.0.1:{fake.server_port}", "T07_FAKE_PROVIDER_CONTROL_TOKEN": token}, timeout=1100)
        if args.run_kind == "regression" and output.is_file():
            after = verify_worker_topology(environment, args.replicas, args.concurrency)
            report = json.loads(output.read_text(encoding="utf-8"))
            report["isolated_worker_topology"] = {"before": topology, "after": after}
            report["scope_limit"] = "Disposable MySQL/Redis and fake model on the CI host; not VPS/provider capacity acceptance."
            output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return result.returncode
    finally:
        if environment is not None:
            # Logs are copied under the requested report directory with secrets redacted.
            os.environ["API_HTTP_ARTIFACT_DIR"] = str(output.parent / (output.stem + "-logs"))
            environment.close()
        fake.shutdown()
        fake.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
