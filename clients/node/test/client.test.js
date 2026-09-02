import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ALLOWED_KEYS,
  AgentBrowser,
  AgentBrowserError,
  DEFAULT_BASE_URL,
  withSession,
} from '../src/index.js'

/** Record every request and reply with scripted responses. */
function stubFetch(responses) {
  const calls = []
  const queue = [...responses]
  const fetch = async (url, init) => {
    calls.push({
      url,
      method: init.method,
      headers: init.headers,
      body: init.body ? JSON.parse(init.body) : undefined,
    })
    const next = queue.shift()
    if (!next) throw new Error(`unexpected request to ${url}`)
    if (next.networkError) throw new Error(next.networkError)
    return {
      ok: next.status === undefined || next.status < 400,
      status: next.status ?? 200,
      headers: { get: (name) => next.headers?.[name] },
      text: async () => JSON.stringify(next.body ?? {}),
    }
  }
  return { fetch, calls }
}

const CREATED = {
  api_version: 'v1',
  status: 'created',
  session_id: '11111111-2222-3333-4444-555555555555',
  state: 'active',
  max_vision_steps: 25,
  view_url: 'http://127.0.0.1:6080/vnc.html',
  created_at: '2026-09-02T00:00:00Z',
  expires_at: '2026-09-02T00:30:00Z',
}

const ENDED = {
  api_version: 'v1',
  status: 'ended',
  session_id: CREATED.session_id,
  ended_at: '2026-09-02T00:05:00Z',
}

function client(responses, options = {}) {
  const { fetch, calls } = stubFetch(responses)
  return { browser: new AgentBrowser({ fetch, env: {}, ...options }), calls }
}

test('defaults to numeric loopback and sends no Authorization when no token is set', async () => {
  const { browser, calls } = client([{ body: { api_version: 'v1', status: 'ok' } }])
  assert.equal(browser.baseUrl, DEFAULT_BASE_URL)
  await browser.health()
  assert.equal(calls[0].url, `${DEFAULT_BASE_URL}/browser/health`)
  assert.equal(calls[0].method, 'GET')
  assert.equal(calls[0].headers.authorization, undefined)
})

test('stamps api_version on every request body', async () => {
  const { browser, calls } = client([{ body: CREATED }])
  await browser.createSession()
  assert.equal(calls[0].body.api_version, 'v1')
})

test('omits max_vision_steps when unset so the closed model never sees a null', async () => {
  const { browser, calls } = client([{ body: CREATED }])
  await browser.createSession()
  assert.deepEqual(Object.keys(calls[0].body), ['api_version'])
})

test('sends max_vision_steps when provided', async () => {
  const { browser, calls } = client([{ body: CREATED }])
  await browser.createSession({ maxVisionSteps: 5 })
  assert.equal(calls[0].body.max_vision_steps, 5)
})

test('routes reads to the observer token and writes to the controller token', async () => {
  const { browser, calls } = client([{ body: { status: 'ok' } }, { body: CREATED }], {
    observerToken: 'obs',
    controllerToken: 'ctl',
  })
  await browser.health()
  await browser.createSession()
  assert.equal(calls[0].headers.authorization, 'Bearer obs')
  assert.equal(calls[1].headers.authorization, 'Bearer ctl')
})

test('falls back to the only token supplied', async () => {
  const { browser, calls } = client([{ body: { status: 'ok' } }], { controllerToken: 'ctl' })
  await browser.health()
  assert.equal(calls[0].headers.authorization, 'Bearer ctl')
})

test('reads connection settings from the environment', () => {
  const browser = new AgentBrowser({
    fetch: async () => {},
    env: { AGENT_BROWSER_URL: 'http://127.0.0.1:9000/', AGENT_BROWSER_CONTROLLER_TOKEN: 'from-env' },
  })
  assert.equal(browser.baseUrl, 'http://127.0.0.1:9000')
  assert.equal(browser.controllerToken, 'from-env')
})

test('maps the documented error envelope onto a typed error', async () => {
  const { browser } = client([
    {
      status: 503,
      headers: { 'retry-after': '7' },
      body: {
        api_version: 'v1',
        status: 'error',
        error: { code: 'SESSION_CAPACITY_REACHED', message: 'A session is already active.' },
      },
    },
  ])
  const error = await browser.createSession().then(
    () => undefined,
    (e) => e,
  )
  assert.ok(error instanceof AgentBrowserError)
  assert.equal(error.code, 'SESSION_CAPACITY_REACHED')
  assert.equal(error.httpStatus, 503)
  assert.equal(error.retryAfterSeconds, 7)
  assert.equal(error.isCapacityReached, true)
  assert.equal(error.message, 'A session is already active.')
})

test('surfaces a transport failure without inventing an error code', async () => {
  const { browser } = client([{ networkError: 'ECONNREFUSED' }])
  const error = await browser.health().then(
    () => undefined,
    (e) => e,
  )
  assert.ok(error instanceof AgentBrowserError)
  assert.equal(error.code, undefined)
  assert.match(error.message, /Could not reach Agent Browser/)
})

test('click sends only a target and never both a selector and coordinates', async () => {
  const { browser, calls } = client([{ body: CREATED }, { body: { status: 'interacted' } }])
  const session = await browser.createSession()
  await session.click({ selector: '#go' })
  assert.equal(calls[1].url.endsWith('/browser/interact'), true)
  assert.deepEqual(calls[1].body, {
    api_version: 'v1',
    session_id: CREATED.session_id,
    action: 'click',
    target: { selector: '#go' },
  })
})

