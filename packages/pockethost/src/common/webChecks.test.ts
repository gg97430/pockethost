import { describe, expect, it } from 'vitest'
import { emptyApplicationChecks, safeCheckPath, validateApplicationChecks } from './webChecks'

const journey = () => ({
  id: 'a'.repeat(15),
  name: 'Lecture du tableau de bord',
  enabled: true,
  intervalMinutes: 5,
  domain: 'generated',
  auth: { kind: 'none', path: '', identityField: '', passwordField: '', identity: '', tokenField: '' },
  steps: [{ name: 'Accueil', path: '/', status: 200, jsonField: '', expected: null }],
})
describe('declarative application checks', () => {
  it('leaves existing applications inactive by default', () => {
    expect(validateApplicationChecks(emptyApplicationChecks()).preflight.enabled).toBe(false)
  })
  it('allows bounded GET journeys and required build/runtime environment contracts', () => {
    const config = emptyApplicationChecks()
    config.journeys = [journey()]
    config.preflight.requiredEnvironment = [{ name: 'DATABASE_URL', scope: 'runtime' }]
    expect(validateApplicationChecks(config)).toEqual(config)
  })
  it('rejects host escapes, scripts, paths with injected headers and prototype access', () => {
    for (const path of [
      'https://example.com',
      '//169.254.169.254/',
      '/\\example.com',
      '/test\r\nHost:localhost',
      '/test#fragment',
    ])
      expect(safeCheckPath(path)).toBe(false)
    for (const change of [
      { method: 'POST' },
      { jsonField: '__proto__.token' },
      { path: '//localhost' },
      { status: 500 },
    ]) {
      const config = emptyApplicationChecks(),
        row = journey()
      row.steps = [{ ...row.steps[0]!, ...change }]
      config.journeys = [row]
      expect(() => validateApplicationChecks(config)).toThrow()
    }
  })
  it('requires explicit same-origin JSON authentication and keeps passwords outside configuration', () => {
    const config = emptyApplicationChecks(),
      row = journey()
    row.auth = {
      kind: 'json',
      path: '/api/auth/login',
      identityField: 'email',
      passwordField: 'password',
      identity: 'test@example.invalid',
      tokenField: 'token',
    }
    config.journeys = [row]
    expect(validateApplicationChecks(config)).toEqual(config)
    expect(() =>
      validateApplicationChecks({ ...config, journeys: [{ ...row, auth: { ...row.auth, password: 'secret' } }] })
    ).toThrow()
    expect(() =>
      validateApplicationChecks({
        ...config,
        journeys: [{ ...row, auth: { ...row.auth, path: '/api/invoices/send' } }],
      })
    ).toThrow()
  })
  it('bounds work and refuses duplicate or unscoped references', () => {
    for (const mutate of [
      (c: any) => (c.journeys = [journey(), journey()]),
      (c: any) => (c.journeys = [{ ...journey(), domain: 'http://localhost' }]),
      (c: any) => (c.journeys = [{ ...journey(), intervalMinutes: 0 }]),
      (c: any) =>
        (c.preflight.authentication = [
          { bindingId: 'not-an-id', collection: '_superusers', identityEnv: 'EMAIL', passwordEnv: 'PASSWORD' },
        ]),
    ]) {
      const config = emptyApplicationChecks()
      mutate(config)
      expect(() => validateApplicationChecks(config)).toThrow()
    }
  })
})
