# Release-candidate evidence

## Status

This document records exact-commit evidence for release candidate `v0.1.0-rc.1`. Every field is
bound to one runtime commit, one immutable image ID, and one hosted workflow run.

Tested runtime commit: `055f68787833c7e00e13ccbe0c0ecb69a8da3659`

Release candidate: `v0.1.0-rc.1`, published as a source-only prerelease. No Chrome-containing
image, image tar, filesystem layer, or reusable layer cache is published.

## Execution environment

The `release-evidence` workflow (`.github/workflows/trusted-runner.yml`) executed on GitHub-hosted
`ubuntu-24.04` runners. The self-hosted VPS6 record is a separate, partial host qualification and
did not execute this release build; see [Host attestation scope](#host-attestation-scope).

## Commit-bound record

| Gate | Required evidence | Recorded result |
|---|---|---|
| Clean checkout | exact commit and clean status | `ref-proof` resolved `main` to `055f6878…`; every downstream job checked out and re-verified that exact SHA ([job](https://github.com/AetherAI3/aetherbrowser/actions/runs/33612893919/job/100191969603)) |
| Hosted CI | exact-commit run URL and job matrix | `ci` succeeded at `055f6878…`: [run 33612893930](https://github.com/AetherAI3/aetherbrowser/actions/runs/33612893930) |
| Trusted runner | runner labels, exact commit, service isolation | GitHub-hosted `ubuntu-24.04`; rootless Podman API bound to a private per-job socket; acceptance ran in an isolated pod with `--network none` |
| Static and unit checks | command, counts, and run URL | `ruff format --check`, `ruff check`, `mypy src`, and `pytest -q` all passed inside the strict gate (`quality`: "format, lint, typing, and tests passed") |
| Dependency audit | locked input and result | `dependency-audit` PASS — locked `requirements.lock` consistency and vulnerability audit passed |
| SBOM and vulnerability scan | artifact names and SHA-256 | Syft `1.51.1` → `sbom.spdx.json` (`fdaa4fd80700b79210aaa426344888df3db8436f783f59f44dd0a60f23867deb`, 629 packages); Grype `0.118.0` → `vulnerabilities.json` (`41dc5cc366c2c63d91930b40d152a5c18f51040990c112cbd5b82944425727f8`) |
| Rootless image build | image ID and base digest | Image ID `sha256:a9253e91fcda87e56dd0c695f68a2da3e9defad7a3e347faa71c060c78a4b101`; base `docker.io/library/python@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84`; final image runs as `USER 10001:10001` |
| Isolated acceptance | exact image ID, network proof, cleanup proof | `acceptance.json` = PASS over 33 checks against the same image ID, including `isolated-pod-network-none`, `same-pod-namespace`, `loopback-listener-proof`, `process-cleanup`, `profile-cleanup`, and `pod-cleanup` ([job](https://github.com/AetherAI3/aetherbrowser/actions/runs/33612893919/job/100193455090)) |
| README quickstart | fresh-directory reproduction | `quickstart.json` = PASS for `docker compose up --build --detach` at the same commit ([job](https://github.com/AetherAI3/aetherbrowser/actions/runs/33612893919/job/100192012527)) |
| Demo | link to `DEMO_EVIDENCE.md` | [`docs/DEMO_EVIDENCE.md`](DEMO_EVIDENCE.md) records the generated media, its provenance, and its limits |
| Third-party review | Chrome terms, exact package/credits, notices, and redistribution decision | `Google Chrome 152.0.7977.75`, package `google-chrome-stable 152.0.7977.75-1`, `amd64`; `installed-notices.tar.gz` (`b0ed7eadfa2872fe0a7366c871c2652282a299d0bdbc7df50b3eb8b186bf4cc7`); distribution is source-only |
| Repository state | visibility and open-PR review | Repository published with branch protection on `main` (strict, linear history; force-push and deletion disabled); no open pull request or unresolved review thread blocks this candidate |

## Strict repository gate

`python scripts/verify_release.py --strict --skip-external` ran on the hosted runner. At the
runtime commit it reported 12 PASS, 2 SKIP, and 3 FAIL; all three failures were the evidence
artifacts this commit supplies (`required-release-files`, `asset-integrity`, and
`exact-commit-evidence`). The two skips are the deliberate `--skip-external` entries
`rootless-container` and `acceptance-and-novnc`, which the separate `container` and `acceptance`
jobs prove independently against the exact image ID.

Passing gate checks at the runtime commit: `required-core-files`, `required-source-modules`,
`version-agreement` (`0.1.0` across project, package, Compose, and image label), `secret-scan`,
`private-infrastructure-scan`, `excluded-domain-scan`, `credential-injection-scan`,
`container-loopback-contract`, `readme-integrity`, `unsupported-claims`, `quality`, and
`dependency-audit`.

## Vulnerability posture

Grype ran against the exact image with `--fail-on high --only-fixed` and did not block the build:
there are no fixed high-or-higher findings. This is not a claim that the image is free of
vulnerabilities. `vulnerabilities.json` records the full match set, which includes findings with no
released fix, and the Debian-derived base contributes findings that upstream marks will-not-fix.
Consumers should re-scan any image they build and apply their own risk policy.

## Host attestation scope

The self-hosted VPS6 qualification run is partial and is not a release-build attestation.

- Passed scope: capacity, CI-only host posture, worker isolation, cgroup bounds, cleanup.
- Failed scope: Podman, Syft, Grype toolchain availability.
- Result: `partial`; the release workload executed independently on ephemeral GitHub-hosted
  runners.

## Closure assertions

- Runtime, authority, navigation, container, CI, and documentation all refer to
  `055f68787833c7e00e13ccbe0c0ecb69a8da3659`.
- No required check is red or attached to another commit; the only skipped checks are the two
  declared `--skip-external` entries, each independently proven by a dedicated job.
- No blocking fixed high-or-higher security finding and no unresolved review thread remains.
- The source release reproduces the documented Linux Compose quickstart.
- Demo media was generated from the exact accepted image at this commit and has recorded
  checksums.
- Chrome-containing image, image-tar, and public layer-cache publication remain off. The SBOM and
  notice bundle are inventory evidence and do not by themselves grant redistribution authorization.
- Artifact existence alone is not treated as success; each job conclusion and the strict-gate
  result are recorded above.

## Sanitization

This document records only public-safe run URLs, commit IDs, image IDs, and artifact checksums. It
contains no tokens, private hostnames, server addresses, runner registration commands,
credentials, private filesystem paths, or raw private logs.
