/**
 * Model Context Protocol server for Agent Browser.
 *
 * Exposes one Agent Browser session as MCP tools over stdio, so any MCP client
 * (Claude Code, Claude Desktop, Cursor, Windsurf, or your own) can drive the same
 * headed Chrome session a human is watching over noVNC.
 *
 * The transport is newline-delimited JSON-RPC 2.0 on stdin/stdout, implemented
 * here directly: this package has no runtime dependencies and the MCP server
 * keeps that property.
 *
 * The server owns the session id so the model never has to carry it. Every
 * response that can carry the live view URL does, because the point of this
 * project is that a human can see and take over what the agent is doing.
 */

import { AgentBrowser, AgentBrowserError, ALLOWED_KEYS, DEFAULT_BASE_URL } from './index.js'

const SERVER_NAME = 'agent-browser'
const SERVER_VERSION = '0.1.0'
const SUPPORTED_PROTOCOLS = new Set(['2024-11-05', '2025-03-26', '2025-06-18'])
const FALLBACK_PROTOCOL = '2025-06-18'

const str = (description) => ({ type: 'string', description })
const int = (description) => ({ type: 'integer', description })

const TOOLS = [
  {
    name: 'browser_open',
    description:
      'Start the one headed Chrome session and return the live view URL. Give that URL to the ' +
      'human: they can watch this exact session, and take over in it, while you drive. Safe to ' +
      'call twice — it reuses the session that is already open.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
  },
  {
    name: 'browser_navigate',
    description:
      'Navigate the session to an http(s) URL and return the page title, final URL and readable ' +
      'text. The server validates the destination and refuses blocked address classes.',
    inputSchema: {
      type: 'object',
      properties: { url: str('Absolute http(s) URL to open.') },
      required: ['url'],
      additionalProperties: false,
    },
  },
  {
    name: 'browser_read',
    description:
      'Read the current page: title, URL, readable text and a bounded accessibility tree. Prefer ' +
      'this over a screenshot — it is structure, not pixels, and it does not spend a vision step ' +
      'unless you ask for the image.',
    inputSchema: {
      type: 'object',
      properties: {
        include_screenshot: {
          type: 'boolean',
          description:
            'Also return the PNG as base64. Costs one vision step from the session budget. ' +
            'Default false.',
        },
      },
      additionalProperties: false,
    },
  },
  {
    name: 'browser_click',
    description: 'Click a CSS selector, or an x/y point in the viewport. Provide one, not both.',
    inputSchema: {
      type: 'object',
      properties: {
        selector: str('CSS selector to click.'),
        x: int('Viewport x coordinate.'),
        y: int('Viewport y coordinate.'),
      },
      additionalProperties: false,
    },
  },
  {
    name: 'browser_type',
    description:
      'Type text into a CSS selector, or at an x/y point. Text is sent byte for byte. Do not use ' +
      'this for a secret a human should enter — ask them to take over in the live view instead.',
    inputSchema: {
      type: 'object',
      properties: {
        text: str('Text to type.'),
        selector: str('CSS selector to type into.'),
        x: int('Viewport x coordinate.'),
        y: int('Viewport y coordinate.'),
      },
      required: ['text'],
      additionalProperties: false,
    },
  },
  {
    name: 'browser_press',
    description: `Press one allowlisted key or combination. Allowed: ${ALLOWED_KEYS.join(', ')}.`,
    inputSchema: {
      type: 'object',
      properties: { key: { type: 'string', enum: [...ALLOWED_KEYS], description: 'Key to press.' } },
      required: ['key'],
      additionalProperties: false,
    },
  },
  {
    name: 'browser_scroll',
    description: 'Scroll the page by a nonzero pixel delta.',
    inputSchema: {
      type: 'object',
      properties: {
        delta_y: int('Vertical pixels. Positive scrolls down.'),
        delta_x: int('Horizontal pixels. Positive scrolls right.'),
      },
      additionalProperties: false,
    },
  },
  {
    name: 'browser_status',
    description:
      'Report whether the runtime is up, whether a session is open, how many vision steps remain, ' +
      'and the live view URL to hand to a human.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
  },
  {
    name: 'browser_close',
    description:
      'End the session and release the browser. Idempotent. Call this when the task is finished so ' +
      'the single session slot is free.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
  },
]

class Server {
  constructor(browser, { out = process.stdout, log = process.stderr } = {}) {
    this.browser = browser
    this.session = null
    this.out = out
    this.log = log
  }

