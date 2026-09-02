/**
 * Compile-only checks for the hand-written declarations.
 *
 * The package ships plain ESM plus `src/index.d.ts` rather than compiled TypeScript, so
 * nothing stops the two from drifting apart except this file. It is type-checked in CI
 * with `tsc --noEmit` and is never executed or published.
 */

import {
  AgentBrowser,
  AgentBrowserError,
  withSession,
  type AllowedKey,
  type ErrorCode,
  type NavigateResponse,
  type SnapshotResponse,
} from '../src/index.js'

const browser = new AgentBrowser({
  baseUrl: 'http://127.0.0.1:8092',
  controllerToken: 'ctl',
  observerToken: 'obs',
  timeoutMs: 5_000,
})

async function reads(): Promise<void> {
  const health = await browser.health()
  const ready: boolean = health.browser_ready
  const slots: number = health.slots_available
  void ready
  void slots
}

async function drives(): Promise<string> {
  return withSession(browser, async (session) => {
    const id: string = session.id
    const view: string = session.viewUrl

    const nav: NavigateResponse = await session.navigate('https://example.com')
    const title: string = nav.title

    const snap: SnapshotResponse = await session.snapshot()
    const png: string = snap.screenshot_base64
    const remaining: number = snap.vision_steps_remaining

    await session.click({ selector: '#submit' })
    await session.click({ x: 10, y: 20 })
    await session.type({ selector: '#name', text: 'hello' })
    await session.type({ x: 5, y: 6, text: 'hello' })
    await session.press('Enter')
    await session.scroll({ deltaY: -240 })

    return `${id}${view}${title}${png}${remaining}`
  })
}

// A key outside the server enum must not type-check.
// @ts-expect-error clipboard shortcuts are deliberately not allowlisted
const forbidden: AllowedKey = 'Control+C'
void forbidden

// Selector and coordinates are mutually exclusive on the wire.
// @ts-expect-error a click targets a selector or a point, never both
void browser.createSession().then((s) => s.click({ selector: '#a', x: 1, y: 2 }))

// Error codes are constrained to the documented enum.
// @ts-expect-error NOT_A_REAL_CODE is not an ErrorCode
const bogus: ErrorCode = 'NOT_A_REAL_CODE'
void bogus

function inspect(error: unknown): string | undefined {
  if (error instanceof AgentBrowserError) {
    const capacity: boolean = error.isCapacityReached
    void capacity
    return error.code
  }
  return undefined
}

void reads
void drives
void inspect
