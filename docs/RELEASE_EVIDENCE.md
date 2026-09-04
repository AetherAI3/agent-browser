# Release evidence

## Status

This document records exact-commit evidence for release `v0.2.1` of Agent Browser. Runtime claims
are bound to one runtime commit, one immutable image ID, and one hosted workflow run.

Tested runtime commit: `38cc23f281001a9d678642916da522c047f9151d`

Release: `v0.2.1`, source-only. The tag is created from the manifest-closure commit after this
runtime record is confirmed. No Chrome-containing image, image tar, filesystem layer, or reusable
layer cache is published. Earlier releases keep their own tags and historical evidence; this patch
also corrects the evidence-packaging order used for `v0.2.0`, whose tag preceded its later evidence
closure.

## Execution environment

The `release-evidence` workflow (`.github/workflows/trusted-runner.yml`) executed on GitHub-hosted
`ubuntu-24.04` runners. The self-hosted VPS6 record is a separate, partial host qualification and
did not execute this release build; see [Host attestation scope](#host-attestation-scope).

## Commit-bound record

Unless stated otherwise, every row below comes from `release-evidence`
[run 33860164841](https://github.com/AetherAI3/agent-browser/actions/runs/33860164841).

| Gate | Required evidence | Recorded result |
|---|---|---|
| Clean checkout | exact commit and clean status | `ref-proof` resolved `main` to `38cc23f2…`; every downstream job checked out and re-verified that exact SHA ([job](https://github.com/AetherAI3/agent-browser/actions/runs/33860164841/job/100982536663)) |
| Hosted CI | exact-commit run URL and job matrix | `ci` succeeded at `38cc23f2…`: [run 33860164864](https://github.com/AetherAI3/agent-browser/actions/runs/33860164864) |
| Hosted release runner | runner labels, exact commit, service isolation | GitHub-hosted `ubuntu-24.04`; rootless Podman API bound to a private per-job socket; acceptance ran in an isolated pod with `--network none` |
| Static and unit checks | command, counts, and run URL | `ruff format --check`, `ruff check`, `mypy src`, and `pytest -q` all passed inside the strict gate (`quality`: "format, lint, typing, and tests passed") |
| Dependency audit | locked input and result | `dependency-audit` PASS — locked `requirements.lock` consistency and vulnerability audit passed |
| SBOM and vulnerability scan | artifact names and SHA-256 | Syft `1.51.1` → `sbom.spdx.json` (`24eba36bffd5b15d476362193a10ae573a08ea22183ae52d442bbda14bd0acef`, 630 packages); Grype `0.118.0` → `vulnerabilities.json` (`f2280ece98d24746ba4718900dbb1bcb0a66557e4937ed04b5307808c36be1a6`) |
| Rootless image build | image ID and base digest | Image ID `sha256:a7f520d657179e0788d7ddd8c98184991443a5b3089efe8f0785558c96ae395d`; base `docker.io/library/python@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84`; final image runs as `USER 10001:10001` ([job](https://github.com/AetherAI3/agent-browser/actions/runs/33860164841/job/100982560823)) |
| Isolated acceptance | exact image ID, network proof, cleanup proof | `acceptance.json` = PASS over 33 checks against the same image ID, including `isolated-pod-network-none`, `same-pod-namespace`, `loopback-listener-proof`, `same-display-color-proof`, `process-cleanup`, `profile-cleanup`, and `pod-cleanup` ([job](https://github.com/AetherAI3/agent-browser/actions/runs/33860164841/job/100983939031)) |
| README quickstart | fresh-directory reproduction | `quickstart.json` = PASS for `docker compose up --build --detach` at the same commit ([job](https://github.com/AetherAI3/agent-browser/actions/runs/33860164841/job/100982560774)) |
| Demo | separate provenance record | [`docs/DEMO_EVIDENCE.md`](DEMO_EVIDENCE.md) records the demo media, current checksums, available public provenance, and independently verifiable limits |
| Third-party review | Chrome terms, exact package/credits, notices, and redistribution decision | `Google Chrome 152.0.7977.82`, package `google-chrome-stable 152.0.7977.82-1`, `amd64`; `installed-notices.tar.gz` (`13a2948fd99f10e6445101d631d02a53b43c3e3676db458947ca543739ee7b79`); distribution is source-only |
| Repository state | visibility and branch protection | Public, with strict protected `main`, required `quality`/`unit`/`security` checks, linear history, conversation resolution, and force-push/deletion disabled; release requires no unrelated open pull request or unresolved review thread |

## Strict repository gate

`python scripts/verify_release.py --strict --skip-external` ran on the hosted runner. At the
runtime commit it reported 14 PASS, 2 SKIP, and 1 FAIL; the single failure was
`exact-commit-evidence`, which is the evidence pointer this document updates. The two skips are the
deliberate `--skip-external` entries `rootless-container` and `acceptance-and-novnc`; the separate
`container` and `acceptance` jobs proved those scopes against the exact image ID.

Passing checks at the runtime commit: `required-core-files`, `required-source-modules`,
`version-agreement` (`0.2.1` across project, package, Compose, and image label), `secret-scan`,
`private-infrastructure-scan`, `excluded-domain-scan`, `credential-injection-scan`,
`container-loopback-contract`, `required-release-files`, `readme-integrity`, `asset-integrity`,
`unsupported-claims`, `quality`, and `dependency-audit`.

## Reading the three release stages

- **Runtime commit** `38cc23f281001a9d678642916da522c047f9151d` is the reviewed code under test.
  Every workflow-produced runtime proof — image build, isolated acceptance, and quickstart — was
  produced from it. The committed demo media is a separate capture; see
  [`docs/DEMO_EVIDENCE.md`](DEMO_EVIDENCE.md).
- **Evidence commit** adds this record to the runtime commit. The gate requires the runtime commit
  to be its ancestor and confines the entire difference to the evidence allowlist. Its successful
  confirmation run produces the committed machine-readable manifest.
- **Manifest-closure commit** adds that generated manifest verbatim and is the commit tagged
  `v0.2.1`. The embedded manifest attests its evidence-commit parent. A fresh successful run at the
  tag commit produces the exact-tag manifest attached to the GitHub release; embedding that final
  manifest would change the commit it attests and create an infinite regress.

The evidence allowlist is `assets/demo.mp4`, `assets/demo-poster.png`,
`docs/DEMO_EVIDENCE.md`, `docs/RELEASE_EVIDENCE.md`, and `release/evidence/manifest.json`; no
executable source may change after the runtime proofs. The runtime-image allowlist in
`.dockerignore` excludes those evidence-only paths, so they do not intentionally change image
inputs. Confirmation runs still rebuild and bind their own resulting image ID, proof colour, and
nonce to each exact checkout.

The successful evidence-commit attestation is committed at
[`release/evidence/manifest.json`](../release/evidence/manifest.json). The exact-tag attestation
belongs on the GitHub release as an attached asset and is a publication gate.

## Vulnerability posture

Grype ran against the exact image with `--fail-on high --only-fixed` and did not block the build:
its reportable match set is empty, so there are no fixed high-or-higher findings. This is not a
claim that the image is free of vulnerabilities. `vulnerabilities.json` contains 917 advisory
matches suppressed by the recorded policy: 12 carry an available fix, 570 have no released fix,
330 are marked will-not-fix by the Debian-derived base, and 5 report no fix state. By severity:
42 critical, 198 high, 289 medium, 57 low, 290 negligible, and 41 unknown. This is a scan-time
snapshot against the workflow's refreshed vulnerability database; consumers should re-scan any
image they build and apply their own risk policy.

## Host attestation scope

The self-hosted VPS6 qualification run is partial and is not a release-build attestation.

- Passed scope: capacity, CI-only host posture, worker isolation, cgroup bounds, cleanup.
- Failed scope: Podman, Syft, Grype toolchain availability.
- Result: `partial`; the release workload executed independently on ephemeral GitHub-hosted
  runners.

## Closure assertions

- Runtime, authority, navigation, container, CI, package, and public-version surfaces bind to
  `38cc23f281001a9d678642916da522c047f9151d`.
- The evidence and manifest confirmation runs must be green before the manifest-closure commit is
  tagged; only the two declared `--skip-external` checks may remain, each proven by its dedicated
  job.
- No blocking fixed high-or-higher security finding or unresolved review thread remains.
- The source release reproduces the documented Linux Compose quickstart.
- The committed demo is separate from this hosted run. Its presentation, checksums, provenance,
  and public verification limits are recorded in [`DEMO_EVIDENCE.md`](DEMO_EVIDENCE.md); this run
  regenerated its own acceptance frames and did not re-attribute the committed media.
- Chrome-containing image, image-tar, and public layer-cache publication remain off. The SBOM and
  notice bundle are inventory evidence and do not themselves grant redistribution authorization.
- Artifact existence alone is not treated as success; job conclusions and strict-gate outcomes
  are checked before release.

## Sanitization

This document records only public-safe run URLs, commit IDs, image IDs, and artifact checksums. It
contains no tokens, private hostnames, server addresses, runner registration commands,
credentials, private filesystem paths, or raw private logs.
