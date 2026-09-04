"""The ``aether-browser`` command line.

The runtime is a container you build from source: Agent Browser publishes no
Chrome-containing image, so ``up`` builds one locally the first time and that build takes
several minutes. This CLI never pretends otherwise.

It mirrors the ``aether-browser`` npm CLI command for command, so the two ecosystems give
the same answers on the same machine.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from ._client import DEFAULT_BASE_URL, AgentBrowser, AgentBrowserError

REPO = "https://github.com/AetherAI3/agent-browser"
NOVNC_URL = "http://127.0.0.1:6080/vnc.html"
# The published source tag for this client. `up` builds this exact tree, so the container
# and the client always speak the same api_version.
SOURCE_TAG = "v0.2.1"

_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def _dim(text: str) -> str:
    return _paint("2", text)


def _bold(text: str) -> str:
    return _paint("1", text)


def _ok(text: str) -> None:
    print(f"  {_paint('32', 'ok')}    {text}")


def _warn(text: str) -> None:
    print(f"  {_paint('33', 'warn')}  {text}")


def _bad(text: str) -> None:
    print(f"  {_paint('31', 'fail')}  {text}")


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a fixed command with no shell.

    Every argument list here is a literal built in this module, so S603 (untrusted input)
    and S607 (partial path) do not apply: resolving `docker` and `tar` through PATH is the
    behaviour a developer expects from a developer tool.
    """
    return subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603


def _version_of(command: str) -> str | None:
    if shutil.which(command) is None:
        return None
    probe = _run([command, "--version"])
    if probe.returncode != 0:
        return None
    return (probe.stdout or probe.stderr).strip().splitlines()[0]


def _compose_version() -> str | None:
    if shutil.which("docker") is None:
        return None
    probe = _run(["docker", "compose", "version"])
    return probe.stdout.strip() if probe.returncode == 0 else None


def _find_compose_dir(*, allow_download: bool) -> tuple[Path, str] | None:
    """Locate a checkout: the current tree if it is one, otherwise the cached tarball."""
    directory = Path.cwd()
    for _ in range(6):
        if (directory / "docker-compose.yml").is_file() and (directory / "Dockerfile").is_file():
            return directory, "local checkout"
        if directory.parent == directory:
            break
        directory = directory.parent
    if not allow_download:
        return None

    cache_root = (
        Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "aether-browser"
    )
    target = cache_root / f"agent-browser-{SOURCE_TAG}"
    if (target / "docker-compose.yml").is_file():
        return target, f"cached source {_dim(str(target))}"

    url = f"{REPO}/archive/refs/tags/{SOURCE_TAG}.tar.gz"
    print(f"  fetching source  {_dim(url)}")
    target.mkdir(parents=True, exist_ok=True)
    tarball = cache_root / f"{SOURCE_TAG}.tar.gz"
    fetch = _run(
        ["curl", "-fsSL", "--proto", "=https", "--tlsv1.2", "-o", str(tarball), url],
    )
    if fetch.returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        print(fetch.stderr.strip())
        return None
    untar = _run(["tar", "-xzf", str(tarball), "-C", str(target), "--strip-components=1"])
    tarball.unlink(missing_ok=True)
    if untar.returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        print(untar.stderr.strip())
        return None
    return target, f"downloaded source {_dim(str(target))}"


def _preflight(*, for_up: bool) -> list[str]:
    problems: list[str] = []

    print(_bold("\nEnvironment"))
    print(f"  python  {platform.python_version()}")
    print(f"  client  aether-browser {__version__}")
    print(f"  os      {sys.platform}")

    print(_bold("\nRuntime prerequisites"))
    if sys.platform.startswith("linux"):
        _ok("Linux host")
    else:
        _bad(
            f"the documented quickstart is Linux only (found {sys.platform}). It relies on "
            "Docker host networking so both listeners stay on numeric loopback; Docker "
            "Desktop is outside that contract."
        )
        problems.append(
            "Run the container on a Linux host. The client library itself works on any "
            "platform against a server you can reach."
        )

    docker = _version_of("docker")
    if docker:
        _ok(docker)
    else:
        _bad("docker not found on PATH")
        problems.append("Install Docker Engine: https://docs.docker.com/engine/install/")

    if docker:
        compose = _compose_version()
        if compose:
            _ok(compose)
        else:
            _bad("docker compose v2 not available")
            problems.append("Install the Docker Compose v2 plugin.")

        info = _run(["docker", "info", "--format", "{{.ServerVersion}}"])
        if info.returncode == 0:
            _ok(f"docker daemon reachable (server {info.stdout.strip()})")
        else:
            _bad("docker daemon is not reachable")
            problems.append("Start Docker, or add your user to the docker group and re-login.")

    if for_up:
        print(_bold("\nSource"))
        found = _find_compose_dir(allow_download=False)
        if found:
            _ok(f"docker-compose.yml found in {found[1]} {_dim(str(found[0]))}")
        else:
            _warn(f"no checkout here; `up` will download {REPO} at {SOURCE_TAG}")

    return problems


def _base_url() -> str:
    return os.environ.get("AGENT_BROWSER_URL") or DEFAULT_BASE_URL


def _probe_health(base_url: str) -> tuple[dict[str, object] | None, AgentBrowserError | None]:
    try:
        return AgentBrowser(base_url=base_url, timeout=4.0).health(), None
    except AgentBrowserError as error:
        return None, error


