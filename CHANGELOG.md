# Changelog

All notable changes to Agent Browser are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0-rc.1] - 2026-09-02

First public, source-only release candidate, published as **Agent Browser by Aether AI**.

### Added

- Single-session runtime that owns one headed Google Chrome Stable instance behind a closed v0.1
  HTTP API: session create/end, navigation, structured snapshots, and bounded interaction
  (click, type, press, scroll).
- Separate controller and observer authorization roles, with fail-closed capacity rejection.
- Live human observation and takeover of the same session over noVNC.
- Egress policy that refuses direct SSRF targets and out-of-policy redirects, keeps downloads
  non-persistent, and bounds popups.
- Deterministic teardown covering browser processes, the browser profile, and the container.
- Docker Compose quickstart that binds user-facing listeners to numeric loopback.
- Release evidence pipeline: rootless image build, SBOM and vulnerability scan, isolated
  acceptance against the exact image ID, quickstart reproduction, and a strict repository gate.

### Naming

The project is **Agent Browser by Aether AI**, and this is the first candidate published under
that name. The repository is `AetherAI3/agent-browser`, the Python package is `agent_browser`, the
distribution is `agent-browser`, and the runtime environment prefix is `AGENT_BROWSER_*`.

An earlier candidate was briefly published under the working name "Aether Browser" before the
rename settled. It was withdrawn rather than kept alongside this one, so `v0.1.0-rc.1` is the only
release and it carries the final naming throughout.

### Security

- The v0.1 noVNC surface is unauthenticated and is intended only for numeric loopback on a machine
  you control. See [`docs/SECURITY-MODEL.md`](docs/SECURITY-MODEL.md).
- Grype runs against the exact image with `--fail-on high --only-fixed`. A green build means no
  fixed high-or-higher findings, not an absence of vulnerabilities; see
  [`docs/RELEASE_EVIDENCE.md`](docs/RELEASE_EVIDENCE.md) for the full posture.

[Unreleased]: https://github.com/AetherAI3/agent-browser/compare/v0.1.0-rc.1...HEAD
[0.1.0-rc.1]: https://github.com/AetherAI3/agent-browser/releases/tag/v0.1.0-rc.1
