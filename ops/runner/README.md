# Browser runner and hosted-release qualification

This directory is a reviewed template, not proof that a host was provisioned. A runner may receive `aetherbrowser-ci` only after live capacity measurement and the exact staging smoke pass.

Required identity: repository-scoped worker `aether-vps6-browser-01`, service account `aether-ci-browser01`, baseline labels `self-hosted`, `linux`, `x64`, `vps6-ci` when true, `aetherbrowser-staging`, and its unique identity. The capability label is added—not substituted—only after smoke evidence is green.

The service account must have no interactive shell, sudo, supplementary groups, Docker group, shared home, operator access, production environment access, SSH keys, Tailscale control socket, or host container socket. Registration consumes a one-time repository token through a non-logged channel; no token or PAT is stored in this repository or in command transcripts.

Qualification records live fleet/queue state, CPU, RAM/swap, disk, current services/jobs, aggregate cgroup capacity, rootless Podman, effective systemd properties, denial probes, cleanup, exact deployed commit, and the unit digest. A queued workflow is not runner proof.

## Workflow trust boundary

Pull-request code runs only in `.github/workflows/ci.yml` on the fixed GitHub-hosted `ubuntu-24.04` image. No workflow with a `pull_request` or `pull_request_target` trigger may contain a self-hosted runner target, expression, or matrix indirection. `tests/test_workflow_shape.py` checks that rule across every workflow. Branch protection must require the hosted quality, unit, and security jobs before a change reaches `main`.

`.github/workflows/trusted-runner.yml` is the exact-main release-evidence workflow. It runs every workload job on a fresh, fixed `ubuntu-24.04` runner. A proof job first checks the default branch, `refs/heads/main`, the event SHA, and a fresh checkout; every later job checks out only that proved SHA and verifies `HEAD`. The container job starts a private rootless Podman API below `RUNNER_TEMP`, builds one immutable image, and performs SBOM, vulnerability, acceptance, and capture work in the same ephemeral VM. Independent hosted jobs validate the acceptance artifact and reproduce the documented Docker Compose quickstart. Direct pushes to `main` remain an administrative trust path and must be disabled by repository rules.

The manual `.github/workflows/runner-smoke.yml` workflow targets only the exact
`aether-vps6-browser-01` identity carrying `vps6-ci` and `aetherbrowser-staging`. It remains a
future dedicated-runner qualification path, not a release dependency. Grant `aetherbrowser-ci`
only after uploaded smoke evidence for the exact reviewed commit is green. Never place either
Browser label on a runner shared with another repository.

The release workflow checksum-verifies Syft `1.51.1` and Grype `0.118.0` before use. A future persistent runner must receive those exact tools from the canonical fleet provisioner rather than an ad hoc job step. The existing VPS6 qualification is host/capacity/isolation evidence only: its missing container/SBOM toolchain is not represented as a successful workload gate.

The hardened Actions service is a client of the separate rootless Podman API service. Qualification requires `CONTAINER_HOST=unix:///run/aether-ci-browser-podman.sock`, an accessible Unix socket owned by the runner's exact UID/GID with mode `0600`, a server-side rootless result from `podman --remote info`, and a successful read-only `podman --remote ps`. It also requires finite inherited cgroup memory and task ceilings, private 256 MiB tmpfs mounts on both `/tmp` and `/var/tmp`, and continued denial of Docker, Tailscale, other worker homes, and privileged host secrets. The temp mounts are charged to the runner cgroup and cannot consume host-root storage. `podman unshare` is deliberately excluded because it is a local-engine operation unsupported by a remote Podman client.

The repository root `.dockerignore` is a runtime allowlist shared by Docker and Podman builds.
It excludes Git metadata, tests, workflows, caches, local environments, evidence, and unrelated
repository material before the context reaches the remote builder; only package metadata,
license material, `src/**`, and `scripts/container-entrypoint.sh` can enter `COPY . /app`.
`.containerignore` and `Dockerfile.dockerignore` must remain absent because Podman and Docker,
respectively, would give them precedence over this reviewed contract.

The local Compose quickstart deliberately uses Linux host networking so its API and unauthenticated browser-view processes can bind the developer host's numeric loopback interface directly. That local-host trust boundary is not the CI topology.

Trusted container acceptance must consume the immutable image ID emitted by the preceding exact-commit build job and use the configured `podman --remote` client to create one resource-bounded pod with `--network none`. Pulling, building, or mutable-tag execution is forbidden in acceptance. The deterministic fixture and Browser join that pod and communicate only over pod-local `127.0.0.1`; every readiness, HTTP, WebSocket, and API probe executes inside the pod. Qualification must retain the image-ID handoff, same-pod identity proof, live loopback-only interface/listener proof, absence of published ports and host/bridge networking, and exact cleanup of the pod. This lets the real headed-browser and same-display checks run without granting release-candidate code arbitrary internet, production, or runner-host network access.
