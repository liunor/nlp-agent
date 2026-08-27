"""Measure Sandbox startup stages on the target Linux/runsc host.

This intentionally is an opt-in operator script: it never claims a local
Windows result.  Run it in the CI/Linux job with a pinned image and persist the
JSON output as a workflow artifact for the preload compatibility matrix.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig
from server.sandbox.contracts import SandboxScope
from server.sandbox.manager import WarmPoolManager
from server.sandbox.optimization import PreloadCompatibility, check_preload_compatibility


class ManagerClaimProbe:
    """Real MySQL/Manager claim seam used by the Linux benchmark job."""

    def __init__(self, *, engine, factory, manager, scope: SandboxScope, lease_id: str) -> None:
        self.engine = engine
        self.factory = factory
        self.manager = manager
        self.scope = scope
        self.lease_id = lease_id

    async def register_runtime(self, external_runtime_id: str) -> str:
        from server.infrastructure.mysql.models import SandboxRuntimeInstanceModel
        from server.sandbox.warm_pool import RuntimeState

        runtime_id = str(uuid4())
        async with self.factory.begin() as session:
            session.add(
                SandboxRuntimeInstanceModel(
                    id=runtime_id,
                    external_runtime_id=external_runtime_id,
                    runtime_kind="docker",
                    resource_profile_id="python-base",
                    state=RuntimeState.READY_UNBOUND,
                    generation=self.scope.generation,
                )
            )
        return runtime_id

    async def close(self) -> None:
        await self.engine.dispose()


async def seed_manager_claim_probe_records(
    session,
    *,
    user,
    workspace_id: str,
    session_id: str,
    environment_id: str,
    lease_id: str,
    now: datetime,
) -> None:
    """Insert probe parents before the lease row so MySQL FKs are visible."""
    from server.infrastructure.mysql.models import (
        SandboxEnvironmentModel,
        SandboxLeaseModel,
        SessionModel,
    )

    expires_at = now.replace(microsecond=0) + timedelta(hours=1)
    session.add(
        SessionModel(
            id=session_id,
            user_id=user.id,
            workspace_id=workspace_id,
            token_hash=f"benchmark-token-{session_id}",
            csrf_hash=f"benchmark-csrf-{session_id}",
            authorization_version=user.authorization_version,
            expires_at=expires_at,
        )
    )
    session.add(
        SandboxEnvironmentModel(
            id=environment_id,
            owner_user_id=user.id,
            resource_profile_id="python-base",
            generation=1,
        )
    )
    # Flush the referenced session/environment before inserting the lease.
    # MySQL enforces these foreign keys immediately and does not defer them.
    await session.flush()
    session.add(
        SandboxLeaseModel(
            id=lease_id,
            environment_id=environment_id,
            user_id=user.id,
            auth_session_id=session_id,
            workspace_id=workspace_id,
            generation=1,
            state="active",
            expires_at=expires_at,
        )
    )
    await session.flush()


def manager_claim_probe_image() -> str:
    """Return the local CI image used by manager integration containers."""
    return os.getenv("NLP_AGENT_SANDBOX_MANAGER_TEST_IMAGE", "alpine:3.20").strip() or "alpine:3.20"


async def create_manager_claim_probe(adapter: DockerRuntimeAdapter) -> ManagerClaimProbe:
    from sqlalchemy import select

    from server.infrastructure.mysql import DatabaseConfig, create_engine, create_session_factory
    from server.infrastructure.mysql.models import (
        WorkspaceMemberModel,
    )
    from server.user.schemas import UserCreate
    from server.user.service import UserService

    database_url = os.getenv("NLP_AGENT_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("NLP_AGENT_DATABASE_URL is required for real Manager claim benchmarks")
    engine = create_engine(DatabaseConfig(database_url, pool_size=2, max_overflow=0))
    factory = create_session_factory(engine)
    now = datetime.now(UTC).replace(tzinfo=None)
    async with factory.begin() as session:
        user = await UserService(session).create_user(
            UserCreate(
                username=f"benchmark{uuid4().hex[:12]}",
                display_name="Sandbox benchmark",
                password="InitialPw0rd1",
            )
        )
        workspace_id = await session.scalar(
            select(WorkspaceMemberModel.workspace_id).where(WorkspaceMemberModel.user_id == user.id)
        )
        session_id = str(uuid4())
        environment_id = str(uuid4())
        lease_id = str(uuid4())
        await seed_manager_claim_probe_records(
            session,
            user=user,
            workspace_id=str(workspace_id),
            session_id=session_id,
            environment_id=environment_id,
            lease_id=lease_id,
            now=now,
        )
    scope = SandboxScope(
        owner_user_id=str(user.id),
        auth_session_id=session_id,
        workspace_id=str(workspace_id),
        generation=1,
        lease_expires_at=now.replace(tzinfo=UTC) + timedelta(hours=1),
    )
    return ManagerClaimProbe(
        engine=engine,
        factory=factory,
        manager=WarmPoolManager(
            session_factory=factory,
            docker=adapter,
            resource_profile_id="python-base",
            ready_target=0,
        ),
        scope=scope,
        lease_id=lease_id,
    )


def preload_probe_source(modules: tuple[str, ...]) -> str:
    encoded = json.dumps(modules)
    return (
        "import importlib.util, json, sys\n"
        f"mods = {encoded}\n"
        "print(json.dumps({'python_version': sys.version.split()[0], 'modules': "
        "{name: importlib.util.find_spec(name) is not None for name in mods}}))\n"
    )


def percentile(values: list[float], quantile: float) -> float | None:
    """Return a deterministic linear-interpolated percentile for CI reports."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 2)


