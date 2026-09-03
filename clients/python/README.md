# aether-browser

Client and CLI for **[Agent Browser](https://github.com/AetherAI3/agent-browser) by Aether AI** —
self-hosted Chrome for AI agents. Drive one browser session through a closed HTTP API, and watch or
take over that exact same session through noVNC.

```bash
pip install aether-browser
```

Zero runtime dependencies. Ships type hints (`py.typed`). Python 3.10+.

This is the Python sibling of the [`aether-browser` npm package](https://www.npmjs.com/package/aether-browser):
same name, same commands, same closed `v1` contract, released version for version.

## Drive a session

The `session` context manager always attempts to end the session, including when the body raises,
so a crash cannot leave the single session slot occupied.

```python
import os

from aether_browser import AgentBrowser, session

browser = AgentBrowser(
    base_url="http://127.0.0.1:8092",
    controller_token=os.environ["AGENT_BROWSER_CONTROLLER_TOKEN"],
)

with session(browser) as live:
    print("watch it live at", live.view_url)

    page = live.navigate("https://example.com")
    print(page["title"], page["readable_text"][:200])

    live.click(selector="#login")
    live.type("ada", selector="#user")
    live.press("Enter")

    shot = live.snapshot()
    print(f"{shot['vision_steps_remaining']} vision steps left")
```

Connection settings fall back to `AGENT_BROWSER_URL`, `AGENT_BROWSER_CONTROLLER_TOKEN`, and
`AGENT_BROWSER_OBSERVER_TOKEN`, so `AgentBrowser()` works with no arguments in a configured
environment.

## Two roles, kept separate

The server splits authority, and this client keeps that split visible in your code. The observer
token covers health and snapshot; the controller token is required to create, navigate, interact,
and end. Give a read-only caller only the observer token:

```python
read_only = AgentBrowser(observer_token=os.environ["AGENT_BROWSER_OBSERVER_TOKEN"])
read_only.health()
```

## Errors

Failures raise `AgentBrowserError` carrying the server's stable `code`:

```python
from aether_browser import AgentBrowserError

try:
    browser.create_session()
except AgentBrowserError as error:
    if error.is_capacity_reached:
        print(f"busy; retry in {error.retry_after_seconds}s")
```

Codes are `AUTH_REQUIRED`, `AUTH_FORBIDDEN`, `SESSION_CAPACITY_REACHED`, `SESSION_NOT_FOUND`,
`SESSION_EXPIRED`, `VISION_BUDGET_EXHAUSTED`, `INVALID_URL`, `DESTINATION_BLOCKED`,
`INVALID_INTERACTION`, `BROWSER_NOT_READY`, and `INTERNAL_ERROR`. A transport failure raises the
same class with `code` left `None`, so a refused connection is never mistaken for a refusal by the
server.

## CLI

```bash
aether-browser doctor   # check Docker, platform, and server health; say what is wrong
aether-browser up       # build and start the runtime
aether-browser status   # print the health document
aether-browser open     # open the live noVNC view
aether-browser down     # stop and clean up
```

Run `doctor` first. It checks the things that actually break a first run and tells you which one
failed, instead of leaving you to read a build log.

### What `up` really does, and its limits

Agent Browser publishes **no Chrome-containing image** — distribution is source-only. So `up` builds
the image locally from source, and **the first build takes several minutes** because it installs a
hash-locked Python environment and the current Google Chrome Stable package. It uses the checkout
you are standing in if there is one, and otherwise downloads the matching tagged source tarball from
the official repository over HTTPS into your cache directory.

`up` requires **Linux** with Docker Compose v2. The documented quickstart uses Docker host
networking so both the API and the noVNC listeners stay bound to numeric loopback; Docker Desktop on
macOS and Windows is outside that contract, and `doctor` will tell you so rather than half-working.

**The library has no such limit.** It is plain HTTP over `urllib` and runs anywhere Python does —
point it at a server on a Linux host and drive it from macOS, Windows, or CI.

## Security

The v0.1 noVNC surface is **unauthenticated** and intended for numeric loopback on a machine you
control. Treat every process and user that can reach that loopback interface as trusted with the
live browser view. Do not expose it through a tunnel, reverse proxy, or container bridge. See the
[security model](https://github.com/AetherAI3/agent-browser/blob/main/docs/SECURITY-MODEL.md).

The API is deliberately closed: `click`, `type`, `scroll`, and `press` are the only interactions,
and there is no arbitrary JavaScript, CDP, upload, clipboard, download, extension, shell,
filesystem, credential, or cookie field. This client cannot widen that surface, because the server
rejects unknown fields.

## Status

`0.1.0` tracks Agent Browser `v0.1.0` and its `api_version: "v1"` contract, and is released
version for version with the npm client. Issues and design discussion are welcome on
[the repository](https://github.com/AetherAI3/agent-browser/issues).

Apache-2.0 · [Aether AI](https://github.com/AetherAI3)
