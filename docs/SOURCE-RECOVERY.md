# Source recovery and provenance record

## Policy

Aether Browser may reuse general browser-runtime behavior from Aether-owned references, but it
must not import private product domains, credentials, host details, account workflows, or
unrelated selectors. Third-party code may be copied only after provenance and compatible terms
are established.

## Current status

The v0.1 source tree is licensed as Aether-owned Apache-2.0 code. This document does not yet
certify a path-by-path historical recovery audit. Before the private RC closes, the integration
owner must attach a sanitized matrix for every reference actually consulted:

| Required field | Meaning |
|---|---|
| Source and exact commit | Immutable provenance |
| Relevant source path | File or contract inspected |
| Behavior recovered | General capability, not copied private context |
| Decision | Copy, adapt, rewrite, or omit |
| Ownership/license basis | Evidence permitting the decision |
| Target path | Result in this repository |
| Regression evidence | Test or contract protecting the result |

## Permanent exclusions

The public core excludes account-specific workflows, financial actions, private selectors,
secrets, credential injection, private infrastructure details, and copied code whose licensing
or ownership is unclear. An unresolved provenance question blocks the affected file's release,
not the documentation of unrelated verified work.
