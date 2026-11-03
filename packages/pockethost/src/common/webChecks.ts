/** Declarative read-only checks. No user script, arbitrary host or HTTP mutation is accepted. */
export type RequiredEnvironment = { name: string; scope: 'build' | 'runtime' | 'both' }
export type PreflightChecks = {
  enabled: boolean
  requiredEnvironment: RequiredEnvironment[]
  privateDatabases: boolean
  publicUrls: { name: string; target: 'application' | 'privateDatabase'; bindingId?: string }[]
  authentication: { bindingId: string; collection: string; identityEnv: string; passwordEnv: string }[]
}
export type JourneyCheck = {
  id: string
  name: string
  enabled: boolean
  intervalMinutes: number
  domain: string
  auth: {
    kind: 'none' | 'json'
    path: string
    identityField: string
    passwordField: string
    identity: string
    tokenField: string
  }
  steps: { name: string; path: string; status: number; jsonField: string; expected: string | null }[]
}
export type ApplicationChecks = { version: 1; preflight: PreflightChecks; journeys: JourneyCheck[] }
const id = /^[a-z0-9]{15}$/
const variable = /^[A-Za-z_][A-Za-z0-9_]{0,119}$/
const failure = () => {
  throw new Error('Configuration de contrôles invalide. Vérifiez les champs et les limites indiquées.')
}
const keys = (value: any, allowed: string[]) => {
  if (
    !value ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    Object.keys(value).some((key) => !allowed.includes(key))
  )
    failure()
}
const text = (value: unknown, max: number, empty = false) =>
  typeof value === 'string' && value.length <= max && (empty || !!value.trim()) && !/[\x00-\x1f\x7f]/.test(value)
export const safeCheckPath = (value: unknown): value is string =>
  text(value, 1000) &&
  (value as string).startsWith('/') &&
  !(value as string).startsWith('//') &&
  !(value as string).includes('\\') &&
  !(value as string).includes('#')
export const safeCheckAuthPath = (value: unknown): value is string =>
  typeof value === 'string' &&
  /^\/(?:api\/auth\/login|api\/client-space\/auth\/login|api\/login|login|api\/collections\/[_a-zA-Z][_a-zA-Z0-9]{0,99}\/auth-with-password)$/.test(
    value
  )
const field = (value: unknown) =>
  typeof value === 'string' &&
  /^[a-zA-Z_][a-zA-Z_0-9]*(?:\.[a-zA-Z_][a-zA-Z_0-9]*){0,3}$/.test(value) &&
  !(value as string).split('.').some((part) => ['__proto__', 'prototype', 'constructor'].includes(part))
const list = (value: unknown, max: number): any[] => {
  if (!Array.isArray(value) || value.length > max) failure()
  return value as any[]
}
export const emptyApplicationChecks = (): ApplicationChecks => ({
  version: 1,
  preflight: { enabled: false, requiredEnvironment: [], privateDatabases: false, publicUrls: [], authentication: [] },
  journeys: [],
})

export const validateApplicationChecks = (value: unknown): ApplicationChecks => {
  const input = value as ApplicationChecks
  keys(input, ['version', 'preflight', 'journeys'])
  if (input.version !== 1) failure()
  const preflight = input.preflight
  keys(preflight, ['enabled', 'requiredEnvironment', 'privateDatabases', 'publicUrls', 'authentication'])
  if (typeof preflight.enabled !== 'boolean' || typeof preflight.privateDatabases !== 'boolean') failure()
  const names = new Set<string>()
  for (const row of list(preflight.requiredEnvironment, 100)) {
    keys(row, ['name', 'scope'])
    if (
      typeof row.name !== 'string' ||
      !variable.test(row.name) ||
      !['build', 'runtime', 'both'].includes(row.scope) ||
      names.has(row.name)
    )
      failure()
    names.add(row.name)
  }
  for (const row of list(preflight.publicUrls, 20)) {
    keys(row, ['name', 'target', 'bindingId'])
    if (
      typeof row.name !== 'string' ||
      !variable.test(row.name) ||
      !['application', 'privateDatabase'].includes(row.target) ||
      (row.target === 'privateDatabase' && (!id.test(row.bindingId) || typeof row.bindingId !== 'string')) ||
      (row.target === 'application' && row.bindingId !== undefined)
    )
      failure()
  }
  for (const row of list(preflight.authentication, 4)) {
    keys(row, ['bindingId', 'collection', 'identityEnv', 'passwordEnv'])
    if (
      typeof row.bindingId !== 'string' ||
      !id.test(row.bindingId) ||
      typeof row.collection !== 'string' ||
      !/^[_a-zA-Z][_a-zA-Z0-9]{0,99}$/.test(row.collection) ||
      typeof row.identityEnv !== 'string' ||
      !variable.test(row.identityEnv) ||
      typeof row.passwordEnv !== 'string' ||
      !variable.test(row.passwordEnv) ||
      /^(NEXT_PUBLIC_|PUBLIC_|VITE_)/.test(row.passwordEnv)
    )
      failure()
  }
  const identifiers = new Set<string>()
  for (const journey of list(input.journeys, 6)) {
    keys(journey, ['id', 'name', 'enabled', 'intervalMinutes', 'domain', 'auth', 'steps'])
    if (
      typeof journey.id !== 'string' ||
      !id.test(journey.id) ||
      identifiers.has(journey.id) ||
      !text(journey.name, 80) ||
      typeof journey.enabled !== 'boolean' ||
      !Number.isInteger(journey.intervalMinutes) ||
      journey.intervalMinutes < 1 ||
      journey.intervalMinutes > 1440 ||
      (journey.domain !== 'generated' && (typeof journey.domain !== 'string' || !id.test(journey.domain)))
    )
      failure()
    identifiers.add(journey.id)
    const auth = journey.auth
    keys(auth, ['kind', 'path', 'identityField', 'passwordField', 'identity', 'tokenField'])
    if (!['none', 'json'].includes(auth.kind)) failure()
    if (
      auth.kind === 'json' &&
      (!safeCheckAuthPath(auth.path) ||
        !field(auth.identityField) ||
        auth.identityField.includes('.') ||
        !field(auth.passwordField) ||
        auth.passwordField.includes('.') ||
        auth.identityField === auth.passwordField ||
        !text(auth.identity, 254) ||
        (auth.tokenField !== '' && !field(auth.tokenField)))
    )
      failure()
    const steps = list(journey.steps, 10)
    if (!steps.length) failure()
    for (const step of steps) {
      keys(step, ['name', 'path', 'status', 'jsonField', 'expected'])
      if (
        !text(step.name, 80) ||
        !safeCheckPath(step.path) ||
        !Number.isInteger(step.status) ||
        step.status < 200 ||
        step.status > 499 ||
        (step.jsonField !== '' && !field(step.jsonField)) ||
        (step.expected !== null && !text(step.expected, 256, true))
      )
        failure()
    }
  }
  // Rebuild through JSON to drop caller prototypes and non-data values.
  return JSON.parse(JSON.stringify(input))
}
