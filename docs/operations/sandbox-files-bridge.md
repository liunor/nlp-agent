# Sandbox personal files bridge

This opt-in feature exposes the current user's current workspace files at
`/files`. Both the FUSE filesystem and the sandbox bind mount are read-only.
The default Compose deployment keeps the bridge disabled.

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

The overlay mounts `nova-data` read-only into the Manager at `/app/.data`, the
same volume used by Web and Worker. If `NLP_AGENT_USER_FILES_ROOT` points outside
that volume, mount the actual storage directory at that configured path as well.
Non-root Manager deployments additionally need FUSE `user_allow_other` enabled.

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
