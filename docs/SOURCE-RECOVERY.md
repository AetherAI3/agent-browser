# Source recovery and provenance record

## Policy

Agent Browser may reuse general browser-runtime behavior from Aether-owned references, but it
must not import private product domains, credentials, host details, account workflows, or
unrelated selectors. Third-party code may be copied only after provenance and compatible terms
are established.

## Current status

The v0.1 source tree is licensed as Aether-owned Apache-2.0 code. The integration review was
completed on 2026-09-02. No historical checkout existed at the requested local path, so it was
omitted rather than treated as evidence. Private references are identified below by sanitized
labels; their repository identities and product-specific context are intentionally not part of
the public release record.

The review found no direct source-code or prompt copying from a private reference. Private
repositories supplied behavioral requirements only, and the implementation was independently
rewritten against this repository's public API and security contracts. The public launch
reference supplied documentation and evidence-discipline patterns under Apache-2.0; none of its
product code, name, or artwork was reused. There is no unresolved provenance question affecting
the v0.1 files.

## Sanitized recovery matrix

| Source and exact commit | Relevant source paths | Behavior reviewed | Decision and ownership/license basis | Target paths and regression evidence |
|---|---|---|---|---|
| Historical local checkout | Requested path was absent; no commit or file was available | None | **Omit.** No source or license claim was inferred from an absent checkout. | The current implementation and tests are the only release source of truth. |
| R1: Aether-owned private browser reference at `b2877aec69166379097ca7a21809fcfacf06ea92` | `agent-browser/app/main.py`, `app/services/patchright_ctx.py`, `app/services/operator_controls.py`, deployment units, and the generic API contract | Headed lifecycle, one API/display session, bounded state, authority separation, and cleanup | **Clean rewrite of behavior.** The private repository has no public license grant, so no implementation was copied. Account actions, private topology, secrets, selectors, and its incomplete IPv6 listener pattern were omitted. | `src/agent_browser/{main,runtime,sessions,auth,policy}.py`, `Dockerfile`, `docker-compose.yml`, and `scripts/container-entrypoint.sh`; protected by `tests/test_{api,auth,runtime,sessions,security,workflow_shape}.py`. |
| R2: Aether-owned private client reference at `f56d91c0e19ee21b6d660f7e132e84903541b2af` | `agent/agent-browser_client.py` and `agent/browser_tool_injector.py` | Thin typed calls, bounded timeouts, explicit capability injection, selector-first interaction, and cleanup guidance | **Rewrite interface ideas; omit the injector.** No public license grant exists. Global secrets, credential forwarding, permissive request shapes, provider prompts, and private framework coupling were excluded. | The closed server contract is in `docs/API.md` and `src/agent_browser/{models,main,sessions,runtime}.py`; covered by `tests/test_{api,models,sessions,runtime,security}.py`. No client SDK ships in v0.1. |
| R3: Aether-owned private browser/noVNC reference at `7a078ac9469d02c2e770e06861a282ff218b999a` | Browser security/contract documents, browser and VNC authority modules, egress units, and their tests | Read-versus-control authority, session binding, expiry/revocation, fail-closed egress, bounded evidence, and sanitized failures | **Adapt principles only.** No public license grant exists. Trading/account semantics, tenant identities, private origins/topology, raw dispatch, secrets, and off-host-custody claims were omitted. | `src/agent_browser/{auth,policy,runtime,sessions,main}.py`, `docs/SECURITY.md`, `docs/RELEASE_EVIDENCE.md`, and `scripts/verify_release.py`; covered by `tests/test_{auth,policy,security,sessions,api,workflow_shape}.py`. Remote noVNC and off-host custody are not claimed. |
| Public launch reference `AetherAI3/AntiFlock@d3ce14fa892bbc2f587ab4c1a63f4ebdc91bb98f` | `docs/launch/`, release workflow, README, `LICENSE`, and `NOTICE` | Evidence-backed claims, immutable release inputs, pinned actions, clean-tree builds, checksums, and draft-before-publish discipline | **Adapt documentation patterns.** Apache-2.0 permits reuse; product code, branding, artwork, and product-specific claims were not reused. | `README.md`, `.github/workflows/`, `docs/{launch,RELEASE_EVIDENCE,DEMO_EVIDENCE}.md`, and `scripts/verify_release.py`; protected by `tests/test_{workflow_shape,demo}.py` and exact-main release checks. |

## Permanent exclusions

The public core excludes account-specific workflows, financial actions, private selectors,
secrets, credential injection, private infrastructure details, and copied code whose licensing
or ownership is unclear. An unresolved provenance question blocks the affected file's release,
not the documentation of unrelated verified work.
