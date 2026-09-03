# Release evidence

## Status

This document records exact-commit evidence for release `v0.1.0` of Agent Browser.
Every field is bound to one runtime commit, one immutable image ID, and one hosted workflow run.

Tested runtime commit: `02ee77bf75c1d27da156528ea5e4f18fc842dfc1`

Release: `v0.1.0`, published source-only. No Chrome-containing image, image tar, filesystem
layer, or reusable layer cache is published. The preceding candidate `v0.1.0-rc.1` keeps its own
tag and its own evidence in the repository history.

## Execution environment

The `release-evidence` workflow (`.github/workflows/trusted-runner.yml`) executed on GitHub-hosted
`ubuntu-24.04` runners. The self-hosted VPS6 record is a separate, partial host qualification and
did not execute this release build; see [Host attestation scope](#host-attestation-scope).

## Commit-bound record

Unless stated otherwise, every row below comes from `release-evidence`
[run 33795812985](https://github.com/AetherAI3/agent-browser/actions/runs/33795812985).

| Gate | Required evidence | Recorded result |
|---|---|---|
| Clean checkout | exact commit and clean status | `ref-proof` resolved `main` to `02ee77bf…`; every downstream job checked out and re-verified that exact SHA ([job](https://github.com/AetherAI3/agent-browser/actions/runs/33795812985/job/100783114131)) |
| Hosted CI | exact-commit run URL and job matrix | `ci` succeeded at `02ee77bf…`: [run 33795812997](https://github.com/AetherAI3/agent-browser/actions/runs/33795812997) |
| Trusted runner | runner labels, exact commit, service isolation | GitHub-hosted `ubuntu-24.04`; rootless Podman API bound to a private per-job socket; acceptance ran in an isolated pod with `--network none` |
| Static and unit checks | command, counts, and run URL | `ruff format --check`, `ruff check`, `mypy src`, and `pytest -q` all passed inside the strict gate (`quality`: "format, lint, typing, and tests passed") |
| Dependency audit | locked input and result | `dependency-audit` PASS — locked `requirements.lock` consistency and vulnerability audit passed |
| SBOM and vulnerability scan | artifact names and SHA-256 | Syft `1.51.1` → `sbom.spdx.json` (`282a4b9ac8b709d48b932473413a3ca5b5208268c46637977dfd698c46b86af1`, 630 packages); Grype `0.118.0` → `vulnerabilities.json` (`cb04d9b10ce1ca34b4dc28dd17874d48bf98339ac9f15dcf595311ce9bd212df`) |
| Rootless image build | image ID and base digest | Image ID `sha256:8b6032816c9f243b481862d388a1f1eb75cd0a3c921ad0f0e0e956c48d103cf7`; base `docker.io/library/python@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84`; final image runs as `USER 10001:10001` ([job](https://github.com/AetherAI3/agent-browser/actions/runs/33795812985/job/100783149548)) |
| Isolated acceptance | exact image ID, network proof, cleanup proof | `acceptance.json` = PASS over 33 checks against the same image ID, including `isolated-pod-network-none`, `same-pod-namespace`, `loopback-listener-proof`, `process-cleanup`, `profile-cleanup`, and `pod-cleanup` ([job](https://github.com/AetherAI3/agent-browser/actions/runs/33795812985/job/100784604199)) |
| README quickstart | fresh-directory reproduction | `quickstart.json` = PASS for `docker compose up --build --detach` at the same commit ([job](https://github.com/AetherAI3/agent-browser/actions/runs/33795812985/job/100783149632)) |
| Demo | link to `DEMO_EVIDENCE.md` | [`docs/DEMO_EVIDENCE.md`](DEMO_EVIDENCE.md) records the generated media, its provenance, and its limits |
| Third-party review | Chrome terms, exact package/credits, notices, and redistribution decision | `Google Chrome 152.0.7977.82`, package `google-chrome-stable 152.0.7977.82-1`, `amd64`; `installed-notices.tar.gz` (`13a2948fd99f10e6445101d631d02a53b43c3e3676db458947ca543739ee7b79`); distribution is source-only |
| Repository state | visibility and open-PR review | Public, with branch protection on `main` (strict, linear history; force-push and deletion disabled); no open pull request or unresolved review thread blocks this candidate |

## Strict repository gate

`python scripts/verify_release.py --strict --skip-external` ran on the hosted runner. At the
runtime commit it reported 14 PASS, 2 SKIP, and 1 FAIL; the single failure was
`exact-commit-evidence`, which is the artifact this document supplies — at that moment the
evidence still named the previous runtime commit. The two skips are the deliberate
`--skip-external` entries `rootless-container` and `acceptance-and-novnc`, which the separate
`container` and `acceptance` jobs prove independently against the exact image ID.

Passing gate checks at the runtime commit: `required-core-files`, `required-source-modules`,
`version-agreement` (`0.1.0` across project, package, Compose, and image label), `secret-scan`,
`private-infrastructure-scan`, `excluded-domain-scan`, `credential-injection-scan`,
`container-loopback-contract`, `required-release-files`, `readme-integrity`, `asset-integrity`,
`unsupported-claims`, `quality`, and `dependency-audit`.

### Reading the two commits

Two commit IDs appear in this evidence and they mean different things:

- **Runtime commit** `02ee77bf75c1d27da156528ea5e4f18fc842dfc1` is the code under test. Every
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
committed demo media stays the capture it was made from and is not replaced by a later run.

The machine-readable attestation from the closing run is committed at
[`release/evidence/manifest.json`](../release/evidence/manifest.json).

## Vulnerability posture

Grype ran against the exact image with `--fail-on high --only-fixed` and did not block the build:
there are no fixed high-or-higher findings. This is not a claim that the image is free of
vulnerabilities. `vulnerabilities.json` records the full match set; of its 878 findings, 12 carry an
available fix, 531 have no released fix, and 330 are marked will-not-fix by the Debian-derived
base. Consumers should re-scan any image they build and apply their own risk policy.

## Host attestation scope

The self-hosted VPS6 qualification run is partial and is not a release-build attestation.

- Passed scope: capacity, CI-only host posture, worker isolation, cgroup bounds, cleanup.
- Failed scope: Podman, Syft, Grype toolchain availability.
- Result: `partial`; the release workload executed independently on ephemeral GitHub-hosted
  runners.

## Closure assertions

- Runtime, authority, navigation, container, CI, and documentation all refer to
  `02ee77bf75c1d27da156528ea5e4f18fc842dfc1`.
- No required check is red or attached to another commit; the only skipped checks are the two
  declared `--skip-external` entries, each independently proven by a dedicated job.
- No blocking fixed high-or-higher security finding and no unresolved review thread remains.
- The source release reproduces the documented Linux Compose quickstart.
- The committed demo media was generated from the exact accepted image at runtime commit
  `e9a9000700e2dc4d1c11724be3ee8894a2709436` and has recorded checksums. This run regenerated its
  own frames from its own image and left the committed media untouched, so the media is evidence
  of the capture it names and is not re-attributed to this commit.
- Chrome-containing image, image-tar, and public layer-cache publication remain off. The SBOM and
  notice bundle are inventory evidence and do not by themselves grant redistribution authorization.
- Artifact existence alone is not treated as success; each job conclusion and the strict-gate
  result are recorded above.

## Sanitization

This document records only public-safe run URLs, commit IDs, image IDs, and artifact checksums. It
contains no tokens, private hostnames, server addresses, runner registration commands,
credentials, private filesystem paths, or raw private logs.
