# Sandbox personal files bridge

This opt-in feature exposes the current user's current workspace files at
`/files`. The test bridge overlay enables scoped read/write access; without
`SANDBOX_FILES_BRIDGE_WRITABLE=true`, FUSE and the sandbox bind remain read-only.
The default Compose deployment keeps the bridge disabled.

The test CD workflow explicitly includes `compose.files-bridge.yaml` in every
Compose operation, including the application recreation helper. Before parsing
Compose it runs `.github/scripts/prepare-files-bridge.sh` to provision the fixed
test directory `/var/lib/nova/test/files-bridge` and its shared mount. The runner
needs passwordless sudo for these mount operations. A missing device or failed
shared mount stops the deployment before application recreation. Production
remains opt-in; this change does not enable the production bridge.

After host reboot, run the preparation script before starting the Manager (or
redeploy through test CD); a plain directory does not replace the shared mount.
For a bounded diagnostic using an existing file, run
`scripts/probe_sandbox_files_bridge.py` in a disposable bridge-enabled container
with the test database environment, read-only platform storage, Docker socket,
and `BRIDGE_PROBE_IMAGE` set to the pinned runtime image. It reads at most 64 bytes
without printing file contents, checks a different owner's lookup is denied,
and checks runsc cannot open the file for writing. It creates no database rows.

## Linux deployment

Use a Linux Docker host with `/dev/fuse` available. Choose an absolute bridge
directory unique to the deployment, for example `/var/lib/nova/test/files-bridge`.
Create it on the host, bind-mount it onto itself, and mark that mount shared
(`mount --bind <directory> <directory>`, then `mount --make-rshared <directory>`).
These are Linux host administration commands, not PowerShell commands. Persist
the shared mount in the host's mount configuration across reboots.

Set `NOVA_SANDBOX_BRIDGE_DIR` to that directory in the Compose environment.
Run Compose with both `compose.yaml` and `compose.files-bridge.yaml`. The overlay
gives only the Manager `/dev/fuse`, `SYS_ADMIN`, and the shared bridge directory;
sandbox containers retain their original capability and network restrictions.
The directory must have the same absolute path inside the Manager and on the
Docker daemon host, because Docker resolves bind sources on the host.

The overlay mounts `nova-data` read/write into the Manager at `/app/.data`, the
same volume used by Web and Worker. If `NLP_AGENT_USER_FILES_ROOT` points outside
that volume, mount the actual storage directory at that configured path as well.
Non-root Manager deployments additionally need FUSE `user_allow_other` enabled.

## Writable file semantics

Python `open(..., 'w'/'a'/'r+')`, directory creation, rename/move, and atomic
save through `os.replace` operate on the current user's current workspace.
Deletion moves items to platform trash; non-empty directories require their
children to be removed first. Replacing a file preserves the previous item in
trash. Links, device nodes, arbitrary ownership and permission changes are not
supported: platform files use UID/GID 10001 and modes 0644/0755.

Every write checks the current database-backed role/quota, reserves growth,
fsyncs a new blob, and commits its metadata pointer, size and SHA256 together.
Transaction failure preserves the previous blob. The retired blob is removed
only after commit; a failed cleanup can leave an unreferenced blob, never a
metadata pointer to a removed new blob. Writers are serialized by the account
quota lock, including other Manager processes. Each completed write is visible
to the platform; large sequential writes currently copy the existing blob for
each FUSE chunk and should be benchmarked before heavy bulk workloads.

Writable runtimes additionally get a hard RLIMIT_FSIZE from the account's
single-file policy. This is needed because shared-mode SetStat in the deployed
runsc can discard a remote truncate error; the kernel limit rejects oversized
truncation before reaching FUSE. Live quota checks remain authoritative for all
writes. Reset a runtime after increasing its per-file policy to apply the new
kernel ceiling. Runsc's existing shared-mode attribute error caveat remains for
changes to a lower quota while a runtime is already running: storage rejects the
operation, but an old runtime may not surface a truncate error reliably.

After sandbox execution, the visible file panel and quota summary refresh.
The writable acceptance probe creates only uniquely named test directories,
checks writes, append, truncate, quota, replacement, move, trash, metadata and
rollback, and permanently removes only its own generated fixtures.

The Manager starts one FUSE mount per active user/workspace and waits for it
without blocking its database event loop. Metadata queries run on the same loop
as its pooled database connections. Failed mounts never fall back to an empty,
writable directory. Reconciliation removes mounts without live runtimes; normal
Manager shutdown detaches its mounts. A hard-killed Manager may leave a
disconnected FUSE endpoint: detach that deployment's stale mount with
`umount -l <mountpoint>` before restarting it.

## Upgrade and rollback

Apply migrations before starting application processes. The workspace migration
can resume after the earlier MySQL failure that left only the new column behind.
Historical environments retain a null workspace and are not reused by scoped
queries; new leases receive workspace-specific environments.

Bridge mode stops replenishing the unscoped warm pool and retires unused slots
so they cannot exhaust capacity. An existing container is reused only after
Docker confirms its read-only bind source matches the current scope. Enabling
the feature may therefore replace an old interactive kernel and lose its
in-memory variables.

To roll back the feature, remove the overlay and set
`SANDBOX_ENABLE_FILES_BRIDGE=false`, then restart the Manager. This restores warm
pool allocation. Keep the workspace migration: downgrading to one environment
per user intentionally fails if that user has multiple workspace environments,
rather than deleting their rows automatically.

Real Linux FUSE-to-container reads, gVisor compatibility, and shared-mount
propagation require validation on the target Linux host; unit tests do not
establish those deployment properties.
