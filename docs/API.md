# Aether Browser API v1 contract

The v0.1 runtime exposes one closed JSON API. Route names are intentionally stable and unprefixed; every request and response carries `api_version: "v1"`. Unknown fields are rejected.

## Transport and authority

Use `Authorization: Bearer <token>`. In authenticated mode, the observer token permits health and snapshot access; the controller token permits create, navigate, interact, and end. Tokens never appear in URLs, responses, logs, screenshots, or examples. Strict loopback local mode may run without tokens only when remote mode is disabled and both API and noVNC listeners are loopback-bound.

| Route | Observer | Controller |
|---|---:|---:|
| `GET /browser/health` | yes | yes |
| `POST /browser/session/create` | no | yes |
| `POST /browser/navigate` | no | yes |
| `POST /browser/snapshot` | yes | yes |
| `POST /browser/interact` | no | yes |
| `POST /browser/session/end` | no | yes |

## Bounds

- URL and selector: 2,048 characters.
- Title: 512 characters.
- Typed input: 16,384 characters.
- Readable text: 65,536 characters.
- Accessibility snapshot: 500 flattened nodes.
- Encoded PNG screenshot: 14,000,000 base64 characters.
- Vision budget: 1–100 snapshots, default 25.
- Coordinates: 0–4,095; scroll delta: -10,000–10,000.
- Capacity retry guidance: 1–300 seconds.

## Session routes

`POST /browser/session/create` accepts an optional `max_vision_steps`. It returns a UUID session ID, loopback view URL, UTC creation time, and absolute UTC expiry. A second create returns HTTP 503 with `SESSION_CAPACITY_REACHED` and bounded retry guidance.

`POST /browser/session/end` is idempotent. A repeated call returns `already_ended` without resurrecting state.

## Navigation and state

`POST /browser/navigate` accepts `session_id` and an HTTP(S) URL. Navigation policy is evaluated separately from schema validation and rejects credentials, unsafe schemes, blocked address classes, unsafe redirects, and DNS rebinding. Responses contain the final URL, bounded title and readable text, a flattened bounded accessibility snapshot, and a UTC timestamp.

`POST /browser/snapshot` atomically consumes one vision step and increments the session sequence. It returns bounded structured state, a base64 PNG, viewport metadata, counters, and capture time.

## Interaction

Only `click`, `type`, `scroll`, and `press` exist. Click/type require either a selector or an x/y pair, never both. Scroll accepts nonzero bounded deltas. Press accepts only the enumerated keys and combinations. There is no arbitrary JavaScript, CDP, upload, clipboard, download, extension, shell, filesystem, credential, or cookie field.

## Error envelope

Errors use:

```json
{
  "api_version": "v1",
  "status": "error",
  "error": {
    "code": "SESSION_NOT_FOUND",
    "message": "Session was not found."
  }
}
```

Stable codes cover authentication, authorization, capacity, session lifecycle, vision budget, URL policy, interaction validation, browser readiness, and bounded internal failures. Schema errors use HTTP 422; authentication failures use 401/403; capacity uses 503; missing sessions use 404; policy conflicts use 400/403; bounded internal failures use 500 without process or host details.
