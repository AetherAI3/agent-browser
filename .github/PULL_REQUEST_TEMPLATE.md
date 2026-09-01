## Purpose

Describe the user-visible or contract-level outcome and why it is needed.

## Exact scope

- Base commit:
- Exact head commit:
- Allowed paths:
- Explicit non-goals:
- Source or contract references:
- Dependency pull requests:

## Verification

List the exact commands and commit-bound CI run URLs. Include success, denial, cleanup, and
regression evidence where relevant.

## Security and privacy

Explain authority, navigation, egress, listener, browser, container, CI, data-handling, and
supply-chain effects. State `No security-boundary change` when that is accurate.

## Rollback

Describe the smallest safe revert and any state that would remain after it.

## Confidence and unknowns

- Confidence:
- Known limitations:
- Unknowns requiring follow-up:

## Checklist

- [ ] The branch is based on the declared commit and the exact head is recorded above.
- [ ] Public behavior and documentation agree.
- [ ] Tests cover the changed contract and its denial or cleanup path.
- [ ] No secrets, private host details, personal data, or private page content are included.
- [ ] Dependency changes update both the input and hash lock through the approved workflow.
- [ ] The change does not publish the repository, Pages, a release, or a hosted service.
- [ ] CI evidence belongs to this exact head and no valid review finding is unresolved.
