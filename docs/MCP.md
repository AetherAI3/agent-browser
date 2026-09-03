# MCP server

Agent Browser ships an [MCP](https://modelcontextprotocol.io) server, so any MCP client —
Claude Code, Claude Desktop, Cursor, Windsurf, or your own — can drive the session while a human
watches the *same* browser over noVNC and takes over when it gets stuck.

It ships in both clients, with the same nine tools under the same names, so you can run whichever
one your machine already has. Neither adds a dependency: the JSON-RPC stdio transport is implemented
directly in [`clients/node/src/mcp.js`](../clients/node/src/mcp.js) and
[`clients/python/src/aether_browser/mcp.py`](../clients/python/src/aether_browser/mcp.py), and a
test asserts the two toolsets stay identical.

## Setup

Start the runtime first — the MCP server is a client of it, not a replacement:

```bash
docker compose up --build
```

Then add the server to your MCP client.

### Claude Code

```bash
claude mcp add agent-browser -- npx -y aether-browser mcp   # Node
claude mcp add agent-browser -- uvx --from aether-browser aether-browser mcp   # or Python
```

### Claude Desktop, Cursor, Windsurf, and other config-file clients

```json
{
  "mcpServers": {
    "agent-browser": {
      "command": "npx",
      "args": ["-y", "aether-browser", "mcp"],
      "env": { "AGENT_BROWSER_URL": "http://127.0.0.1:8092" }
    }
  }
}
```

If you installed the Python client instead (`pip install aether-browser`), use its own entry point:

```json
{
  "mcpServers": {
    "agent-browser": { "command": "aether-browser", "args": ["mcp"] }
  }
}
```

`AGENT_BROWSER_URL` is optional and defaults to `http://127.0.0.1:8092`. If the server runs
authenticated, pass `AGENT_BROWSER_CONTROLLER_TOKEN` and `AGENT_BROWSER_OBSERVER_TOKEN` the same
way; see [the authority contract](API.md#transport-and-authority).

## Tools

| Tool | What it does |
|---|---|
| `browser_open` | Starts the session and returns the live view URL to hand to the human |
| `browser_navigate` | Navigates to an http(s) URL; returns title, final URL, and readable text |
| `browser_read` | Title, URL, readable text, and a bounded accessibility tree. `include_screenshot` adds the PNG and spends one vision step |
| `browser_click` | Clicks a CSS selector or an x/y point |
| `browser_type` | Types into a selector or point |
| `browser_press` | Presses one allowlisted key or combination |
| `browser_scroll` | Scrolls by a bounded, nonzero delta |
| `browser_status` | Runtime health, session state, and the live view URL |
| `browser_close` | Ends the session and frees the single slot |

The MCP server owns the session id, so the model never carries it. Every response that can carry
the live view URL does — the point of this project is that a human can see what the agent is doing.

## The part that makes this different

Other browser MCP servers give a model a browser you cannot see. This one hands you a URL, on the
first tool call, where you can watch the exact session the model is driving — and type into it.

The server's `initialize` response tells the model so:

> When you hit something you should not do on your own — a login, a payment, a 2FA prompt,
> anything ambiguous — stop and ask the user to take over in that view rather than guessing.
> The session stays yours; they hand it straight back.

That is the loop in [the demo](../README.md): the agent drives until it reaches a two-factor
prompt, stops, a human types the code into the same live session, and the agent carries on.

## Notes and limits

- **One session at a time.** The v0.x runtime owns a single browser slot. `browser_open` reuses an
  open session rather than failing; `browser_close` frees the slot. A bounded multi-session pool is
  [issue #14](https://github.com/AetherAI3/agent-browser/issues/14).
- **The navigation policy still applies.** Loopback, private, link-local, and reserved address
  ranges are refused, and `DESTINATION_BLOCKED` from an MCP tool is that policy working, not a bug.
- **No arbitrary JavaScript, shell, or raw CDP** is exposed over MCP, the same as over the HTTP API.
- **stdout is the protocol channel.** The server writes diagnostics to stderr only.
