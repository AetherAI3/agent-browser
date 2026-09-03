import assert from 'node:assert/strict'
import { PassThrough } from 'node:stream'
import test from 'node:test'

import { Server, TOOLS } from '../src/mcp.js'

/** Collect newline-delimited JSON written by the server. */
function harness(browser) {
  const out = new PassThrough()
  const log = new PassThrough()
  const messages = []
  let buffer = ''
  out.on('data', (chunk) => {
    buffer += chunk
    let index
    while ((index = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, index).trim()
      buffer = buffer.slice(index + 1)
      if (line) messages.push(JSON.parse(line))
    }
  })
  return { server: new Server(browser, { out, log }), messages }
}

const textOf = (message) =>
  (message.result?.content ?? []).filter((c) => c.type === 'text').map((c) => c.text).join('\n')

test('tool schemas are closed and well formed', () => {
  assert.equal(TOOLS.length, 9)
  for (const tool of TOOLS) {
    assert.ok(tool.name.startsWith('browser_'), `${tool.name} is namespaced`)
    assert.ok(tool.description.length > 20, `${tool.name} is described`)
    assert.equal(tool.inputSchema.type, 'object')
    assert.equal(tool.inputSchema.additionalProperties, false, `${tool.name} rejects extra fields`)
  }
  const names = TOOLS.map((t) => t.name)
  assert.equal(new Set(names).size, names.length, 'names are unique')
})

test('initialize echoes a supported protocol and advertises tools', async () => {
  const { server, messages } = harness({})
  await server.dispatch({
    jsonrpc: '2.0',
    id: 1,
    method: 'initialize',
    params: { protocolVersion: '2024-11-05' },
  })
  assert.equal(messages[0].result.protocolVersion, '2024-11-05')
  assert.equal(messages[0].result.serverInfo.name, 'agent-browser')
  assert.ok(messages[0].result.capabilities.tools)
  assert.match(messages[0].result.instructions, /take over/)
})

test('initialize falls back when the client asks for an unknown protocol', async () => {
  const { server, messages } = harness({})
  await server.dispatch({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '1999-01-01' } })
  assert.equal(messages[0].result.protocolVersion, '2025-06-18')
})

test('notifications are not answered', async () => {
  const { server, messages } = harness({})
  await server.dispatch({ jsonrpc: '2.0', method: 'notifications/initialized' })
  assert.equal(messages.length, 0)
})

test('an unknown method is a JSON-RPC error', async () => {
  const { server, messages } = harness({})
  await server.dispatch({ jsonrpc: '2.0', id: 7, method: 'nope/nope' })
  assert.equal(messages[0].error.code, -32601)
})

test('browser_open reuses an open session and always surfaces the view URL', async () => {
  let created = 0
  const session = {
    id: 's-1',
    viewUrl: 'http://127.0.0.1:6080/vnc.html',
    createdAt: 'now',
    expiresAt: 'later',
    maxVisionSteps: 25,
    ended: false,
  }
  const { server, messages } = harness({
    createSession: async () => (created += 1, session),
  })
  await server.dispatch({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'browser_open', arguments: {} } })
  await server.dispatch({ jsonrpc: '2.0', id: 2, method: 'tools/call', params: { name: 'browser_open', arguments: {} } })
  assert.equal(created, 1, 'the second call reuses the session')
  assert.match(textOf(messages[0]), /Session open/)
  assert.match(textOf(messages[1]), /Reusing the open session/)
  for (const message of messages) assert.match(textOf(message), /6080\/vnc\.html/)
})

test('acting without a session asks the model to open one', async () => {
  const { server, messages } = harness({})
  await server.dispatch({
    jsonrpc: '2.0',
    id: 1,
    method: 'tools/call',
    params: { name: 'browser_click', arguments: { selector: '#go' } },
  })
  assert.equal(messages[0].result.isError, true)
  assert.match(textOf(messages[0]), /browser_open/)
})

test('a blocked destination is reported as policy, not as a bug', async () => {
  const session = {
    id: 's-1', viewUrl: 'http://127.0.0.1:6080/vnc.html', createdAt: '', expiresAt: '',
    maxVisionSteps: 25, ended: false,
    navigate: async () => {
      const error = new Error('The navigation destination is blocked.')
      error.code = 'DESTINATION_BLOCKED'
      throw error
    },
  }
  const { server, messages } = harness({ createSession: async () => session })
  await server.dispatch({
    jsonrpc: '2.0', id: 1, method: 'tools/call',
    params: { name: 'browser_navigate', arguments: { url: 'http://192.168.0.1/' } },
  })
  assert.equal(messages[0].result.isError, true)
  assert.match(textOf(messages[0]), /by design/)
})

test('browser_read returns structure and only attaches the image when asked', async () => {
  const snapshot = {
    url: 'http://example.test/', title: 'T', vision_steps_remaining: 24,
    readable_text: 'hello', screenshot_base64: 'AAAA',
    accessibility: { nodes: [{ role: 'button', name: 'Verify', disabled: false }], truncated: false },
  }
  const session = {
    id: 's', viewUrl: 'http://127.0.0.1:6080/vnc.html', ended: false, maxVisionSteps: 25,
    createdAt: '', expiresAt: '', snapshot: async () => snapshot,
  }
  const { server, messages } = harness({ createSession: async () => session })
  await server.dispatch({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'browser_open', arguments: {} } })
  await server.dispatch({ jsonrpc: '2.0', id: 2, method: 'tools/call', params: { name: 'browser_read', arguments: {} } })
  await server.dispatch({
    jsonrpc: '2.0', id: 3, method: 'tools/call',
    params: { name: 'browser_read', arguments: { include_screenshot: true } },
  })
  assert.ok(!messages[1].result.content.some((c) => c.type === 'image'), 'no image by default')
  assert.match(textOf(messages[1]), /button "Verify"/)
  const image = messages[2].result.content.find((c) => c.type === 'image')
  assert.equal(image.mimeType, 'image/png')
})
