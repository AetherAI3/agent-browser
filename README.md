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
> Runtime and API source are present, while complete security integration, dedicated-runner
> evidence, container acceptance, exact-main CI, and a real demo remain unclaimed until they
> are merged and independently verified.

## One browser, two views

Aether Browser is built around a simple idea: the agent and the human should share the exact
same browser session.

- The **agent** works through a deliberately closed HTTP API.
- The **human** observes or takes over through a loopback-only noVNC view.
- Both meet at one headed Chrome instance owned by one bounded session.
- Structured text and accessibility state come before pixels; screenshots remain available
  when visual context is necessary.

The checked-in runtime already models headed Chrome, structured snapshots, bounded actions,
and single-session ownership. The noVNC host integration and security-lane convergence are
still private-RC work, so this README treats them as contracts—not as completed release proof.

| Surface | Focused v0.1 behavior | Evidence in this checkout |
|---|---|---|
| Browser runtime | Headed Chrome through Patchright, with one owned page and a temporary profile | Runtime source |
| Agent state | URL, title, readable text, bounded accessibility nodes, viewport data, and PNG snapshots | Runtime source + [API contract](docs/API.md) |
| Interaction | `click`, `type`, `scroll`, and `press`; selector-first with bounded coordinate fallback | Runtime source + [API contract](docs/API.md) |
| Session lifecycle | One UUID session, explicit states, vision budget, expiry, and idempotent end | Runtime source + [architecture contract](docs/ARCHITECTURE.md) |
| Human view | The same headed display exposed locally through noVNC | Architecture contract; integration proof pending |
| Authority and navigation | Observer/controller roles plus HTTP(S), redirect, and address-policy checks | Documented contract; security integration pending |

## Quick start

> [!NOTE]
> This is the integrated RC launch shape. On the exact docs-only commit in this pull request,
> the required security modules have not merged into `main` yet, so the command is not presented
> as runnable proof. Use it after security integration lands and exact-main validation is green.

The integrated Python package requires **Python 3.11+**, an installed Chrome channel usable by
Patchright, and—on Linux—an active headed display. The package does not itself install or start
Xvfb, x11vnc, noVNC, or websockify.

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m uvicorn aether_browser.main:app --host 127.0.0.1 --port 8092
```

This is the intended application entry point and loopback default after convergence. The
security lane is adding the fail-closed authority and navigation-policy wiring required for the
RC; until that work is merged, keep both API and noVNC on loopback and do not treat this command
as a completed quick-start proof.

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
both listeners remain loopback-bound. Authenticated deployments use distinct observer and
controller tokens; see the [transport and authority contract](docs/API.md#transport-and-authority).

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
- Non-loopback API binding requires explicit remote mode and distinct strong observer/controller
  tokens under the contract.
- Top-level HTTP(S) destinations and redirects must be revalidated against credential, scheme,
  address-class, rebinding, and browser-initiated navigation rules.
- URLs, selectors, input, text, accessibility trees, screenshots, coordinates, timeouts, and
  vision steps are bounded.
- The public API contains no arbitrary JavaScript, DevTools, upload, clipboard, download,
  extension, shell, filesystem, credential, or cookie-import operation.
- Ending, expiry, failure, and shutdown converge on owned-resource cleanup.

These are contract requirements, not a claim that the still-unmerged security and acceptance
gates have passed. The release candidate must prove them before its status changes.

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
