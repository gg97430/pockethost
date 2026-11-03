import { beforeEach, describe, expect, it, vi } from 'vitest'
import { syncMothershipVersions } from './syncMothershipVersions'
const sdk = vi.hoisted(() => ({ getFirstListItem: vi.fn(), update: vi.fn(), create: vi.fn() }))
vi.mock('@', () => ({
  LoggerService: () => ({ create: () => ({ info: vi.fn(), warn: vi.fn() }) }),
  MOTHERSHIP_URL: () => 'http://localhost:8091',
  MOTHERSHIP_ADMIN_USERNAME: () => 'test',
  MOTHERSHIP_ADMIN_PASSWORD: () => 'test',
  adminAuthWithPassword: vi.fn(),
  PocketBase: class {
    collection() {
      return sdk
    }
  },
}))
const versions = [{ range: '0.40.*', patch: '0.40.6' }]
describe('startup version catalog synchronization', () => {
  beforeEach(() => vi.resetAllMocks())
  it('creates a missing catalog so new servers can create their first database', async () => {
    sdk.getFirstListItem.mockRejectedValue({ status: 404 })
    expect(await syncMothershipVersions(versions, true)).toBe(true)
    expect(sdk.create).toHaveBeenCalledWith(expect.objectContaining({ value: expect.objectContaining({ versions }) }))
  })
  it('preserves the operator catalog on restart', async () => {
    sdk.getFirstListItem.mockResolvedValue({
      id: 'catalog',
      value: JSON.stringify({ versions: [{ range: '0.39.*', patch: '0.39.11' }] }),
    })
    expect(await syncMothershipVersions(versions, true)).toBe(true)
    expect(sdk.update).not.toHaveBeenCalled()
    expect(sdk.create).not.toHaveBeenCalled()
  })
  it.each([{}, { versions: [] }, '{broken'])('repairs an empty or invalid catalog: %j', async (value) => {
    sdk.getFirstListItem.mockResolvedValue({ id: 'catalog', value })
    expect(await syncMothershipVersions(versions, true)).toBe(true)
    expect(sdk.update).toHaveBeenCalledWith(
      'catalog',
      expect.objectContaining({ value: expect.objectContaining({ versions }) })
    )
    expect(sdk.create).not.toHaveBeenCalled()
  })
  it('does not create a duplicate after a read or update failure', async () => {
    sdk.getFirstListItem.mockRejectedValue({ status: 503 })
    expect(await syncMothershipVersions(versions, true)).toBe(false)
    expect(sdk.create).not.toHaveBeenCalled()
    sdk.getFirstListItem.mockResolvedValue({ id: 'catalog', value: {} })
    sdk.update.mockRejectedValue({ status: 403 })
    expect(await syncMothershipVersions(versions, true)).toBe(false)
    expect(sdk.create).not.toHaveBeenCalled()
  })
})
