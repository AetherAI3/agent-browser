# Changelog

All notable changes to Agent Browser are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-09-03

### Added

- **MCP server**, in both clients, under the same nine tool names: `aether-browser mcp` (Python) and
  `npx aether-browser mcp` (Node) serve one Agent Browser session to any MCP client over stdio, so
  Claude Code, Claude Desktop, Cursor and Windsurf can drive the session a human is watching. The
  JSON-RPC transport is implemented directly in `clients/node/src/mcp.js` and
  `clients/python/src/aether_browser/mcp.py`, so neither client gains a runtime dependency.
  Documented in `docs/MCP.md`.
- The MCP server owns the session id, so the model never carries it, and every response that can
  carry the live view URL does. Its `initialize` instructions tell the model to stop and ask for a
  human takeover at a login, a payment, or a 2FA prompt rather than guessing.
- A packaging test asserts the two MCP servers expose an identical toolset, so the Node and Python
  servers cannot drift apart without failing CI. Each server also reports the version its own
  package declares, with a test per client, so a release bump cannot leave an MCP server
  advertising a stale version.

### Changed

- The README demo is a real recorded run rather than the previous montage of three static frames,
  and it plays inline instead of linking to a file. `docs/DEMO_EVIDENCE.md` records how it was made
  and states its limits.
- Documentation that describes an ongoing property of the pre-1.0 line — the unauthenticated
  loopback-only noVNC surface, the closed API contract, the locally built container — now says
  `v0.x` rather than `v0.1`, so those statements stay true across releases instead of silently
  going stale on every bump. Statements that name the release itself say `v0.2.0`.
- `SECURITY.md` no longer describes the project as preparing its first release candidate, and its
  supported-version table names the current release line.

## [0.1.0] - 2026-09-03

First stable release of the `v1` API contract and both clients.

### Added

- Python client and CLI in `clients/python`, published to PyPI as `aether-browser` — the same
  distribution name, commands, and closed `v1` contract as the npm client, released version for
  version with it (`0.1.0rc1` is npm's `0.1.0-rc.1`). No runtime dependencies: the transport is
  `urllib` from the standard library. Requires Python 3.10 or newer.
- `pypi-publish.yml`, the exact sibling of `npm-publish.yml`: manual dispatch, the same
  exact-current-main ref proof, a default dry run, and PyPI Trusted Publishing (OIDC) instead of a
  stored token.
- `python-client` CI job covering unit tests, strict typing, wheel contents, and a server-free CLI
  run. Its packaging tests read `clients/node/package.json`, so a version or command that drifts
  between the two clients fails in CI rather than on a registry.

### Changed

- Both clients leave the release-candidate line: npm `0.1.0-rc.1` and PyPI `0.1.0rc1` become
  `0.1.0`, so `npm install aether-browser` and `pip install aether-browser` need no prerelease
  flag. The CLI's `up` now fetches the `v0.1.0` source tag.

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

[Unreleased]: https://github.com/AetherAI3/agent-browser/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/AetherAI3/agent-browser/releases/tag/v0.2.0
[0.1.0]: https://github.com/AetherAI3/agent-browser/releases/tag/v0.1.0
[0.1.0-rc.1]: https://github.com/AetherAI3/agent-browser/releases/tag/v0.1.0-rc.1
