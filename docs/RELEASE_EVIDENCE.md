# Release-candidate evidence

## Status

This document records exact-commit evidence for release candidate `v0.1.0-rc.1` of Agent Browser.
Every field is bound to one runtime commit, one immutable image ID, and one hosted workflow run.

Tested runtime commit: `5d40be5ec3022170058920abbad49c9f942a6fd7`

Release candidate: `v0.1.0-rc.1`, published as a source-only prerelease. No Chrome-containing
image, image tar, filesystem layer, or reusable layer cache is published.

## Execution environment

The `release-evidence` workflow (`.github/workflows/trusted-runner.yml`) executed on GitHub-hosted
`ubuntu-24.04` runners. The self-hosted VPS6 record is a separate, partial host qualification and
did not execute this release build; see [Host attestation scope](#host-attestation-scope).

## Commit-bound record

Unless stated otherwise, every row below comes from `release-evidence`
[run 33678503714](https://github.com/AetherAI3/agent-browser/actions/runs/33678503714).

| Gate | Required evidence | Recorded result |
|---|---|---|
| Clean checkout | exact commit and clean status | `ref-proof` resolved `main` to `5d40be5e…`; every downstream job checked out and re-verified that exact SHA ([job](https://github.com/AetherAI3/agent-browser/actions/runs/33678503714/job/100409072289)) |
| Hosted CI | exact-commit run URL and job matrix | `ci` succeeded at `5d40be5e…`: [run 33678503395](https://github.com/AetherAI3/agent-browser/actions/runs/33678503395) |
| Trusted runner | runner labels, exact commit, service isolation | GitHub-hosted `ubuntu-24.04`; rootless Podman API bound to a private per-job socket; acceptance ran in an isolated pod with `--network none` |
| Static and unit checks | command, counts, and run URL | `ruff format --check`, `ruff check`, `mypy src`, and `pytest -q` all passed inside the strict gate (`quality`: "format, lint, typing, and tests passed") |
| Dependency audit | locked input and result | `dependency-audit` PASS — locked `requirements.lock` consistency and vulnerability audit passed |
| SBOM and vulnerability scan | artifact names and SHA-256 | Syft `1.51.1` → `sbom.spdx.json` (`fb3a9d1f5269a636b8899cedb3b3baf92166b56b3eef947ab2d98d940706c807`, 629 packages); Grype `0.118.0` → `vulnerabilities.json` (`49c1e13799f6efc0b8ed796cc69b3594844b6c275aef7da883ebd6165fb716bf`) |
| Rootless image build | image ID and base digest | Image ID `sha256:9b307a99fa0a82ea978192336baee9cb503c55237d1c6c99981c9f248605d756`; base `docker.io/library/python@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84`; final image runs as `USER 10001:10001` ([job](https://github.com/AetherAI3/agent-browser/actions/runs/33678503714/job/100409121725)) |
| Isolated acceptance | exact image ID, network proof, cleanup proof | `acceptance.json` = PASS over 33 checks against the same image ID, including `isolated-pod-network-none`, `same-pod-namespace`, `loopback-listener-proof`, `process-cleanup`, `profile-cleanup`, and `pod-cleanup` ([job](https://github.com/AetherAI3/agent-browser/actions/runs/33678503714/job/100410615522)) |
| README quickstart | fresh-directory reproduction | `quickstart.json` = PASS for `docker compose up --build --detach` at the same commit ([job](https://github.com/AetherAI3/agent-browser/actions/runs/33678503714/job/100409121762)) |
| Demo | link to `DEMO_EVIDENCE.md` | [`docs/DEMO_EVIDENCE.md`](DEMO_EVIDENCE.md) records the generated media, its provenance, and its limits |
| Third-party review | Chrome terms, exact package/credits, notices, and redistribution decision | `Google Chrome 152.0.7977.75`, package `google-chrome-stable 152.0.7977.75-1`, `amd64`; `installed-notices.tar.gz` (`b0ed7eadfa2872fe0a7366c871c2652282a299d0bdbc7df50b3eb8b186bf4cc7`); distribution is source-only |
| Repository state | visibility and open-PR review | Public, with branch protection on `main` (strict, linear history; force-push and deletion disabled); no open pull request or unresolved review thread blocks this candidate |

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

### Reading the two commits

Two commit IDs appear in this evidence and they mean different things:

- **Runtime commit** `5d40be5ec3022170058920abbad49c9f942a6fd7` is the code under test. Every
  runtime proof — the image build, isolated acceptance, quickstart, and the committed demo media —
  was produced from it.
- **Evidence commit** is the runtime commit plus the evidence artifacts themselves. The gate
  enforces that the runtime commit is an ancestor of the evidence checkout and that the entire
  difference between them is confined to `assets/demo.mp4`, `assets/demo-poster.png`,
  `docs/DEMO_EVIDENCE.md`, `docs/RELEASE_EVIDENCE.md`, and `release/evidence/manifest.json`. No
  executable source changed after the runtime proofs.

Because the evidence commit adds files to the build context, the confirmation run that closes the
gate necessarily builds a different image ID than the runtime image recorded above. Its acceptance
suite re-runs the same 33 checks against that image with a freshly generated proof colour and
nonce, which is why the visual binding is evidence of a real run rather than a fixed fixture. The
committed demo media stays the runtime-commit capture and is not replaced by that confirmation run.

The machine-readable attestation from the closing run is committed at
[`release/evidence/manifest.json`](../release/evidence/manifest.json).

## Vulnerability posture

Grype ran against the exact image with `--fail-on high --only-fixed` and did not block the build:
there are no fixed high-or-higher findings. This is not a claim that the image is free of
vulnerabilities. `vulnerabilities.json` records the full match set; of its findings, 12 carry an
available fix, 520 have no released fix, and 330 are marked will-not-fix by the Debian-derived
base. Consumers should re-scan any image they build and apply their own risk policy.

## Host attestation scope

The self-hosted VPS6 qualification run is partial and is not a release-build attestation.

- Passed scope: capacity, CI-only host posture, worker isolation, cgroup bounds, cleanup.
- Failed scope: Podman, Syft, Grype toolchain availability.
- Result: `partial`; the release workload executed independently on ephemeral GitHub-hosted
  runners.

## Closure assertions

- Runtime, authority, navigation, container, CI, and documentation all refer to
  `5d40be5ec3022170058920abbad49c9f942a6fd7`.
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
