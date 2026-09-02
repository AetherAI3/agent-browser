# Changelog

All notable changes to Agent Browser are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Renamed the project to **Agent Browser by Aether AI**. The repository moved to
  `AetherAI3/agent-browser`, the Python package is now `agent_browser`, the distribution is
  `agent-browser`, and the runtime environment prefix is `AGENT_BROWSER_*`.
- Replaced the logo with a real transparent PNG. The previous JPEG had a transparency checkerboard
  and a dashed border painted into its pixels; the replacement has a genuine alpha channel, no
  painted border, and a flattened palette, at roughly 7% of the former file size.

## [0.1.0-rc.1] - 2026-09-02

First public, source-only release candidate.

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

### Security

- The v0.1 noVNC surface is unauthenticated and is intended only for numeric loopback on a machine
  you control. See [`docs/SECURITY-MODEL.md`](docs/SECURITY-MODEL.md).

[Unreleased]: https://github.com/AetherAI3/agent-browser/compare/v0.1.0-rc.1...HEAD
[0.1.0-rc.1]: https://github.com/AetherAI3/agent-browser/releases/tag/v0.1.0-rc.1
