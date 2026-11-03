import { describe, expect, it } from 'vitest'
import { nextWebCron, parseWebCron, webCronOffset } from './webCron'
const next = (expression: string, after = '2026-10-29T10:00:00Z') =>
  nextWebCron(parseWebCron(expression), Date.parse(after))
describe('strict UTC scheduled tasks', () => {
  it('accepts deterministic lists ranges and steps', () => {
    expect(next('*/15 * * * *')).toBe('2026-10-29T10:15:00.000Z')
    expect(next('5,35 9-17/2 * * 1-5')).toBe('2026-10-29T11:05:00.000Z')
    expect(next('0 0 * * 7')).toBe('2026-11-01T00:00:00.000Z')
  })
  it('handles calendar impossibilities leap years and traditional day OR', () => {
    expect(next('0 0 31 2 *')).toBeNull()
    expect(next('0 0 29 2 *')).toBe('2028-02-29T00:00:00.000Z')
    expect(next('0 0 1 * 5')).toBe('2026-10-30T00:00:00.000Z')
    expect(next('0 0 1 * *')).toBe('2026-11-01T00:00:00.000Z')
  })
  it('rejects aliases seconds names commands descending ranges and malformed steps', () => {
    for (const expression of [
      '@hourly',
      '* * * * * *',
      '0 0 * * MON',
      '60 * * * *',
      '0 24 * * *',
      '0 0 0 * *',
      '0 0 * 13 *',
      '* * * * 8',
      '*/0 * * * *',
      '1/2 * * * *',
      '10-1 * * * *',
      '1,,2 * * * *',
      '* * * * *;touch /tmp/x',
    ])
      expect(() => parseWebCron(expression)).toThrow()
  })
  it('uses Reunion calendar days across UTC midnight and year boundaries', () => {
    expect(nextWebCron(parseWebCron('0 2 * * *'), Date.parse('2026-10-29T10:00:00Z'), 'Indian/Reunion')).toBe(
      '2026-10-29T22:00:00.000Z'
    )
    expect(nextWebCron(parseWebCron('0 0 1 1 *'), Date.parse('2026-12-31T19:59:59Z'), 'Indian/Reunion')).toBe(
      '2026-12-31T20:00:00.000Z'
    )
    expect(nextWebCron(parseWebCron('0 0 * * 0'), Date.parse('2026-10-31T10:00:00Z'), 'Indian/Reunion')).toBe(
      '2026-10-31T20:00:00.000Z'
    )
    expect(() => webCronOffset('Europe/Paris')).toThrow()
  })
})
