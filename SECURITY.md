# Security policy

## Supported versions

Agent Browser ships from the current `main` branch. Security fixes land on the latest release
line; older lines are not backported.

| Version | Supported |
|---|:---:|
| Current `main` / v0.2.x source release | Yes |
| Older snapshots | No |

## Report a vulnerability

Do not disclose a suspected vulnerability in a public issue, pull request, discussion, log, or
demo. Use GitHub's private vulnerability-reporting flow:

<https://github.com/AetherAI3/agent-browser/security/advisories/new>

Private vulnerability reporting is enabled on this repository, so that form is the preferred
channel. If it is ever unavailable to you, contact the AetherAI3 organization through a private
contact method shown on its GitHub profile. Do not include credentials, session material,
live host details, or personal data in an unencrypted public channel.

Include, when safe:

- the affected commit or version;
- the boundary involved (API authority, navigation, egress, browser runtime, noVNC, container,
  or CI);
- minimal reproduction steps using a disposable environment;
- impact and required attacker access;
- relevant sanitized logs; and
- any suggested mitigation.

The maintainers target an acknowledgement within three business days and an initial assessment
within seven. These are response targets, not a guaranteed remediation schedule. Please allow a
coordinated fix and disclosure window.

## Scope

Reports about authority bypass, SSRF or redirect bypass, egress escape, session crossover,
secret exposure, browser/runtime escape, unintended non-loopback exposure, dependency compromise,
or CI trust-boundary failure are in scope. Findings that require intentionally exposing the
documented unauthenticated local noVNC listener beyond loopback are configuration risks, but a
fail-open or misleading default remains worth reporting.

The full trust model and known limitations are in [`docs/SECURITY.md`](docs/SECURITY.md).

## Safe research

Use systems and data you own or are authorized to test. Do not access other people's sessions,
degrade shared services, retain personal data, or publish weaponized details before a fix is
available. No bounty or safe-harbor program is promised by this policy.
