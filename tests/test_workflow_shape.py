from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
GITHUB_HOSTED_RUNNER = "ubuntu-24.04"
STAGING_RUNNER = "[self-hosted, linux, x64, vps6-ci, agent-browser-staging, aether-vps6-browser-01]"


def _workflow_paths() -> tuple[Path, ...]:
    return tuple(sorted((*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml"))))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _trigger_names(text: str) -> set[str]:
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if line == "on:"]
    assert len(starts) == 1, "workflow must use one explicit block-style on: mapping"
    names: set[str] = set()
    for line in lines[starts[0] + 1 :]:
        if line and not line.startswith((" ", "#")):
            break
        match = re.fullmatch(r"  ([a-z_]+):(?:\s.*)?", line)
        if match is not None:
            names.add(match.group(1))
    assert names, "workflow trigger mapping must not be empty"
    return names


def _runs_on_values(text: str) -> tuple[str, ...]:
    values: list[str] = []
    for line in text.splitlines():
        if "runs-on:" not in line:
            continue
        match = re.fullmatch(r"\s+runs-on:\s*(\S(?:.*\S)?)\s*", line)
        assert match is not None, "runs-on must be a direct, non-empty scalar or inline list"
        values.append(match.group(1))
    return tuple(values)


def test_pull_request_workflows_use_only_fixed_github_hosted_runners() -> None:
    found_pull_request = False
    for path in _workflow_paths():
        text = _read(path)
        triggers = _trigger_names(text)
        assert "pull_request_target" not in triggers
        if "pull_request" not in triggers:
            continue
        found_pull_request = True
        runners = _runs_on_values(text)
        assert runners, f"{path.name} has no jobs to enforce"
        assert set(runners) == {GITHUB_HOSTED_RUNNER}, (
            f"{path.name} executes pull-request code outside the fixed "
            f"{GITHUB_HOSTED_RUNNER} trust boundary: {runners}"
        )
    assert found_pull_request, "at least one bounded pull-request workflow is required"


def test_every_self_hosted_workflow_excludes_pull_request_events() -> None:
    found_self_hosted = False
    for path in _workflow_paths():
        text = _read(path)
        runners = _runs_on_values(text)
        if not any("self-hosted" in runner for runner in runners):
            continue
        found_self_hosted = True
        triggers = _trigger_names(text)
        assert "pull_request" not in triggers, path.name
        assert "pull_request_target" not in triggers, path.name
    assert found_self_hosted, "the dedicated runner workflows are missing"


def test_bounded_ci_is_github_hosted_for_every_job() -> None:
    text = _read(WORKFLOW_DIR / "ci.yml")

    assert _trigger_names(text) == {"pull_request", "push", "workflow_dispatch"}
    assert set(_runs_on_values(text)) == {GITHUB_HOSTED_RUNNER}
    for job in ("change-map", "quality", "unit", "security"):
        assert f"  {job}:\n" in text
    assert "agent-browser-ci" not in text
    assert "agent-browser-staging" not in text


def test_release_evidence_accepts_only_exact_current_main_on_hosted_runners() -> None:
    text = _read(WORKFLOW_DIR / "trusted-runner.yml")

    assert "group: release-evidence-${{ github.sha }}" in text
    assert "cancel-in-progress: false" in text
    assert _trigger_names(text) == {"push", "workflow_dispatch"}
    assert "    branches: [main]" in text
    assert set(_runs_on_values(text)) == {GITHUB_HOSTED_RUNNER}
    assert text.count(f"runs-on: {GITHUB_HOSTED_RUNNER}") == 5
    assert "self-hosted" not in text
    assert "agent-browser-ci" not in text
    assert "agent-browser-staging" not in text
    assert "      expected_sha:\n" in text
    assert 'test "$EVENT_REF" = "refs/heads/main"' in text
    assert 'test "$EXPECTED_SHA" = "$EVENT_SHA"' in text
    assert 'test "$checkout_sha" = "$EVENT_SHA"' in text
    # Every downstream job checks out github.sha, which ref-proof has already pinned to the
    # exact refs/heads/main tip. Using a statically trusted ref keeps a privileged run from
    # ever executing an unproved tree, and each job still cross-checks HEAD against the
    # proved SHA. Reintroducing an indirect ref would reopen the cache-poisoning path.
    assert text.count("ref: ${{ github.sha }}") == 4
    assert "ref: ${{ needs.ref-proof.outputs.sha }}" not in text
    assert text.count("TRUSTED_SHA: ${{ needs.ref-proof.outputs.sha }}") == 5
    assert text.count("git rev-parse --verify HEAD^{commit}") == 5
    assert "id: image_proof" in text
    assert "image_id: ${{ steps.image_proof.outputs.image_id }}" in text
    assert (
        'image_id="$(podman --remote image inspect '
        "agent-browser:${GITHUB_SHA} --format '{{.Id}}')\"" in text
    )
    assert "AETHER_ACCEPTANCE_IMAGE_ID: ${{ steps.image_proof.outputs.image_id }}" in text
    assert text.count("bash scripts/acceptance.sh") == 1
    assert 'grype --config .grype.yaml "sbom:artifacts/sbom.spdx.json"' in text
    assert "--fail-on high --only-fixed --output json" in text
    assert "> artifacts/vulnerabilities.json" in text
    assert "> artifacts/browser-package.txt" in text
    assert "podman --remote run --rm -i --pull=never --http-proxy=false" in text
    assert "test -s artifacts/python-distributions.json" in text
    assert "python -m json.tool artifacts/python-distributions.json" in text
    assert "/opt/google/chrome/chrome --version" in text
    assert r"grep -Eq '^Google Chrome [0-9]+\.[0-9]+\.[0-9]+\.[0-9]+[[:space:]]*$'" in text
    for job in ("container", "acceptance", "quickstart", "release-integrity"):
        assert f"  {job}:\n" in text
    release_integrity = text.split("  release-integrity:\n", maxsplit=1)[1]
    assert "          fetch-depth: 0\n" in release_integrity


def test_container_browser_launcher_and_scan_advisory_are_narrowly_bound() -> None:
    dockerfile = _read(ROOT / "Dockerfile")
    entrypoint = _read(ROOT / "scripts" / "container-entrypoint.sh")
    runtime = _read(ROOT / "src" / "agent_browser" / "runtime.py")
    x11vnc_command = "x11vnc -display :99 -listen 127.0.0.1 -no6 -noipv6"

    assert entrypoint.count("python -m agent_browser.main &") == 1
    assert entrypoint.count(x11vnc_command) == 1
    assert entrypoint.count("-rfbport 5900 -rfbportv6 -1 -httpportv6 -1") == 1
    assert "agent_browser.main:app" not in entrypoint
    assert "python -m uvicorn" not in entrypoint
    assert "python -m patchright install --with-deps chrome" in dockerfile
    assert "python -m patchright install --with-deps chromium" not in dockerfile
    assert "command -v google-chrome-stable" in dockerfile
    assert "org.opencontainers.image.licenses" not in dockerfile
    assert "org.opencontainers.image.documentation=" in dockerfile
    assert 'chrome_channel: str = "chrome"' in runtime
    assert "channel=self._chrome_channel" in runtime

    policy_lines = tuple(
        line.rstrip()
        for line in _read(ROOT / ".grype.yaml").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    assert policy_lines == (
        "ignore:",
        "  - namespace: nvd:cpe",
        "    fix-state: fixed",
        "    match-type: cpe-match",
        "    reason: CPE-only UnknownPackage match retained as an advisory; "
        "authoritative package findings remain blocking",
        "    package:",
        "      type: UnknownPackage",
    )


def test_staging_smoke_is_manual_exact_main_only() -> None:
    text = _read(WORKFLOW_DIR / "runner-smoke.yml")

    assert _trigger_names(text) == {"workflow_dispatch"}
    assert text.count(f"runs-on: {STAGING_RUNNER}") == 1
    assert "agent-browser-ci" not in text
    assert "      expected_sha:\n" in text
    assert 'test "$EVENT_REF" = "refs/heads/main"' in text
    assert 'test "$EXPECTED_SHA" = "$EVENT_SHA"' in text
    # Same trusted-ref contract as the release workflow; see the comment there.
    assert text.count("ref: ${{ github.sha }}") == 1
    assert "ref: ${{ needs.ref-proof.outputs.sha }}" not in text
    assert "TRUSTED_SHA: ${{ needs.ref-proof.outputs.sha }}" in text


def test_package_installs_cannot_escape_the_hash_lock_via_build_isolation() -> None:
    paths = (*_workflow_paths(), ROOT / "Dockerfile")

    found_package_install = False
    for path in paths:
        for line in _read(path).splitlines():
            if "pip install" not in line or "--no-deps" not in line:
                continue
            found_package_install = True
            assert "--no-build-isolation" in line, path
    assert found_package_install, "the package itself is never installed"


def test_container_build_context_is_a_runtime_allowlist_without_git_metadata() -> None:
    ignore_path = ROOT / ".dockerignore"
    assert ignore_path.is_file()
    assert not (ROOT / ".containerignore").exists(), (
        "Podman gives .containerignore precedence; it must not override the reviewed allowlist"
    )
    assert not (ROOT / "Dockerfile.dockerignore").exists(), (
        "Docker gives Dockerfile.dockerignore precedence; it must not override "
        "the reviewed allowlist"
    )

    rules = tuple(
        line
        for raw_line in _read(ignore_path).splitlines()
        if (line := raw_line.strip()) and not line.startswith("#")
    )
    assert rules == (
        "**",
        ".git",
        ".git/**",
        "!pyproject.toml",
        "!requirements.lock",
        "!README.md",
        "!LICENSE",
        "!THIRD_PARTY_NOTICES.md",
        "!src",
        "!src/**",
        "!scripts",
        "!scripts/container-entrypoint.sh",
    )
    assert "COPY . /app" in _read(ROOT / "Dockerfile")


def test_runner_temp_storage_is_private_and_cgroup_bounded() -> None:
    text = _read(ROOT / "ops" / "runner" / "agent-browser-runner.service")

    assert "PrivateTmp=yes" not in text
    assert "MemoryMax=8G" in text
    assert "TemporaryFileSystem=/tmp:rw,nosuid,nodev,size=256M,mode=1777" in text
    assert "TemporaryFileSystem=/var/tmp:rw,nosuid,nodev,size=256M,mode=1777" in text


def test_runner_smoke_uses_only_the_private_remote_podman_service() -> None:
    text = _read(ROOT / "scripts" / "runner-smoke.sh")

    assert re.search(r"(?m)^[^#\n]*\bpodman\s+unshare\b", text) is None
    assert 'podman_socket="/run/aether-ci-browser-podman.sock"' in text
    assert 'expected_container_host="unix://${podman_socket}"' in text
    assert "podman --remote info" in text
    assert "podman --remote ps" in text
    assert "podman-remote-socket-private" in text
    assert "cgroup-memory-and-task-limits" in text
    assert 'cgroup_cursor="${cgroup_cursor%/*}"' in text


def test_acceptance_uses_an_offline_pod_through_remote_podman() -> None:
    text = _read(ROOT / "scripts" / "acceptance.sh")

    assert text.count("podman_cli=(podman --remote)") == 1
    assert 'default_podman_socket="/run/aether-ci-browser-podman.sock"' in text
    assert "AETHER_ACCEPTANCE_EPHEMERAL_PODMAN_SOCKET" in text
    assert '"${GITHUB_ACTIONS:-}" != "true"' in text
    assert '"$RUNNER_TEMP"/*' in text
    assert "stat -Lc '%u:%g:%a'" in text
    assert '"$(id -u):$(id -g):600"' in text
    assert 'expected_sha="${GITHUB_SHA:-}"' in text
    assert 'expected_image_id="${AETHER_ACCEPTANCE_IMAGE_ID:-}"' in text
    assert text.count('"Aa1!" + secrets.token_urlsafe(32)') == 2
    assert "image inspect \"$image_tag\" --format '{{.Id}}'" in text
    assert 'actual_image_hash="${actual_image_id#sha256:}"' in text
    assert 'if [ "$actual_image_hash" != "$image_hash" ]' in text
    assert '"${browser_image#sha256:}" != "$image_hash"' in text
    assert '"${fixture_image#sha256:}" != "$image_hash"' in text
    assert '"${podman_cli[@]}" pod create' in text
    assert '--name "$pod" --network none --share net --hosts-file none' in text
    assert "--cpus=2 --memory=2g" in text
    assert text.count('--pod "$pod"') == 2
    assert "same-pod-namespace-proof" in text
    assert "pod-network-none-proof" in text
    assert '"${podman_cli[@]}" exec -i "$fixture" python -' in text
    assert 'headers={"Authorization": f"Bearer {sys.argv[4]}"}' in text
    assert 'socket.create_connection(("127.0.0.1", 5900), timeout=1)' in text
    assert '"vnc-rfb-readiness"' in text
    assert "find /tmp /home/agent -type f -name blocked.txt -print -quit" in text
    assert '"download-non-persistence"' in text
    assert '"isolated-pod-network-none"' in text
    assert '"in-namespace-http-driver"' in text
    assert '"exact-image-id-handoff"' in text
    assert '"runtime-image-id-proof"' in text
    assert '"resource-bounded-pod"' in text
    assert '"pod-cleanup"' in text
    assert text.count("--pull=never") == 2
    assert text.count("--http-proxy=false") == 2
    assert "--network host" not in text
    assert "--network=host" not in text
    assert "--publish " not in text
    assert '"${podman_cli[@]}" pull ' not in text
    assert '"${podman_cli[@]}" build ' not in text
    assert "podman network create" not in text
    assert re.search(r"(?m)^\s*podman(?:\s|$)", text) is None
