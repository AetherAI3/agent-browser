#!/bin/sh
set -eu

children=""
novnc_bind="${AGENT_BROWSER_NOVNC_BIND:-127.0.0.1}"
novnc_port="${AGENT_BROWSER_NOVNC_PORT:-6080}"

# noVNC is intentionally unauthenticated in v0.1. Refuse every configurable
# exposure wider than numeric IPv4 loopback instead of relying on port publishing.
if [ "$novnc_bind" != "127.0.0.1" ]; then
  echo "AGENT_BROWSER_NOVNC_BIND must be exactly 127.0.0.1 in v0.1" >&2
  exit 1
fi

stop_children() {
  trap - TERM INT EXIT
  for child in $children; do
    kill -TERM "$child" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}

trap stop_children TERM INT EXIT

Xvfb :99 -screen 0 1280x720x24 -nolisten tcp -ac &
children="$children $!"

display_attempt=0
while [ ! -S /tmp/.X11-unix/X99 ]; do
  display_attempt=$((display_attempt + 1))
  if [ "$display_attempt" -gt 100 ]; then
    echo "display failed to become ready" >&2
    exit 1
  fi
  sleep 0.1
done

x11vnc -display :99 -listen 127.0.0.1 -no6 -noipv6 \
  -rfbport 5900 -rfbportv6 -1 -httpportv6 -1 \
  -forever -shared -nopw -quiet &
children="$children $!"

websockify --web=/usr/share/novnc "$novnc_bind:$novnc_port" 127.0.0.1:5900 &
children="$children $!"

python -m agent_browser.main &
api_pid=$!
children="$children $api_pid"

wait "$api_pid"
