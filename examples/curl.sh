#!/bin/sh
set -eu

api_base="http://127.0.0.1:8092"
session_id=""

cleanup() {
  if [ -n "$session_id" ]; then
    curl -fsS -X POST "$api_base/browser/session/end" \
      -H 'Content-Type: application/json' \
      -d "{\"api_version\":\"v1\",\"session_id\":\"$session_id\"}" >/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 1; }

curl -fsS "$api_base/browser/health" | jq .

session_id="$(
  curl -fsS -X POST "$api_base/browser/session/create" \
    -H 'Content-Type: application/json' \
    -d '{"api_version":"v1"}' |
    jq -er '.session_id'
)"
printf 'session_id=%s\n' "$session_id"

curl -fsS -X POST "$api_base/browser/navigate" \
  -H 'Content-Type: application/json' \
  -d "{\"api_version\":\"v1\",\"session_id\":\"$session_id\",\"url\":\"https://example.com\"}" |
  jq '{status, final_url, title, readable_text}'

curl -fsS -X POST "$api_base/browser/snapshot" \
  -H 'Content-Type: application/json' \
  -d "{\"api_version\":\"v1\",\"session_id\":\"$session_id\"}" |
  jq '{status, url, title, sequence, vision_steps_remaining, screenshot_base64_chars: (.screenshot_base64 | length)}'
