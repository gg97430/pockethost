import { describe, expect, it } from 'vitest'
import { normalizeInstanceProjectLabel } from './instanceProjectLabel'
describe('instance project labels', () => {
  it('normalizes spacing and Unicode, and supports removing the label', () => {
    expect(normalizeInstanceProjectLabel('  Socie\u0301te\u0301   LAPUB ')).toBe('Société LAPUB')
    expect(normalizeInstanceProjectLabel('   ')).toBe('')
    expect(normalizeInstanceProjectLabel('🏷'.repeat(60))).toHaveLength(120)
  })
  it('rejects structured data, controls and oversized labels', () => {
    for (const value of [null, [], {}, 1, 'A\nB', 'A\u0000B', 'x'.repeat(61)])
      expect(() => normalizeInstanceProjectLabel(value)).toThrow()
  })
})
