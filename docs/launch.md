# Agent Browser launch kit

> **Status: published.** The repository is public, `v0.1.0-rc.1` is tagged and released, and the
> client is on npm as [`aether-browser`](https://www.npmjs.com/package/aether-browser). The copy
> below is ready to post; each channel is still a deliberate, separate action. Chrome-containing
> images, archives, and public layer caches remain prohibited without separately documented
> redistribution authorization.

## Shared facts

- Name: **Agent Browser**
- Tagline: **See what your agent sees.**
- Positioning: Self-hosted Google Chrome for AI agents with bounded API control, structured snapshots,
  a live local noVNC view, and human takeover of the same session.
- Quickstart: `docker compose up --build` on Linux Docker Engine.
- Client: `npm install aether-browser` — TypeScript client and CLI, zero runtime dependencies.
- Repository: <https://github.com/AetherAI3/agent-browser>
- Release: `v0.1.0-rc.1` (prerelease, source-only).
- Distribution: Apache-2.0 Aether-owned source; locally built container for v0.1.
- Boundary: no hosted-service claim, no remote noVNC claim, and no Chrome-containing image, image
  tar, or public layer-cache publication unless separate redistribution authorization is documented.

## GitHub Release

### Agent Browser v0.1.0-rc.1 — See what your agent sees

Agent Browser runs one headed Google Chrome Stable session that an agent controls through a small JSON API
while a human watches or takes over the same local display through noVNC.

The v0.1 source release includes:

- explicit session ownership, expiry, vision budget, and idempotent cleanup;
- navigate, snapshot, click, type, scroll, and allowlisted key actions;
- bounded readable text, accessibility state, viewport metadata, and PNG snapshots;
- numeric-loopback API and noVNC defaults;
- SSRF, redirect, DNS-rebinding, browser-egress, and WebRTC escape controls; and
- Docker Compose quickstart plus rootless isolated acceptance tooling; and
- a published TypeScript client and CLI, `aether-browser` on npm, with zero runtime dependencies.

Start on Linux with:

```bash
docker compose up --build
```

and drive it with `npm install aether-browser`.

Then open `http://127.0.0.1:6080/vnc.html` and follow the README API example. Review the security
model before changing any listener or authority setting. Commit-bound verification, artifact
checksums, known limitations, and third-party notice status are recorded in the release evidence.

## Show HN

### Show HN: Agent Browser — self-hosted Chrome an agent and human can share

I built Agent Browser around one simple interaction: an agent controls a headed Chrome session
through a bounded JSON API while you watch or take over the exact same local display through
noVNC.

It returns structured text and accessibility state as well as screenshots, owns one explicit
session with cleanup and budgets, and keeps both user-facing listeners on numeric loopback by
default. The v0.1 API intentionally excludes shell access, arbitrary JavaScript, raw DevTools,
credential import, and a hosted control plane.

The source release runs with `docker compose up --build` on Linux. I would value feedback on the
API contract, the local human-takeover workflow, and the documented security boundary.

## Reddit

### Agent Browser: self-hosted Chrome with API control and a live local noVNC view

Agent Browser gives an AI agent a small browser-control API and gives the human operator the live
view of that same headed session. It exposes bounded structured state plus screenshots, supports a
narrow set of browser actions, and makes session ownership and cleanup explicit.

The project is designed for local, self-hosted use. API and noVNC bind to numeric loopback by
default, and the README calls out what v0.1 does not provide. The Linux quickstart is one Docker
Compose command, with the API example and commit-bound release evidence in the repository.

I am especially interested in concrete feedback on SDK ergonomics, accessibility snapshot shape,
and the observer/controller boundary.

## Dev.to

### Building Agent Browser: one headed session for an agent and its human operator

Most browser automation tools optimize for invisible execution. Agent Browser starts from a
different product requirement: the agent and the human should meet in one owned, visible browser
session.

The agent uses closed JSON requests for lifecycle, navigation, structured state, and four bounded
interaction types. The operator uses a local noVNC view attached to the same Xvfb display. A single
session manager owns the browser context, temporary profile, timers, counters, egress pins, and
cleanup paths.

The security model is deliberately narrow. User-facing listeners default to numeric loopback,
remote API mode requires a separately operated same-host HTTPS proxy, noVNC stays local, browser
TCP egress is pinned, and non-proxied WebRTC UDP is disabled. The project does not expose a shell,
arbitrary JavaScript, raw DevTools, or credential import.

The repository includes a Linux Docker Compose quickstart, API and architecture contracts,
security documentation, rootless acceptance tooling, and exact-commit release evidence.

## Short social copy

**Agent Browser — See what your agent sees.**

Self-hosted Chrome with bounded API control, structured snapshots, and a live local noVNC view
for human takeover of the same session. Linux quickstart: `docker compose up --build`.

## Publication checklist

- Owner explicitly approved publication.
- Repository visibility and release action match that approval.
- Exact-main CI, trusted-runner acceptance, SBOM, vulnerability, quickstart, and cleanup evidence
  are green and linked.
- Demo, poster, and 1280×640 social preview come from the accepted commit and their SHA-256 values
  are recorded.
- Release notes name known limitations and do not imply hosted operation or remote noVNC safety.
- Third-party notices and source-distribution obligations were reviewed for the exact candidate.
- No Chrome-containing image, image tar, or public layer cache is attached unless separately
  documented redistribution authorization exists.
- Links resolve after publication, and no private host, token, log, or user data appears in copy.
