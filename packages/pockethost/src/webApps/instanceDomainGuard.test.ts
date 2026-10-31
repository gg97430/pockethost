import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import vm from 'node:vm'
import ts from 'typescript'
import { describe, expect, it } from 'vitest'

const source = readFileSync(
  resolve('packages/pockethost/src/mothership-app/src/lib/handlers/webApps/domains.ts'),
  'utf8'
)
const build = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
const guards = (enabled = true) => {
  const api: any = {},
    context = {
      exports: api,
      require: () => ({}),
      $os: { getenv: (name: string) => (name === 'WEB_APPS_ENABLED' ? (enabled ? 'true' : 'false') : 'monappli.re') },
      $app: {
        findCollectionByNameOrId: () => ({}),
        findRecordsByFilter: (
          _collection: string,
          _filter: string,
          _sort: string,
          _limit: number,
          _offset: number,
          params: { host: string }
        ) => (params.host === 'www.example.com' ? [{ id: 'registered-web-domain' }] : []),
      },
      BadRequestError: class extends Error {},
    }
  vm.runInNewContext(build, context)
  return api
}
const event = (current: Record<string, string>, previous: Record<string, string> = {}) => {
  let called = 0
  return {
    record: {
      getString: (key: string) => current[key] || '',
      original: () => ({ getString: (key: string) => previous[key] || '' }),
    },
    next: () => {
      called++
    },
    get called() {
      return called
    },
  }
}
describe('native PocketBase request reservations', () => {
  it('blocks reserved namespace on native create and rename', () => {
    const api = guards()
    expect(() => api.GuardWebInstanceDomainCreate(event({ subdomain: 'web-site' }))).toThrow('réservé')
    expect(() => api.GuardWebInstanceDomainUpdate(event({ subdomain: 'web-site' }, { subdomain: 'old-db' }))).toThrow(
      'réservé'
    )
  })
  it('preserves existing legacy names and ordinary database changes', () => {
    const api = guards(),
      legacy = event({ subdomain: 'web-legacy' }, { subdomain: 'web-legacy' }),
      normal = event({ subdomain: 'ordinary-db' })
    api.GuardWebInstanceDomainUpdate(legacy)
    api.GuardWebInstanceDomainCreate(normal)
    expect(legacy.called).toBe(1)
    expect(normal.called).toBe(1)
  })
  it('rejects custom domains reserved by web applications including canonical variants', () => {
    const api = guards()
    expect(() =>
      api.GuardWebInstanceDomainUpdate(event({ subdomain: 'db', cname: 'WWW.EXAMPLE.COM.' }, { subdomain: 'db' }))
    ).toThrow('réservé')
    expect(() => api.GuardWebInstanceDomainCreate(event({ subdomain: 'db', cname: 'web-site.monappli.re' }))).toThrow(
      'réservé'
    )
  })
  it('does not change disabled-node behavior', () => {
    const api = guards(false),
      req = event({ subdomain: 'web-site', cname: 'www.example.com' })
    api.GuardWebInstanceDomainCreate(req)
    expect(req.called).toBe(1)
  })
})
