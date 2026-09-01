#!/usr/bin/env python3
"""Drive one local Aether Browser session with only the Python standard library."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def validate_api_base(value: str) -> str:
    """Return a canonical HTTP origin for an explicit numeric-loopback listener."""

    message = "--api-base must be an HTTP numeric-loopback root origin with an explicit port"
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
        address = ipaddress.ip_address(parsed.hostname or "")
    except (TypeError, ValueError) as exc:
        raise ValueError(message) from exc

    if (
        parsed.scheme != "http"
        or not address.is_loopback
        or getattr(address, "ipv4_mapped", None) is not None
        or getattr(address, "scope_id", None) is not None
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(message)

    host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    return f"http://{host}:{port}"


def request_json(
    api_base: str,
    path: str,
    *,
    body: dict[str, object] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    api_origin = validate_api_base(api_base)
    headers = {"Accept": "application/json"}
    data = None
    method = "GET"
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        method = "POST"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # The URL origin is canonicalized above without DNS resolution or userinfo.
    request = urllib.request.Request(  # noqa: S310
        api_origin + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{path} returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"could not reach {api_origin}: {exc.reason}") from exc


def print_result(label: str, payload: dict[str, Any]) -> None:
    print(f"\n{label}")
    print(json.dumps(payload, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-base",
        default=os.environ.get("AETHER_BROWSER_API_BASE", "http://127.0.0.1:8092"),
        help="numeric-loopback API origin",
    )
    parser.add_argument(
        "--url",
        default="https://example.com/",
        help="HTTP(S) page to open in the shared headed browser",
    )
    parser.add_argument(
        "--controller-token",
        default=os.environ.get("AETHER_BROWSER_CONTROLLER_TOKEN"),
        help="controller bearer token when authenticated mode is enabled",
    )
    parser.add_argument(
        "--observer-token",
        default=os.environ.get("AETHER_BROWSER_OBSERVER_TOKEN"),
        help="observer bearer token when authenticated mode is enabled",
    )
    args = parser.parse_args()

    session_id: str | None = None
    controller = args.controller_token
    observer = args.observer_token or controller
    try:
        health = request_json(args.api_base, "/browser/health", token=observer)
        print_result("health", health)

        created = request_json(
            args.api_base,
            "/browser/session/create",
            body={"api_version": "v1", "max_vision_steps": 3},
            token=controller,
        )
        session_id = str(created["session_id"])
        print_result("session created", created)
        print("\nOpen the returned view_url in a local browser to watch this same session.")

        navigated = request_json(
            args.api_base,
            "/browser/navigate",
            body={"api_version": "v1", "session_id": session_id, "url": args.url},
            token=controller,
        )
        print_result(
            "navigated",
            {
                "status": navigated.get("status"),
                "final_url": navigated.get("final_url"),
                "title": navigated.get("title"),
                "readable_text": navigated.get("readable_text"),
            },
        )

        snapshot = request_json(
            args.api_base,
            "/browser/snapshot",
            body={"api_version": "v1", "session_id": session_id},
            token=observer,
        )
        screenshot = snapshot.pop("screenshot_base64", "")
        snapshot["screenshot_base64_chars"] = len(screenshot)
        print_result("structured snapshot", snapshot)
    finally:
        if session_id is not None:
            ended = request_json(
                args.api_base,
                "/browser/session/end",
                body={"api_version": "v1", "session_id": session_id},
                token=controller,
            )
            print_result("session ended", ended)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, RuntimeError, ValueError) as exc:
        print(f"demo failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
