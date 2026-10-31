import { beforeAll, describe, expect, it, vi } from 'vitest'

vi.mock('./api', () => ({
  decryptWebValue: vi.fn(),
  encryptWebValue: vi.fn(),
  webAppsCapability: () => ({}),
  webInput: vi.fn(),
  webJson: vi.fn(),
}))
vi.mock('./access', () => ({ requireWebAppPermission: vi.fn() }))

import { validateRuntimeProfile } from './runtimes'

const image = 'typesense/typesense@sha256:' + 'a'.repeat(64)
const compose = () => ({
  kind: 'compose',
  compose: {
    front: 'app',
    services: {
      app: {
        dockerfile: 'Dockerfile',
        resources: { cpuMillicores: 500, memoryMiB: 768, pids: 256 },
        depends_on: { typesense: { condition: 'service_healthy' } },
      },
      typesense: {
        image,
        port: 8108,
        resources: { cpuMillicores: 2000, memoryMiB: 2048, pids: 256 },
        healthcheck: { test: ['CMD', 'bash', '-c', 'exit 0'], startPeriodSeconds: 10 },
      },
    },
  },
})

beforeAll(() => {
  vi.stubGlobal('BadRequestError', class extends Error {})
})

describe('bounded Compose service resources and dependencies', () => {
  it('accepts Typesense and reports the actual aggregate without changing ordinary limits', () => {
    const value = validateRuntimeProfile(compose())
    expect(value.resources).toEqual({ cpuMillicores: 2500, memoryMiB: 2816, pids: 512 })
    expect(value.compose.services.app.depends_on).toEqual({ typesense: { condition: 'service_healthy' } })
    expect(value.compose.services.typesense.healthcheck.startPeriodSeconds).toBe(10)
    expect(() => validateRuntimeProfile({ kind: 'dockerfile', resources: { cpuMillicores: 1001 } })).toThrow()
    expect(() => validateRuntimeProfile({ kind: 'nextjs', resources: { memoryMiB: 1537 } })).toThrow()
  })

  it.each(['cpuMillicores', 'memoryMiB', 'pids'] as const)('rejects a sum above the group %s ceiling', (field) => {
    const source = compose()
    for (const service of Object.values(source.compose.services))
      service.resources = { cpuMillicores: 2000, memoryMiB: 4096, pids: 256 }
    source.compose.services.app.resources[field] += 1
    expect(() => validateRuntimeProfile(source)).toThrow()
  })

  it('preserves list dependencies and idempotently normalizes old explicit service budgets', () => {
    const source: any = compose()
    source.compose.services.app.depends_on = ['typesense']
    const once = validateRuntimeProfile(source)
    expect(once.compose.services.app.depends_on).toEqual(['typesense'])
    expect(validateRuntimeProfile(once)).toEqual(once)
  })

  it.each([
    { condition: 'service_completed_successfully' },
    { condition: 'service_healthy', required: false },
    { other: 'service_healthy' },
    'service_healthy',
  ])('rejects unsupported/one-shot completion semantics %j', (condition) => {
    const source: any = compose()
    source.compose.services.app.depends_on.typesense = condition
    expect(() => validateRuntimeProfile(source)).toThrow()
  })

  it('rejects cycles, host options and literal environment values', () => {
    const cycle: any = compose()
    cycle.compose.services.typesense.depends_on = { app: { condition: 'service_started' } }
    expect(() => validateRuntimeProfile(cycle)).toThrow()
    for (const extra of [
      { privileged: true },
      { ports: ['8108:8108'] },
      { environment: { API_KEY: 'literal' } },
      { volumes: ['/host:/data'] },
      { profiles: ['maintenance'] },
    ]) {
      const source: any = compose()
      Object.assign(source.compose.services.typesense, extra)
      expect(() => validateRuntimeProfile(source)).toThrow()
    }
  })
})
