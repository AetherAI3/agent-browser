from __future__ import annotations

# ruff: noqa: S104 -- unspecified addresses are deliberate SSRF test inputs.
import asyncio
from collections.abc import Iterable

import pytest

from agent_browser.policy import (
    NavigationGuard,
    NavigationPolicy,
    PolicyConfigurationError,
    PolicyError,
    PolicyReason,
)

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V4_ALT = "8.8.8.8"
PUBLIC_V6 = "2606:4700:4700::1111"


class FakeResolver:
    def __init__(self, answers: dict[str, Iterable[str]]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    async def __call__(self, hostname: str) -> Iterable[str]:
        self.calls.append(hostname)
        return self.answers[hostname]


class SequencedResolver:
    def __init__(self, answers: dict[str, list[list[str]]]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    async def __call__(self, hostname: str) -> Iterable[str]:
        self.calls.append(hostname)
        sequence = self.answers[hostname]
        index = min(self.calls.count(hostname) - 1, len(sequence) - 1)
        return sequence[index]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://example.com", "http://example.com"),
        ("HTTPS://EXAMPLE.COM./path?q=1", "https://example.com/path?q=1"),
        ("https://example.com:8443/path", "https://example.com:8443/path"),
    ],
)
async def test_valid_http_and_https_urls_are_normalized(url: str, expected: str) -> None:
    resolver = FakeResolver({"example.com": [PUBLIC_V4, PUBLIC_V6]})
    result = await NavigationPolicy(resolver).validate_url(url)
    assert result.url == expected
    assert result.resolved_address_count == 2
    assert resolver.calls == ["example.com"]


