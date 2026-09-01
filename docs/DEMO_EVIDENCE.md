# Demo evidence

## Status

**Exact release-candidate runtime capture pending.** No `demo.mp4` or poster is claimed by this
document. The social preview is a prepared brand asset, not runtime proof. Runtime media must be
captured from the same immutable commit and image that pass release acceptance; mock media and
captures from earlier builds are not acceptable proof.

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
| `assets/social-preview.png` and Pages-copy SHA-256 and dimensions | `4562ec4b176f97dfd6563c5f381bd7f564b8567c592d240f7f7376f79e259999`; 1280×640; `docs/assets/social-preview.png` is byte-identical |

## Social-preview provenance

The preview was created from the owner-supplied Aether Browser logo, whose committed
`assets/aether-browser-logo.jpg` SHA-256 is
`af73b9d1694810fc431cb9fb3fb33a0eb9e21c0ccd36700e10b822291e2feedc`. The generated 2:1 artwork
was mechanically resized to the required 1280×640 PNG. Its visible API panel uses only the
implemented v0.1 routes, and its footer states: “Self-hosted Chrome · API control · Live human
takeover.” `docs/assets/` contains byte-identical publication copies of the logo and preview so a
`/docs` Pages build cannot escape its source root. The artwork depicts the product contract but
does not claim a captured live run.

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
