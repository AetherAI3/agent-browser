/**
 * Types for the Agent Browser v1 API client.
 *
 * These mirror the closed server models in `src/agent_browser/models.py`. The server
 * rejects unknown fields, so these types are deliberately exact rather than permissive.
 */

export declare const API_VERSION: 'v1'
export declare const DEFAULT_BASE_URL: 'http://127.0.0.1:8092'

export type AllowedKey =
  | 'Enter'
  | 'Escape'
  | 'Tab'
  | 'Backspace'
  | 'Delete'
  | 'Space'
  | 'ArrowUp'
  | 'ArrowDown'
  | 'ArrowLeft'
  | 'ArrowRight'
  | 'Home'
  | 'End'
  | 'PageUp'
  | 'PageDown'
  | 'Control+A'
  | 'Control+Z'
  | 'Control+Shift+Z'
  | 'Meta+A'
  | 'Meta+Z'
  | 'Meta+Shift+Z'

export declare const ALLOWED_KEYS: readonly AllowedKey[]

export type ErrorCode =
  | 'AUTH_REQUIRED'
  | 'AUTH_FORBIDDEN'
  | 'SESSION_CAPACITY_REACHED'
  | 'SESSION_NOT_FOUND'
  | 'SESSION_EXPIRED'
  | 'VISION_BUDGET_EXHAUSTED'
  | 'INVALID_URL'
  | 'DESTINATION_BLOCKED'
  | 'INVALID_INTERACTION'
  | 'BROWSER_NOT_READY'
  | 'INTERNAL_ERROR'

export type InteractionAction = 'click' | 'type' | 'scroll' | 'press'

export interface AccessibilityNode {
  role: string
  name: string
  value: string
  focused: boolean
  disabled: boolean
}

export interface AccessibilitySnapshot {
  nodes: AccessibilityNode[]
  truncated: boolean
}

export interface Viewport {
  width: number
  height: number
  device_scale_factor: number
}

export interface HealthResponse {
  api_version: 'v1'
  status: 'ok'
  version: string
  browser_ready: boolean
  session_active: boolean
  slots_available: number
  started_at: string
}

export interface CreateSessionResponse {
  api_version: 'v1'
  status: 'created'
  session_id: string
  state: 'active'
  max_vision_steps: number
  view_url: string
  created_at: string
  expires_at: string
}

export interface NavigateResponse {
  api_version: 'v1'
  status: 'navigated'
  session_id: string
  final_url: string
  title: string
  readable_text: string
  accessibility: AccessibilitySnapshot
  navigated_at: string
}

export interface SnapshotResponse {
  api_version: 'v1'
  status: 'snapshot'
  session_id: string
  url: string
  title: string
  readable_text: string
  accessibility: AccessibilitySnapshot
  screenshot_base64: string
  viewport: Viewport
  sequence: number
  captured_at: string
  vision_steps_used: number
  vision_steps_remaining: number
}

export interface InteractResponse {
  api_version: 'v1'
  status: 'interacted'
  session_id: string
  action: InteractionAction
  sequence: number
  interacted_at: string
}

export interface EndSessionResponse {
  api_version: 'v1'
  status: 'ended' | 'already_ended'
  session_id: string
  ended_at: string
}

export interface RequestOptions {
  /** Abort the request early. Composed with the client timeout. */
  signal?: AbortSignal
  /** Override the client timeout for this call. Pass 0 to disable. */
  timeoutMs?: number
}

export interface AgentBrowserOptions {
  /** Defaults to `AGENT_BROWSER_URL`, then `http://127.0.0.1:8092`. */
  baseUrl?: string
  /** Required for create, navigate, interact, and end. Defaults to `AGENT_BROWSER_CONTROLLER_TOKEN`. */
  controllerToken?: string
  /** Sufficient for health and snapshot. Defaults to `AGENT_BROWSER_OBSERVER_TOKEN`. */
  observerToken?: string
  /** Per-request timeout in milliseconds. Defaults to 30000. Pass 0 to disable. */
  timeoutMs?: number
  /** Injectable transport, for tests or a custom agent. Defaults to global fetch. */
  fetch?: typeof globalThis.fetch
  /** Injectable environment, for tests. Defaults to `process.env`. */
  env?: Record<string, string | undefined>
}

export declare class AgentBrowserError extends Error {
  name: 'AgentBrowserError'
  /** The server's stable error code, or undefined for a transport failure. */
  code?: ErrorCode
  httpStatus?: number
  retryAfterSeconds?: number
  readonly isCapacityReached: boolean
  readonly isDestinationBlocked: boolean
  constructor(
    message: string,
    options?: {
      code?: ErrorCode
      httpStatus?: number
      retryAfterSeconds?: number
      cause?: unknown
    },
  )
}

export declare class Session {
  readonly id: string
  readonly viewUrl: string
  readonly createdAt: string
  readonly expiresAt: string
  readonly maxVisionSteps: number
  ended: boolean

  navigate(url: string, options?: RequestOptions): Promise<NavigateResponse>
  snapshot(options?: RequestOptions): Promise<SnapshotResponse>
  click(
    target: { selector: string; x?: never; y?: never } | { x: number; y: number; selector?: never },
    options?: RequestOptions,
  ): Promise<InteractResponse>
  type(
    input:
      | { text: string; selector: string; x?: never; y?: never }
      | { text: string; x: number; y: number; selector?: never },
    options?: RequestOptions,
  ): Promise<InteractResponse>
  press(key: AllowedKey, options?: RequestOptions): Promise<InteractResponse>
  scroll(
    input: { deltaX?: number; deltaY?: number; selector?: string; x?: number; y?: number },
    options?: RequestOptions,
  ): Promise<InteractResponse>
  end(options?: RequestOptions): Promise<EndSessionResponse>
}

export declare class AgentBrowser {
  constructor(options?: AgentBrowserOptions)
  readonly baseUrl: string
  health(options?: RequestOptions): Promise<HealthResponse>
  createSession(
    input?: { maxVisionSteps?: number },
    options?: RequestOptions,
  ): Promise<Session>
}

/**
 * Run `fn` against a fresh session and always attempt to end it, including when `fn`
 * throws. A failure to end never masks the original error.
 */
export declare function withSession<T>(
  browser: AgentBrowser,
  fn: (session: Session) => Promise<T>,
  input?: { maxVisionSteps?: number },
): Promise<T>

export default AgentBrowser
