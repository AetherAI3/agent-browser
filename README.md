<p align="center">
  <img
    src="assets/aether-browser-logo.jpg"
    alt="Pixel-art computer displaying a globe and pointer, the Aether Browser logo"
    width="520"
  >
</p>

<h1 align="center">Aether Browser</h1>

<p align="center"><strong>See what your agent sees.</strong></p>

<p align="center">
  Self-hosted, headed Chrome for AI agents.<br>
  Control one session through a small JSON API, then observe or take over that same display locally.
</p>

<p align="center">
  <code>PRIVATE RC · BUILD IN PROGRESS</code>&nbsp;&nbsp;
  <code>PYTHON 3.11+</code>&nbsp;&nbsp;
  <code>APACHE-2.0</code>&nbsp;&nbsp;
  <code>SELF-HOSTED</code>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="docs/API.md">API contract</a> ·
  <a href="docs/ARCHITECTURE.md">Architecture</a> ·
  <a href="#safety-boundary">Safety boundary</a> ·
  <a href="#release-status">Release status</a> ·
  <a href="LICENSE">License</a>
</p>

## Release status

> [!IMPORTANT]
> **Aether Browser v0.1.0 is a private release-candidate build in progress.** This
> repository is not approved for public release and is not presented as production ready.
> Runtime, API, authority, navigation, and egress boundaries are present. Dedicated-runner
> evidence, container acceptance, exact-main CI, and a real demo remain unclaimed until they
> are independently verified.

## One browser, two views

Aether Browser is built around a simple idea: the agent and the human should share the exact
same browser session.

- The **agent** works through a deliberately closed HTTP API.
- The **human** observes or takes over through a loopback-only noVNC view.
- Both meet at one headed Chrome instance owned by one bounded session.
- Structured text and accessibility state come before pixels; screenshots remain available
  when visual context is necessary.

The checked-in runtime models headed Chrome, structured snapshots, bounded actions,
single-session ownership, fail-closed authority, and pinned browser egress. noVNC host
integration and full private-RC acceptance still require exact-main proof.

| Surface | Focused v0.1 behavior | Evidence in this checkout |
|---|---|---|
| Browser runtime | Headed Chrome through Patchright, with one owned page and a temporary profile | Runtime source |
| Agent state | URL, title, readable text, bounded accessibility nodes, viewport data, and PNG snapshots | Runtime source + [API contract](docs/API.md) |
| Interaction | `click`, `type`, `scroll`, and `press`; selector-first with bounded coordinate fallback | Runtime source + [API contract](docs/API.md) |
| Session lifecycle | One UUID session, explicit states, vision budget, expiry, and idempotent end | Runtime source + [architecture contract](docs/ARCHITECTURE.md) |
| Human view | The same headed display exposed locally through noVNC | Architecture contract; integration proof pending |
| Authority and navigation | Observer/controller roles plus HTTP(S), redirect, address, and pinned-egress checks | Source + security tests |

## Quick start

> [!NOTE]
> This local command exercises the loopback application shape. It does not start Chrome's Linux
> display services or constitute container, noVNC, or exact-main release proof.

The integrated Python package requires **Python 3.11+**, an installed Chrome channel usable by
Patchright, and—on Linux—an active headed display. The package does not itself install or start
Xvfb, x11vnc, noVNC, or websockify.

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m aether_browser.main
```

Keep both API and noVNC listeners on numeric loopback. Named `localhost`, wildcard binds, and
direct non-loopback binds are rejected so DNS, container, or interface ambiguity cannot silently
broaden the authority boundary. The supported module launcher validates and owns the Uvicorn bind
and disables proxy-header interpretation; do not bypass it with a raw Uvicorn CLI invocation.

| Setting | Current default |
|---|---:|
| API | `127.0.0.1:8092` |
| noVNC view URL | `http://127.0.0.1:6080/vnc.html` |
| Idle timeout | `300` seconds |
| Absolute lifetime | `3,600` seconds |
| Maximum default vision budget | `25` snapshots |

After the integrated runtime is healthy, the smallest local contract check is:

```bash
curl http://127.0.0.1:8092/browser/health
```

Strict loopback local mode may run without bearer tokens only while remote mode is disabled and
both listeners remain numeric-loopback-bound. Authenticated local mode may use distinct observer
and controller tokens.

### Authenticated remote API

v0.1 never binds the API directly to a remote interface. Remote clients must terminate HTTPS at
a trusted proxy on the same host, while Aether Browser continues to listen on numeric loopback.
The deployment must set all of the following or startup fails closed:

