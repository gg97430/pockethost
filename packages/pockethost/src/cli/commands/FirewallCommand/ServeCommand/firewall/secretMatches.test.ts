import { describe, expect, it } from 'vitest'
import { secretMatches } from './secretMatches'

describe('secretMatches', () => {
  it('accepts only the exact secret', () => {
    expect(secretMatches('correct-horse', 'correct-horse')).toBe(true)
    expect(secretMatches('correct-hors', 'correct-horse')).toBe(false)
    expect(secretMatches('correct-horse-battery', 'correct-horse')).toBe(false)
  })

  it('refuses everything when the secret is empty or unset', () => {
    for (const secret of ['', undefined]) {
      expect(secretMatches('', secret)).toBe(false)
      expect(secretMatches('anything', secret)).toBe(false)
      expect(secretMatches(undefined, secret)).toBe(false)
    }
  })

  it('refuses a non-string candidate', () => {
    expect(secretMatches(['correct-horse'], 'correct-horse')).toBe(false)
    expect(secretMatches({ $ne: '' }, 'correct-horse')).toBe(false)
  })
})
