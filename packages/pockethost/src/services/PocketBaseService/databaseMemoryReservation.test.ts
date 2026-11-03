import { describe, expect, it } from 'vitest'
import {
  adaptiveDatabaseMemory,
  databaseMemoryReservation,
  parseDatabaseMemoryReservations,
} from './databaseMemoryReservation'
const G = 1024 ** 3
describe('native database memory policy', () => {
  it('keeps explicitly reserved 32 GiB separate from other databases', () => {
    const map = parseDatabaseMemoryReservations({ version: 1, databases: { ['a'.repeat(15)]: 32768 } })
    expect(map['a'.repeat(15)]).toBe(32 * G)
    expect(map['b'.repeat(15)]).toBeUndefined()
  })
  it('rejects malformed or unbounded operator reservations', () => {
    for (const value of [
      null,
      {},
      [],
      { version: 1, databases: [] },
      { version: 2, databases: {} },
      { version: 1, databases: { bad: 32768 } },
      { version: 1, databases: { ['a'.repeat(15)]: true } },
      { version: 1, databases: { ['a'.repeat(15)]: 0 } },
      { version: 1, databases: { ['a'.repeat(15)]: 524289 } },
    ]) {
      expect(() => parseDatabaseMemoryReservations(value)).toThrow()
    }
  })
  it('boots above tiny idle measurements and restores a learned cap within host bounds', () => {
    expect(adaptiveDatabaseMemory(0, 64 * G)).toBe(4 * G)
    expect(adaptiveDatabaseMemory(12 * G, 64 * G)).toBe(12 * G)
    expect(adaptiveDatabaseMemory(32 * G, 64 * G)).toBe(16 * G)
    expect(adaptiveDatabaseMemory(12 * G, 8 * G)).toBe(4 * G)
  })
  it('uses the adaptive boot floor when neither operator policy nor history exists', () => {
    const value = databaseMemoryReservation(
      'a'.repeat(15),
      '/nonexistent-kolab-memory-policy',
      '/nonexistent-kolab-memory-history'
    )
    expect(value.Memory).toBeGreaterThanOrEqual(512 * 1024 ** 2)
    expect(value.MemoryReservation).toBe(0)
    expect(value.MemorySwap).toBe(-1)
  })
})
