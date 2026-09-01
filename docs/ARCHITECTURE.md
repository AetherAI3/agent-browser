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
                                         Patchright + Chrome
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
- The supported module launcher owns the validated Uvicorn bind; raw Uvicorn CLI overrides are
  outside the transport contract.
- Direct non-loopback API binding is rejected even when bearer tokens are configured.
- Authenticated remote API use requires explicit remote and reverse-proxy modes, a non-loopback
  effective host, distinct strong observer/controller tokens, an exact loopback trusted-proxy
  CIDR, an `https` proxy-scheme declaration, disabled test mode, and no test origins.
- The raw proxy peer and Host authority are validated. Uvicorn proxy-header parsing is disabled,
  and forwarding headers are rejected instead of becoming authority inputs.
- noVNC remains literal loopback-only and is never included in the remote proxy surface in v0.1.
- Container gateway integration is pending. Isolated-namespace/gateway or exec-based acceptance
  must preserve the loopback listener contract rather than widening either bind for reachability.
- Test-only local origins require explicit test mode and an exact allowlist; production defaults never enable that exception.

## Browser boundary

The browser accepts top-level HTTP(S) navigation only after address and DNS policy checks. Every redirect and browser-initiated top-level navigation is revalidated. Downloads are disabled; popups are denied or closed; new tabs remain bounded to session ownership. The public API exposes no script evaluation, DevTools, upload, clipboard, extension, shell, filesystem, credential, or cookie import operation.

## Non-goals

There is no database, account system, hosted service, cloud control plane, dashboard, bundled model, proxy rotation, CAPTCHA bypass, anti-detection guarantee, MCP server, credential vault, credential injection, multi-session pool, trading integration, or production remote-hosting claim in v0.1.
