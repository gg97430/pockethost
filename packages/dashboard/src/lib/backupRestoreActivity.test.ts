import { describe, expect, it } from 'vitest'
import { restoreActivityState } from './backupRestoreActivity'

describe('restore activity state', () => {
  it('uses a fresh server observation as the source of truth', () => {
    expect(restoreActivityState('backup1', ['backup1'], 2_000, 1_000)).toBe(true)
    expect(restoreActivityState('backup1', [], 2_000, 1_000)).toBe(false)
  })

  it('falls back while the server observation predates the restore request', () => {
    expect(restoreActivityState('backup1', [], 500, 1_000)).toBeNull()
    expect(restoreActivityState('backup1', [], 0, 1_000)).toBeNull()
  })
})
