#!/usr/bin/env bash
set -Eeuo pipefail

if ! command -v podman >/dev/null 2>&1; then
  echo '{"result":"FAIL","reason":"the Podman remote client is required"}'
  exit 1
fi
podman_cli=(podman --remote)
default_podman_socket="/run/aether-ci-browser-podman.sock"
podman_socket="$default_podman_socket"
if [ -n "${AETHER_ACCEPTANCE_EPHEMERAL_PODMAN_SOCKET:-}" ]; then
  if [ "${GITHUB_ACTIONS:-}" != "true" ] || [ -z "${RUNNER_TEMP:-}" ]; then
    echo '{"result":"FAIL","reason":"ephemeral Podman override is not authorized"}'
    exit 1
  fi
  case "$AETHER_ACCEPTANCE_EPHEMERAL_PODMAN_SOCKET" in
    ("$RUNNER_TEMP"/*)
      podman_socket="$AETHER_ACCEPTANCE_EPHEMERAL_PODMAN_SOCKET"
      ;;
    (*)
      echo '{"result":"FAIL","reason":"ephemeral Podman socket is outside runner temp"}'
      exit 1
      ;;
  esac
fi
expected_container_host="unix://${podman_socket}"
if [ "${CONTAINER_HOST:-}" != "$expected_container_host" ]; then
  echo '{"result":"FAIL","reason":"the private Podman service is not configured"}'
  exit 1
fi
if [ ! -S "$podman_socket" ] \
  || [ "$(stat -Lc '%u:%g:%a' "$podman_socket" 2>/dev/null || true)" \
    != "$(id -u):$(id -g):600" ]; then
  echo '{"result":"FAIL","reason":"the private Podman socket identity is invalid"}'
  exit 1
fi
if [ "$("${podman_cli[@]}" info --format '{{.Host.Security.Rootless}}' 2>/dev/null || true)" != "true" ]; then
  echo '{"result":"FAIL","reason":"the configured Podman service is not rootless"}'
  exit 1
fi

run_key="${GITHUB_RUN_ID:-local}-$$"
run_key="${run_key//[^A-Za-z0-9_.-]/-}"
pod="agent-browser-acceptance-${run_key}"
browser="agent-browser-api-${run_key}"
fixture="agent-browser-fixture-${run_key}"
work_dir="$(mktemp -d)"
api_port="${AETHER_ACCEPTANCE_API_PORT:-18092}"
novnc_port="${AETHER_ACCEPTANCE_NOVNC_PORT:-16080}"
fixture_port="${AETHER_ACCEPTANCE_FIXTURE_PORT:-18080}"
expected_sha="${GITHUB_SHA:-}"
expected_image_id="${AETHER_ACCEPTANCE_IMAGE_ID:-}"
capture_dir="${AETHER_ACCEPTANCE_CAPTURE_DIR:-}"
pod_created=false

cleanup() {
  status=$?
  trap - EXIT INT TERM
  set +e
  if [ "$pod_created" = true ]; then
    if [ "$status" -ne 0 ]; then
      "${podman_cli[@]}" logs "$fixture" >&2
      "${podman_cli[@]}" logs "$browser" >&2
    fi
    "${podman_cli[@]}" pod rm -f "$pod" >/dev/null 2>&1
    pod_created=false
  fi
  rm -rf -- "$work_dir"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

image_hash="${expected_image_id#sha256:}"
if [ "${#expected_sha}" -ne 40 ] \
  || [ "${#expected_image_id}" -ne 71 ] \
  || [ "${#image_hash}" -ne 64 ]; then
  echo '{"result":"FAIL","reason":"exact commit and image identity are required"}'
  exit 1
fi
case "${expected_sha}${image_hash}" in
  (*[!0-9a-f]*)
    echo '{"result":"FAIL","reason":"commit and image identities must be lowercase hex"}'
    exit 1
    ;;
esac
image_tag="localhost/agent-browser:${expected_sha}"
actual_image_id="$(
  "${podman_cli[@]}" image inspect "$image_tag" --format '{{.Id}}' 2>/dev/null || true
)"
actual_image_hash="${actual_image_id#sha256:}"
if [ "$actual_image_hash" != "$image_hash" ]; then
  echo '{"result":"FAIL","reason":"the exact prebuilt image is absent or mismatched"}'
  exit 1
fi

read -r nonce color controller_token observer_token < <(python - <<'PY'
import hashlib
import secrets

nonce = secrets.token_hex(16)
digest = hashlib.sha256(nonce.encode()).hexdigest()
# Keep every color channel away from browser chrome's near-black/near-white palette.
channels = [48 + (int(digest[index:index + 2], 16) % 160) for index in (0, 2, 4)]
controller = "Aa1!" + secrets.token_urlsafe(32)
observer = "Aa1!" + secrets.token_urlsafe(32)
print(nonce, "".join(f"{channel:02x}" for channel in channels), controller, observer)
PY
)

cat > "$work_dir/fixture_server.py" <<'PY'
from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NONCE = os.environ["AETHER_FIXTURE_NONCE"]
COLOR = os.environ["AETHER_FIXTURE_COLOR"]
PORT = int(os.environ["AETHER_FIXTURE_PORT"])
HTML = f"""<!doctype html>
<html><head><title>Aether deterministic fixture</title>
<style>
html, body {{ margin: 0; min-height: 1800px; background: #{COLOR}; color: #101010; }}
#proof {{ font: 32px sans-serif; padding: 40px; }}
</style></head><body>
<main id="proof">visual-proof-{NONCE}</main>
<button id="button" onclick="this.textContent='clicked'">click me</button>
<input id="field" aria-label="acceptance input">
<a id="popup" href="/popup-target" target="_blank">popup</a>
<a id="download" href="/download" download>download</a>
</body></html>""".encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/redirect-private":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
            return
        if self.path == "/download":
            self.send_response(200)
            self.send_header("Content-Disposition", "attachment; filename=blocked.txt")
            self.end_headers()
            self.wfile.write(b"must not be persisted")
            return
        body = HTML if self.path in {"/", "/popup-target"} else b"not found"
        self.send_response(200 if self.path in {"/", "/popup-target"} else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
PY

# The fixture pod must never receive a managed /etc/hosts. Podman 5 spells that
# --hosts-file none and Podman 4 spells it --no-hosts, and hosted runner images have
# shipped both. Detect the supported spelling instead of pinning one, and fail closed
# if neither exists rather than quietly creating a pod with host entries.
pod_hosts_flag=""
pod_create_help="$("${podman_cli[@]}" pod create --help 2>/dev/null || true)"
if printf '%s' "$pod_create_help" | grep -q -- "--hosts-file"; then
  pod_hosts_flag="--hosts-file=none"
elif printf '%s' "$pod_create_help" | grep -q -- "--no-hosts"; then
  pod_hosts_flag="--no-hosts"
else
  echo "podman pod create supports neither --hosts-file nor --no-hosts" >&2
  exit 1
fi

"${podman_cli[@]}" pod create \
  --name "$pod" --network none --share net "$pod_hosts_flag" \
  --cpus=2 --memory=2g >/dev/null
pod_created=true

"${podman_cli[@]}" run -d --name "$fixture" --pod "$pod" \
  --read-only --cap-drop=all --security-opt=no-new-privileges --pids-limit=64 \
  --memory=256m --cpus=0.5 --tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m \
  -e "AETHER_FIXTURE_NONCE=$nonce" \
  -e "AETHER_FIXTURE_COLOR=$color" \
  -e "AETHER_FIXTURE_PORT=$fixture_port" \
  --pull=never --http-proxy=false --entrypoint python "$expected_image_id" \
  -I -c "$(<"$work_dir/fixture_server.py")" >/dev/null

"${podman_cli[@]}" run -d --name "$browser" --pod "$pod" \
  --read-only --cap-drop=all --security-opt=no-new-privileges --pids-limit=512 \
  --memory=2g --cpus=2 --shm-size=1g \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=1g \
  --tmpfs /home/agent/.cache:rw,noexec,nosuid,nodev,size=256m \
  -e AGENT_BROWSER_API_BIND=127.0.0.1 \
  -e AGENT_BROWSER_API_HOST=127.0.0.1 \
  -e "AGENT_BROWSER_API_PORT=$api_port" \
  -e AGENT_BROWSER_CONTAINER_MODE=1 \
  -e AGENT_BROWSER_NOVNC_BIND=127.0.0.1 \
  -e AGENT_BROWSER_NOVNC_HOST=127.0.0.1 \
  -e "AGENT_BROWSER_NOVNC_PORT=$novnc_port" \
  -e "AGENT_BROWSER_VIEW_URL=http://127.0.0.1:${novnc_port}/vnc.html" \
  -e AGENT_BROWSER_REMOTE_MODE=0 \
  -e AGENT_BROWSER_REVERSE_PROXY_EXPOSED=0 \
  -e AGENT_BROWSER_TEST_MODE=1 \
  -e "AGENT_BROWSER_TEST_ORIGINS=http://127.0.0.1:${fixture_port}" \
  -e "AGENT_BROWSER_CONTROLLER_TOKEN=$controller_token" \
  -e "AGENT_BROWSER_OBSERVER_TOKEN=$observer_token" \
  --pull=never --http-proxy=false "$expected_image_id" >/dev/null

api_base="http://127.0.0.1:${api_port}"
novnc_base="http://127.0.0.1:${novnc_port}"
fixture_origin="http://127.0.0.1:${fixture_port}"

# same-pod-namespace-proof: both workload containers must be members of the
# one isolated pod before any request is attempted.
browser_pod="$("${podman_cli[@]}" inspect --format '{{.Pod}}' "$browser")"
fixture_pod="$("${podman_cli[@]}" inspect --format '{{.Pod}}' "$fixture")"
if [ -z "$browser_pod" ] || [ "$browser_pod" != "$fixture_pod" ]; then
  echo '{"result":"FAIL","reason":"fixture and Browser do not share one Podman pod"}'
  exit 1
fi
browser_image="$("${podman_cli[@]}" inspect --format '{{.Image}}' "$browser")"
fixture_image="$("${podman_cli[@]}" inspect --format '{{.Image}}' "$fixture")"
if [ "${browser_image#sha256:}" != "$image_hash" ] \
  || [ "${fixture_image#sha256:}" != "$image_hash" ]; then
  echo '{"result":"FAIL","reason":"a workload does not use the handed-off image ID"}'
  exit 1
fi

# pod-network-none-proof: `--network none` must result in a namespace with no
# interface except loopback. Run the proof in both workload containers.
for container in "$fixture" "$browser"; do
  "${podman_cli[@]}" exec -i "$container" python - <<'PY'
from pathlib import Path

interfaces = {path.name for path in Path("/sys/class/net").iterdir()}
if interfaces != {"lo"}:
    raise SystemExit("acceptance pod has a non-loopback network interface")
PY
done

ready=false
for _ in $(seq 1 120); do
  if "${podman_cli[@]}" exec -i "$fixture" \
    python - "$api_base" "$novnc_base" "$fixture_origin" "$observer_token" \
      <<'PY' >/dev/null 2>&1
import json
import socket
import sys
import urllib.request

health_request = urllib.request.Request(
    sys.argv[1] + "/browser/health",
    headers={"Authorization": f"Bearer {sys.argv[4]}"},
)
with urllib.request.urlopen(health_request, timeout=1) as response:
    body = json.load(response)
with urllib.request.urlopen(sys.argv[2] + "/vnc.html", timeout=1) as response:
    novnc_ready = response.status == 200
with urllib.request.urlopen(sys.argv[3] + "/", timeout=1) as response:
    fixture_ready = response.status == 200
with socket.create_connection(("127.0.0.1", 5900), timeout=1) as connection:
    banner = b""
    while len(banner) < 12:
        chunk = connection.recv(12 - len(banner))
        if not chunk:
            break
        banner += chunk
    vnc_ready = banner.startswith(b"RFB ")
raise SystemExit(
    0 if body.get("status") == "ok" and novnc_ready and fixture_ready and vnc_ready else 1
)
PY
  then ready=true; break; fi
  sleep 0.5
done
if [ "$ready" != true ]; then
  "${podman_cli[@]}" logs "$fixture" >&2
  "${podman_cli[@]}" logs "$browser" >&2
  echo '{"result":"FAIL","reason":"runtime did not become healthy"}'
  exit 1
fi

# Live loopback-listener-proof: every service must remain on numeric IPv4
# loopback even inside the isolated pod namespace.
"${podman_cli[@]}" exec -i "$browser" python - "$api_port" "$novnc_port" 5900 <<'PY'
import sys
from pathlib import Path


def listening_addresses(path: str, port: int) -> set[str]:
    addresses: set[str] = set()
    source = Path(path)
    if not source.exists():
        return addresses
    for line in source.read_text(encoding="ascii").splitlines()[1:]:
        columns = line.split()
        address, encoded_port = columns[1].rsplit(":", 1)
        if columns[3] == "0A" and int(encoded_port, 16) == port:
            addresses.add(address)
    return addresses


for service, raw_port in zip(("api", "novnc", "vnc"), sys.argv[1:], strict=True):
    port = int(raw_port)
    ipv4 = listening_addresses("/proc/net/tcp", port)
    ipv6 = listening_addresses("/proc/net/tcp6", port)
    if ipv4 != {"0100007F"} or ipv6:
        raise SystemExit(
            f"{service} listener is not exclusively IPv4 loopback "
            f"(ipv4={sorted(ipv4)!r}, ipv6={sorted(ipv6)!r})"
        )
PY

"${podman_cli[@]}" exec -i "$fixture" python - \
  "$api_base" "$novnc_base" "$fixture_origin" \
  "$controller_token" "$observer_token" "$nonce" \
  /tmp/api.png /tmp/session-id <<'PY'
from __future__ import annotations

import base64
import json
import os
import socket
import sys
import urllib.error
import urllib.request

(
    api_base,
    novnc_base,
    fixture_origin,
    controller,
    observer,
    nonce,
    screenshot_path,
    session_path,
) = sys.argv[1:]


def call(path: str, body: dict[str, object], token: str) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(
        api_base + path,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


with urllib.request.urlopen(novnc_base + "/vnc.html", timeout=5) as response:
    assert response.status == 200 and b"noVNC" in response.read(200_000)

host = "127.0.0.1"
port = int(novnc_base.rsplit(":", 1)[1])
key = base64.b64encode(os.urandom(16)).decode()
with socket.create_connection((host, port), timeout=5) as connection:
    request = (
        "GET /websockify HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    )
    connection.sendall(request.encode())
    assert b" 101 " in connection.recv(4096).split(b"\r\n", 1)[0]

status, created = call("/browser/session/create", {"max_vision_steps": 25}, controller)
assert status == 200, (status, created)
session_id = created["session_id"]
open(session_path, "w", encoding="utf-8").write(str(session_id))

status, capacity = call("/browser/session/create", {}, controller)
assert status == 503 and capacity["error"]["code"] == "SESSION_CAPACITY_REACHED"

status, refused = call(
    "/browser/navigate", {"session_id": session_id, "url": fixture_origin + "/"}, observer
)
assert status == 403 and refused["error"]["code"] == "AUTH_FORBIDDEN"

status, navigated = call(
    "/browser/navigate", {"session_id": session_id, "url": fixture_origin + "/"}, controller
)
assert status == 200 and f"visual-proof-{nonce}" in navigated["readable_text"]

status, direct_ssrf = call(
    "/browser/navigate",
    {"session_id": session_id, "url": "http://169.254.169.254/latest/meta-data/"},
    controller,
)
assert status in {400, 403} and direct_ssrf["status"] == "error"

status, redirected_ssrf = call(
    "/browser/navigate",
    {"session_id": session_id, "url": fixture_origin + "/redirect-private"},
    controller,
)
assert status == 403 and redirected_ssrf["error"]["code"] == "DESTINATION_BLOCKED"

# A refused redirect must not discard the original safe page.
status, navigated = call(
    "/browser/navigate", {"session_id": session_id, "url": fixture_origin + "/"}, controller
)
assert status == 200

status, snapshot = call("/browser/snapshot", {"session_id": session_id}, observer)
assert status == 200 and f"visual-proof-{nonce}" in snapshot["readable_text"]
open(screenshot_path, "wb").write(base64.b64decode(snapshot["screenshot_base64"], validate=True))

actions = (
    {"action": "click", "target": {"selector": "#button"}},
    {"action": "type", "target": {"selector": "#field"}, "text": " acceptance text "},
    {"action": "press", "key": "Enter"},
    {"action": "scroll", "delta_y": 500},
)
for action in actions:
    status, result = call("/browser/interact", {"session_id": session_id, **action}, controller)
    assert status == 200 and result["status"] == "interacted", (status, result)

status, second_snapshot = call("/browser/snapshot", {"session_id": session_id}, observer)
assert status == 200 and second_snapshot["sequence"] > snapshot["sequence"]

status, popup = call(
    "/browser/interact",
    {"session_id": session_id, "action": "click", "target": {"selector": "#popup"}},
    controller,
)
assert status in {200, 400, 403}, (status, popup)

status, download = call(
    "/browser/interact",
    {"session_id": session_id, "action": "click", "target": {"selector": "#download"}},
    controller,
)
assert status in {200, 400, 403}, (status, download)
if status == 200:
    assert download["status"] == "interacted", download

PY

download_path="$(
  "${podman_cli[@]}" exec "$browser" \
    find /tmp /home/agent -type f -name blocked.txt -print -quit
)"
if [ -n "$download_path" ]; then
  echo '{"result":"FAIL","reason":"a refused download persisted in browser-writable storage"}'
  exit 1
fi

"${podman_cli[@]}" exec "$browser" scrot -o /tmp/display-proof.png
"${podman_cli[@]}" exec "$fixture" base64 /tmp/api.png \
  | base64 --decode > "$work_dir/api.png"
"${podman_cli[@]}" exec "$browser" base64 /tmp/display-proof.png \
  | base64 --decode > "$work_dir/display.png"
if [ -n "$capture_dir" ]; then
  case "$capture_dir" in
    (artifacts/*) ;;
    (*)
      echo '{"result":"FAIL","reason":"capture output must remain below artifacts"}'
      exit 1
      ;;
  esac
  mkdir -p -- "$capture_dir"
  install -m 0644 "$work_dir/api.png" "$capture_dir/api-before.png"
  install -m 0644 "$work_dir/display.png" "$capture_dir/display-after.png"
fi
if ! "${podman_cli[@]}" exec "$browser" \
  sh -c "ps -eo args | grep -q '[x]11vnc -display :99'"; then
  echo '{"result":"FAIL","reason":"x11vnc is not attached to the browser display"}'
  exit 1
fi

python - "$work_dir/api.png" "$work_dir/display.png" "$color" <<'PY'
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path


def rgb_pixels(path: str) -> list[tuple[int, int, int]]:
    data = Path(path).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    position = 8
    chunks: list[bytes] = []
    width = height = color_type = bit_depth = 0
    while position < len(data):
        length = struct.unpack(">I", data[position : position + 4])[0]
        kind = data[position + 4 : position + 8]
        payload = data[position + 8 : position + 8 + length]
        position += length + 12
        if kind == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", payload[:10])
        elif kind == b"IDAT":
            chunks.append(payload)
        elif kind == b"IEND":
            break
    assert bit_depth == 8 and color_type in {2, 6}
    channels = 3 if color_type == 2 else 4
    stride = width * channels
    raw = zlib.decompress(b"".join(chunks))
    rows: list[bytearray] = []
    offset = 0
    for _ in range(height):
        filter_type = raw[offset]
        current = bytearray(raw[offset + 1 : offset + 1 + stride])
        offset += stride + 1
        previous = rows[-1] if rows else bytearray(stride)
        for index in range(stride):
            left = current[index - channels] if index >= channels else 0
            up = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                current[index] = (current[index] + left) & 255
            elif filter_type == 2:
                current[index] = (current[index] + up) & 255
            elif filter_type == 3:
                current[index] = (current[index] + ((left + up) // 2)) & 255
            elif filter_type == 4:
                estimate = left + up - upper_left
                distances = (abs(estimate - left), abs(estimate - up), abs(estimate - upper_left))
                current[index] = (current[index] + (left, up, upper_left)[distances.index(min(distances))]) & 255
            elif filter_type != 0:
                raise AssertionError(f"unsupported PNG filter {filter_type}")
        rows.append(current)
    return [tuple(row[index : index + 3]) for row in rows for index in range(0, stride, channels)]


target = tuple(bytes.fromhex(sys.argv[3]))
api_count = rgb_pixels(sys.argv[1]).count(target)
display_count = rgb_pixels(sys.argv[2]).count(target)
assert api_count > 10_000, api_count
assert display_count > 10_000, display_count
PY

"${podman_cli[@]}" exec -i "$fixture" \
  python - "$api_base" "$controller_token" /tmp/session-id <<'PY'
import json
import sys
import urllib.request

api_base, token, session_path = sys.argv[1:]
session_id = open(session_path, encoding="utf-8").read().strip()


def end() -> dict[str, object]:
    request = urllib.request.Request(
        api_base + "/browser/session/end",
        data=json.dumps({"session_id": session_id}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        assert response.status == 200
        return json.load(response)


assert end()["status"] == "ended"
assert end()["status"] == "already_ended"
PY

if "${podman_cli[@]}" exec "$browser" \
  sh -c "pgrep -f '[c]hrome|[c]hromium' >/dev/null"; then
  echo '{"result":"FAIL","reason":"browser process survived idempotent session end"}'
  exit 1
fi
if "${podman_cli[@]}" exec "$browser" \
  sh -c "find /tmp -maxdepth 1 -type d -name 'agent-browser-*' -print -quit | grep -q ."; then
  echo '{"result":"FAIL","reason":"temporary browser profile survived session end"}'
  exit 1
fi

"${podman_cli[@]}" pod rm -f "$pod" >/dev/null
pod_created=false
if "${podman_cli[@]}" pod inspect "$pod" >/dev/null 2>&1; then
  echo '{"result":"FAIL","reason":"isolated acceptance pod survived cleanup"}'
  exit 1
fi

mkdir -p artifacts
python - \
  "$nonce" "$color" "$expected_sha" "$expected_image_id" \
  > artifacts/acceptance.json <<'PY'
import hashlib
import json
import sys

# Retain only a digest of the per-run nonce; no bearer values are recorded.
print(json.dumps({
    "schema_version": 1,
    "result": "PASS",
    "checks": [
        "exact-image-id-handoff", "runtime-image-id-proof", "remote-podman",
        "isolated-pod-network-none", "resource-bounded-pod",
        "same-pod-namespace", "loopback-fixture", "in-namespace-http-driver",
        "health", "session-create", "capacity-rejection",
        "observer-refusal", "controller-success", "navigation", "snapshot", "click", "type",
        "press", "scroll", "second-snapshot", "direct-ssrf-refusal", "redirect-refusal",
        "download-non-persistence", "popup-bounded", "novnc-web", "novnc-websocket",
        "vnc-rfb-readiness",
        "loopback-listener-proof",
        "same-display-color-proof", "idempotent-end", "process-cleanup", "profile-cleanup",
        "pod-cleanup",
    ],
    "visual_nonce_sha256": hashlib.sha256(sys.argv[1].encode()).hexdigest(),
    "proof_color": "#" + sys.argv[2],
    "tested_commit": sys.argv[3],
    "image_id": sys.argv[4],
}, sort_keys=True))
PY
cat artifacts/acceptance.json
