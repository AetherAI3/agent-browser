# Release evidence bundle

This directory is the landing zone for sanitized, commit-bound private-RC artifacts. It contains
no generated proof yet.

For the exact candidate, place or link a manifest covering:

- commit, tag or prerelease identifier, and clean-checkout proof;
- hosted and trusted CI run URLs;
- test, lint, type, audit, and vulnerability results;
- SBOM filename and SHA-256;
- immutable container image ID and base digest;
- isolated acceptance and cleanup summaries;
- demo and poster checksums;
- installed third-party notice bundle checksum; and
- a statement that visibility, Pages, and launch publication remained off.

The canonical human-readable schema is [`../../docs/RELEASE_EVIDENCE.md`](../../docs/RELEASE_EVIDENCE.md).
Do not add secrets, private infrastructure identifiers, or raw unredacted logs.
