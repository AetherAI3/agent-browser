# Third-party notices

Aether Browser's own source is licensed under Apache-2.0. This file identifies directly declared
runtime and build/validation Python dependencies plus directly installed container components. It
is an attribution aid, not legal advice, and it does not replace the license text shipped by each
dependency.

This source repository does not contain Google Chrome. The Dockerfile instructs Patchright to
download and install Google Chrome Stable while an image is built. Google Chrome is separately
licensed and is not covered by Aether's Apache-2.0 source license. Completion of notices or an
SBOM does not itself grant permission to redistribute the Chrome executable or an image
containing it.

The exact versions in `pyproject.toml`, `requirements.lock`, the base-image digest in
`Dockerfile`, a build SBOM, and the built image's `/usr/share/doc/*/copyright` files are the
authoritative inventory for a particular build. Transitive packages and Patchright's downloaded
browser payload add further notices. Do not distribute a Chrome-containing prebuilt image without
separately documented redistribution authorization; exact inventory review and required license
texts remain necessary but are not themselves authorization.

## Direct runtime Python dependencies

| Component | Declared version | Upstream license identified in source | Source |
|---|---:|---|---|
| FastAPI | 0.141.1 | MIT | [fastapi/fastapi LICENSE](https://github.com/fastapi/fastapi/blob/master/LICENSE) |
| HTTPX | 0.28.1 | BSD 3-Clause | [encode/httpx LICENSE.md](https://github.com/encode/httpx/blob/master/LICENSE.md) |
| Patchright for Python | 1.62.2 | Apache-2.0 | [patchright-python LICENSE](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python/blob/main/LICENSE) |
| Pydantic | 2.13.5 | MIT | [pydantic LICENSE](https://github.com/pydantic/pydantic/blob/main/LICENSE) |
| Uvicorn | 0.52.4 | BSD 3-Clause | [encode/uvicorn LICENSE.md](https://github.com/encode/uvicorn/blob/master/LICENSE.md) |

`uvicorn[standard]` and the locked environment install additional transitive Python packages.
Their versions are enumerated in `requirements.lock`; their package metadata and license files
must be captured by the release SBOM/notices process rather than inferred from the direct
dependency above them.

## Direct build and validation Python dependencies

The current Dockerfile installs the full `requirements.lock` before installing Aether Browser, so
the following directly declared tools are present in the candidate image even though they are not
runtime API dependencies. Their versions are pinned by `requirements.in`, `pyproject.toml`, and the
hash-locked environment:

| Component | Declared version | Role |
|---|---:|---|
| Bandit | 1.9.4 | Python security lint |
| mypy | 2.3.1 | Static type checking |
| pip-audit | 2.10.1 | Python dependency vulnerability audit |
| pytest | 9.1.1 | Test runner |
| pytest-asyncio | 1.4.0 | Async pytest support |
| pytest-cov | 7.1.0 | Coverage integration |
| Ruff | 0.16.5 | Formatting and lint checks |
| setuptools | 84.0.0 | Package build backend |

Their installed distribution metadata and license texts must be included in the exact-image
SBOM/notices review. This source notice deliberately defers license classification for that built
payload to the captured metadata instead of guessing from package names. Binary publication
remains blocked until that inventory is complete.

## Browser and display stack

| Component | Governing terms/notices | Distribution status |
|---|---|---|
| Google Chrome Stable executable | [Google Chrome Additional Terms](https://www.google.com/chrome/terms/) and [Google Terms of Service](https://policies.google.com/terms#toc-software) | Separately licensed branded software installed during the image build. Do not publish an OCI image, image archive, or reusable public layer cache containing it without separately documented redistribution authorization. |
| Chromium-derived and other open-source components included in Chrome | Notices exposed by `chrome://credits` and the [Chromium license](https://chromium.googlesource.com/chromium/src/+/main/LICENSE) | These component licenses apply only to their identified components and do not relicense the Google Chrome executable as a whole. Preserve the exact notices corresponding to the installed package. |
| noVNC | [noVNC LICENSE.txt](https://github.com/novnc/noVNC/blob/master/LICENSE.txt) | The distribution is mixed-license: MPL-2.0 core, BSD 2-Clause HTML/CSS, OFL-1.1 Orbitron font, CC BY-SA 3.0 images, and MIT pako as identified by upstream. Review the exact Debian payload. |
| websockify | [websockify COPYING](https://github.com/novnc/websockify/blob/master/COPYING) (LGPL-3.0) | The installed Debian package and its dependencies control the shipped notice set. |
| x11vnc | [x11vnc COPYING](https://github.com/LibVNC/x11vnc/blob/master/COPYING) (GPL-2.0) | Preserve the exact installed package's copyright file and corresponding source obligations. |
| Xvfb / X.Org server | [X.Org server COPYING](https://gitlab.freedesktop.org/xorg/xserver/-/blob/master/COPYING) | X.Org server sources are multi-license; do not describe the full installed stack with one blanket license. |
| DejaVu fonts | [DejaVu license](https://dejavu-fonts.github.io/License.html) | Font files and derived variants may carry multiple notices; inspect the exact package contents. |

Google Chrome is a trademark of Google LLC. Aether is not affiliated with, sponsored by, or
endorsed by Google.

## Direct container packages and base image

The image starts from a digest-pinned Docker Official Image for Python 3.11 on Debian Bookworm.
The [docker-library/python packaging repository](https://github.com/docker-library/python/blob/master/LICENSE)
is MIT-licensed, but that license does **not** cover all Python, Debian, or third-party contents
inside the resulting base image.

The Dockerfile directly requests `ca-certificates`, `curl`, `dumb-init`, `fonts-dejavu-core`,
`novnc`, `procps`, `scrot`, `websockify`, `x11vnc`, and `xvfb`. Relevant upstream/package records
include:

- [dumb-init LICENSE](https://github.com/Yelp/dumb-init/blob/master/LICENSE) — MIT;
- [curl COPYING](https://github.com/curl/curl/blob/master/COPYING) — curl license;
- [scrot COPYING](https://github.com/resurrecting-open-source-projects/scrot/blob/master/COPYING)
  — MIT-style feh terms published upstream;
- [Debian ca-certificates package record](https://tracker.debian.org/pkg/ca-certificates);
- [Debian procps package record](https://tracker.debian.org/pkg/procps);
- [Debian noVNC package record](https://tracker.debian.org/pkg/novnc);
- [Debian websockify package record](https://tracker.debian.org/pkg/websockify);
- [Debian x11vnc package record](https://tracker.debian.org/pkg/x11vnc); and
- [Debian xorg-server package record](https://tracker.debian.org/pkg/xorg-server).

Several of these packages are multi-file or multi-license. `ca-certificates` includes a trust
store sourced from multiple certificate authorities; `procps` and X.Org components do not have
one license that safely characterizes every installed file. For a candidate image, preserve at
least:

```text
/usr/share/doc/ca-certificates/copyright
/usr/share/doc/curl/copyright
/usr/share/doc/dumb-init/copyright
/usr/share/doc/fonts-dejavu-core/copyright
/usr/share/doc/novnc/copyright
/usr/share/doc/procps/copyright
/usr/share/doc/scrot/copyright
/usr/share/doc/websockify/copyright
/usr/share/doc/x11vnc/copyright
/usr/share/doc/xvfb/copyright
```

## Release obligation

Before any binary image publication, generate an SBOM from the exact image, record resolved
Debian and Python package versions, archive installed copyright/license files, retain Google
Chrome's terms plus its Chromium and third-party notices, and review redistribution and
reciprocal-license source/offer requirements. The private
v0.1 RC currently targets a source release with a locally built container; this notice does not
authorize a binary image publication.