async def benchmark(
    image: str,
    *,
    iterations: int,
    modules: tuple[str, ...] = (),
    profile_id: str = "python-base",
    runtime_version: str = "nova-runtime",
    matrix_path: Path | None = None,
    update_matrix: bool = False,
    measure_manager_claim: bool = False,
) -> dict[str, object]:
    adapter = DockerRuntimeAdapter(
        DockerRuntimeConfig(
            image=image,
            allow_local_image_id=image.startswith("sha256:"),
        )
    )
    claim_probe = await create_manager_claim_probe(adapter) if measure_manager_claim else None
    samples: list[dict[str, object]] = []
    image_started = perf_counter()
    image_cached = await adapter.image_cached()
    image_cached_ms = round((perf_counter() - image_started) * 1000, 2)
    for index in range(iterations):
        started = perf_counter()
        result = await adapter.run_scratch(source=preload_probe_source(modules))
        elapsed_ms = round((perf_counter() - started) * 1000, 2)
        try:
            probe = json.loads(str(result.get("stdout") or "").strip().splitlines()[-1])
        except (AttributeError, IndexError, json.JSONDecodeError):
            probe = {"python_version": "unknown", "modules": {}}
        stages: dict[str, float | str] = {}
        warm_runtime_id: str | None = None
        runtime_row_id: str | None = None
        try:
            stage_started = perf_counter()
            warm_runtime_id = await adapter.create_l1(name=f"nova-benchmark-{index + 1}")
            stages["create_ms"] = round((perf_counter() - stage_started) * 1000, 2)
            stage_started = perf_counter()
            await adapter.start_l1(warm_runtime_id)
            stages["start_ms"] = round((perf_counter() - stage_started) * 1000, 2)
            stage_started = perf_counter()
            ready = False
            for _ in range(60):
                if await adapter.kernel_ready(warm_runtime_id):
                    ready = True
                    break
                await asyncio.sleep(0.5)
            stages["kernel_ready_ms"] = round((perf_counter() - stage_started) * 1000, 2)
            if not ready:
                raise TimeoutError("benchmark kernel did not become ready")
            if claim_probe is not None:
                runtime_row_id = await claim_probe.register_runtime(warm_runtime_id)
                stage_started = perf_counter()
                claim = await claim_probe.manager.claim(
                    claim_probe.scope, lease_id=claim_probe.lease_id
                )
                stages["claim_ms"] = round((perf_counter() - stage_started) * 1000, 2)
                if claim is None:
                    raise RuntimeError("Manager claim returned no runtime")
                stage_started = perf_counter()
                first_output = await claim_probe.manager.execute_claimed(
                    claim_probe.scope,
                    lease_id=claim_probe.lease_id,
                    runtime_id=str(claim.runtime.id),
                    generation=claim.runtime.generation,
                    nonce=claim.nonce,
                    source=preload_probe_source(modules),
                )
            else:
                stage_started = perf_counter()
                first_output = await adapter.execute(
                    warm_runtime_id,
                    source=preload_probe_source(modules),
                    timeout_seconds=15,
                    output_limit_bytes=1_000_000,
                )
            stages["first_output_ms"] = round((perf_counter() - stage_started) * 1000, 2)
            try:
                probe = json.loads(str(first_output.get("stdout") or "").strip().splitlines()[-1])
            except (AttributeError, IndexError, json.JSONDecodeError):
                pass
        except Exception as error:
            stages["error"] = f"{type(error).__name__}: {error}"[:200]
        finally:
            if claim_probe is not None and runtime_row_id is not None:
                try:
                    await claim_probe.manager.destroy_runtime(runtime_row_id, reason="benchmark.complete")
                except Exception:
                    pass
            if warm_runtime_id:
                try:
                    await adapter.destroy(warm_runtime_id)
                except Exception:
                    pass
        samples.append(
            {
                "iteration": index + 1,
                "scratch_ms": elapsed_ms,
                "ok": int(result.get("ok", True)),
                "python_version": str(probe.get("python_version", "unknown")),
                "available_modules": sorted(name for name, present in dict(probe.get("modules", {})).items() if present),
                "stages": stages,
            }
        )
    if claim_probe is not None:
        await claim_probe.close()
    python_version = str(samples[-1].get("python_version", "unknown")) if samples else "unknown"
    compatibility = check_preload_compatibility(
        PreloadCompatibility(profile_id, image, python_version, runtime_version, modules),
        python_version=python_version,
        runtime_version=runtime_version,
        available_modules=samples[-1].get("available_modules", []) if samples else (),
    )
    output = {
        "runtime": adapter.config.runtime,
        "image": image,
        "image_cached": image_cached,
        "image_cached_ms": image_cached_ms,
        "preload_modules": list(modules),
        "compatibility": compatibility.as_dict(),
        "claim_measurement": "manager" if measure_manager_claim else "docker-only",
        "iterations": samples,
        "stage_percentiles_ms": {
            stage: {
                quantile: percentile(
                    [
                        float(row["stages"][stage])
                        for row in samples
                        if isinstance(row.get("stages"), dict)
                        and isinstance(row["stages"].get(stage), (int, float))
                    ],
                    probability,
                )
                for quantile, probability in (("p50", 0.50), ("p95", 0.95))
            }
            for stage in ("create_ms", "start_ms", "kernel_ready_ms", "claim_ms", "first_output_ms")
        },
    }
    if update_matrix and matrix_path is not None:
        update_preload_matrix(matrix_path, compatibility, output)
        output["matrix_updated"] = str(matrix_path)
    return output


