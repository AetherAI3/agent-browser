# Optional Jev web decision layer

`aether_browser.jev` is an opt-in Python client module. It lets Jev choose a narrow next step
before your application calls its selected model. It does not change the browser server, npm
client, MCP tools, controller permissions, or default behavior.

## Decision and handoff

1. Your application creates and owns a browser `Session`, navigates to the initial page, and
   supplies a goal, an `excerpt_for(page)` function, an `options_for(page)` function, and a
   `selected_model(handoff)` callback.
2. Before making a provider call, the layer checks for common login, verification, checkout,
   and payment signs. If found, it returns `HumanTakeover` with the live `view_url`. Your code
   keeps the session open for the person to continue; close it when they are finished.
3. Otherwise, it sends a bounded text state to OpenRouter's System One endpoint using
   `typesafe/jev-1.13`. A single `Choice` selects `handoff`, `takeover`, or one of at most three
   caller-provided navigation URLs. The reply must have the expected versioned model, typed
   answer, valid option, probabilities, and usage receipt.
4. An approved navigation runs through the existing browser `navigate` API. At most three
   navigations occur by default (configurable from zero to five), and repeated URLs stop the
   loop. The server still validates requested, redirected, and browser-initiated destinations.
5. On `handoff`, the selected-model callback receives the current URL, title, bounded readable
   text, visited URLs, reason, live view URL, and Jev receipts. It may invoke whichever reasoning
   model your application selects. The client does not pick or call that model itself.

The external Jev request includes the goal (up to 2,000 characters), current URL (up to 2,048
characters), title (up to 512 characters), up to 6,000 characters returned by `excerpt_for`,
and up to three option labels and URLs. This is a paid provider request on the caller's
OpenRouter account, separate from any product billing. The provider key is passed only as an
Authorization header from the client process and is never sent to the browser server. Responses
are limited to 64 KiB, redirects are not followed, the timeout defaults to five seconds, and
there are no retries. The module adds no runtime dependencies.

## Fallback and boundaries

| Situation | Outcome |
|---|---|
| Jev chooses handoff | Invoke the selected-model callback with `reason="jev_handoff"`. |
| Jev is unavailable, times out, or returns an invalid reply | Invoke the selected-model callback with `reason="jev_unavailable"`; do not navigate. |
| Jev chooses a caller-approved URL | Navigate through the ordinary browser API; the browser may still refuse it. |
| Navigation is refused or repeats a URL | Invoke the selected-model callback with the current page; do not retry the navigation. |
| Common authentication/payment page is detected | Return `HumanTakeover` before sending that page's text to Jev or the selected-model callback. |
| Jev requests a person | Return `HumanTakeover` and leave the session open. |

This is a **text decision layer**, not a complete autonomous web agent. Browser responses have
accessible labels but no stable click targets for Jev to safely select, and Jev cannot see
screenshots or produce an explanation, code, or final user response. The host model or human
handles visual interpretation, forms, clicks, and synthesis. A page may hide sensitive content
from the common-signs check; the caller must filter page text and URLs before provider egress.
The selected-model callback receives readable text from the current page, bounded to 6,000
characters, regardless of what excerpt was sent to Jev; the caller should apply its own data
policy there too. Page instructions are untrusted. Jev only chooses fixed identifiers; it cannot
inject its own URL or broaden server permissions.

Provider contract: [OpenRouter's Jev example](https://openrouter.ai/blog/insights/what-is-jev/)
and [TypeSafe's System One overview](https://docs.typesafe.ai/concepts/system-one).
