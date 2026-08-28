# Sandbox Phase 5 extension boundary

Phase 5 is deliberately opt-in. The Phase 3 gVisor `runsc` backend remains the
default and the only backend enabled by the production examples.

## Runtime adapters

- `KataRuntimeAdapter` uses the same hardened Docker command contract with
  `kata-qemu` and an immutable image digest.
- `FirecrackerRuntimeAdapter` exposes pinned guest inputs and a jailer launch
  command, but guest-agent lifecycle operations fail closed until a Linux
  deployment provides the integration and its security tests.
- `runtime_factory.create_runtime_adapter()` selects a backend explicitly;
  unknown backends are rejected. The Kubernetes option requires
  `NLP_AGENT_SANDBOX_KUBERNETES_CLIENT_FACTORY` in the isolated Manager
  environment. The value is a `module:function` factory that returns an
  implementation of `KubernetesRuntimeClient`; it is loaded only by the
  Manager and fails closed when absent or incomplete.

The test and production publish workflows build the `sandbox-runtime` image,
promote its manifest digest alongside the application image, pull that exact
digest on the deployment host, and start `nova-sandbox-manager` with the
Docker socket. The deployment `.env` must set
`NLP_AGENT_SANDBOX_DOCKER_IMAGE_DIGEST` to the promoted runtime digest.
The Linux validation workflow records stage P50/P95 measurements and, on the
dedicated sandbox branch, commits the measured compatibility row back to
`configs/sandbox_preload_matrix.json`.

## Multi-node scheduling

`SandboxNodeScheduler` filters labels, taints, and available slots before
choosing a deterministic least-loaded node. `KubernetesRuntimeManifest`
produces a reviewable non-root Pod manifest with no service-account token and
all Linux capabilities dropped. Kubernetes API ownership remains with the
isolated Manager when a cluster deployment is introduced.

## Project Storage

Persistent project code is not enabled by default. `DisabledProjectStorage`
is the configured implementation unless the product explicitly sets both
`NLP_AGENT_SANDBOX_PROJECT_STORAGE_ENABLED=true` and a storage root. The local
opt-in implementation validates project-relative paths, rejects symlinks, and
writes files atomically.

## Runtime snapshots

Snapshots remain disabled by default. `RuntimeSnapshotPolicy` requires an
explicit enablement flag, a recognized backend, clean runtime state, and
entropy re-seeding. No snapshot command is issued until the backend-specific
guest lifecycle and restore tests satisfy that gate.
