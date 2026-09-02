/**
 * Client for the Agent Browser v1 API.
 *
 * The server exposes one closed JSON API. Every request and response carries
 * `api_version: "v1"` and unknown fields are rejected, so this client sends
 * exactly the documented fields and omits anything left undefined.
 *
 * @see https://github.com/AetherAI3/agent-browser/blob/main/docs/API.md
 */

export const API_VERSION = 'v1'

export const DEFAULT_BASE_URL = 'http://127.0.0.1:8092'

/** Keys the server accepts for `press`. Anything else is refused server-side. */
export const ALLOWED_KEYS = Object.freeze([
  'Enter',
  'Escape',
  'Tab',
  'Backspace',
  'Delete',
  'Space',
  'ArrowUp',
  'ArrowDown',
  'ArrowLeft',
  'ArrowRight',
  'Home',
  'End',
  'PageUp',
  'PageDown',
  'Control+A',
  'Control+Z',
  'Control+Shift+Z',
  'Meta+A',
  'Meta+Z',
  'Meta+Shift+Z',
])

/**
 * An error returned by the Agent Browser API, or a transport failure reaching it.
 *
 * `code` is the server's stable error code when the response carried the documented
 * error envelope, and `undefined` for transport-level failures.
 */
export class AgentBrowserError extends Error {
  /**
   * @param {string} message
   * @param {{ code?: string, httpStatus?: number, retryAfterSeconds?: number, cause?: unknown }} [options]
   */
  constructor(message, options = {}) {
    super(message, options.cause === undefined ? undefined : { cause: options.cause })
    this.name = 'AgentBrowserError'
    this.code = options.code
    this.httpStatus = options.httpStatus
    this.retryAfterSeconds = options.retryAfterSeconds
  }

  /** True when the server refused because a session is already active. */
  get isCapacityReached() {
    return this.code === 'SESSION_CAPACITY_REACHED'
  }

  /** True when the server refused the destination rather than failing to reach it. */
  get isDestinationBlocked() {
    return this.code === 'DESTINATION_BLOCKED' || this.code === 'INVALID_URL'
  }
}

/** Strip undefined values so the closed server models never see unknown or null keys. */
function compact(object) {
  const out = {}
  for (const [key, value] of Object.entries(object)) {
    if (value !== undefined) out[key] = value
  }
  return out
}

function normalizeBaseUrl(value) {
  return String(value).replace(/\/+$/, '')
}

/**
 * A live session. Obtained from `AgentBrowser#createSession`; not constructed directly.
 *
 * Every method is a thin, typed call onto the documented routes. The session does not
 * cache page state: `navigate` and `snapshot` each return the server's own bounded view.
 */
export class Session {
  /**
   * @param {AgentBrowser} browser
   * @param {import('./index.d.ts').CreateSessionResponse} created
   */
  constructor(browser, created) {
    this.browser = browser
    this.id = created.session_id
    this.viewUrl = created.view_url
    this.createdAt = created.created_at
    this.expiresAt = created.expires_at
    this.maxVisionSteps = created.max_vision_steps
    this.ended = false
  }

  /** Navigate to an HTTP(S) URL. The server evaluates its egress policy separately from schema validation. */
  navigate(url, options = {}) {
    return this.browser._post('/browser/navigate', { session_id: this.id, url }, 'controller', options)
  }

  /** Capture bounded page state plus a base64 PNG. Consumes exactly one vision step. */
  snapshot(options = {}) {
    return this.browser._post('/browser/snapshot', { session_id: this.id }, 'observer', options)
  }

  /**
   * Click a selector or an x/y point. Exactly one of the two is allowed by the server.
   * @param {{ selector?: string, x?: number, y?: number }} target
   */
  click(target, options = {}) {
    return this._interact({ action: 'click', target: compact(target) }, options)
  }

  /**
   * Type text into a selector or an x/y point. Text is preserved byte for byte,
   * including leading and trailing whitespace.
   * @param {{ selector?: string, x?: number, y?: number, text: string }} input
   */
  type({ text, ...target }, options = {}) {
    return this._interact({ action: 'type', target: compact(target), text }, options)
  }

  /**
   * Press one of the allowed keys or combinations. Clipboard shortcuts are not allowlisted.
   * @param {import('./index.d.ts').AllowedKey} key
   */
  press(key, options = {}) {
    return this._interact({ action: 'press', key }, options)
  }

  /**
   * Scroll by a bounded, nonzero delta.
   * @param {{ deltaX?: number, deltaY?: number, selector?: string, x?: number, y?: number }} input
   */
  scroll({ deltaX, deltaY, ...target } = {}, options = {}) {
    const body = { action: 'scroll', delta_x: deltaX, delta_y: deltaY }
    const where = compact(target)
    if (Object.keys(where).length > 0) body.target = where
    return this._interact(body, options)
  }

  _interact(body, options) {
    return this.browser._post(
      '/browser/interact',
      { session_id: this.id, ...compact(body) },
      'controller',
      options,
    )
  }

