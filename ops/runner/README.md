# Dedicated Browser runner qualification

This directory is a reviewed template, not proof that a host was provisioned. A runner may receive `aetherbrowser-ci` only after live capacity measurement and the exact staging smoke pass.

Required identity: repository-scoped worker `aether-vps6-browser-01`, service account `aether-ci-browser01`, baseline labels `self-hosted`, `linux`, `x64`, `vps6-ci` when true, `aetherbrowser-staging`, and its unique identity. The capability label is added—not substituted—only after smoke evidence is green.

The service account must have no interactive shell, sudo, supplementary groups, Docker group, shared home, operator access, production environment access, SSH keys, Tailscale control socket, or host container socket. Registration consumes a one-time repository token through a non-logged channel; no token or PAT is stored in this repository or in command transcripts.

Qualification records live fleet/queue state, CPU, RAM/swap, disk, current services/jobs, aggregate cgroup capacity, rootless Podman, effective systemd properties, denial probes, cleanup, exact deployed commit, and the unit digest. A queued workflow is not runner proof.

## Workflow trust boundary

Pull-request code runs only in `.github/workflows/ci.yml` on the fixed GitHub-hosted `ubuntu-24.04` image. No workflow with a `pull_request` or `pull_request_target` trigger may contain a self-hosted runner target, expression, or matrix indirection. `tests/test_workflow_shape.py` checks that rule across every workflow. Branch protection must require the hosted quality, unit, and security jobs before a change reaches `main`.

The `aetherbrowser-ci` capability is used only by `.github/workflows/trusted-runner.yml`. It triggers on a push to protected `main`, or an explicit manual dispatch whose required `expected_sha` equals the event's exact 40-character SHA. Before any persistent-runner job starts, an `ubuntu-24.04` proof job checks the default branch, `refs/heads/main`, the selected SHA, and a fresh checkout of current `main`. Every self-hosted job then checks out only that proof output and verifies `HEAD` before executing repository code. Direct pushes to `main` therefore remain an administrative trust path and must be disabled by repository rules.

Staging qualification is likewise manual and exact-SHA-bound, but it targets only `aetherbrowser-staging`. Grant `aetherbrowser-ci` only after the uploaded smoke evidence for that exact reviewed commit is green. Never place either Browser label on a runner shared with another repository.

The qualified image toolchain is also fixed in the host deployment record: Syft `1.51.1` and Grype `0.118.0`. Staging must verify those exact versions, refresh Grype's vulnerability database without printing network or host details, and then run `scripts/runner-smoke.sh`. Tool installation belongs to the canonical fleet provisioner, not an ad hoc job step.

The hardened Actions service is a client of the separate rootless Podman API service. Qualification requires `CONTAINER_HOST=unix:///run/aether-ci-browser-podman.sock`, an accessible Unix socket owned by the runner's exact UID/GID with mode `0600`, a server-side rootless result from `podman --remote info`, and a successful read-only `podman --remote ps`. It also requires finite inherited cgroup memory and task ceilings, private 256 MiB tmpfs mounts on both `/tmp` and `/var/tmp`, and continued denial of Docker, Tailscale, other worker homes, and privileged host secrets. The temp mounts are charged to the runner cgroup and cannot consume host-root storage. `podman unshare` is deliberately excluded because it is a local-engine operation unsupported by a remote Podman client.

The repository root `.dockerignore` is a runtime allowlist shared by Docker and Podman builds.
It excludes Git metadata, tests, workflows, caches, local environments, evidence, and unrelated
repository material before the context reaches the remote builder; only package metadata,
license material, `src/**`, and `scripts/container-entrypoint.sh` can enter `COPY . /app`.
`.containerignore` and `Dockerfile.dockerignore` must remain absent because Podman and Docker,
respectively, would give them precedence over this reviewed contract.

The local Compose quickstart deliberately uses Linux host networking so its API and unauthenticated browser-view processes can bind the developer host's numeric loopback interface directly. That local-host trust boundary is not the CI topology.

Trusted container acceptance must consume the immutable image ID emitted by the preceding exact-commit build job and use the configured `podman --remote` client to create one resource-bounded pod with `--network none`. Pulling, building, or mutable-tag execution is forbidden in acceptance. The deterministic fixture and Browser join that pod and communicate only over pod-local `127.0.0.1`; every readiness, HTTP, WebSocket, and API probe executes inside the pod. Qualification must retain the image-ID handoff, same-pod identity proof, live loopback-only interface/listener proof, absence of published ports and host/bridge networking, and exact cleanup of the pod. This lets the real headed-browser and same-display checks run without granting release-candidate code arbitrary internet, production, or runner-host network access.
