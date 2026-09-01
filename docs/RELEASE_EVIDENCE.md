# Private release-candidate evidence

## Status

This is an evidence schema, not a completed release claim. Exact-main convergence has not filled
the commit-bound fields below.

Tested commit: not yet captured

Release candidate: `v0.1.0-rc.1` proposed; tag or private prerelease not yet evidenced here.

## Required commit-bound record

| Gate | Required evidence | Current record |
|---|---|---|
| Clean checkout | exact commit and clean status | Not captured |
| Hosted CI | exact-commit run URL and job matrix | Not captured |
| Trusted runner | runner labels, exact commit, service isolation | Not captured |
| Static and unit checks | command, counts, and run URL | Not captured |
| Dependency audit | locked input and result | Not captured |
| SBOM and vulnerability scan | artifact names and SHA-256 | Not captured |
| Rootless image build | image ID and base digest | Not captured |
| Isolated acceptance | exact image ID, network proof, cleanup proof | Not captured |
| README quickstart | fresh-directory reproduction | Not captured |
| Demo | link to `DEMO_EVIDENCE.md` after capture | Not captured |
| Third-party review | exact installed-package notice bundle | Not captured |
| Repository state | private visibility and open-PR review | Not captured |

## Closure assertions

Each assertion needs exact evidence before it may be marked true:

- Runtime, authority, navigation, container, CI, and documentation refer to one exact commit.
- No required check is red, skipped without an approved reason, or attached to another commit.
- No high-severity security finding or unresolved review thread remains.
- The source release reproduces the documented Linux Compose quickstart.
- Demo media was captured from the exact accepted image and has recorded checksums.
- Binary image publication remains off until installed-license obligations are reviewed.
- Repository visibility, Pages, and launch channels remain unpublished until owner approval.

## Sanitization

Record public-safe run URLs, commit IDs, test totals, image IDs, and artifact checksums. Never put
tokens, private hostnames, server addresses, runner registration commands, credentials, private
filesystem paths, or raw private logs in this document.