@pytest.mark.asyncio
async def test_public_ip_literal_is_allowed_without_dns() -> None:
    resolver = FakeResolver({})
    result = await NavigationPolicy(resolver).validate_url("https://8.8.8.8/dns")
    assert result.hostname == "8.8.8.8"
    assert resolver.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "",
        " https://example.com",
        "https://example.com ",
        "https://example.com/\x00",
        "https://example.com/%0d%0aX-Evil:yes",
        "https://example.com/%zz",
        "https://example.com/#fragment",
        "https://user@example.com",
        "https://user:password@example.com",
        "https://example.com\\@127.0.0.1",
        "https:///missing-host",
        "https://example.com:99999",
        "https://[v1.foo]/",
        "https://*.example.com",
        "x" * 2_049,
    ],
)
async def test_empty_oversized_malformed_and_credential_urls_are_rejected(url: str) -> None:
    with pytest.raises(PolicyError) as raised:
        await NavigationPolicy(FakeResolver({"example.com": [PUBLIC_V4]})).validate_url(url)
    assert raised.value.code == "INVALID_URL"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,hello",
        "blob:https://example.com/id",
        "chrome://settings",
        "chrome-extension://abc/page.html",
        "devtools://devtools",
        "ftp://example.com/file",
        "custom://example.com",
    ],
)
async def test_unsafe_schemes_are_rejected(url: str) -> None:
    with pytest.raises(PolicyError) as raised:
        await NavigationPolicy(FakeResolver({})).validate_url(url)
    assert raised.value.reason is PolicyReason.UNSUPPORTED_SCHEME


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "0.0.0.0",
        "10.0.0.1",
        "100.64.0.1",
        "100.100.100.200",
        "127.0.0.1",
        "169.254.1.1",
        "169.254.169.254",
        "172.16.0.1",
        "192.168.1.1",
        "192.0.2.1",
        "192.0.0.9",
        "192.88.99.1",
        "198.18.0.1",
        "198.51.100.1",
        "203.0.113.1",
        "224.0.0.1",
        "240.0.0.1",
        "255.255.255.255",
    ],
)
async def test_prohibited_ipv4_ranges_are_rejected(address: str) -> None:
    with pytest.raises(PolicyError) as raised:
        await NavigationPolicy(FakeResolver({})).validate_url(f"http://{address}/")
    assert raised.value.code == "DESTINATION_BLOCKED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "::",
        "::1",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "2001:db8::1",
        "::ffff:127.0.0.1",
        "::ffff:8.8.8.8",
        "64:ff9b::7f00:1",
        "64:ff9b:1::1",
        "100::1",
        "100:0:0:1::1",
        "2001::1",
        "2001:2::1",
        "2001:10::1",
        "2002:7f00:1::",
        "3fff::1",
        "5f00::1",
    ],
)
async def test_prohibited_ipv6_ranges_and_transition_forms_are_rejected(address: str) -> None:
    with pytest.raises(PolicyError) as raised:
        await NavigationPolicy(FakeResolver({})).validate_url(f"http://[{address}]/")
    assert raised.value.code == "DESTINATION_BLOCKED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hostname",
    [
        "localhost",
        "api.localhost",
        "localhost.localdomain",
        "localhost4",
        "localhost6.localdomain6",
        "ip6-localhost",
        "ip6-loopback",
        "localtest.me",
        "api.localtest.me",
        "lvh.me",
        "api.lvh.me",
        "printer.local",
        "service.internal",
        "metadata",
        "metadata.google.internal",
        "instance-data.ec2.internal",
        "intranet",
    ],
)
async def test_localhost_aliases_metadata_and_local_names_are_rejected(hostname: str) -> None:
    resolver = FakeResolver({hostname: [PUBLIC_V4]})
    with pytest.raises(PolicyError) as raised:
        await NavigationPolicy(resolver).validate_url(f"http://{hostname}/")
    assert raised.value.reason is PolicyReason.PROHIBITED_DESTINATION
    assert resolver.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hostname",
    [
        "2130706433",
        "017700000001",
        "0x7f000001",
        "127.1",
        "127.0.1",
        "0177.0.0.1",
        "0x7f.0x0.0x0.0x1",
        "127.0.0.01",
        "4294967295",
        "１２７.０.０.１",
        "①②⑦.⓪.⓪.①",
    ],
)
async def test_unusual_numeric_ip_forms_are_rejected_before_resolution(hostname: str) -> None:
    resolver = FakeResolver({hostname: [PUBLIC_V4]})
    with pytest.raises(PolicyError) as raised:
        await NavigationPolicy(resolver).validate_url(f"http://{hostname}/")
    assert raised.value.code == "INVALID_URL"
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_any_prohibited_dns_answer_blocks_mixed_answer_set() -> None:
    resolver = FakeResolver({"mixed.example.com": [PUBLIC_V4, "10.0.0.2", PUBLIC_V6]})
    with pytest.raises(PolicyError) as raised:
        await NavigationPolicy(resolver).validate_url("https://mixed.example.com/")
    assert raised.value.code == "DESTINATION_BLOCKED"
    assert resolver.calls == ["mixed.example.com"]


@pytest.mark.asyncio
@pytest.mark.parametrize("answers", [[], ["not-an-ip"], [PUBLIC_V4] * 65])
async def test_resolution_failures_and_answer_bounds_fail_closed(answers: list[str]) -> None:
    resolver = FakeResolver({"resolve.example.com": answers})
    with pytest.raises(PolicyError) as raised:
        await NavigationPolicy(resolver, max_dns_answers=16).validate_url(
            "https://resolve.example.com/"
        )
    assert raised.value.reason is PolicyReason.RESOLUTION_FAILED


@pytest.mark.asyncio
async def test_resolution_timeout_is_bounded_and_sanitized() -> None:
    async def slow_resolver(hostname: str) -> Iterable[str]:
        del hostname
        await asyncio.sleep(0.1)
        return [PUBLIC_V4]

    with pytest.raises(PolicyError) as raised:
        await NavigationPolicy(slow_resolver, resolution_timeout_seconds=0.01).validate_url(
            "https://slow.example.com/"
        )
    assert raised.value.reason is PolicyReason.RESOLUTION_FAILED


