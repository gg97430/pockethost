import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { HandleOperatorControls, HandleOperatorDeploymentMaintenance, HandleOperatorMemory } from './controls'

class ApiFailure extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message)
  }
}
const actor = (superAdmin = true, suspension = '', collection = 'users') => ({
  id: 'a'.repeat(15),
  collection: () => ({ name: collection }),
  getBool: () => superAdmin,
  getString: (key: string) => (key === 'suspension' ? suspension : 'operator@example.invalid'),
})
const event = (auth: unknown, body: unknown = {}) => ({
  auth,
  requestInfo: () => ({ body }),
  response: { header: () => ({ set: vi.fn() }) },
  json: vi.fn((status, body) => ({ status, body })),
})
beforeEach(() => {
  vi.stubGlobal('toString', (value: unknown) => String(value))
  vi.stubGlobal('ApiError', ApiFailure)
  for (const [name, status] of [
    ['UnauthorizedError', 401],
    ['ForbiddenError', 403],
    ['BadRequestError', 400],
  ] as const)
    vi.stubGlobal(
      name,
      class extends ApiFailure {
        constructor(message: string) {
          super(status, message)
        }
      }
    )
})
afterEach(() => vi.unstubAllGlobals())

describe('operator controls without privilege escalation in the controller', () => {
  it('rejects anonymous, member, suspended and native superuser before any operation', () => {
    const cmd = vi.fn()
    vi.stubGlobal('$os', { cmd })
    for (const auth of [null, actor(false), actor(true, 'blocked'), actor(true, '', '_superusers')])
      for (const handler of [HandleOperatorControls, HandleOperatorMemory, HandleOperatorDeploymentMaintenance])
        expect(() => handler(event(auth) as any)).toThrow(ApiFailure)
    expect(cmd).not.toHaveBeenCalled()
  })
  it('uses only the fixed unprivileged client and bounded status action', () => {
    const cmd = vi.fn(() => ({ output: () => JSON.stringify({ ok: true, value: { revision: 'b'.repeat(64) } }) }))
    vi.stubGlobal('$os', { cmd })
    const result = HandleOperatorControls(event(actor(), { command: 'whoami' }) as any) as any
    expect(result.status).toBe(200)
    expect(cmd.mock.calls[0]).toEqual([
      '/usr/bin/timeout',
      '30',
      '/usr/bin/python3',
      '-I',
      '/opt/pockethost-web/platform_operator_socket.py',
      'request',
      'status',
      '{}',
    ])
    expect(JSON.stringify(cmd.mock.calls)).not.toContain('sudo')
  })
  it('retains conflict and validation statuses and hides transport exception details', () => {
    for (const status of [400, 409, 503]) {
      vi.stubGlobal('$os', {
        cmd: () => ({ output: () => JSON.stringify({ ok: false, status, message: 'safe error' }) }),
      })
      try {
        HandleOperatorControls(event(actor()) as any)
        throw Error('Expected refusal')
      } catch (error) {
        expect((error as ApiFailure).status).toBe(status)
      }
    }
    vi.stubGlobal('$os', {
      cmd: () => {
        throw Error('private filesystem details')
      },
    })
    expect(() => HandleOperatorControls(event(actor()) as any)).toThrow('Le contrôle local est indisponible.')
  })
})