  note(message) {
    // stdout is the protocol channel; anything human-readable goes to stderr.
    this.log.write(`[agent-browser mcp] ${message}\n`)
  }

  send(message) {
    this.out.write(`${JSON.stringify(message)}\n`)
  }

  reply(id, result) {
    if (id !== undefined && id !== null) this.send({ jsonrpc: '2.0', id, result })
  }

  fail(id, code, message) {
    if (id !== undefined && id !== null) this.send({ jsonrpc: '2.0', id, error: { code, message } })
  }

  text(value) {
    return { content: [{ type: 'text', text: value }] }
  }

  errorText(value) {
    return { content: [{ type: 'text', text: value }], isError: true }
  }

  async ensureSession() {
    if (this.session && !this.session.ended) return this.session
    this.session = await this.browser.createSession()
    return this.session
  }

  requireSession() {
    if (!this.session || this.session.ended) {
      throw new AgentBrowserError(
        'No session is open. Call browser_open first.',
        'SESSION_NOT_FOUND',
      )
    }
    return this.session
  }

  view() {
    return this.session && !this.session.ended ? this.session.viewUrl : null
  }

  withView(lines) {
    const url = this.view()
    return url ? `${lines}\n\nLive view (a human can watch or take over here): ${url}` : lines
  }

  async handleTool(name, args) {
    const a = args ?? {}
    switch (name) {
      case 'browser_open': {
        const reused = Boolean(this.session && !this.session.ended)
        const session = await this.ensureSession()
        return this.text(
          `${reused ? 'Reusing the open session' : 'Session open'}: ${session.id}\n` +
            `Vision steps available: ${session.maxVisionSteps}\n` +
            `Expires: ${session.expiresAt}\n\n` +
            `Live view (a human can watch or take over here): ${session.viewUrl}\n` +
            'Anything they do in that window happens in this same session.',
        )
      }
      case 'browser_navigate': {
        const session = await this.ensureSession()
        const r = await session.navigate(String(a.url))
        return this.text(
          this.withView(
            `Navigated to ${r.final_url}\nTitle: ${r.title}\n\n${trim(r.readable_text, 4000)}`,
          ),
        )
      }
      case 'browser_read': {
        const session = this.requireSession()
        const wantImage = a.include_screenshot === true
        const r = await session.snapshot()
        const nodes = (r.accessibility?.nodes ?? [])
          .slice(0, 60)
          .map((n) => `  ${n.role}${n.name ? ` "${n.name}"` : ''}${n.disabled ? ' [disabled]' : ''}`)
          .join('\n')
        const body =
          `URL: ${r.url}\nTitle: ${r.title}\n` +
          `Vision steps remaining: ${r.vision_steps_remaining}\n\n` +
          `Readable text:\n${trim(r.readable_text, 6000)}\n\n` +
          `Accessibility (first ${Math.min(60, r.accessibility?.nodes?.length ?? 0)} nodes):\n${nodes}`
        const content = [{ type: 'text', text: this.withView(body) }]
        if (wantImage && r.screenshot_base64) {
          content.push({ type: 'image', data: r.screenshot_base64, mimeType: 'image/png' })
        }
        return { content }
      }
      case 'browser_click': {
        const session = this.requireSession()
        await session.click(pick(a, ['selector', 'x', 'y']))
        return this.text(this.withView(`Clicked ${describeTarget(a)}.`))
      }
      case 'browser_type': {
        const session = this.requireSession()
        await session.type({ text: String(a.text), ...pick(a, ['selector', 'x', 'y']) })
        return this.text(this.withView(`Typed ${String(a.text).length} characters into ${describeTarget(a)}.`))
      }
      case 'browser_press': {
        const session = this.requireSession()
        await session.press(a.key)
        return this.text(this.withView(`Pressed ${a.key}.`))
      }
      case 'browser_scroll': {
        const session = this.requireSession()
        await session.scroll({ deltaX: a.delta_x, deltaY: a.delta_y ?? (a.delta_x ? undefined : 600) })
        return this.text(this.withView('Scrolled.'))
      }
      case 'browser_status': {
        const h = await this.browser.health()
        const url = this.view()
        return this.text(
          `Runtime: ${h.status} (v${h.version})\n` +
            `Browser ready: ${h.browser_ready}\n` +
            `Session active: ${h.session_active}\n` +
            `Free session slots: ${h.slots_available}\n` +
            (url ? `\nLive view: ${url}` : '\nNo session open. Call browser_open.'),
        )
      }
      case 'browser_close': {
        if (!this.session || this.session.ended) return this.text('No session was open.')
        const r = await this.session.end()
        this.session = null
        return this.text(`Session ${r.status}. The browser slot is free.`)
      }
      default:
        return this.errorText(`Unknown tool: ${name}`)
    }
  }