def test_fixture_allowlist_requires_test_mode_and_valid_exact_origins() -> None:
    with pytest.raises(PolicyConfigurationError):
        NavigationPolicy(test_origins=["http://localhost:8765"])
    for origin in (
        "http://*.test:8765",
        "http://localhost:8765/path",
        "http://localhost:8765?query=yes",
        "http://user@localhost:8765",
    ):
        with pytest.raises(PolicyConfigurationError):
            NavigationPolicy(test_mode=True, test_origins=[origin])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"test_mode": "1"},
        {"test_mode": True, "test_origins": "http://localhost:8765"},
        {"max_redirects": True},
        {"resolution_timeout_seconds": float("nan")},
        {"resolver": object()},
    ],
)
def test_ambiguous_policy_configuration_types_fail_closed(kwargs: dict[str, object]) -> None:
    with pytest.raises(PolicyConfigurationError):
        NavigationPolicy(**kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_fixture_allowlist_uses_exact_normalized_origin_not_prefix_or_near_match() -> None:
    resolver = FakeResolver(
        {
            "localhost": ["127.0.0.1"],
            "localhost.example.com": ["127.0.0.1"],
        }
    )
    policy = NavigationPolicy(
        resolver,
        test_mode=True,
        test_origins=["HTTP://LOCALHOST:8765/"],
    )
    allowed = await policy.validate_url("http://localhost:8765/fixture?q=1")
    assert allowed.origin == "http://localhost:8765"

    for url in (
        "http://localhost:8766/fixture",
        "https://localhost:8765/fixture",
        "http://localhost.example.com:8765/fixture",
    ):
        with pytest.raises(PolicyError):
            await policy.validate_url(url)


@pytest.mark.asyncio
async def test_original_redirect_and_subsequent_navigation_are_each_revalidated() -> None:
    resolver = FakeResolver(
        {
            "start.example.com": [PUBLIC_V4],
            "next.example.com": [PUBLIC_V6],
            "later.example.com": [PUBLIC_V4_ALT],
            "blocked.example.com": ["169.254.169.254"],
        }
    )
    guard = NavigationPolicy(resolver).new_guard()
    await guard.validate_initial("https://start.example.com/")
    await guard.validate_redirect("https://next.example.com/")
    await guard.validate_navigation("https://later.example.com/")
    with pytest.raises(PolicyError):
        await guard.validate_redirect("http://blocked.example.com/")
    assert resolver.calls == [
        "start.example.com",
        "next.example.com",
        "later.example.com",
        "blocked.example.com",
    ]


@pytest.mark.asyncio
async def test_dns_rebinding_to_new_public_or_prohibited_set_is_rejected() -> None:
    public_change = SequencedResolver({"rebind.example.com": [[PUBLIC_V4], [PUBLIC_V4_ALT]]})
    guard = NavigationPolicy(public_change).new_guard()
    await guard.validate("https://rebind.example.com/one")
    with pytest.raises(PolicyError) as changed:
        await guard.validate("https://rebind.example.com/two")
    assert changed.value.reason is PolicyReason.DNS_REBINDING

    blocked_change = SequencedResolver({"blocked-rebind.example.com": [[PUBLIC_V4], ["127.0.0.1"]]})
    blocked_guard = NavigationPolicy(blocked_change).new_guard()
    await blocked_guard.validate("https://blocked-rebind.example.com/one")
    with pytest.raises(PolicyError) as blocked:
        await blocked_guard.validate("https://blocked-rebind.example.com/two")
    assert blocked.value.reason is PolicyReason.PROHIBITED_DESTINATION


@pytest.mark.asyncio
async def test_dns_answer_order_change_is_not_treated_as_rebinding() -> None:
    resolver = SequencedResolver(
        {"stable.example.com": [[PUBLIC_V4, PUBLIC_V6], [PUBLIC_V6, PUBLIC_V4]]}
    )
    guard = NavigationPolicy(resolver).new_guard()
    await guard.validate("https://stable.example.com/one")
    await guard.validate("https://stable.example.com/two")
    plan = await guard.connection_plan("stable.example.com", 443)
    assert tuple(str(address) for address in plan.addresses) == (PUBLIC_V4, PUBLIC_V6)


@pytest.mark.asyncio
async def test_connection_plan_refuses_unknown_hostname_and_wrong_port_without_dns() -> None:
    resolver = FakeResolver({"known.example.com": [PUBLIC_V4]})
    guard = NavigationPolicy(resolver).new_guard()
    await guard.authorize_request("https://known.example.com/resource")

    with pytest.raises(PolicyError):
        await guard.connection_plan("unknown.example.com", 443)
    with pytest.raises(PolicyError):
        await guard.connection_plan("known.example.com", 8443)

    assert resolver.calls == ["known.example.com"]


@pytest.mark.asyncio
async def test_http_and_websocket_authorization_share_exact_endpoint_pin() -> None:
    resolver = FakeResolver({"shared.example.com": [PUBLIC_V4]})
    guard = NavigationPolicy(resolver).new_guard()

    await guard.authorize_request("https://shared.example.com/app.js")
    await guard.authorize_websocket("wss://shared.example.com/events")

    assert resolver.calls == ["shared.example.com"]
    assert (await guard.connection_plan("shared.example.com", 443)).port == 443


@pytest.mark.asyncio
async def test_concurrent_conflicting_first_answers_publish_exactly_one_pin() -> None:
    entered = 0
    both_entered = asyncio.Event()
    release = asyncio.Event()

    async def resolver(_hostname: str) -> list[str]:
        nonlocal entered
        entered += 1
        call_number = entered
        if entered == 2:
            both_entered.set()
        await release.wait()
        return [PUBLIC_V4 if call_number == 1 else PUBLIC_V4_ALT]

    guard = NavigationPolicy(resolver).new_guard()
    first = asyncio.create_task(guard.authorize_request("https://race.example.com/a"))
    second = asyncio.create_task(guard.authorize_request("https://race.example.com/b"))
    await asyncio.wait_for(both_entered.wait(), timeout=1.0)
    release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)

    failures = [result for result in results if isinstance(result, PolicyError)]
    assert len(failures) == 1
    assert failures[0].reason is PolicyReason.DNS_REBINDING
    assert len(guard.endpoint_pins) == 1


