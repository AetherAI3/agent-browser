# Aether Browser v0.1 architecture contract

Aether Browser is one headed Chrome session with two interfaces to the same runtime: a small HTTP API for agent control and loopback-only noVNC for human observation or takeover.

```text
remote API client ──HTTPS──> trusted same-host TLS proxy
                                      │
                                      │ loopback HTTP; no forwarded headers
                                      v
local API client ───────────────> FastAPI ──> authority + navigation policy
                                                │
                                                v
                                         single-session manager
                                                │
                                                v
                                  Patchright + Google Chrome Stable
                                                │
                                            Xvfb display
                                                │
                                       x11vnc + websockify
                                                │
Human browser <────────────loopback noVNC────────┘
```

## State and ownership

The runtime owns at most one UUID session. Its state advances `idle → starting → active → ending → ended`; expiry changes active to `expired`, and launch or process failure changes the current state to `failed` before cleanup. Creation is guarded by one asynchronous lock. Snapshot budget and sequence are updated under the same session lock. End is idempotent, and shutdown cleans every resource owned by the runtime: page, context, browser, Patchright manager, temporary profile, timers, registry state, and child processes.

The session ID remains explicit in every session-scoped payload so a future multi-worker pool can preserve this API without changing clients.

## Runtime boundaries

- API default: `127.0.0.1:8092`.
- noVNC default: `127.0.0.1:6080`.
- Actual API and noVNC listeners accept numeric loopback literals only.
- The container invokes `python -m aether_browser.main`; that validated module launcher owns the
  Uvicorn bind, and raw Uvicorn CLI overrides are outside the transport contract.
- Direct non-loopback API binding is rejected even when bearer tokens are configured.
- Authenticated remote API use requires explicit remote and reverse-proxy modes, a non-loopback
  effective host, distinct strong observer/controller tokens, an exact loopback trusted-proxy
  CIDR, an `https` proxy-scheme declaration, disabled test mode, and no test origins.
- The raw proxy peer and Host authority are validated. Uvicorn proxy-header parsing is disabled,
  and forwarding headers are rejected instead of becoming authority inputs.
- noVNC remains unauthenticated, literal loopback-only, and outside the remote proxy surface in
  v0.1. The container entrypoint rejects every `AETHER_BROWSER_NOVNC_BIND` value except
  `127.0.0.1`; its implementation-only raw VNC socket is fixed to `127.0.0.1:5900`.
- Test-only local origins require explicit test mode and an exact allowlist; production defaults never enable that exception.

The local Compose quickstart uses Linux host networking so those loopback binds are the developer host's loopback interface. There are no published container ports and no noVNC listener on a bridge interface. This local mode joins the host network trust boundary: every process that can access host loopback is trusted, and the Browser container can access host-network services. Docker Desktop bridge networking is not equivalent to this topology. URL and browser-egress policy remain mandatory.

Trusted CI uses a different, stricter topology. The preceding build job publishes the immutable image ID for the exact proved commit; acceptance fails unless that ID still matches the commit-tagged local image and runs containers by ID with pulling disabled. Acceptance then creates one resource-bounded Podman pod with `--network none`; the deterministic fixture and Browser share only that pod's loopback interface. The runner publishes no ports, joins no host or bridge network, and drives readiness, HTTP, WebSocket, and API checks through remote `podman exec` inside the fixture container. Live interface and listener checks prove that the namespace contains only loopback and that API, noVNC, and raw VNC remain bound to `127.0.0.1`. This preserves the real same-display proof without giving release-candidate code internet, production, or host-network access.

## Browser boundary

The browser accepts top-level HTTP(S) navigation only after address and DNS policy checks. Every
redirect and browser-initiated network-producing top-level navigation is revalidated;
same-document and history-only changes do not produce a routed request and are not claimed as
revalidated. Chrome disables non-proxied WebRTC UDP so WebRTC cannot bypass the pinned TCP
proxy boundary. Downloads are disabled; popups are denied or closed; new tabs remain bounded to
session ownership. The public API exposes no script evaluation, DevTools, upload, clipboard,
extension, shell, filesystem, credential, or cookie import operation.

## Non-goals

There is no database, account system, hosted service, cloud control plane, dashboard, bundled model, proxy rotation, CAPTCHA bypass, anti-detection guarantee, MCP server, credential vault, credential injection, multi-session pool, trading integration, or production remote-hosting claim in v0.1.
