from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
GITHUB_HOSTED_RUNNER = "ubuntu-24.04"
PRODUCTION_RUNNER = "[self-hosted, linux, x64, aetherbrowser-ci]"
STAGING_RUNNER = "[self-hosted, linux, x64, aetherbrowser-staging]"


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
    assert "aetherbrowser-ci" not in text
    assert "aetherbrowser-staging" not in text


def test_production_runner_accepts_only_exact_current_main() -> None:
    text = _read(WORKFLOW_DIR / "trusted-runner.yml")

    assert _trigger_names(text) == {"push", "workflow_dispatch"}
    assert "    branches: [main]" in text
    assert text.count(f"runs-on: {PRODUCTION_RUNNER}") == 3
    assert "aetherbrowser-staging" not in text
    assert "      expected_sha:\n" in text
    assert 'test "$EVENT_REF" = "refs/heads/main"' in text
    assert 'test "$EXPECTED_SHA" = "$EVENT_SHA"' in text
    assert 'test "$checkout_sha" = "$EVENT_SHA"' in text
    assert text.count("ref: ${{ needs.ref-proof.outputs.sha }}") == 3
    assert text.count("git rev-parse --verify HEAD^{commit}") == 4
    assert "id: image_proof" in text
    assert "image_id: ${{ steps.image_proof.outputs.image_id }}" in text
    assert (
        "image_id=\"$(podman image inspect aether-browser:${GITHUB_SHA} --format '{{.Id}}')\""
        in text
    )
    assert "AETHER_ACCEPTANCE_IMAGE_ID: ${{ needs.container.outputs.image_id }}" in text
    for job in ("container", "acceptance", "release-integrity"):
        assert f"  {job}:\n" in text


def test_staging_smoke_is_manual_exact_main_only() -> None:
    text = _read(WORKFLOW_DIR / "runner-smoke.yml")

    assert _trigger_names(text) == {"workflow_dispatch"}
    assert text.count(f"runs-on: {STAGING_RUNNER}") == 1
    assert "aetherbrowser-ci" not in text
    assert "      expected_sha:\n" in text
    assert 'test "$EVENT_REF" = "refs/heads/main"' in text
    assert 'test "$EXPECTED_SHA" = "$EVENT_SHA"' in text
    assert text.count("ref: ${{ needs.ref-proof.outputs.sha }}") == 1


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
    assert 'expected_container_host="unix:///run/aether-ci-browser-podman.sock"' in text
    assert 'expected_sha="${GITHUB_SHA:-}"' in text
    assert 'expected_image_id="${AETHER_ACCEPTANCE_IMAGE_ID:-}"' in text
    assert "image inspect \"$image_tag\" --format '{{.Id}}'" in text
    assert 'if [ "$actual_image_id" != "$expected_image_id" ]' in text
    assert '"${podman_cli[@]}" pod create' in text
    assert '--name "$pod" --network none --share net --hosts-file none' in text
    assert "--cpus=2 --memory=2g" in text
    assert text.count('--pod "$pod"') == 2
    assert "same-pod-namespace-proof" in text
    assert "pod-network-none-proof" in text
    assert '"${podman_cli[@]}" exec -i "$fixture" python -' in text
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
