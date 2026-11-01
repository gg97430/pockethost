export type WebProjectLimits = {
  traffic: { enabled: boolean; requestsPerMinute: number; concurrentRequests: number }
  resources: {
    enabled: boolean
    cpuMillicores: number
    memoryMiB: number
    buildMinutes: number
    queuedBuilds: number
  }
}

export const defaultWebProjectLimits = (): WebProjectLimits => ({
  traffic: { enabled: false, requestsPerMinute: 600, concurrentRequests: 50 },
  resources: { enabled: false, cpuMillicores: 2000, memoryMiB: 3072, buildMinutes: 15, queuedBuilds: 3 },
})

/** Shared validation for the API, registry and dashboard; no coercion of booleans or numbers. */
export function normalizeWebProjectLimits(input: unknown = {}): WebProjectLimits {
  const object = (value: unknown, keys: string[]): Record<string, unknown> => {
    if (
      !value ||
      typeof value !== 'object' ||
      Array.isArray(value) ||
      Object.keys(value).some((k) => !keys.includes(k))
    )
      throw Error('Réglages de limites invalides.')
    return value as Record<string, unknown>
  }
  const top = object(input, ['traffic', 'resources']),
    defaults = defaultWebProjectLimits()
  const traffic = { ...defaults.traffic, ...object(top.traffic ?? {}, Object.keys(defaults.traffic)) }
  const resources = { ...defaults.resources, ...object(top.resources ?? {}, Object.keys(defaults.resources)) }
  const integer = (value: unknown, min: number, max: number) => {
    if (!Number.isInteger(value) || (value as number) < min || (value as number) > max)
      throw Error(`Limite entière attendue entre ${min} et ${max}.`)
  }
  if (typeof traffic.enabled !== 'boolean' || typeof resources.enabled !== 'boolean')
    throw Error('L’activation des limites doit être explicite.')
  integer(traffic.requestsPerMinute, 1, 1_000_000)
  integer(traffic.concurrentRequests, 1, 10_000)
  integer(resources.cpuMillicores, 50, 32_000)
  integer(resources.memoryMiB, 64, 131_072)
  integer(resources.buildMinutes, 1, 30)
  integer(resources.queuedBuilds, 1, 100)
  return { traffic, resources } as WebProjectLimits
}
