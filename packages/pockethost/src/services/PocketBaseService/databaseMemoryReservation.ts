import { closeSync, constants, fstatSync, openSync, readFileSync } from 'node:fs'
import { totalmem } from 'node:os'

export const DATABASE_MEMORY_POLICY = '/etc/kolab/database-memory-reservations.json'
export const DATABASE_MEMORY_HISTORY = '/var/lib/kolab-memory-allocation/database-history.json'
const MIB = 1024 ** 2

export const parseDatabaseMemoryReservations = (input: unknown): Record<string, number> => {
  const value = input as { version?: unknown; databases?: unknown }
  if (
    !value ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    Object.keys(value).sort().join(',') !== 'databases,version' ||
    value.version !== 1 ||
    !value.databases ||
    typeof value.databases !== 'object' ||
    Array.isArray(value.databases)
  )
    throw new Error('Invalid database memory policy')
  const rows = Object.entries(value.databases)
  if (
    rows.length > 1000 ||
    rows.some(
      ([id, mib]) =>
        !/^[a-z0-9]{15}$/.test(id) || typeof mib !== 'number' || !Number.isInteger(mib) || mib < 512 || mib > 524288
    )
  )
    throw new Error('Invalid database memory policy')
  return Object.fromEntries(rows.map(([id, mib]) => [id, (mib as number) * MIB]))
}

const readPolicy = (path: string): Record<string, number> => {
  let fd: number
  try {
    fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW)
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return {}
    throw error
  }
  try {
    const s = fstatSync(fd)
    if (!s.isFile() || s.uid !== 0 || s.mode & 0o022 || s.size > 65536)
      throw new Error('Invalid database memory policy')
    return parseDatabaseMemoryReservations(JSON.parse(readFileSync(fd, 'utf8')))
  } finally {
    closeSync(fd)
  }
}

export const adaptiveDatabaseMemory = (learned: number, hostBytes: number) =>
  Math.min(16 * 1024 ** 3, Math.floor(hostBytes / 2), Math.max(4 * 1024 ** 3, learned))

export const databaseMemoryReservation = (
  id: string,
  path = DATABASE_MEMORY_POLICY,
  history = DATABASE_MEMORY_HISTORY
) => {
  const policy = readPolicy(path),
    reserved = Object.values(policy).reduce((a, b) => a + b, 0)
  if (reserved && reserved + Math.max(2 * 1024 ** 3, totalmem() * 0.15) > totalmem())
    throw new Error('Database memory reservations exceed host capacity')
  const amount = policy[id]
  if (!amount)
    return {
      Memory: adaptiveDatabaseMemory(readPolicy(history)[id] || 0, totalmem()),
      MemoryReservation: 0,
      MemorySwap: -1,
    }
  if (amount + Math.max(2 * 1024 ** 3, totalmem() * 0.15) > totalmem())
    throw new Error('Database memory reservation exceeds host capacity')
  return { Memory: amount, MemoryReservation: amount, MemorySwap: -1 }
}
