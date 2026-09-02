# Release evidence bundle

This directory holds the sanitized, commit-bound attestation for the current release candidate.

[`manifest.json`](manifest.json) is committed verbatim from the artifact emitted by the
`release-evidence` workflow run that closed the strict gate. It records:

- the repository and the exact tested commit;
- the run URL that produced it, and the strict-gate result;
- the immutable container image ID;
- a SHA-256 for every collected evidence artifact, including the acceptance record, the SBOM, the
  vulnerability scan, the Chrome and Python inventories, the installed notice bundle, the captured
  frames, and the generated demo media;
- the scope of the partial self-hosted host attestation, recorded separately because that host did
  not execute the release build.

The canonical human-readable record is
[`../../docs/RELEASE_EVIDENCE.md`](../../docs/RELEASE_EVIDENCE.md), and the demo media provenance
is in [`../../docs/DEMO_EVIDENCE.md`](../../docs/DEMO_EVIDENCE.md).

`manifest.json` is generated output. Regenerate it from a workflow run rather than editing it by
hand, and never add secrets, private infrastructure identifiers, or raw unredacted logs here.
