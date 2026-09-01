# Aether Browser v0.1 architecture contract

Aether Browser is one headed Chrome session with two interfaces to the same runtime: a small HTTP API for agent control and loopback-only noVNC for human observation or takeover.

```text
API client ──HTTP──> FastAPI ──> authority + navigation policy
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
Human browser <──loopback noVNC───┘
```

## State and ownership

The runtime owns at most one UUID session. Its state advances `idle → starting → active → ending → ended`; expiry changes active to `expired`, and launch or process failure changes the current state to `failed` before cleanup. Creation is guarded by one asynchronous lock. Snapshot budget and sequence are updated under the same session lock. End is idempotent, and shutdown cleans every resource owned by the runtime: page, context, browser, Patchright manager, temporary profile, timers, registry state, and child processes.

The session ID remains explicit in every session-scoped payload so a future multi-worker pool can preserve this API without changing clients.

## Runtime boundaries

- API default: `127.0.0.1:8092`.
- noVNC default: `127.0.0.1:6080`.
- Non-loopback API binding requires explicit remote mode and distinct strong observer/controller tokens.
- noVNC remains loopback-only in v0.1.
- Test-only local origins require explicit test mode and an exact allowlist; production defaults never enable that exception.

## Browser boundary

The browser accepts top-level HTTP(S) navigation only after address and DNS policy checks. Every redirect and browser-initiated top-level navigation is revalidated. Downloads are disabled; popups are denied or closed; new tabs remain bounded to session ownership. The public API exposes no script evaluation, DevTools, upload, clipboard, extension, shell, filesystem, credential, or cookie import operation.

## Non-goals

There is no database, account system, hosted service, cloud control plane, dashboard, bundled model, proxy rotation, CAPTCHA bypass, anti-detection guarantee, MCP server, credential vault, credential injection, multi-session pool, trading integration, or production remote-hosting claim in v0.1.
