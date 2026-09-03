<p align="center">
  <img src="assets/agent-browser-logo.png" alt="Pixel-art computer displaying a globe and pointer, the Agent Browser logo" width="200">
</p>

<h1 align="center">Agent Browser</h1>
<h3 align="center">See what your agent sees.</h3>

<p align="center">
  Self-hosted Chrome for AI agents.<br>
  Your agent drives one real browser session — and you can watch it happen, and step in.
</p>

<p align="center">
  <a href="https://github.com/AetherAI3/agent-browser/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/AetherAI3/agent-browser/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://www.npmjs.com/package/aether-browser"><img alt="npm: aether-browser" src="https://img.shields.io/npm/v/aether-browser?color=cb3837&label=npm%3A%20aether-browser"></a>
  <a href="https://pypi.org/project/aether-browser/"><img alt="PyPI: aether-browser" src="https://img.shields.io/pypi/v/aether-browser?color=3775a9&label=PyPI%3A%20aether-browser"></a>
  <a href="LICENSE"><img alt="Source license: Apache-2.0" src="https://img.shields.io/badge/source%20license-Apache--2.0-0b7285"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776ab">
  <img alt="Self-hosted" src="https://img.shields.io/badge/runtime-self--hosted-2f9e44">
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> ·
  <a href="docs/API.md">API</a> ·
  <a href="docs/MCP.md">MCP</a> ·
  <a href="docs/SECURITY.md">Security model</a> ·
  <a href="clients/node/README.md">Node client</a> ·
  <a href="clients/python/README.md">Python client</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

---

<p align="center">
  <img src="assets/demo.gif" alt="An agent signs in through the Agent Browser JSON API, stops at a two-factor prompt it cannot answer, a human takes over the same live session and types the code, and the agent resumes and reads the dashboard" width="820">
</p>

<p align="center"><sub>
  A real 18-second run. The agent signs in, reaches a 2FA prompt it has no way to answer, and stops.
  A human types the code into <strong>that same live session</strong> and hands it straight back.<br>
  No second browser. No replay. Nothing to reconstruct.
  &nbsp;·&nbsp; <a href="docs/DEMO_EVIDENCE.md">How this was recorded</a>
</sub></p>

---

## Why this exists

Most browser tooling for agents hands the model a browser you cannot see. When it misreads a page
or stalls on a login, all you get is a transcript and a guess.

Agent Browser runs **one headed Chrome session**. Your agent drives it through a small JSON API,
and a live view of that **same** session sits open in front of you. When the agent gets stuck, you
take over in the window it is already using — then hand it back.

Start the runtime:

```bash
docker compose up --build
```

Then reach it however you like:

```bash
npm install aether-browser    # TypeScript
pip install aether-browser    # Python
claude mcp add agent-browser -- npx -y aether-browser mcp    # any MCP client
```

## Quickstart

One command, on **Docker Engine for Linux** with Compose v2. It builds from the source checkout
and starts Xvfb, x11vnc, noVNC, and the API — the stack that owns a single headed Chrome session.
Both user-facing listeners bind to numeric loopback.

```bash
docker compose up --build
```