def _cmd_doctor(_: list[str]) -> int:
    problems = _preflight(for_up=True)

    print(_bold("\nServer"))
    base_url = _base_url()
    health, error = _probe_health(base_url)
    if health is not None:
        _ok(f"{base_url} responding (version {health.get('version')})")
        print(
            f"        browser_ready={health.get('browser_ready')} "
            f"session_active={health.get('session_active')} "
            f"slots_available={health.get('slots_available')}"
        )
    else:
        _warn(f"{base_url} not responding yet {_dim(f'({error})')}")
        print(f"        start it with {_bold('aether-browser up')}")

    print(_bold("\nTokens"))
    if os.environ.get("AGENT_BROWSER_CONTROLLER_TOKEN"):
        _ok("AGENT_BROWSER_CONTROLLER_TOKEN is set")
    else:
        _warn("AGENT_BROWSER_CONTROLLER_TOKEN unset (fine for strict loopback local mode)")

    if problems:
        print(_bold(_paint("31", "\nBlocking problems")))
        for problem in problems:
            print(f"  - {problem}")
        print()
        return 1
    print(_paint("32", "\nReady.\n"))
    return 0


def _cmd_up(argv: list[str]) -> int:
    problems = _preflight(for_up=False)
    if problems:
        print(_bold(_paint("31", "\nCannot start")))
        for problem in problems:
            print(f"  - {problem}")
        print()
        return 1

    print(_bold("\nSource"))
    found = _find_compose_dir(allow_download=True)
    if found is None:
        print(_paint("31", "\nCould not obtain a source checkout to build from.\n"))
        return 1
    _ok(found[1])

    print(_bold("\nBuilding and starting"))
    print(
        _dim(
            "  The first build installs a hash-locked Python environment and the current\n"
            "  Google Chrome Stable package, so it can take several minutes.\n"
        )
    )
    detach = "--foreground" not in argv
    command = ["docker", "compose", "up", "--build", *(["--detach"] if detach else [])]
    result = subprocess.run(command, cwd=found[0], check=False)  # noqa: S603
    if result.returncode != 0:
        return result.returncode

    if detach:
        print(f"\n  API     {DEFAULT_BASE_URL}/browser/health")
        print(f"  noVNC   {NOVNC_URL}")
        print(_dim("\n  Stop with: aether-browser down\n"))
    return 0


def _cmd_down(_: list[str]) -> int:
    found = _find_compose_dir(allow_download=False)
    if found is None:
        print(
            _paint("31", "No checkout found here. Run `down` from the directory you ran `up` in.")
        )
        return 1
    command = ["docker", "compose", "down", "--volumes", "--remove-orphans"]
    result = subprocess.run(command, cwd=found[0], check=False)  # noqa: S603
    return result.returncode


def _cmd_status(_: list[str]) -> int:
    base_url = _base_url()
    health, error = _probe_health(base_url)
    if health is None:
        print(_paint("31", f"Agent Browser is not responding at {base_url}"), file=sys.stderr)
        if error is not None and error.code:
            print(_dim(f"  {error.code}"), file=sys.stderr)
        return 1
    print(json.dumps(health, indent=2))
    return 0


def _cmd_open(_: list[str]) -> int:
    opener = {"darwin": "open", "win32": "explorer"}.get(sys.platform, "xdg-open")
    print(f"Opening {NOVNC_URL}")
    if shutil.which(opener) is None or _run([opener, NOVNC_URL]).returncode != 0:
        print(f"Open it manually: {NOVNC_URL}")
    return 0


def _cmd_help(_: list[str]) -> int:
    print(
        f"""
{_bold("aether-browser")} {_dim(__version__)}
Client and CLI for Agent Browser by Aether AI.

{_bold("Usage")}
  aether-browser <command>

{_bold("Commands")}
  doctor    Check Docker, platform, ports, and server health, and say what is wrong
  up        Build and start the runtime (Linux + Docker Compose v2; first build is slow)
  down      Stop the runtime and remove its volumes
  status    Print the server health document as JSON
  open      Open the live noVNC view in a browser
  mcp       Serve this session to an MCP client over stdio (Claude Code, Cursor, ...)
  help      Show this message

{_bold("Environment")}
  AGENT_BROWSER_URL               Server base URL (default {DEFAULT_BASE_URL})
  AGENT_BROWSER_CONTROLLER_TOKEN  Controller token, if the server runs authenticated
  AGENT_BROWSER_OBSERVER_TOKEN    Observer token

{_bold("MCP")}
  Add to your MCP client config:
  {{"mcpServers":{{"agent-browser":{{"command":"aether-browser","args":["mcp"]}}}}}}

{_bold("Library")}
  from aether_browser import AgentBrowser, session

{_dim(REPO)}
"""
    )
    return 0


def _cmd_mcp(_: list[str]) -> int:
    """Serve MCP over stdio. stdout is the protocol channel from here on."""
    from .mcp import run

    return run(_base_url())


def _cmd_version(_: list[str]) -> int:
    print(__version__)
    return 0


COMMANDS = {
    "doctor": _cmd_doctor,
    "up": _cmd_up,
    "down": _cmd_down,
    "status": _cmd_status,
    "open": _cmd_open,
    "mcp": _cmd_mcp,
    "help": _cmd_help,
    "--help": _cmd_help,
    "-h": _cmd_help,
    "--version": _cmd_version,
    "-v": _cmd_version,
}


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    command = arguments[0] if arguments else "help"
    handler = COMMANDS.get(command)
    if handler is None:
        print(_paint("31", f"Unknown command: {command}"), file=sys.stderr)
        _cmd_help([])
        return 1
    return handler(arguments[1:])


if __name__ == "__main__":
    raise SystemExit(main())