  async dispatch(message) {
    const { id, method, params } = message
    switch (method) {
      case 'initialize': {
        const asked = params?.protocolVersion
        return this.reply(id, {
          protocolVersion: SUPPORTED_PROTOCOLS.has(asked) ? asked : FALLBACK_PROTOCOL,
          capabilities: { tools: { listChanged: false } },
          serverInfo: { name: SERVER_NAME, version: SERVER_VERSION },
          instructions:
            'Agent Browser drives one headed Chrome session that a human can watch and take over ' +
            'over noVNC. Call browser_open first and show the returned live view URL to the user. ' +
            'When you hit something you should not do on your own — a login, a payment, a 2FA ' +
            'prompt, anything ambiguous — stop and ask the user to take over in that view rather ' +
            'than guessing. The session stays yours; they hand it straight back.',
        })
      }
      case 'notifications/initialized':
      case 'notifications/cancelled':
        return
      case 'ping':
        return this.reply(id, {})
      case 'tools/list':
        return this.reply(id, { tools: TOOLS })
      case 'tools/call': {
        const name = params?.name
        try {
          return this.reply(id, await this.handleTool(name, params?.arguments))
        } catch (error) {
          const detail =
            error instanceof AgentBrowserError
              ? `${error.code ?? 'ERROR'}: ${error.message}`
              : String(error?.message ?? error)
          const hint =
            error?.code === 'SESSION_CAPACITY_REACHED'
              ? '\n\nThe runtime holds one browser session and something else already owns it — ' +
                'another client, or an earlier run that did not call browser_close. This server ' +
                'cannot adopt a session it did not create. Free the slot on the runtime, then ' +
                'call browser_open again.'
              : error?.code === 'DESTINATION_BLOCKED'
              ? '\n\nThe navigation policy refused that destination. It is not a bug: loopback, ' +
                'private and reserved address ranges are blocked by design.'
              : error?.cause?.code === 'ECONNREFUSED'
                ? `\n\nNothing is listening on ${this.browser.baseUrl}. Start the runtime with ` +
                  '`docker compose up --build`, or set AGENT_BROWSER_URL.'
                : ''
          return this.reply(id, this.errorText(`${detail}${hint}`))
        }
      }
      case 'resources/list':
        return this.reply(id, { resources: [] })
      case 'prompts/list':
        return this.reply(id, { prompts: [] })
      default:
        return this.fail(id, -32601, `Method not found: ${method}`)
    }
  }

  listen(input = process.stdin) {
    let buffer = ''
    input.setEncoding('utf8')
    input.on('data', (chunk) => {
      buffer += chunk
      let index
      while ((index = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, index).trim()
        buffer = buffer.slice(index + 1)
        if (!line) continue
        let message
        try {
          message = JSON.parse(line)
        } catch {
          this.note('ignored a line that was not JSON')
          continue
        }
        Promise.resolve(this.dispatch(message)).catch((error) => {
          this.note(`dispatch failed: ${error?.message ?? error}`)
          this.fail(message?.id, -32603, String(error?.message ?? error))
        })
      }
    })
    input.on('end', () => process.exit(0))
  }
}

function pick(source, keys) {
  const out = {}
  for (const key of keys) if (source[key] !== undefined) out[key] = source[key]
  return out
}

function describeTarget(a) {
  if (a.selector) return `\`${a.selector}\``
  if (a.x !== undefined && a.y !== undefined) return `(${a.x}, ${a.y})`
  return 'the focused element'
}

function trim(value, limit) {
  const text = String(value ?? '')
  return text.length <= limit ? text : `${text.slice(0, limit)}\n… (${text.length - limit} more characters)`
}

export function runMcpServer(options = {}) {
  const browser = new AgentBrowser({
    baseUrl: options.baseUrl ?? process.env.AGENT_BROWSER_URL ?? DEFAULT_BASE_URL,
    timeoutMs: 120_000,
  })
  const server = new Server(browser)
  server.note(`serving MCP over stdio against ${browser.baseUrl}`)
  server.listen()
  return server
}

export { TOOLS, Server }
