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
| Exact 40-character commit | `055f68787833c7e00e13ccbe0c0ecb69a8da3659` |
| Immutable container image ID | `sha256:a9253e91fcda87e56dd0c695f68a2da3e9defad7a3e347faa71c060c78a4b101` |
| Build command and run URL | Rootless `podman build` against a private per-job Podman API, `release-evidence` workflow, [container job](https://github.com/AetherAI3/agent-browser/actions/runs/33612893919/job/100192012520) |
| Acceptance command and run URL | Isolated exact-image acceptance in a `--network none` pod, same run, [acceptance job](https://github.com/AetherAI3/agent-browser/actions/runs/33612893919/job/100193455090) |
| Agent Browser version | `0.1.0` (agreed across project, package, Compose, and image label at this commit) |
| Google Chrome Stable executable, package version, architecture, and source | `/opt/google/chrome/chrome`, `Google Chrome 152.0.7977.75`, package `google-chrome-stable 152.0.7977.75-1`, `amd64`, installed in-image via `patchright install --with-deps chrome` |
| Capture date in UTC | 2026-09-02, 09:17:44Z to 09:18:06Z |
| Capture operator | Automated `release-evidence` workflow on a GitHub-hosted `ubuntu-24.04` runner; no interactive operator step |
| `assets/demo.mp4` SHA-256 | `68cff191b83c87e3b150f3364fd4e3900a35ff4541410d753b5884ec3befd952` |
| `assets/demo-poster.png` SHA-256 | `8ad2d324c104bf6c62b6a1d209340395bb4d0a1b24367e4f91bc5964a79c6b79` |
| `assets/social-preview.png` and Pages-copy SHA-256 and dimensions | `4562ec4b176f97dfd6563c5f381bd7f564b8567c592d240f7f7376f79e259999`; 1280x640; `docs/assets/social-preview.png` is byte-identical |

## How the media was produced

Two real frames were taken during the isolated acceptance run against the exact image:

- `demo-frames/api-before.png` (`a65f92d0e76575517db9c1101d61d3f2e2c9893fe4f06e9c7338b83b172b533c`) —
  the page as returned through the control API, before the interaction batch.
- `demo-frames/display-after.png` (`8ad2d324c104bf6c62b6a1d209340395bb4d0a1b24367e4f91bc5964a79c6b79`) —
  an X11 screenshot of the container display, after the interaction batch.

`assets/demo-poster.png` is a byte-identical copy of `demo-frames/display-after.png`.

`assets/demo.mp4` is an eight-second silent H.264 montage (1280x720, 30 fps) concatenating three
still images: two seconds of `assets/social-preview.png`, three seconds of `api-before.png`, and
three seconds of `display-after.png`. It contains no audio track and no live motion. Checksums for
both generated files were written by the workflow to `generated-assets/SHA256SUMS` and match the
committed files byte for byte.

## What the frames show

Both frames render the same deterministic release fixture, served over container loopback at
`127.0.0.1:18080`, with the same generated nonce `visual-proof-24e97a62d4cd21b85e4d42bc3817db06`
and the same generated proof color `#3341cc`. `acceptance.json` records that color and the nonce
digest `a3b19c951199f207b0602605e845fddcd5dc20709179e9be469a97d3be8e6719`, which is how the
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

The preview was derived from the owner-supplied Agent Browser artwork. That artwork was
originally committed as a JPEG carrying a painted-on transparency checkerboard and a dashed
border, neither of which was real transparency. The committed logo is now
`assets/agent-browser-logo.png`, SHA-256
`fd715919bb45791c432d19e1fc82cf6d8c6cb9fdc89051b19202f3dc8f04f7dc`: an 848x904 PNG with a real
alpha channel, the painted border removed, and the palette flattened to 64 colours to clear JPEG
ringing. The generated 2:1 preview artwork was mechanically resized to the required 1280x640
PNG. Its visible API panel uses only the implemented v0.1 routes, and its footer states: “Self-hosted Chrome · API control · Live human
takeover.” `docs/assets/` contains byte-identical publication copies of the logo and preview so a
`/docs` Pages build cannot escape its source root. The artwork depicts the product contract but is
not itself a record of a live run.

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
