import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createAdminPbClient } from './createAdminPbClient'

const { send, debug } = vi.hoisted(() => ({ send: vi.fn(), debug: vi.fn() }))
vi.mock('@', async () => ({
  GetUserTokenPayloadSchema: (await import('../../common/schema/Rest/GetUserTokenInfo')).GetUserTokenPayloadSchema,
  PocketBase: class {
    send = send
    autoCancellation() {}
  },
  RestCommands: { UserToken: 'userToken' },
  RestMethods: { Get: 'GET' },
  adminAuthWithPassword: vi.fn(),
  createFirstAdmin: vi.fn(),
}))
vi.mock('./InstanceMIxin', () => ({ createInstanceMixin: () => ({}) }))

const logger = { create: () => ({ info: vi.fn(), dbg: debug }) } as any
beforeEach(() => vi.clearAllMocks())
describe('private native identity request', () => {
  it('sends the instance query and never logs the credential response', async () => {
    const value = { id: 'b'.repeat(15), tokenKey: 'fixture-token-key', passwordHash: 'fixture-password-hash' }
    send.mockResolvedValue(value)
    const client = createAdminPbClient('https://controller.example.invalid', logger)
    await expect(client.getUserTokenInfo({ id: 'a'.repeat(15) as any, instanceId: 'c'.repeat(15) })).resolves.toEqual(
      value
    )
    expect(send).toHaveBeenCalledWith('/api/userToken/' + 'a'.repeat(15), {
      method: 'GET',
      query: { instanceId: 'c'.repeat(15) },
    })
    expect(debug).not.toHaveBeenCalled()
  })
  it('keeps legacy requests free of an instance query', async () => {
    send.mockResolvedValue({ email: 'fixture@example.invalid' })
    await createAdminPbClient('https://controller.example.invalid', logger).getUserTokenInfo({
      id: 'a'.repeat(15) as any,
    })
    expect(send).toHaveBeenCalledWith('/api/userToken/' + 'a'.repeat(15), { method: 'GET', query: undefined })
  })
  it('rejects malformed instance identities before any request', async () => {
    const client = createAdminPbClient('https://controller.example.invalid', logger)
    await expect(client.getUserTokenInfo({ id: 'a'.repeat(15) as any, instanceId: '../other' })).rejects.toThrow(
      'Invalid admin identity request'
    )
    expect(send).not.toHaveBeenCalled()
  })
})
