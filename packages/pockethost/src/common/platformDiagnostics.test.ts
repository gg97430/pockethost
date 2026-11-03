import { describe, expect, it } from 'vitest'
import {
  deploymentDiagnosticCategory,
  platformDiagnostics,
  type DiagnosticBinding,
  type DiagnosticDeployment,
} from './platformDiagnostics'
import { monitoringReport, type MonitorCheck } from './platformMonitoring'

const now = Date.parse('2026-11-03T12:00:00Z')
const db = 'a'.repeat(15),
  app = 'b'.repeat(15)
const check = (id: string, state: MonitorCheck['state'] = 'error'): MonitorCheck => ({
  id,
  name: id,
  state,
  detail: 'Mesure HTTP indépendante',
  group: id.startsWith('database:') ? 'databases' : 'hosting',
})
const report = (rows: MonitorCheck[], date = now) => monitoringReport('fixture', 'fixture.invalid', rows, date)
const link = (value: Partial<DiagnosticBinding> = {}): DiagnosticBinding => ({
  id: 'c'.repeat(15),
  appId: app,
  instanceId: db,
  status: 'ready',
  errorCode: '',
  lastSeenAt: new Date(now).toISOString(),
  ...value,
})
describe('evidence-based operator diagnostics', () => {
  it('groups missing worker measurements without declaring published sites down', () => {
    const value = platformDiagnostics(
      report([
        check('web-worker'),
        check('web-runtime-metrics'),
        check('web-queue'),
        check('web:' + app, 'unknown'),
        check('web:' + 'd'.repeat(15)),
      ]),
      [],
      [],
      { now }
    )
    expect(value.items).toHaveLength(2)
    expect(value.items[0]?.checkIds).toEqual(['web-worker', 'web-runtime-metrics', 'web-queue', 'web:' + app])
    expect(value.items[0]?.explanation).toContain('ne prouve pas l’arrêt des sites')
    expect(value.items[0]?.hypotheses).toEqual([])
    expect(value.items[1]?.id).toBe('web:' + 'd'.repeat(15))
  })
  it('uses an old or future report solely to request current measurements', () => {
    for (const date of [now - 180001, now + 30001]) {
      const value = platformDiagnostics(report([check('database:' + db), check('web:' + app)], date), [link()], [], {
        now,
      })
      expect(value.items).toHaveLength(1)
      expect(value.items[0]?.id).toBe('report-stale')
      expect(value.items[0]?.state).toBe('unknown')
      expect(value.items[0]?.hypotheses).toEqual([])
    }
  })
  it('marks coincident database/application failures as a hypothesis rather than a root cause', () => {
    const value = platformDiagnostics(report([check('database:' + db), check('web:' + app)]), [link()], [], { now })
    expect(value.items).toHaveLength(1)
    expect(value.items[0]?.checkIds).toHaveLength(2)
    expect(value.items[0]?.hypotheses[0]).toContain('ne prouvent pas cette cause')
    expect(value.items[0]?.evidence.every((row) => row.checkedAt === new Date(now).toISOString())).toBe(true)
  })
  it('does not turn an origin HTTPS error into database failure or dependency proof', () => {
    const value = platformDiagnostics(
      report([check('origin'), check('database:' + db, 'ok'), check('web:' + app, 'ok')]),
      [link()],
      [],
      { now }
    )
    expect(value.items).toHaveLength(1)
    expect(value.items[0]?.id).toBe('origin')
    expect(value.items[0]?.explanation).toContain('ne teste pas le moteur des bases')
    expect(value.items[0]?.hypotheses).toEqual([])
  })
  it('retains a healthy application response beside a failing declared base', () => {
    const value = platformDiagnostics(report([check('database:' + db), check('web:' + app, 'ok')]), [link()], [], {
      now,
    })
    expect(value.items[0]?.evidence.map((row) => row.state)).toEqual(['error', 'ok'])
    expect(value.items[0]?.checkIds).toEqual(['database:' + db])
    expect(value.items[0]?.hypotheses).toEqual([])
  })
  it('uses only fresh allowlisted private-link errors, never arbitrary stored details', () => {
    const value = platformDiagnostics(
      report([check('database:' + db, 'ok'), check('web:' + app, 'ok')]),
      [link({ status: 'unavailable', errorCode: 'proxy_unavailable' })],
      [],
      { now }
    )
    expect(value.items[0]?.id).toBe('private-link:' + 'c'.repeat(15))
    expect(value.items[0]?.evidence[0]?.checkedAt).toBe(new Date(now).toISOString())
    expect(value.items[0]?.hypotheses).toEqual([])
    for (const candidate of [
      link({ status: 'unavailable', errorCode: 'secret-value' }),
      link({ status: 'unavailable', errorCode: 'proxy_unavailable', lastSeenAt: new Date(now - 45001).toISOString() }),
      link({ id: '../bad', status: 'unavailable', errorCode: 'proxy_unavailable' }),
    ])
      expect(platformDiagnostics(report([]), [candidate], [], { now }).items).toEqual([])
  })
  it('does not change signals, original checks or counts when grouping', () => {
    const input = report([check('web-worker'), check('web-runtime-metrics'), check('database:' + db, 'paused')]),
      before = JSON.stringify(input)
    platformDiagnostics(input, [], [], { now })
    expect(JSON.stringify(input)).toBe(before)
    expect(input.counts.error).toBe(2)
  })
  it('classifies known events without returning raw errors and retains bounded recent metadata only', () => {
    const category = deploymentDiagnosticCategory('failed', 'La commande de construction a échoué. token=not-public')
    expect(category).toBe('build')
    const events = Array.from(
      { length: 30 },
      (_, i) =>
        ({
          id: String(i).padStart(15, '0'),
          appId: app,
          status: 'failed',
          finishedAt: new Date(now - i * 1000).toISOString(),
          category,
          secret: 'not-public',
        }) as DiagnosticDeployment
    )
    events.push({ ...events[0]!, id: 'e'.repeat(15), finishedAt: new Date(now - 3600001).toISOString() })
    const result = platformDiagnostics(report([]), [], events, { now })
    expect(result.events).toHaveLength(20)
    expect(JSON.stringify(result)).not.toContain('not-public')
    expect(deploymentDiagnosticCategory('cancelled', '')).toBe('other')
    expect(
      deploymentDiagnosticCategory('failed', 'Un commit plus récent est en attente. Cette version ne sera pas publiée.')
    ).toBe('superseded')
    expect(deploymentDiagnosticCategory('failed', 'Capacité toujours insuffisante après 120 s d’attente.')).toBe(
      'resources'
    )
    expect(deploymentDiagnosticCategory('failed', 'La nouvelle version ne passe pas le contrôle de santé.')).toBe(
      'health'
    )
    expect(deploymentDiagnosticCategory('failed', 'Worker interrompu pendant le déploiement.')).toBe('interrupted')
  })
  it('bounds a large fleet report without claiming omitted checks healthy', () => {
    const value = platformDiagnostics(report(Array.from({ length: 130 }, (_, i) => check('other:' + i))), [], [], {
      now,
    })
    expect(value.items).toHaveLength(100)
    expect(value.omittedCount).toBe(30)
  })
  it('makes incomplete inventories explicit and does not invent dependencies', () => {
    const value = platformDiagnostics(report([check('database:' + db), check('web:' + app)]), [], [], {
      now,
      dependencyInventoryAvailable: false,
      eventInventoryAvailable: false,
    })
    expect(value.items).toHaveLength(2)
    expect(value.dependencyInventoryAvailable).toBe(false)
    expect(value.eventInventoryAvailable).toBe(false)
    expect(value.items.every((row) => !row.hypotheses.length)).toBe(true)
  })
})
