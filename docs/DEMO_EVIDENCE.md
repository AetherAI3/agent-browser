# Demo evidence

## Status

The demo media in `assets/` was generated from the exact release-candidate commit and the exact
immutable image that passed acceptance. `assets/demo.mp4` is a short generated montage, not a
continuous live screen recording; the interaction, noVNC/RFB, same-display, refusal, and cleanup
proofs come from `acceptance.json`, not from the video. The social preview is a prepared brand
asset and is not runtime proof.

## Capture record

| Field | Recorded value |
|---|---|
| Exact 40-character commit | `e9a9000700e2dc4d1c11724be3ee8894a2709436` |
| Immutable container image ID | `sha256:25e81e9acb1a9a8ff3f36dc8f6775f5ad1736eabdf95ae53f4c6c3384ef777f1` |
| Build command and run URL | Rootless `podman build` against a private per-job Podman API, `release-evidence` workflow, [container job](https://github.com/AetherAI3/agent-browser/actions/runs/33705469219/job/100493552354) |
| Acceptance command and run URL | Isolated exact-image acceptance in a `--network none` pod, same run, [acceptance job](https://github.com/AetherAI3/agent-browser/actions/runs/33705469219/job/100494370929) |
| Agent Browser version | `0.1.0` (agreed across project, package, Compose, and image label at this commit) |
| Google Chrome Stable executable, package version, architecture, and source | `/opt/google/chrome/chrome`, `Google Chrome 152.0.7977.75`, package `google-chrome-stable 152.0.7977.75-1`, `amd64`, installed in-image via `patchright install --with-deps chrome` |
| Capture date in UTC | 2026-09-03, 01:57:24Z to 01:57:41Z |
| Capture operator | Automated `release-evidence` workflow on a GitHub-hosted `ubuntu-24.04` runner; no interactive operator step |
| `assets/demo.mp4` SHA-256 | `0ded738bd0c7cf2646cefdb94e1194ee77f5b5430fc578503015ac6dc0987e81` |
| `assets/demo-poster.png` SHA-256 | `f8197cafcf4d7ce0bc7e323256307a9d2ed687a0fb96d416dd4e4178feb8a9f3` |
| `assets/social-preview.png` and Pages-copy SHA-256 and dimensions | `e3473f97af387d28bed32e8959873b79175ccc305e17dfa7aa310ef1e9329556`; 1280x640; `docs/assets/social-preview.png` is byte-identical |

## How the media was produced

Two real frames were taken during the isolated acceptance run against the exact image:

- `demo-frames/api-before.png` (`834c3641a04a1c20ba76cda64da82567272581f13c15bb5b93c210f1eaab9763`) —
  the page as returned through the control API, before the interaction batch.
- `demo-frames/display-after.png` (`f8197cafcf4d7ce0bc7e323256307a9d2ed687a0fb96d416dd4e4178feb8a9f3`) —
  an X11 screenshot of the container display, after the interaction batch.

`assets/demo-poster.png` is a byte-identical copy of `demo-frames/display-after.png`.

`assets/demo.mp4` is an eight-second silent H.264 montage (1280x720, 30 fps) concatenating three
still images: two seconds of `assets/social-preview.png`, three seconds of `api-before.png`, and
three seconds of `display-after.png`. It contains no audio track and no live motion. Checksums for
both generated files were written by the workflow to `generated-assets/SHA256SUMS` and match the
committed files byte for byte.

## What the frames show

Both frames render the same deterministic release fixture, served over container loopback at
`127.0.0.1:18080`, with the same generated nonce `visual-proof-a3c237fb698c9854200f497ee196b811`
and the same generated proof color `#68c173`. `acceptance.json` records that color and the nonce
digest `d89143ca65da86f83a2f69b2a72c0b2ba2691d686feaa0202baa6af54064af6f`, which is how the
same-display check is bound to this run rather than to a stock page.

In `display-after.png` the owned fixture page is the active, foregrounded tab; the button reads
`clicked` and the text field contains `acceptance text`, so the API-driven interaction is visible
on the container display. A bounded, denied popup tab remains present but inactive in the
background, which is the expected outcome of the `popup-bounded` check: the popup is refused and
bounded rather than silently allowed, and the owned page is restored to the foreground before the
click returns.

## What acceptance proved

`acceptance.json` reports PASS over 33 checks bound to the same image ID, covering:

- image binding: `exact-image-id-handoff`, `runtime-image-id-proof`, `remote-podman`;
- isolation: `isolated-pod-network-none`, `resource-bounded-pod`, `same-pod-namespace`,
  `loopback-fixture`, `in-namespace-http-driver`, `loopback-listener-proof`;
- session authority: `health`, `session-create`, `capacity-rejection`, `observer-refusal`,
  `controller-success`;
- interaction: `navigation`, `snapshot`, `click`, `type`, `press`, `scroll`, `second-snapshot`;
- refusals: `direct-ssrf-refusal`, `redirect-refusal`, `download-non-persistence`,
  `popup-bounded`;
- human takeover surface: `novnc-web`, `novnc-websocket`, `vnc-rfb-readiness`;
- visual binding: `same-display-color-proof`;
- teardown: `idempotent-end`, `process-cleanup`, `profile-cleanup`, `pod-cleanup`.

## Social-preview provenance

The committed logo is `assets/agent-browser-logo.png`, SHA-256
`bb3a63b2a54e3a48dbdc77605f3511d5211ae5b695bac831ca689c7bc593b4e2`. It is rebuilt from the
owner-supplied artwork, which had previously been committed as a JPEG carrying a transparency
checkerboard and a dashed border painted into its pixels rather than a real alpha channel. The
committed PNG is 424x452, has a genuine alpha channel, drops the painted border, and uses a
64-colour palette so that JPEG ringing no longer blurs the pixel-art edges.

`assets/social-preview.png` is a generated 1280x640 brand card, not a screenshot and not runtime
proof. It is composed programmatically from the committed logo plus flat vector shapes and text:
the product name, the vendor line, the tagline, a decorative API panel, and the footer
"Self-hosted Chrome · API control · Live human takeover". The API panel lists only routes that the
v0.1 surface actually implements, and its "CONNECTED / 1280x800 / noVNC" strip is decorative
styling rather than a captured session readout. `docs/assets/` holds byte-identical publication
copies of both images so a `/docs` Pages build cannot escape its source root. The card depicts
the product contract but is not itself a record of a live run.

## Observation record

- Automated acceptance proved: every check listed above, executed against the exact image inside a
  network-isolated pod.
- Human visual observation: the two committed frames and the generated poster were reviewed after
  the run and match the recorded nonce, proof color, and interaction state described above.
- Takeover exercised: the noVNC web endpoint, its WebSocket, and VNC/RFB readiness were verified as
  reachable on container loopback. An interactive human takeover session was not part of this
  automated run.
- Edits applied to the final media: scaling and letterbox padding to 1280x720, concatenation of the
  three stills, H.264 encoding at CRF 20, and removal of the encoder creation timestamp. No crop,
  speed change, redaction, or retouching was applied to the frame content.

## Limitations

- The video is generated stills, not a continuous recording, and proves nothing on its own.
- This evidence covers a single local, network-isolated container run. It does not demonstrate
  hosted or multi-tenant operation, remote network security, sustained load, or availability.
- Chrome inventory and notice evidence are for transparency and do not grant redistribution
  authorization. Distribution of this release is source-only.
- Rebuilding the same source later may resolve a newer Chrome Stable version than the one recorded
  here.

## Integrity check

The committed assets were re-hashed after copying and match `generated-assets/SHA256SUMS` from the
run. The poster and video were visually reviewed, the social preview is exactly 1280x640, and the
release verifier is re-run at the commit that contains these assets. Evidence produced at a
different commit is not valid for this candidate.