def update_preload_matrix(
    path: Path, compatibility: PreloadCompatibility, benchmark_result: dict[str, object]
) -> None:
    """Persist the CI result into the operator-visible compatibility matrix."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        payload = {"version": 1, "profiles": {}}
    if not isinstance(payload, dict):
        payload = {"version": 1, "profiles": {}}
    profiles = payload.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
        payload["profiles"] = profiles
    row = dict(compatibility.as_dict())
    # A locally built Docker image exposes an image ID, not a registry
    # manifest digest.  Keep that diagnostic separate so the persisted matrix
    # can never be mistaken for a deployable ``repo@sha256:...`` reference.
    if compatibility.image_digest.startswith("sha256:"):
        row["image_digest"] = ""
        row["image_id"] = compatibility.image_digest
    else:
        row["image_digest"] = compatibility.image_digest
    row["measured_at"] = datetime.now(UTC).isoformat()
    row["benchmark"] = {
        "iterations": len(benchmark_result.get("iterations", [])),
        "image_cached": benchmark_result.get("image_cached"),
        "stage_percentiles_ms": benchmark_result.get("stage_percentiles_ms", {}),
    }
    profiles[compatibility.profile_id] = row
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image",
        required=True,
        help="immutable image@sha256 digest (or a local sha256 image ID in CI)",
    )
    parser.add_argument("--iterations", type=int, default=3, choices=range(1, 21))
    parser.add_argument("--modules", default="numpy,pandas,matplotlib", help="comma-separated preload modules to probe")
    parser.add_argument("--profile-id", default="python-base")
    parser.add_argument("--runtime-version", default="nova-runtime")
    parser.add_argument("--matrix-path", type=Path)
    parser.add_argument("--update-matrix", action="store_true")
    parser.add_argument(
        "--measure-manager-claim",
        action="store_true",
        help="measure the real MySQL + Sandbox Manager claim path (requires NLP_AGENT_DATABASE_URL)",
    )
    args = parser.parse_args()
    result = asyncio.run(
        benchmark(
            args.image,
            iterations=args.iterations,
            modules=tuple(item.strip() for item in args.modules.split(",") if item.strip()),
            profile_id=args.profile_id,
            runtime_version=args.runtime_version,
            matrix_path=args.matrix_path,
            update_matrix=args.update_matrix,
            measure_manager_claim=args.measure_manager_claim,
        )
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
