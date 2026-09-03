# Agent Browser v0.x security model

This document describes the implemented v0.x boundary and the assumptions a deployer must keep
true. It is not a certification or a claim that the software has no vulnerabilities.

## Assets and trust zones

The protected assets are browser state, page contents, screenshots, typed input, navigation
authority, and the host running the browser. API clients, destination websites, DNS answers,
redirects, page scripts, selectors, and interaction payloads are untrusted.

The local operator, host administrator, container engine, exact application image, and processes
that can reach host loopback are trusted. In authenticated proxy mode, the separately managed
same-host TLS proxy is also trusted. Agent Browser does not create a secure boundary against a
host administrator or a compromised container engine.

## Listener and authority boundary

- API default: `127.0.0.1:8092`.
- noVNC default: `127.0.0.1:6080`.
- Raw VNC inside the local container profile: `127.0.0.1:5900`.
- Named localhost, wildcard, mapped, scoped, and direct non-loopback listener forms are rejected.
- Strict local mode may omit bearer tokens only when remote mode is disabled and both user-facing
  listeners remain numeric-loopback-bound.
- Authenticated mode separates observer and controller authority with distinct token digests.
  Observer access is limited to health and snapshots; controller access covers lifecycle,
  navigation, and interaction.

The v0.x noVNC surface has no application authentication. Any process or user able to reach the
host loopback interface is trusted with the live view and takeover capability. Do not proxy,
tunnel, publish, or bridge the noVNC or raw VNC ports.

## Remote API mode

The application still listens on numeric loopback. A remote client must terminate HTTPS at a
same-host reverse proxy while the runtime verifies the complete remote-mode tuple described in
[`API.md`](API.md#transport-and-authority). Partial configuration fails startup. Forwarding
headers are rejected rather than interpreted, the raw peer must match one exact loopback CIDR,
and Host authority must match the configured external host.

The proxy must strip forwarding headers, reject unexpected authority, keep the backend socket
private, and never route noVNC. Agent Browser does not ship or configure that TLS proxy.

## Navigation and egress

Only HTTP(S) top-level destinations are accepted. Policy rejects embedded credentials,
unsupported schemes, malformed authority, prohibited host classes, blocked IP ranges, unsafe
redirects, and DNS rebinding. The runtime validates browser-initiated main-frame navigation as
well as API-requested navigation.

Allowed address sets become per-session connection plans. Browser TCP traffic is routed through
an owned loopback SOCKS proxy that dials only published pins. Non-proxied WebRTC UDP is disabled.
This reduces network escape paths; it does not make hostile websites safe or replace host-level
egress controls.

## Browser and session containment

- One active session is permitted per runtime.
- Public inputs and returned state are size-bounded.
- Click and type use a selector or bounded coordinates, never both.
- Key presses use an allowlist; there is no arbitrary JavaScript, raw CDP, shell, filesystem,
  upload, download, clipboard, extension, cookie import, or credential import API.
- Idle timeout, absolute lifetime, and vision budget limit resource ownership.
- End, expiry, failure, and application shutdown converge on cleanup of the page, context,
  browser, temporary profile, timers, proxy, and registry state.

The container profile drops Linux capabilities, enables no-new-privileges, uses a read-only root
filesystem plus bounded tmpfs mounts, and sets CPU, memory, PID, and shared-memory limits. These
controls complement, but do not replace, a patched host and rootless container engine.

## Data handling

Snapshots may contain sensitive page text, accessibility data, and a PNG image. Treat API
responses, demo captures, traces, and logs as confidential unless their source page is known to
be public and deterministic. The runtime does not provide a credential vault or durable session
recording. Deployers own retention, transport security, and access control outside the process.

## Known limitations

- noVNC is unauthenticated and local-only in the v0.x line.
- The Compose quickstart uses Linux host networking and trusts processes that can reach host
  loopback.
- The runtime is a single-session worker, not a multi-tenant isolation boundary.
- Page content can be malicious; structured state is still untrusted input to an agent.
- DNS and upstream network infrastructure remain external trust dependencies.
- Container builds resolve the then-current Google Chrome Stable package. The read-only runtime
  does not auto-update it; retain the recorded version and image ID, and rebuild promptly for
  browser security fixes.
- A locally built container is the intended v0.x runtime. Do not publish a Chrome-containing
  image, image tar, or public layer cache without separately documented redistribution
  authorization.
- Exact-runner, exact-main acceptance, and demo claims are valid only when the corresponding
  commit-bound evidence records are complete.

## Deployment checklist

1. Build from a reviewed exact commit and retain its image ID and SBOM.
2. Record the resolved Chrome version and rebuild when browser fixes are released.
3. Keep API, noVNC, and raw VNC off non-loopback interfaces.
4. Use rootless containers and do not mount a container-engine socket into the workload.
5. Apply host firewall and egress controls in addition to application policy.
6. Use distinct high-entropy observer and controller tokens for authenticated mode.
7. Put remote API access behind a same-host HTTPS proxy that matches the documented tuple.
8. Never route noVNC through that proxy.
9. Sanitize logs and evidence before sharing them.

Report suspected boundary failures through the private process in [`../SECURITY.md`](../SECURITY.md).
