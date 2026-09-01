# Demo evidence

## Status

**Exact release-candidate capture pending.** No `demo.mp4`, poster, or social-preview asset is
claimed by this document. Media must be captured from the same immutable commit and image that
pass release acceptance; mock media and captures from earlier builds are not acceptable proof.

## Capture record

Populate this record during exact-main convergence. Until every field is complete and verified,
the README must continue to show the pending-capture notice.

| Field | Required value |
|---|---|
| Exact 40-character commit | Not captured |
| Immutable container image ID | Not captured |
| Build command and run URL | Not captured |
| Acceptance command and run URL | Not captured |
| Aether Browser version | `0.1.0` (verify at capture) |
| Chromium version | Not captured |
| Capture date in UTC | Not captured |
| Capture operator | Not captured |
| `assets/demo.mp4` SHA-256 | Asset absent |
| `assets/demo-poster.png` SHA-256 | Asset absent |
| `assets/social-preview.png` SHA-256 and dimensions | Asset absent |

## Required sequence

1. Show the Aether Browser title and “See what your agent sees.”
2. Start the exact image and show API, browser, display, and noVNC readiness.
3. Create one session and show its session ID and numeric-loopback view URL.
4. Navigate to the deterministic release fixture and show the same page in noVNC.
5. Click, type, and press through the API while the display visibly changes.
6. Show a structured snapshot next to the same visible page.
7. End the session and record cleanup evidence.

## Observation record

Record separately:

- what the automated acceptance suite proved;
- what a human visually observed in noVNC;
- whether takeover was exercised;
- any edit, crop, speed change, or redaction applied to the final media; and
- limitations, especially that the demo does not prove hosted operation or remote security.

## Integrity check

After capture, compute SHA-256 checksums from the final committed assets, verify the poster and
video visually, verify that the social preview is exactly 1280×640, and rerun the release verifier
at the commit containing those assets. Evidence from a different commit is invalid.
