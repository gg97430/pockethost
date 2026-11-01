import { describe, expect, it } from 'vitest'
import { monitoringReport, type MonitorCheck } from '../../../common/platformMonitoring'
import { monitoringEmail } from './monitorEmail'

const incident: MonitorCheck = { id: 'db:a', group: 'databases', name: 'CRMAV', state: 'error', detail: 'HTTP 503' }
const report = (checks: MonitorCheck[] = [incident]) =>
  monitoringReport('ns-fixture', 'app4.monappli.re', checks, Date.parse('2026-11-01T05:00:00Z'))

describe('monitoring email', () => {
  it('shows incident severity, source, time, global counters and the dashboard link in both alternatives', () => {
    const email = monitoringEmail(report(), [incident], ['Sauvegardes'])
    expect(email.subject).toBe('[Kolab] Incident détecté · app4.monappli.re')
    for (const body of [email.text, email.html]) {
      for (const value of [
        'CRMAV',
        'HTTP 503',
        'Sauvegardes',
        'ns-fixture',
        '09:00',
        'UTC+4',
        'https://app4.monappli.re/monitoring',
      ])
        expect(body).toContain(value)
    }
    expect(email.html).toContain('ÉTAT AU DERNIER CONTRÔLE')
    expect(email.html).toContain('RÉTABLI')
    expect(email.html).not.toMatch(/<script|<img|<link/i)
  })
  it('distinguishes warnings and recoveries without claiming the whole server is healthy', () => {
    expect(monitoringEmail(report(), [{ ...incident, state: 'warning' }], []).subject).toContain('Point de vigilance')
    const recovered = monitoringEmail(report(), [], ['Site web'])
    expect(recovered.subject).toContain('Services rétablis')
    expect(recovered.text).toContain('1 en erreur')
  })
  it('escapes untrusted names, details and host metadata without losing the text version', () => {
    const row = { ...incident, name: '<img src=x onerror="alert(1)">', detail: 'A & B <script>alert(1)</script>' }
    const email = monitoringEmail({ ...report(), host: '<b>host</b>' }, [row], [])
    expect(email.html).not.toContain('<img')
    expect(email.html).not.toContain('<script>')
    expect(email.html).toContain('&lt;script&gt;')
    expect(email.html).toContain('&lt;b&gt;host&lt;/b&gt;')
    expect(email.text).toContain(row.detail)
  })
  it('refuses a dashboard link with an injected host, path or credentials', () => {
    for (const domain of ['app4.monappli.re@evil.invalid', 'evil.invalid/path', 'evil.invalid?x=', 'evil.invalid:444'])
      expect(() => monitoringEmail({ ...report(), domain }, [incident], [])).toThrow()
  })
  it('bounds the email and clearly reports omitted changes', () => {
    const rows = Array.from({ length: 30 }, (_, i) => ({ ...incident, id: `db:${i}`, name: `Database ${i}` }))
    const email = monitoringEmail(report(rows), rows, [])
    expect(email.text).toContain('5 autre(s) changement(s)')
    expect(email.text).not.toContain('Database 29')
    expect(email.html.length).toBeLessThan(100000)
  })
})