Once health responds, open the live view at
[`127.0.0.1:6080/vnc.html`](http://127.0.0.1:6080/vnc.html) and check the API from another terminal:

```bash
curl -fsS http://127.0.0.1:8092/browser/health | jq .
```

`Ctrl+C` stops it.

> **The first build takes several minutes.** It installs the hash-locked Python environment, then
> uses Patchright to install the current Google Chrome Stable package. The exact browser version is
> captured with each accepted image, so rebuilding the same source later may pick up a newer Stable.

> **Linux host networking is deliberate.** It keeps the unauthenticated v0.x noVNC surface on
> numeric loopback. Docker Desktop and remote-host deployment are outside this quickstart.

## The API in four calls

With the runtime healthy and `curl` plus `jq` installed. Local loopback needs no bearer token by
design — read the [authority contract](docs/API.md#transport-and-authority) before you change the
deployment shape.

```bash
# Open the session. This is the browser you are about to watch.
SESSION_ID="$(curl -fsS -X POST http://127.0.0.1:8092/browser/session/create \
  -H 'Content-Type: application/json' \
  -d '{"api_version":"v1"}' | jq -er '.session_id')"

# Go somewhere. The page changes in the live view as this runs.
curl -fsS -X POST http://127.0.0.1:8092/browser/navigate \
  -H 'Content-Type: application/json' \
  -d "{\"api_version\":\"v1\",\"session_id\":\"${SESSION_ID}\",\"url\":\"https://example.com\"}" \
  | jq '{status, final_url, title, readable_text}'

# Read it back as structure, not pixels.
curl -fsS -X POST http://127.0.0.1:8092/browser/snapshot \
  -H 'Content-Type: application/json' \
  -d "{\"api_version\":\"v1\",\"session_id\":\"${SESSION_ID}\"}" \
  | jq '{status, url, title, sequence, vision_steps_remaining}'

# Give the browser back.
curl -fsS -X POST http://127.0.0.1:8092/browser/session/end \
  -H 'Content-Type: application/json' \
  -d "{\"api_version\":\"v1\",\"session_id\":\"${SESSION_ID}\"}" | jq .
```

What that did: `session/create` started one headed Chrome session and returned its UUID plus the
local view URL. `navigate` validated the destination, pinned the allowed addresses, and changed the
page on the shared display. `snapshot` returned bounded text, accessibility state, viewport
metadata, counters, and a PNG of that same page. At no point did the API and the human view create
competing browsers — they met at one owned session.

The same flow ships as [`examples/curl.sh`](examples/curl.sh).

## Use it from an MCP client

Any MCP client — Claude Code, Claude Desktop, Cursor, Windsurf — can drive the session while you
watch, and take over when it gets stuck.

```bash
claude mcp add agent-browser -- npx -y aether-browser mcp
```

Already have the Python client? `aether-browser mcp` serves the same nine tools.

<details>
<summary>Config-file clients (Claude Desktop, Cursor, Windsurf)</summary>

```json
{
  "mcpServers": {
    "agent-browser": {
      "command": "npx",
      "args": ["-y", "aether-browser", "mcp"]
    }
  }
}
```
</details>

**Nine tools** — `browser_open` · `browser_navigate` · `browser_read` · `browser_click` ·
`browser_type` · `browser_press` · `browser_scroll` · `browser_status` · `browser_close`

`browser_open` hands back the live view URL, and every response after it repeats that URL, so you
always know where to look. On connect, the server tells the model to stop and ask for a takeover at
a login, a payment, or a 2FA prompt rather than guessing — the loop in the recording above.

Setup and limits: [`docs/MCP.md`](docs/MCP.md).

## Node and TypeScript client

The same API from Node, with types and cleanup you cannot forget:

```bash
npm install aether-browser
```

```ts
import { AgentBrowser, withSession } from 'aether-browser'

const browser = new AgentBrowser({ controllerToken: process.env.AGENT_BROWSER_CONTROLLER_TOKEN })

await withSession(browser, async (session) => {
  const page = await session.navigate('https://example.com')
  await session.click({ selector: '#login' })
  await session.type({ selector: '#user', text: 'ada' })
  console.log(page.title, session.viewUrl)
})
```

`withSession` always ends the session, including when your callback throws, so a crash cannot leave
the single slot occupied. No runtime dependencies, and it runs anywhere that can reach the server.
It carries a small CLI too: `npx aether-browser doctor` tells you what is missing before a first
run, and `up` builds and starts the runtime on a Linux host.

See [`clients/node/README.md`](clients/node/README.md).

## Python client

The same client, same name, same commands, released version for version with the npm package:

```bash
pip install aether-browser
```

```python
import os

from aether_browser import AgentBrowser, session

browser = AgentBrowser(controller_token=os.environ["AGENT_BROWSER_CONTROLLER_TOKEN"])

with session(browser) as live:
    page = live.navigate("https://example.com")
    live.click(selector="#login")
    live.type("ada", selector="#user")
    print(page["title"], live.view_url)
```

The `session` context manager always ends the session, including when the body raises. No runtime
dependencies — the transport is `urllib` from the standard library — ships type hints, and runs on
Python 3.10 or newer, anywhere that can reach the server. Same CLI as the npm package:
`aether-browser doctor`, `up`, `status`, `open`, `down`.

See [`clients/python/README.md`](clients/python/README.md).

## What makes it different

- **One session, two participants.** The agent acts through JSON. You watch the same display, and
  take the controls whenever you want them.
- **Structure before pixels.** Readable text and a bounded accessibility tree come back before you
  spend a vision step on a screenshot.
- **A small control surface.** The v0.x API exposes explicit browser actions — not a shell, not
  arbitrary JavaScript, not raw DevTools.
- **Model-agnostic and self-hosted.** Bring the framework you already use, and keep the browser on
  hardware you control.

## What it does today

| Capability | v0.x contract |
|---|---|
| Browser | One headed Google Chrome Stable session launched through Patchright |
| State | URL, title, readable text, bounded accessibility nodes, viewport, and PNG snapshot |
| Actions | Navigate, click, type, scroll, and allowlisted key presses |
| Human view | The same Xvfb display through loopback-only x11vnc and noVNC |
| Ownership | One explicit UUID session with expiry, vision budget, and idempotent cleanup |
| Authority | Observer/controller separation when authenticated; strict local loopback mode otherwise |
| MCP | Nine stdio tools from either client (`aether-browser mcp`), no extra dependencies |
| Navigation | HTTP(S)-only validation across requested, redirected, and browser-initiated navigation |

Request and response shapes, limits, and stable error codes: [`docs/API.md`](docs/API.md).

## Architecture

```mermaid
flowchart LR
    Agent["Agent client"] -->|bounded JSON API| API["FastAPI"]
    API --> Guard["authority + navigation policy"]
    Guard --> Session["single-session manager"]
    Session --> Chrome["Patchright + headed Google Chrome"]
    Chrome --> State["text · accessibility · PNG"]
    State --> Agent
    Chrome --> Display["shared Xvfb display"]
    Display -->|loopback noVNC| Human["Human observer / takeover"]
```

The session manager owns the page, browser context, temporary profile, timers, counters, and
cleanup. The API and the live view are different interfaces to that shared resource, not two
independent automation paths. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

> [!IMPORTANT]
> **Agent Browser v0.2.0 is a source-first, self-hosted release.** It is not a hosted service, and
> the v0.x noVNC surface is unauthenticated and meant for numeric loopback on a machine you control.
> No Chrome-containing image, image tar, or public layer cache is distributed unless separate
> redistribution authorization is documented.

## Security boundary

- API and noVNC listen on numeric loopback by default; noVNC remains loopback-only in the v0.x line.
- Remote API clients require a separately operated same-host HTTPS reverse proxy, an exact
  trusted loopback peer, strict Host validation, and distinct strong observer/controller tokens.
- Destination validation rejects credentials, unsupported schemes, blocked address classes,
  unsafe redirects, and DNS rebinding. Browser egress is pinned through an owned TCP proxy.
- Non-proxied WebRTC UDP is disabled so it cannot silently bypass the TCP egress boundary.
- Inputs, outputs, interactions, timeouts, lifetimes, and screenshot budgets are bounded.
- Cleanup converges on session end, expiry, launch failure, application shutdown, and process
  failure.

Trust assumptions and residual risks are spelled out in [`docs/SECURITY.md`](docs/SECURITY.md).
Report vulnerabilities privately through [`SECURITY.md`](SECURITY.md) — please do not open a public
security issue.

### Source recovery and exclusions

The source-recovery rule is **reuse general browser behavior, not private domain code**.
Lifecycle, structured-state, interaction, and cleanup patterns may be adapted from authorized
references; ATS/trading integrations, broker or account selectors, order actions, secrets,
and credential injection are excluded from the public core. Provenance status is tracked in
[`docs/SOURCE-RECOVERY.md`](docs/SOURCE-RECOVERY.md).

## What it does not do

- No hosted cloud service, cloud control plane, or production remote-hosting claim.
- No bundled LLM, account system, dashboard, credential vault, or credential injection.
- No CAPTCHA bypass, anti-detection guarantee, stealth claim, or proxy rotation.
- No arbitrary JavaScript, shell, filesystem, upload, clipboard, download, or raw CDP API.
- No multi-session pool, ATS integration, trading integration, or brokerage behavior.

## Roadmap

**Shipped.** Both clients and their CLI are published as `aether-browser`, version for version,
[on npm](https://www.npmjs.com/package/aether-browser) from [`clients/node`](clients/node) and
[on PyPI](https://pypi.org/project/aether-browser/) from [`clients/python`](clients/python). Since
`0.2.0` both also serve the MCP server.

Two tracks are open, each with an issue, and each is a good first contribution:

1. [Multi-session worker pool](https://github.com/AetherAI3/agent-browser/issues/14) — explicit
   isolation and capacity semantics.
2. [Session trace and recording export](https://github.com/AetherAI3/agent-browser/issues/15) —
   with clear privacy controls.

These are candidates, not shipped features.

## Contributing

Start with [`CONTRIBUTING.md`](CONTRIBUTING.md), the
[`Code of Conduct`](CODE_OF_CONDUCT.md), and the current [`API contract`](docs/API.md). Small,
well-tested changes that keep the authority boundary narrow are very welcome — the two roadmap
issues above are the best place to start. Security reports go through the private process in
[`SECURITY.md`](SECURITY.md), never a public issue.

## License and third-party notices

Aether-owned source code is licensed under the [Apache License 2.0](LICENSE). Google Chrome is
separately licensed under [Google's Chrome terms](https://www.google.com/chrome/terms/) and is not
covered by Aether's Apache license; dependencies, system packages, fonts, and web assets also
remain under their respective terms. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
Aether is not affiliated with or endorsed by Google. The v0.x distribution target is source that
builds locally; this repository does not distribute a prebuilt Chrome-containing image.

<p align="center"><sub><strong>Agent Browser</strong> · See what your agent sees.</sub></p>
