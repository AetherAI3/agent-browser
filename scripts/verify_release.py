#!/usr/bin/env python3
"""Deterministic, structured release gate for Aether Browser."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_FILES = (
    ".grype.yaml",
    ".dockerignore",
    ".aether-ci.yml",
    ".github/workflows/ci.yml",
    ".github/workflows/runner-smoke.yml",
    "Dockerfile",
    "LICENSE",
    "README.md",
    "docker-compose.yml",
    "pyproject.toml",
    "requirements.lock",
    "scripts/acceptance.sh",
    "scripts/runner-smoke.sh",
    "scripts/verify_release.py",
)
RELEASE_FILES = (
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
    ".github/ISSUE_TEMPLATE/bug.yml",
    ".github/ISSUE_TEMPLATE/feature.yml",
    ".github/PULL_REQUEST_TEMPLATE.md",
    "assets/demo.mp4",
    "assets/demo-poster.png",
    "assets/social-preview.png",
    "docs/API.md",
    "docs/ARCHITECTURE.md",
    "docs/DEMO_EVIDENCE.md",
    "docs/RELEASE_EVIDENCE.md",
    "docs/SECURITY-MODEL.md",
    "docs/SOURCE-RECOVERY.md",
    "docs/index.html",
    "docs/launch.md",
    "examples/curl.sh",
    "examples/demo.py",
)
SOURCE_MODULES = tuple(
    f"src/aether_browser/{name}.py"
    for name in ("__init__", "auth", "main", "models", "policy", "runtime", "sessions")
)
TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".html",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".service",
    ".sh",
    ".slice",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _run(command: list[str], timeout: int = 900) -> tuple[bool, str]:
    try:
        result = subprocess.run(  # noqa: S603 - commands are fixed release-gate argv
            command,
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if len(output) > 2_000:
        output = output[-2_000:]
    return result.returncode == 0, output or f"exit={result.returncode}"


def _tracked_files() -> list[Path]:
    git = shutil.which("git")
    if git is None:
        return [path for path in ROOT.rglob("*") if path.is_file() and ".git" not in path.parts]
    result = subprocess.run(  # noqa: S603 - resolved executable and fixed argv
        [git, "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        names = [name for name in result.stdout.decode().split("\0") if name]
        return [ROOT / name for name in names if (ROOT / name).is_file()]
    return [path for path in ROOT.rglob("*") if path.is_file() and ".git" not in path.parts]


def _text_files() -> Iterable[Path]:
    for path in _tracked_files():
        if path.suffix.lower() in TEXT_SUFFIXES and path.stat().st_size <= 2_000_000:
            yield path


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _present(paths: Iterable[str]) -> tuple[bool, str]:
    missing = [path for path in paths if not (ROOT / path).is_file()]
    return (not missing, "all required paths present" if not missing else f"missing: {missing}")


def _version_agreement() -> tuple[bool, str]:
    try:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        project_version = str(project["project"]["version"])
        package_text = (ROOT / "src/aether_browser/__init__.py").read_text(encoding="utf-8")
        package_match = re.search(r'__version__[^=]*=\s*["\']([^"\']+)', package_text)
        docker_text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        docker_match = re.search(r'org\.opencontainers\.image\.version="([^"]+)"', docker_text)
        compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        compose_match = re.search(r"image:\s*aether-browser:([^\s]+)", compose_text)
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        return False, str(exc)
    versions = {
        "project": project_version,
        "package": package_match.group(1) if package_match else "MISSING",
        "image-label": docker_match.group(1) if docker_match else "MISSING",
        "compose": compose_match.group(1) if compose_match else "MISSING",
    }
    return len(set(versions.values())) == 1, json.dumps(versions, sort_keys=True)


def _readme_integrity() -> tuple[bool, str]:
    readme = ROOT / "README.md"
    if not readme.is_file():
        return False, "README.md missing"
    text = readme.read_text(encoding="utf-8")
    placeholders = re.findall(
        r"(?im)(?:\bTODO\b|\bTBD\b|\bFIXME\b|\bCHANGEME\b|<[^>]*(?:replace|placeholder)[^>]*>)",
        text,
    )
    dead: list[str] = []
    for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
        target = target.strip().split("#", 1)[0]
        if not target or re.match(r"^(?:https?://|mailto:)", target):
            continue
        if not (ROOT / target).exists():
            dead.append(target)
    compose_ok = "docker compose up --build" in text and "127.0.0.1:8092" in text
    issues = []
    if placeholders:
        issues.append(f"placeholders={len(placeholders)}")
    if dead:
        issues.append(f"dead_local_links={sorted(set(dead))}")
    if not compose_ok:
        issues.append("quickstart does not match loopback Compose invocation")
    return not issues, "; ".join(issues) if issues else "README paths and quickstart verified"


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    data = path.read_bytes()[:24]
    if len(data) != 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def _asset_integrity() -> tuple[bool, str]:
    issues: list[str] = []
    for name in ("demo.mp4", "demo-poster.png", "social-preview.png"):
        path = ROOT / "assets" / name
        if not path.is_file():
            issues.append(f"missing {name}")
            continue
        data = path.read_bytes()
        if data.startswith(b"version https://git-lfs.github.com/spec"):
            issues.append(f"{name} is a Git LFS pointer")
        if len(data) < 1_024:
            issues.append(f"{name} is implausibly small ({len(data)} bytes)")
        if name.endswith(".png") and _png_dimensions(path) is None:
            issues.append(f"{name} is not a valid PNG header")
        if name.endswith(".mp4") and b"ftyp" not in data[:64]:
            issues.append("demo.mp4 lacks an ISO media file signature")
    social = ROOT / "assets/social-preview.png"
    if social.is_file() and _png_dimensions(social) != (1280, 640):
        issues.append(
            f"social-preview.png dimensions are {_png_dimensions(social)!r}, expected (1280, 640)"
        )
    return not issues, "; ".join(
        issues
    ) if issues else "media signatures, sizes, and dimensions verified"


def _scan_patterns(
    name: str,
    patterns: Iterable[re.Pattern[str]],
    include: Callable[[str], bool] | None = None,
    allowed_lines: frozenset[tuple[str, str]] = frozenset(),
) -> tuple[bool, str]:
    hits: list[str] = []
    for path in _text_files():
        rel = _relative(path)
        if include is not None and not include(rel):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if (rel, line.strip()) in allowed_lines:
                continue
            if any(pattern.search(line) for pattern in patterns):
                hits.append(f"{rel}:{line_number}")
                if len(hits) >= 25:
                    break
        if len(hits) >= 25:
            break
    return not hits, f"{name}: clean" if not hits else f"{name} hits: {hits}"


def _secret_scan() -> tuple[bool, str]:
    patterns = (
        re.compile(("gh" + "p_") + r"[A-Za-z0-9]{30,}"),
        re.compile(("github" + "_pat_") + r"[A-Za-z0-9_]{40,}"),
        re.compile(r"AKIA[A-Z0-9]{16}"),
        re.compile(("-----BEGIN " + r"(?:RSA |OPENSSH |EC )?") + "PRIVATE KEY-----"),
        re.compile(
            r"(?i)(?:password|secret|api[_-]?key)\s*[:=]\s*["
            + "\"'"
            + r"][^"
            + "\"'"
            + r"]{12,}["
            + "\"'"
            + "]"
        ),
    )
    return _scan_patterns("secret scan", patterns)


def _private_infrastructure_scan() -> tuple[bool, str]:
    hits: list[str] = []
    ipv4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
    private_hostname = re.compile(
        r"(?i)(?<![a-z0-9_-])(?:\.(?:internal|lan|ts\.net)|"
        r"(?:[a-z0-9-]+\.)+(?:internal|lan|ts\.net))(?![a-z0-9_-])"
    )
    policy_path = "src/aether_browser/policy.py"
    policy_denylist_blocks = (
        "_PROHIBITED_HOSTS",
        "_PROHIBITED_SUFFIXES",
        "_PROHIBITED_IPV4_NETWORKS",
        "_PROHIBITED_IPV6_NETWORKS",
    )
    public_copy = {"README.md", "docs/index.html", "docs/launch.md"}
    for path in _text_files():
        rel = _relative(path)
        if (
            rel not in public_copy
            and not rel.startswith("src/")
            and not rel.startswith("examples/")
        ):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        in_policy_denylist = False
        for line_number, line in enumerate(text.splitlines(), start=1):
            if rel == policy_path:
                if line.startswith(policy_denylist_blocks):
                    in_policy_denylist = True
                elif line and not line[0].isspace():
                    in_policy_denylist = False
            bad = bool(private_hostname.search(line))
            for candidate in ipv4.findall(line):
                try:
                    address = ipaddress.ip_address(candidate)
                except ValueError:
                    continue
                if (address.is_private or address.is_link_local) and not address.is_loopback:
                    bad = True
            if bad and in_policy_denylist and line.strip().startswith('"'):
                bad = False
            if bad:
                hits.append(f"{rel}:{line_number}")
    return (
        not hits,
        "private infrastructure scan: clean"
        if not hits
        else f"private infrastructure hits: {hits[:25]}",
    )


def _banned_domain_scan() -> tuple[bool, str]:
    words = ("trad" + "ing", "bro" + "ker", "cus" + "tody", "protocol" + "-c", "anti" + "flock")
    patterns = tuple(re.compile(rf"(?i)\b{re.escape(word)}\b") for word in words)

    def public_or_executable(rel: str) -> bool:
        return (
            rel == "README.md"
            or rel.startswith(("src/", "examples/"))
            or rel
            in {
                "docs/index.html",
                "docs/launch.md",
            }
        )

    documented_non_goals = frozenset(
        {
            (
                "README.md",
                "references; ATS/trading integrations, broker or account selectors, order "
                "actions, secrets,",
            ),
            (
                "README.md",
                "- No multi-session pool, ATS integration, trading integration, or brokerage "
                "behavior.",
            ),
        }
    )
    return _scan_patterns(
        "excluded-domain scan",
        patterns,
        public_or_executable,
        allowed_lines=documented_non_goals,
    )


def _credential_injection_scan() -> tuple[bool, str]:
    fragments = (
        "add_" + "cookies",
        "storage_" + "state",
        "cookie_" + "inject",
        "credential_" + "inject",
        "password_" + "inject",
    )
    patterns = tuple(re.compile(rf"(?i){re.escape(fragment)}") for fragment in fragments)
    return _scan_patterns(
        "credential-injection scan",
        patterns,
        lambda rel: rel.startswith(("src/", "examples/")),
    )


def _container_loopback_contract() -> tuple[bool, str]:
    try:
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        entrypoint = (ROOT / "scripts/container-entrypoint.sh").read_text(encoding="utf-8")
        acceptance = (ROOT / "scripts/acceptance.sh").read_text(encoding="utf-8")
        grype_policy = (ROOT / ".grype.yaml").read_text(encoding="utf-8")
        trusted_workflow = (ROOT / ".github/workflows/trusted-runner.yml").read_text(
            encoding="utf-8"
        )
    except OSError as exc:
        return False, str(exc)

    issues: list[str] = []
    network_modes = re.findall(r"(?m)^\s*network_mode:\s*([^\s#]+)", compose)
    if network_modes != ["host"]:
        issues.append("Compose does not use host networking")
    if re.search(r"(?m)^\s*ports:\s*$", compose) is not None:
        issues.append("Compose publishes container ports")
    compose_novnc_binds = re.findall(
        r'(?m)^\s*AETHER_BROWSER_NOVNC_BIND:\s*["\']?([^"\'\s#]+)', compose
    )
    if compose_novnc_binds != ["127.0.0.1"]:
        issues.append("Compose noVNC bind is not numeric loopback")
    if 'AETHER_BROWSER_API_BIND: "127.0.0.1"' not in compose:
        issues.append("Compose API bind is not numeric loopback")
    if re.search(r"(?im)^\s*EXPOSE\b[^\n]*\b6080\b", dockerfile) is not None:
        issues.append("image advertises the unauthenticated noVNC port")

    entrypoint_contract = (
        "${AETHER_BROWSER_NOVNC_BIND:-127.0.0.1}",
        '[ "$novnc_bind" != "127.0.0.1" ]',
        "x11vnc -display :99 -listen 127.0.0.1",
        'websockify --web=/usr/share/novnc "$novnc_bind:$novnc_port"',
        "python -m aether_browser.main &",
    )
    if any(fragment not in entrypoint for fragment in entrypoint_contract):
        issues.append("entrypoint does not enforce the noVNC loopback bind")
    if "aether_browser.main:app" in entrypoint or "python -m uvicorn" in entrypoint:
        issues.append("entrypoint bypasses the validated module launcher")
    browser_contract = (
        "python -m patchright install --with-deps chrome",
        "command -v google-chrome-stable",
        "google-chrome-stable --version",
        "dpkg-query --show google-chrome-stable",
        "org.opencontainers.image.documentation=",
    )
    if any(fragment not in dockerfile for fragment in browser_contract):
        issues.append("image does not install and verify the runtime-selected Chrome channel")
    if "python -m patchright install --with-deps chromium" in dockerfile:
        issues.append("image installs a browser payload that does not match the runtime channel")
    if "org.opencontainers.image.licenses" in dockerfile:
        issues.append("image incorrectly applies Aether's source-only license to all contents")
    websockify_commands = [
        line.strip() for line in entrypoint.splitlines() if line.startswith("websockify ")
    ]
    if websockify_commands != [
        'websockify --web=/usr/share/novnc "$novnc_bind:$novnc_port" 127.0.0.1:5900 &'
    ]:
        issues.append("entrypoint contains an unexpected websockify listener")

    acceptance_contract = (
        "podman_cli=(podman --remote)",
        'default_podman_socket="/run/aether-ci-browser-podman.sock"',
        "AETHER_ACCEPTANCE_EPHEMERAL_PODMAN_SOCKET",
        '"${GITHUB_ACTIONS:-}" != "true"',
        '"$RUNNER_TEMP"/*',
        "stat -Lc '%u:%g:%a'",
        '"$(id -u):$(id -g):600"',
        'expected_sha="${GITHUB_SHA:-}"',
        'expected_image_id="${AETHER_ACCEPTANCE_IMAGE_ID:-}"',
        "image inspect \"$image_tag\" --format '{{.Id}}'",
        'actual_image_hash="${actual_image_id#sha256:}"',
        'if [ "$actual_image_hash" != "$image_hash" ]',
        '"${browser_image#sha256:}" != "$image_hash"',
        '"${fixture_image#sha256:}" != "$image_hash"',
        '--name "$pod" --network none --share net --hosts-file none',
        "--cpus=2 --memory=2g",
        '--name "$fixture" --pod "$pod"',
        '--name "$browser" --pod "$pod"',
        "AETHER_BROWSER_NOVNC_BIND=127.0.0.1",
        "same-pod-namespace-proof",
        "pod-network-none-proof",
        "loopback-listener-proof",
        '"${podman_cli[@]}" exec -i "$fixture" python -',
        '"isolated-pod-network-none"',
        '"in-namespace-http-driver"',
        '"exact-image-id-handoff"',
        '"runtime-image-id-proof"',
        '"resource-bounded-pod"',
        '"pod-cleanup"',
    )
    if any(fragment not in acceptance for fragment in acceptance_contract):
        issues.append("acceptance lacks the isolated-pod noVNC proof")
    acceptance_novnc_binds = re.findall(r"AETHER_BROWSER_NOVNC_BIND=([^\s\\]+)", acceptance)
    if acceptance_novnc_binds != ["127.0.0.1"]:
        issues.append("acceptance noVNC bind is not uniquely numeric loopback")
    if acceptance.count("--pull=never") != 2:
        issues.append("acceptance does not disable pulling for both workload containers")
    if acceptance.count("--http-proxy=false") != 2:
        issues.append("acceptance does not suppress inherited proxy configuration")
    acceptance_exposure = (
        "--network host",
        "--network=host",
        "--publish ",
        "--publish=",
        "podman network create",
        '"${podman_cli[@]}" pull ',
        '"${podman_cli[@]}" build ',
        '-p "127.0.0.1:${novnc_port}:6080"',
    )
    if any(fragment in acceptance for fragment in acceptance_exposure):
        issues.append("acceptance exposes, connects, pulls, or rebuilds its isolated workload")
    workflow_handoff = (
        "id: image_proof",
        "image_id: ${{ steps.image_proof.outputs.image_id }}",
        'image_id="$(podman --remote image inspect '
        "aether-browser:${GITHUB_SHA} --format '{{.Id}}')\"",
        "AETHER_ACCEPTANCE_IMAGE_ID: ${{ steps.image_proof.outputs.image_id }}",
    )
    if any(fragment not in trusted_workflow for fragment in workflow_handoff):
        issues.append("trusted workflow lacks the immutable image-ID handoff")
    policy_lines = tuple(
        line.rstrip()
        for line in grype_policy.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    expected_policy = (
        "ignore:",
        "  - namespace: nvd:cpe",
        "    fix-state: fixed",
        "    match-type: cpe-match",
        "    reason: CPE-only UnknownPackage match retained as an advisory; "
        "authoritative package findings remain blocking",
        "    package:",
        "      type: UnknownPackage",
    )
    if policy_lines != expected_policy:
        issues.append(
            "Grype policy is broader than the single fixed nvd:cpe UnknownPackage "
            "cpe-match advisory class"
        )
    vulnerability_scan_contract = (
        'grype --config .grype.yaml "sbom:artifacts/sbom.spdx.json"',
        "--fail-on high --only-fixed --output json",
        "> artifacts/vulnerabilities.json",
        "> artifacts/browser-package.txt",
        "/opt/google/chrome/chrome --version",
        r"grep -Eq '^Google Chrome [0-9]+\.[0-9]+\.[0-9]+\.[0-9]+[[:space:]]*$'",
    )
    if any(fragment not in trusted_workflow for fragment in vulnerability_scan_contract):
        issues.append("trusted workflow weakens or omits exact-image vulnerability evidence")

    return (
        not issues,
        "; ".join(issues)
        if issues
        else "Compose host-loopback and acceptance isolated-pod contracts verified",
    )


def _unsupported_claim_scan() -> tuple[bool, str]:
    claims = (
        r"(?i)\b(?:production[- ]ready|unhackable|guaranteed secure|zero vulnerabilities)\b",
        r"(?i)\b(?:fastest|best-in-class|fully compatible|works with every)\b",
        r"(?i)\b\d+(?:\.\d+)?x faster\b",
        r"(?i)\b(?:thousands|millions) of (?:users|developers|sessions)\b",
    )
    return _scan_patterns(
        "unsupported-claim scan",
        tuple(re.compile(pattern) for pattern in claims),
        lambda rel: rel in {"README.md", "docs/index.html", "docs/launch.md"},
    )


def _release_evidence() -> tuple[bool, str]:
    path = ROOT / "docs/RELEASE_EVIDENCE.md"
    if not path.is_file():
        return False, "docs/RELEASE_EVIDENCE.md missing"
    ok, head = _run(["git", "rev-parse", "HEAD"], timeout=30)
    if not ok:
        return False, head
    head = head.strip().splitlines()[-1]
    workflow_commit = os.environ.get("AETHER_RELEASE_EVIDENCE_COMMIT", head)
    if workflow_commit != head:
        return False, f"workflow attestation={workflow_commit}; checkout={head}"
    text = path.read_text(encoding="utf-8")
    match = re.search(r"(?im)^Tested runtime commit:\s*`?([0-9a-f]{40})`?\s*$", text)
    candidate = match.group(1) if match else "MISSING"
    if candidate == "MISSING":
        return False, "tested runtime commit is missing"
    ancestor_ok, ancestor_detail = _run(
        ["git", "merge-base", "--is-ancestor", candidate, head], timeout=30
    )
    if not ancestor_ok:
        return False, f"runtime commit is not an ancestor: {ancestor_detail}"
    diff_ok, diff_detail = _run(["git", "diff", "--name-only", f"{candidate}..{head}"], timeout=30)
    if not diff_ok:
        return False, diff_detail
    changed = {line for line in diff_detail.splitlines() if line}
    allowed = {
        "assets/demo-poster.png",
        "assets/demo.mp4",
        "docs/DEMO_EVIDENCE.md",
        "docs/RELEASE_EVIDENCE.md",
        "release/evidence/manifest.json",
    }
    unexpected = sorted(changed - allowed)
    if unexpected:
        return False, f"post-runtime changes are not evidence-only: {unexpected}"
    evidence_text = text + "\n" + (ROOT / "docs/DEMO_EVIDENCE.md").read_text(encoding="utf-8")
    pending = re.findall(r"(?im)\b(?:not captured|asset absent|capture pending)\b", evidence_text)
    if pending:
        return False, f"pending evidence markers={len(pending)}"
    return True, f"runtime={candidate}; evidence_checkout={head}; evidence_only={sorted(changed)}"


def _quality() -> tuple[bool, str]:
    commands = (
        [sys.executable, "-m", "ruff", "format", "--check", "."],
        [sys.executable, "-m", "ruff", "check", "."],
        [sys.executable, "-m", "mypy", "src"],
        [sys.executable, "-m", "pytest", "-q"],
    )
    failures = []
    for command in commands:
        ok, detail = _run(command)
        if not ok:
            failures.append(f"{' '.join(command[2:])}: {detail}")
    return not failures, "format, lint, typing, and tests passed" if not failures else " | ".join(
        failures
    )


def _dependency_audit() -> tuple[bool, str]:
    checks = (
        [sys.executable, "-m", "pip", "check"],
        [sys.executable, "-m", "pip_audit", "--strict", "--disable-pip", "-r", "requirements.lock"],
    )
    failures = []
    for command in checks:
        ok, detail = _run(command)
        if not ok:
            failures.append(detail)
    return (
        not failures,
        "dependency consistency and vulnerability audit passed"
        if not failures
        else " | ".join(failures),
    )


def _rootless_engine() -> tuple[bool, str]:
    if shutil.which("podman") is None:
        return False, "podman is not installed"
    ok, detail = _run(["podman", "info", "--format", "{{.Host.Security.Rootless}}"], timeout=60)
    return ok and detail.strip().lower() == "true", f"rootless={detail.strip()}"


def _acceptance() -> tuple[bool, str]:
    bash = shutil.which("bash")
    if bash is None:
        return False, "bash is not installed"
    return _run([bash, "scripts/acceptance.sh"], timeout=1_800)


def _record(name: str, function: Callable[[], tuple[bool, str]]) -> Check:
    try:
        ok, detail = function()
    except Exception as exc:  # release gate converts every unexpected condition to failure
        return Check(name=name, status="FAIL", detail=f"{type(exc).__name__}: {exc}")
    return Check(name=name, status="PASS" if ok else "FAIL", detail=detail)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--manifest-only", action="store_true")
    modes.add_argument("--security-only", action="store_true")
    modes.add_argument("--strict", action="store_true")
    parser.add_argument(
        "--skip-external", action="store_true", help="Skip container and acceptance checks"
    )
    args = parser.parse_args()

    if args.skip_external and not args.strict:
        parser.error("--skip-external is valid only with --strict")

    checks: list[Check] = []
    if args.manifest_only or args.strict:
        checks.extend(
            (
                _record("required-core-files", lambda: _present(CORE_FILES)),
                _record("required-source-modules", lambda: _present(SOURCE_MODULES)),
                _record("version-agreement", _version_agreement),
            )
        )
    if args.security_only or args.strict:
        checks.extend(
            (
                _record("secret-scan", _secret_scan),
                _record("private-infrastructure-scan", _private_infrastructure_scan),
                _record("excluded-domain-scan", _banned_domain_scan),
                _record("credential-injection-scan", _credential_injection_scan),
                _record("container-loopback-contract", _container_loopback_contract),
            )
        )
    if args.strict:
        checks.extend(
            (
                _record("required-release-files", lambda: _present(RELEASE_FILES)),
                _record("readme-integrity", _readme_integrity),
                _record("asset-integrity", _asset_integrity),
                _record("unsupported-claims", _unsupported_claim_scan),
                _record("quality", _quality),
                _record("dependency-audit", _dependency_audit),
                _record("exact-commit-evidence", _release_evidence),
            )
        )
        if args.skip_external:
            checks.extend(
                (
                    Check("rootless-container", "SKIP", "explicit --skip-external"),
                    Check("acceptance-and-novnc", "SKIP", "explicit --skip-external"),
                )
            )
        else:
            checks.extend(
                (
                    _record("rootless-container", _rootless_engine),
                    _record("acceptance-and-novnc", _acceptance),
                )
            )

    failed = sum(check.status == "FAIL" for check in checks)
    summary = {
        "schema_version": 1,
        "gate": "aether-browser-release",
        "mode": "strict" if args.strict else "security" if args.security_only else "manifest",
        "result": "PASS" if failed == 0 else "FAIL",
        "counts": {
            "pass": sum(check.status == "PASS" for check in checks),
            "fail": failed,
            "skip": sum(check.status == "SKIP" for check in checks),
        },
        "checks": [asdict(check) for check in checks],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
