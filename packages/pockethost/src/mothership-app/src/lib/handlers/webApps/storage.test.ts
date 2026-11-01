import { beforeAll, describe, expect, it, vi } from 'vitest'

vi.mock('./api', () => ({
  decryptWebValue: vi.fn(),
  encryptWebValue: vi.fn(),
  queueWebDeployment: vi.fn(),
  webDirectLoopback: vi.fn(),
  webEnabled: vi.fn(),
  webInput: vi.fn(),
  webJson: vi.fn(),
  webRoot: vi.fn(),
}))
vi.mock('./access', () => ({ requireWebAppPermission: vi.fn() }))
vi.mock('./projects', () => ({ assertWebDeploymentInScope: vi.fn() }))

import { restoreWebStorageBackup } from './storage'

beforeAll(() => {
  vi.stubGlobal('BadRequestError', class extends Error {})
  vi.stubGlobal('$security', { randomString: () => 'r'.repeat(24) })
})

const apply = (volumes: any) => {
  const values: Record<string, any> = {}
  const row = {
    set: (key: string, value: any) => {
      values[key] = value
    },
  }
  const store = { findRecordsByFilter: () => [row], save: vi.fn() }
  restoreWebStorageBackup({ id: 'a'.repeat(15) } as any, volumes, store)
  return values.volumes
}

describe('exact Next image cache named volume', () => {
  it('accepts only the cache target with existing size and ownership workflow', () => {
    expect(apply([{ name: 'next-cache', target: '/app/.next/cache', sizeMiB: 256 }])).toEqual([
      { name: 'next-cache', target: '/app/.next/cache', sizeMiB: 256, readOnly: false },
    ])
  })

  it.each([
    '/app/.next',
    '/app/.next/server',
    '/app/node_modules',
    '/app',
    '/app/.next/cache/images',
    '/app/.next/cache-extra',
    '/app/.next/cache/../server',
    '/app/.next/cache\n',
  ])('still refuses code/extended target %s', (target) => {
    expect(() => apply([{ name: 'next-cache', target, sizeMiB: 256 }])).toThrow()
  })

  it('retains quota and overlapping-mount validation', () => {
    expect(() => apply([{ name: 'next-cache', target: '/app/.next/cache', sizeMiB: 2049 }])).toThrow()
    expect(() =>
      apply([
        { name: 'one', target: '/app/.next/cache' },
        { name: 'two', target: '/app/.next/cache' },
      ])
    ).toThrow()
    expect(() =>
      apply([
        { name: 'one', target: '/data', sizeMiB: 2048 },
        { name: 'two', target: '/mnt/a', sizeMiB: 2048 },
        { name: 'three', target: '/app/.next/cache', sizeMiB: 16 },
      ])
    ).toThrow()
  })
})