@pytest.mark.asyncio
async def test_endpoint_publication_limit_includes_numeric_literals() -> None:
    guard = NavigationGuard(
        NavigationPolicy(FakeResolver({}), max_resolutions=4),
        max_endpoint_pins=1,
    )
    await guard.authorize_request("https://8.8.8.8/")
    with pytest.raises(PolicyError) as raised:
        await guard.authorize_request("https://1.1.1.1/")
    assert raised.value.reason is PolicyReason.NAVIGATION_LIMIT


@pytest.mark.asyncio
async def test_redirect_resolution_and_navigation_counts_are_bounded() -> None:
    resolver = FakeResolver({"limit.example.com": [PUBLIC_V4]})
    guard = NavigationGuard(
        NavigationPolicy(resolver),
        max_redirects=1,
        max_resolutions=3,
        max_top_level_navigations=2,
    )
    await guard.validate_initial("https://limit.example.com/")
    await guard.validate_redirect("https://limit.example.com/r1")
    with pytest.raises(PolicyError) as redirect_limit:
        await guard.validate_redirect("https://limit.example.com/r2")
    assert redirect_limit.value.reason is PolicyReason.REDIRECT_LIMIT
    await guard.validate_navigation("https://limit.example.com/later")
    with pytest.raises(PolicyError) as navigation_limit:
        await guard.validate_navigation("https://limit.example.com/too-many")
    assert navigation_limit.value.reason is PolicyReason.NAVIGATION_LIMIT


@pytest.mark.asyncio
async def test_policy_errors_do_not_expose_target_host_or_ip_details() -> None:
    hostname = "sensitive-canary.example.com"
    address = "10.20.30.40"
    with pytest.raises(PolicyError) as raised:
        await NavigationPolicy(FakeResolver({hostname: [address]})).validate_url(
            f"https://{hostname}/private-canary"
        )
    rendered = " ".join(
        (
            str(raised.value),
            repr(raised.value),
            repr(raised.value.args),
            raised.value.code,
            raised.value.reason.value,
        )
    )
    assert hostname not in rendered
    assert address not in rendered
    assert "private-canary" not in rendered