test('type splits text out of the target', async () => {
  const { browser, calls } = client([{ body: CREATED }, { body: { status: 'interacted' } }])
  const session = await browser.createSession()
  await session.type({ selector: '#name', text: '  spaced  ' })
  assert.deepEqual(calls[1].body.target, { selector: '#name' })
  assert.equal(calls[1].body.text, '  spaced  ', 'text must survive byte for byte')
})

test('scroll maps camelCase deltas to the wire field names and omits an empty target', async () => {
  const { browser, calls } = client([{ body: CREATED }, { body: { status: 'interacted' } }])
  const session = await browser.createSession()
  await session.scroll({ deltaY: -240 })
  assert.deepEqual(calls[1].body, {
    api_version: 'v1',
    session_id: CREATED.session_id,
    action: 'scroll',
    delta_y: -240,
  })
})

test('press sends the key alone', async () => {
  const { browser, calls } = client([{ body: CREATED }, { body: { status: 'interacted' } }])
  const session = await browser.createSession()
  await session.press('Enter')
  assert.deepEqual(calls[1].body, {
    api_version: 'v1',
    session_id: CREATED.session_id,
    action: 'press',
    key: 'Enter',
  })
})

test('the allowed key list matches the server enum exactly', () => {
  assert.equal(ALLOWED_KEYS.length, 20)
  for (const key of ['Enter', 'Control+Shift+Z', 'Meta+Shift+Z', 'PageDown']) {
    assert.ok(ALLOWED_KEYS.includes(key), `${key} should be allowed`)
  }
  for (const key of ['Control+C', 'Control+V', 'F5']) {
    assert.ok(!ALLOWED_KEYS.includes(key), `${key} must not be offered: clipboard is not allowlisted`)
  }
})

test('withSession ends the session on success', async () => {
  const { browser, calls } = client([
    { body: CREATED },
    { body: { status: 'navigated' } },
    { body: ENDED },
  ])
  const result = await withSession(browser, async (session) => {
    await session.navigate('https://example.com')
    return 'done'
  })
  assert.equal(result, 'done')
  assert.equal(calls.at(-1).url.endsWith('/browser/session/end'), true)
})

test('withSession ends the session when the body throws, and preserves the original error', async () => {
  const { browser, calls } = client([{ body: CREATED }, { body: ENDED }])
  const error = await withSession(browser, async () => {
    throw new Error('boom')
  }).then(
    () => undefined,
    (e) => e,
  )
  assert.equal(error.message, 'boom')
  assert.equal(calls.at(-1).url.endsWith('/browser/session/end'), true)
})

test('a cleanup failure never masks the caller error', async () => {
  const { browser } = client([{ body: CREATED }, { networkError: 'ECONNRESET' }])
  const error = await withSession(browser, async () => {
    throw new Error('original')
  }).then(
    () => undefined,
    (e) => e,
  )
  assert.equal(error.message, 'original')
})

test('a session that was never created is never ended', async () => {
  const { browser, calls } = client([
    {
      status: 503,
      body: { error: { code: 'SESSION_CAPACITY_REACHED', message: 'busy' } },
    },
  ])
  await withSession(browser, async () => 'unreachable').then(
    () => assert.fail('should have thrown'),
    () => {},
  )
  assert.equal(calls.length, 1, 'only the create attempt should have been sent')
})

test('end is reflected on the session object', async () => {
  const { browser } = client([{ body: CREATED }, { body: ENDED }])
  const session = await browser.createSession()
  assert.equal(session.ended, false)
  await session.end()
  assert.equal(session.ended, true)
})

test('exposes the loopback view URL the server reported', async () => {
  const { browser } = client([{ body: CREATED }])
  const session = await browser.createSession()
  assert.equal(session.viewUrl, CREATED.view_url)
  assert.equal(session.id, CREATED.session_id)
})

test('an explicit timeout of 0 disables the timer', async () => {
  const { browser } = client([{ body: { status: 'ok' } }], { timeoutMs: 0 })
  await browser.health()
})

test('rejects construction when no fetch implementation exists', () => {
  const saved = globalThis.fetch
  try {
    globalThis.fetch = undefined
    assert.throws(() => new AgentBrowser({ env: {} }), TypeError)
  } finally {
    globalThis.fetch = saved
  }
})

test('trims trailing slashes from the base URL without regex backtracking', () => {
  const make = (url) => new AgentBrowser({ fetch: async () => {}, env: {}, baseUrl: url })
  assert.equal(make('http://127.0.0.1:8092').baseUrl, 'http://127.0.0.1:8092')
  assert.equal(make('http://127.0.0.1:8092/').baseUrl, 'http://127.0.0.1:8092')
  assert.equal(make('http://127.0.0.1:8092///').baseUrl, 'http://127.0.0.1:8092')
  assert.equal(make('http://127.0.0.1:8092/base/').baseUrl, 'http://127.0.0.1:8092/base')
  assert.equal(make('///').baseUrl, '')

  // A long run of slashes is linear here; the previous /\/+$/ backtracked polynomially.
  const started = process.hrtime.bigint()
  assert.equal(make(`http://x${'/'.repeat(60_000)}`).baseUrl, 'http://x')
  const elapsedMs = Number(process.hrtime.bigint() - started) / 1e6
  assert.ok(elapsedMs < 250, `normalisation should stay linear, took ${elapsedMs.toFixed(1)}ms`)
})