- `AETHER_BROWSER_REMOTE_MODE=1`
- `AETHER_BROWSER_REVERSE_PROXY_EXPOSED=1`
- a numeric-loopback `AETHER_BROWSER_API_BIND` (normally `127.0.0.1`)
- a non-loopback `AETHER_BROWSER_API_HOST`
- `AETHER_BROWSER_TRUSTED_PROXY_CIDR` as one exact loopback `/32` or `/128`
- `AETHER_BROWSER_TRUSTED_PROXY_SCHEME=https`
- distinct strong `AETHER_BROWSER_OBSERVER_TOKEN` and
  `AETHER_BROWSER_CONTROLLER_TOKEN` values
- `AETHER_BROWSER_TEST_MODE=0` and no `AETHER_BROWSER_TEST_ORIGINS`

The proxy must strip `Forwarded`, every `X-Forwarded-*` header, `X-Real-IP`, and
`X-Original-Host`; Aether Browser rejects them and validates the raw peer plus Host authority.
Never proxy port 6080 or the noVNC paths. See the
[transport and authority contract](docs/API.md#transport-and-authority).

Container gateway integration for this proxy-only contract remains pending. Container
acceptance must use an isolated namespace/gateway or an exec-based probe; it must not make the
API or noVNC listener non-loopback merely to make a host-side test reachable.

## API surface

Every payload carries `api_version: "v1"`, unknown fields are rejected, and session-scoped
payloads keep the UUID explicit.

| Route | Observer | Controller | Purpose |
|---|:---:|:---:|---|
| `GET /browser/health` | ✓ | ✓ | Read bounded runtime health |
| `POST /browser/session/create` | — | ✓ | Create the single owned session |
| `POST /browser/navigate` | — | ✓ | Navigate to an allowed HTTP(S) destination |
| `POST /browser/snapshot` | ✓ | ✓ | Consume one vision step and return structured state + PNG |
| `POST /browser/interact` | — | ✓ | Apply one bounded interaction |
| `POST /browser/session/end` | — | ✓ | End and clean up idempotently |

Request limits, response shapes, stable error codes, and authority semantics live in
[`docs/API.md`](docs/API.md).

## Architecture

```mermaid
flowchart LR
    Agent["Agent client"] -->|closed JSON API| API["FastAPI"]
    API --> Guard["authority + navigation policy"]
    Guard --> Session["single-session manager"]
    Session --> Chrome["Patchright + headed Chrome"]
    Chrome --> State["text · accessibility · PNG"]
    State --> Agent
    Chrome --> Display["shared display"]
    Display -->|loopback noVNC| Human["Human observer / takeover"]
```

The session manager owns browser state, timing, sequence, snapshot budget, temporary profile,
and cleanup. Keeping the session ID explicit preserves a stable client contract without
pretending v0.1 is a multi-worker pool. See the full
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) contract.

## Safety boundary

The safety model is intentionally based on a smaller surface:

- API and noVNC defaults are loopback-only; noVNC stays loopback-only in v0.1.
- Direct non-loopback API binding is rejected. Remote API use requires the complete trusted
  same-host HTTPS proxy tuple and distinct strong observer/controller tokens.
- Top-level HTTP(S) destinations and redirects must be revalidated against credential, scheme,
  address-class, rebinding, and browser-initiated navigation rules.
- URLs, selectors, input, text, accessibility trees, screenshots, coordinates, timeouts, and
  vision steps are bounded.
- The public API contains no arbitrary JavaScript, DevTools, upload, clipboard, download,
  extension, shell, filesystem, credential, or cookie-import operation.
- Ending, expiry, failure, and shutdown converge on owned-resource cleanup.

These boundaries are implemented, but the release candidate must still prove its remaining
container, dedicated-runner, exact-main, and acceptance gates before its status changes.

### Source recovery and exclusions

The source-recovery rule is **reuse general browser behavior, not private domain code**.
Lifecycle, structured-state, interaction, and cleanup patterns may be adapted from authorized
references; ATS/trading integrations, broker or account selectors, order actions, secrets,
private host details, and credential injection are excluded from the public core. A complete
source-recovery and license report is still an RC deliverable and is not claimed by this README.

### Deliberate non-goals for v0.1

- No hosted cloud service, cloud control plane, or production remote-hosting claim.
- No bundled model, account system, dashboard, credential vault, or credential injection.
- No CAPTCHA bypass, anti-detection guarantee, stealth claim, or proxy rotation.
- No arbitrary JavaScript, shell, filesystem, upload, clipboard, or download API.
- No multi-session pool, ATS integration, trading integration, or brokerage behavior.

## Development verification

The repository's configured local checks are:

```bash
python -m pip install -e ".[dev]"
python -m ruff check .
python -m mypy src
python -m pytest
```

Those commands describe the checked-in development toolchain; this README edit does not assert
that exact-main CI, dedicated-runner, container, or end-to-end acceptance gates are green.

## License

Aether-owned code in this repository is licensed under the
[Apache License 2.0](LICENSE). Third-party components remain subject to their own licenses and
distribution terms.

<p align="center"><strong>Aether Browser</strong> · See what your agent sees.</p>
