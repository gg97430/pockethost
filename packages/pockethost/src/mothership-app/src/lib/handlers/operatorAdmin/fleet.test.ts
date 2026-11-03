import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { HandleOperatorFleet } from './fleet'

class ApiFailure extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message)
  }
}
const actor = (override: Record<string, unknown> = {}) => {
  const value = { email: 'actual@example.invalid', superAdmin: true, verified: true, suspension: '', ...override }
  return {
    id: 'a'.repeat(15),
    collection: () => ({ name: 'users' }),
    getString: (key: keyof typeof value) => String(value[key] || ''),
    getBool: (key: keyof typeof value) => value[key] === true,
  }
}
const reply = () => ({
  version: 1,
  checkedAt: new Date().toISOString(),
  self: 'app4',
  servers: ['app3', 'app4', 'app5'].map((key) => ({
    key,
    hostname: `${key}.monappli.re`,
    checkedAt: new Date().toISOString(),
    access: 'denied',
    password: 'never-return',
  })),
})
const event = (auth: unknown) => ({
  auth,
  requestInfo: () => ({ body: { email: 'attacker@example.invalid', host: 'app1' } }),
  response: { header: () => ({ set: vi.fn() }) },
  json: vi.fn((status, body) => ({ status, body })),
})

beforeEach(() => {
  vi.stubGlobal(
    'UnauthorizedError',
    class extends ApiFailure {
      constructor(message: string) {
        super(401, message)
      }
    }
  )
  vi.stubGlobal(
    'ForbiddenError',
    class extends ApiFailure {
      constructor(message: string) {
        super(403, message)
      }
    }
  )
  vi.stubGlobal('ApiError', ApiFailure)
  vi.stubGlobal('toString', (value: unknown) => String(value))
})
afterEach(() => vi.unstubAllGlobals())

describe('operator fleet handler', () => {
  it('requires users auth, active superadmin and verified email before any helper execution', () => {
    const cmd = vi.fn()
    vi.stubGlobal('$os', { cmd })
    for (const [auth, status] of [
      [null, 401],
      [actor({ superAdmin: false }), 403],
      [actor({ suspension: 'blocked' }), 403],
      [actor({ verified: false }), 403],
      [{ ...actor(), collection: () => ({ name: '_superusers' }) }, 403],
    ] as const) {
      try {
        HandleOperatorFleet(event(auth) as any)
        throw Error('Expected refusal')
      } catch (error) {
        expect((error as ApiFailure).status).toBe(status)
      }
    }
    expect(cmd).not.toHaveBeenCalled()
  })
  it('uses only the authenticated email and fixed native argument vector, strips extra peer fields', () => {
    const cmd = vi.fn(() => ({ output: () => JSON.stringify(reply()) }))
    vi.stubGlobal('$os', { cmd })
    const result = HandleOperatorFleet(event(actor()) as any) as any
    expect(result.status).toBe(200)
    expect(cmd.mock.calls[0]).toEqual([
      '/usr/bin/timeout',
      '13',
      '/usr/bin/python3',
      '-I',
      '/opt/pockethost-web/platform_fleet_reader.py',
      'request',
      JSON.stringify({ version: 1, email: 'actual@example.invalid' }),
    ])
    expect(JSON.stringify(result)).not.toContain('attacker@example.invalid')
    expect(JSON.stringify(result)).not.toContain('never-return')
    expect(JSON.stringify(result)).not.toContain('app1')
  })
  it('masks command exceptions and refuses malformed helper output', () => {
    for (const cmd of [
      vi.fn(() => {
        throw Error('ssh private-key-or-sql-value')
      }),
      vi.fn(() => ({ output: () => '{"secrets":"private-value"}' })),
    ]) {
      vi.stubGlobal('$os', { cmd })
      try {
        HandleOperatorFleet(event(actor()) as any)
        throw Error('Expected refusal')
      } catch (error) {
        expect((error as ApiFailure).status).toBe(503)
        expect((error as Error).message).not.toContain('private')
      }
    }
  })
})
