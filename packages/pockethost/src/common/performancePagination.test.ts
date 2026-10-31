import { describe, expect, it } from 'vitest'
import { paginatePerformance } from './performancePagination'
describe('performance pagination', () => {
  const report = {
    snapshot: 'fixed',
    groups: Array.from({ length: 130 }, (_, i) => ({
      method: 'GET',
      endpoint: `/route/${i}`,
      maxMs: i,
      count: 130 - i,
      p95Ms: i,
    })),
    slowest: Array.from({ length: 1251 }, (_, i) => ({ durationMs: 2000 - i })),
  }
  it('exposes every slow result beyond 1000 without duplicates', () => {
    const rows = Array.from({ length: 26 }, (_, i) => paginatePerformance(report, i + 1, 1, 'maxMs').slowest).flat()
    expect(rows).toEqual(report.slowest)
    expect(paginatePerformance(report, 26, 1, 'maxMs').slowPagination).toMatchObject({
      totalItems: 1251,
      totalPages: 26,
      page: 26,
    })
  })
  it('sorts all routes before paginating and clamps final pages', () => {
    const value = paginatePerformance(report, 999, 3, 'count')
    expect(value.groups).toHaveLength(30)
    expect(value.groups[0].count).toBe(30)
    expect(value.slowest).toHaveLength(1)
    expect(value.snapshot).toBe('fixed')
  })
  it('provides stable empty pagination', () =>
    expect(paginatePerformance({ groups: [], slowest: [] }, 1, 1, 'maxMs').slowPagination).toMatchObject({
      page: 1,
      totalItems: 0,
      totalPages: 1,
    }))
})
