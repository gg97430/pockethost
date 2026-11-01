import { describe, expect, it } from 'vitest'
import {
  HOST_SERIES,
  RESOURCE_GIB,
  resourceFormatBytes,
  resourceFormatPercent,
  resourceHistory,
  resourceHref,
  resourcePercent,
  resourceSegments,
  resourceSortRows,
  resourceValue,
  type PlatformResourceHistoryPoint,
} from './platformResources'

const point = (timestamp: number, value: number | null): PlatformResourceHistoryPoint => ({
  timestamp,
  cpuPercent: value,
  memoryBytes: value === null ? null : value * RESOURCE_GIB,
  diskBytes: null,
  categories: Object.fromEntries(
    ['databases', 'hosting', 'builds', 'platform', 'backups', 'system'].map((id) => [
      id,
      { cpuPercent: null, memoryBytes: null, diskBytes: null },
    ])
  ) as PlatformResourceHistoryPoint['categories'],
})
describe('resource history presentation', () => {
  it('distinguishes unavailable values from a real zero', () => {
    expect(resourceFormatPercent(null)).toBe('—')
    expect(resourceFormatPercent(0)).toMatch(/^0/)
    expect(resourceFormatBytes(undefined)).toBe('—')
    expect(resourceFormatBytes(0)).toBe('0 o')
    expect(resourcePercent(0, 32)).toBe(0)
    expect(resourcePercent(null, 32)).toBeNull()
    expect(resourcePercent(2, 0)).toBeNull()
  })
  it('does not create readings before collection began and keeps actual timestamps', () => {
    const points = resourceHistory([point(75000, 3), point(60000, 2), point(60000, 4), point(999999, 5)], 0, 90000)
    expect(points.map((row) => row.timestamp)).toEqual([60000, 75000])
    expect(points[0]!.cpuPercent).toBe(4)
    expect(resourceHistory([], 0, 90000)).toEqual([])
  })
  it('breaks curves at missing readings and collection interruptions, keeping one reading as a point', () => {
    const points = [point(0, 10), point(15000, 20), point(30000, null), point(45000, 30), point(120000, 40)]
    const segments = resourceSegments(points, HOST_SERIES, 'cpuPercent', 0, 120000, 100, 15)
    expect(segments.map((row) => row.length)).toEqual([2, 1, 1])
    expect(segments[0]![0]).toEqual({ x: 54, y: 164 })
    expect(segments[2]![0]).toEqual({ x: 696, y: 116 })
  })
  it('scales memory on the same host capacity and does not substitute host values for a missing category', () => {
    const row = point(0, 8)
    expect(resourceSegments([row], HOST_SERIES, 'memoryBytes', 0, 90000, 32, 15)[0]![0]!.y).toBe(140)
    expect(resourceValue(row, { ...HOST_SERIES, category: 'hosting' }, 'cpuPercent')).toBeNull()
    expect(resourceSegments([row], { ...HOST_SERIES, category: 'hosting' }, 'cpuPercent', 0, 90000, 100, 15)).toEqual(
      []
    )
  })
  it('only links operator rows to known same-origin detail routes', () => {
    expect(resourceHref('/applications/abcdefghijk1234')).toBe('/applications/abcdefghijk1234')
    expect(resourceHref('/instances/abcdefghijk1234/usage')).toBe('/instances/abcdefghijk1234/usage')
    for (const href of [
      'https://attacker.test/',
      '//attacker.test',
      '/admin',
      'javascript:alert(1)',
      '/applications/not-an-id',
    ])
      expect(resourceHref(href)).toBeUndefined()
  })
})

describe('resource table sorting', () => {
  const rows = Object.freeze([
    { id: 'missing', value: null },
    { id: 'first', value: 4 },
    { id: 'zero', value: 0 },
    { id: 'second', value: 4 },
    { id: 'undefined', value: undefined },
    { id: 'nan', value: NaN },
  ])
  it('keeps missing values last in ascending and descending order', () => {
    expect(resourceSortRows(rows, (row) => row.value, 'asc').map((row) => row.id)).toEqual([
      'zero',
      'first',
      'second',
      'missing',
      'undefined',
      'nan',
    ])
    expect(resourceSortRows(rows, (row) => row.value, 'desc').map((row) => row.id)).toEqual([
      'first',
      'second',
      'zero',
      'missing',
      'undefined',
      'nan',
    ])
  })
  it('keeps ties stable without modifying the input array', () => {
    expect(
      resourceSortRows(rows, (row) => row.value, 'desc')
        .filter((row) => row.value === 4)
        .map((row) => row.id)
    ).toEqual(['first', 'second'])
    expect(rows[0]!.id).toBe('missing')
  })
  it('sorts names naturally in French and keeps equal accented names stable', () => {
    const names = [{ name: 'Projet 10' }, { name: 'école' }, { name: 'Projet 2' }, { name: 'ecole' }]
    expect(resourceSortRows(names, (row) => row.name, 'asc').map((row) => row.name)).toEqual([
      'école',
      'ecole',
      'Projet 2',
      'Projet 10',
    ])
    expect(resourceSortRows(names, (row) => row.name, 'desc').map((row) => row.name)).toEqual([
      'Projet 10',
      'Projet 2',
      'école',
      'ecole',
    ])
  })
})