  /**
   * End the session. Idempotent: a repeated call reports `already_ended` rather than
   * resurrecting state, so calling this twice is safe.
   */
  async end(options = {}) {
    const result = await this.browser._post(
      '/browser/session/end',
      { session_id: this.id },
      'controller',
      options,
    )
    this.ended = true
    return result
  }
}

/**
 * A client bound to one Agent Browser server.
 *
 * Observer and controller tokens are kept separate so the server's role split is visible
 * in your own code: reads may be given only the observer token, while anything that
 * creates, navigates, interacts, or ends requires the controller token.
 */
export class AgentBrowser {
  /**
   * @param {import('./index.d.ts').AgentBrowserOptions} [options]
   */
  constructor(options = {}) {
    const env = options.env ?? (typeof process !== 'undefined' ? process.env : {}) ?? {}
    this.baseUrl = normalizeBaseUrl(options.baseUrl ?? env.AGENT_BROWSER_URL ?? DEFAULT_BASE_URL)
    this.controllerToken = options.controllerToken ?? env.AGENT_BROWSER_CONTROLLER_TOKEN
    this.observerToken = options.observerToken ?? env.AGENT_BROWSER_OBSERVER_TOKEN
    this.timeoutMs = options.timeoutMs ?? 30_000
    this._fetch = options.fetch ?? globalThis.fetch
    if (typeof this._fetch !== 'function') {
      throw new TypeError(
        'No fetch implementation available. Use Node 18 or newer, or pass options.fetch.',
      )
    }
  }

  /** Liveness and readiness. Accepts the observer token. */
  health(options = {}) {
    return this._request('GET', '/browser/health', undefined, 'observer', options)
  }

  /**
   * Create the one owned session. A second concurrent create is refused with
   * `SESSION_CAPACITY_REACHED` rather than queued.
   * @param {{ maxVisionSteps?: number }} [input]
   */
  async createSession(input = {}, options = {}) {
    const created = await this._post(
      '/browser/session/create',
      compact({ max_vision_steps: input.maxVisionSteps }),
      'controller',
      options,
    )
    return new Session(this, created)
  }

  _post(path, body, role, options) {
    return this._request('POST', path, body, role, options)
  }

  _tokenFor(role) {
    if (role === 'observer') return this.observerToken ?? this.controllerToken
    return this.controllerToken ?? this.observerToken
  }

  async _request(method, path, body, role, options = {}) {
    const headers = { accept: 'application/json' }
    const token = this._tokenFor(role)
    if (token) headers.authorization = `Bearer ${token}`

    let payload
    if (body !== undefined) {
      headers['content-type'] = 'application/json'
      payload = JSON.stringify({ api_version: API_VERSION, ...body })
    }

    const timeoutMs = options.timeoutMs ?? this.timeoutMs
    const controller = new AbortController()
    const onAbort = () => controller.abort(options.signal?.reason)
    if (options.signal) {
      if (options.signal.aborted) onAbort()
      else options.signal.addEventListener('abort', onAbort, { once: true })
    }
    const timer =
      timeoutMs > 0
        ? setTimeout(
            () => controller.abort(new Error(`Agent Browser request timed out after ${timeoutMs}ms`)),
            timeoutMs,
          )
        : undefined

    let response
    try {
      response = await this._fetch(`${this.baseUrl}${path}`, {
        method,
        headers,
        body: payload,
        signal: controller.signal,
      })
    } catch (cause) {
      throw new AgentBrowserError(
        `Could not reach Agent Browser at ${this.baseUrl}: ${cause?.message ?? cause}`,
        { cause },
      )
    } finally {
      if (timer !== undefined) clearTimeout(timer)
      options.signal?.removeEventListener?.('abort', onAbort)
    }

    const text = await response.text()
    let parsed
    try {
      parsed = text ? JSON.parse(text) : undefined
    } catch {
      parsed = undefined
    }

    if (!response.ok) {
      const detail = parsed?.error
      const retryAfter = Number(response.headers?.get?.('retry-after'))
      throw new AgentBrowserError(detail?.message ?? `Agent Browser returned HTTP ${response.status}`, {
        code: detail?.code,
        httpStatus: response.status,
        retryAfterSeconds: Number.isFinite(retryAfter) ? retryAfter : undefined,
      })
    }

    return parsed
  }
}

/**
 * Run `fn` against a fresh session and always attempt to end it, including when `fn`
 * throws. A failure to end never masks the original error.
 *
 * @template T
 * @param {AgentBrowser} browser
 * @param {(session: Session) => Promise<T>} fn
 * @returns {Promise<T>}
 */
export async function withSession(browser, fn, input = {}) {
  const session = await browser.createSession(input)
  try {
    return await fn(session)
  } finally {
    try {
      await session.end()
    } catch {
      // The caller's outcome is what matters; a cleanup failure must not replace it.
    }
  }
}

export default AgentBrowser
