#!/usr/bin/env bash
set -Eeuo pipefail

# No hostnames, paths outside the fixed probes, environment values, or account
# names are emitted. The uploaded JSON is deliberately safe to retain.
failures=()
passes=()

pass() { passes+=("$1"); }
fail() { failures+=("$1"); }
probe() {
  local name="$1"
  shift
  if "$@" >/dev/null 2>&1; then pass "$name"; else fail "$name"; fi
}

probe checkout-present test -d .git
probe python-toolchain python --version
probe git-toolchain git --version

workspace_probe=".runner-smoke-write-${GITHUB_RUN_ID:-local}-$$"
if (umask 077 && : > "$workspace_probe") && rm -f "$workspace_probe"; then
  pass workspace-writable
else
  fail workspace-writable
fi

podman_socket="/run/aether-ci-browser-podman.sock"
expected_container_host="unix://${podman_socket}"
if [ "${CONTAINER_HOST:-}" = "$expected_container_host" ] \
  && [ -S "$podman_socket" ] \
  && [ -r "$podman_socket" ] \
  && [ -w "$podman_socket" ]; then
  pass podman-remote-socket-reachable
else
  fail podman-remote-socket-reachable
fi

expected_socket_identity="$(id -u):$(id -g):600"
if [ -S "$podman_socket" ] \
  && [ "$(stat -c '%u:%g:%a' "$podman_socket" 2>/dev/null || true)" \
    = "$expected_socket_identity" ]; then
  pass podman-remote-socket-private
else
  fail podman-remote-socket-private
fi

# The runner is a hardened client of a separate socket-activated rootless
# Podman service. `podman unshare` is a local-engine operation and is not
# supported by a remote client, so qualification uses only remote API calls.
if command -v podman >/dev/null 2>&1 \
  && [ "${CONTAINER_HOST:-}" = "$expected_container_host" ] \
  && [ "$(podman --remote info --format '{{.Host.Security.Rootless}}' \
    2>/dev/null || true)" = "true" ] \
  && podman --remote ps --all --format '{{.ID}}' >/dev/null 2>&1; then
  pass rootless-podman-remote
else
  fail rootless-podman-remote
fi

if syft version 2>/dev/null | grep -Eq 'Version:[[:space:]]+1\.51\.1'; then
  pass syft-version
else
  fail syft-version
fi
if grype version 2>/dev/null | grep -Eq 'Version:[[:space:]]+0\.118\.0'; then
  pass grype-version
else
  fail grype-version
fi

if [ ! -S /var/run/docker.sock ] && [ ! -S /run/docker.sock ]; then
  pass docker-socket-unreachable
else
  fail docker-socket-unreachable
fi

if [ ! -e /run/tailscale/tailscaled.sock ] && [ ! -e /var/run/tailscale/tailscaled.sock ]; then
  pass tailscale-control-unreachable
else
  fail tailscale-control-unreachable
fi

if [ ! -r /etc/shadow ] && [ ! -r /root ]; then
  pass privileged-host-secrets-unreadable
else
  fail privileged-host-secrets-unreadable
fi

if env | cut -d= -f1 | grep -Eq '^(AWS_|AZURE_|AETHER_.*(TOKEN|SECRET|KEY)|SSH_AUTH_SOCK|DOCKER_HOST)'; then
  fail custom-secret-environment-absent
else
  pass custom-secret-environment-absent
fi

other_home_readable=false
while IFS= read -r candidate; do
  [ "$candidate" = "$HOME" ] && continue
  if [ -r "$candidate" ]; then other_home_readable=true; break; fi
done < <(find /home -mindepth 1 -maxdepth 1 -type d 2>/dev/null || true)
if [ "$other_home_readable" = false ]; then
  pass other-worker-homes-unreadable
else
  fail other-worker-homes-unreadable
fi

cgroup_rel="$(awk -F: '$1 == "0" {print $3}' /proc/self/cgroup)"
cgroup_base="/sys/fs/cgroup"
cgroup_cursor="${cgroup_base}${cgroup_rel}"
memory_limited=false
tasks_limited=false
case "$cgroup_cursor" in
  "$cgroup_base"|"$cgroup_base"/*) ;;
  *) cgroup_cursor="" ;;
esac
while [ -n "$cgroup_cursor" ]; do
  if [ -r "$cgroup_cursor/memory.max" ]; then
    memory_max="$(cat "$cgroup_cursor/memory.max")"
    if [[ "$memory_max" =~ ^[0-9]+$ ]] && [ "$memory_max" -gt 0 ]; then
      memory_limited=true
    fi
  fi
  if [ -r "$cgroup_cursor/pids.max" ]; then
    pids_max="$(cat "$cgroup_cursor/pids.max")"
    if [[ "$pids_max" =~ ^[0-9]+$ ]] && [ "$pids_max" -gt 0 ]; then
      tasks_limited=true
    fi
  fi
  [ "$cgroup_cursor" = "$cgroup_base" ] && break
  cgroup_cursor="${cgroup_cursor%/*}"
done
if [ "$memory_limited" = true ] && [ "$tasks_limited" = true ]; then
  pass cgroup-memory-and-task-limits
else
  fail cgroup-memory-and-task-limits
fi

sleep 600 &
smoke_pid=$!
kill "$smoke_pid"
wait "$smoke_pid" 2>/dev/null || true
if ! kill -0 "$smoke_pid" 2>/dev/null; then
  pass job-process-cleanup
else
  fail job-process-cleanup
fi

pass artifact-payload-created

export AETHER_SMOKE_PASSES="$(IFS=,; echo "${passes[*]}")"
export AETHER_SMOKE_FAILURES="$(IFS=,; echo "${failures[*]}")"
python - <<'PY'
import json
import os

passes = [item for item in os.environ["AETHER_SMOKE_PASSES"].split(",") if item]
failures = [item for item in os.environ["AETHER_SMOKE_FAILURES"].split(",") if item]
print(json.dumps({
    "schema_version": 1,
    "result": "PASS" if not failures else "FAIL",
    "passed": sorted(passes),
    "failed": sorted(failures),
}, sort_keys=True))
raise SystemExit(1 if failures else 0)
PY
