import { describe, expect, it } from 'vitest'
import {
  migrationDnsReviewable,
  migrationDrillVerified,
  migrationExpired,
  migrationExportCommand,
  migrationHasWriteBarrier,
  migrationResourceHref,
  type MigrationItem,
} from './projectMigration'

const now = Date.parse('2026-11-03T14:00:00Z')
const fixture = () =>
  ({
    id: 'm'.repeat(15),
    revision: 'a'.repeat(64),
    source: 'app5',
    target: 'app4',
    status: 'review',
    plan: {
      projectId: 'p'.repeat(15),
      expiresAt: now / 1000 - 1,
      drill: { verified: true, sandboxUid: 1002, noExternalNetwork: true, cleanedUp: true, services: 3 },
      writeBarrier: {
        version: 1,
        projectId: 'p'.repeat(15),
        source: 'app5',
        attestedAt: '2026-11-03T13:00:00Z',
        sourceWritesPaused: true,
        sourceCronsPaused: true,
        sourceWorkersPaused: true,
        attestationOnly: true,
      },
    },
    dnsPreview: {
      version: 1,
      migrationId: 'm'.repeat(15),
      planRevision: 'a'.repeat(64),
      revision: 'd'.repeat(64),
      expiresAt: now / 1000 + 900,
      changes: [
        {
          host: 'fixture.example.invalid',
          before: [],
          after: { type: 'A', name: 'fixture.example.invalid', content: '51.255.69.154', proxied: true },
        },
      ],
    },
  }) as unknown as MigrationItem
describe('migration review boundaries', () => {
  it('builds resource links only to known fleet hosts and fresh PocketBase identities', () => {
    expect(migrationResourceHref('app4', 'database', 'a'.repeat(15))).toBe(
      'https://app4.monappli.re/instances/' + 'a'.repeat(15)
    )
    expect(migrationResourceHref('https://attacker.invalid', 'application', 'a'.repeat(15))).toBeUndefined()
    expect(migrationResourceHref('app1', 'application', 'a'.repeat(15))).toBeUndefined()
    expect(migrationResourceHref('app4', 'application', '../../admin')).toBeUndefined()
  })
  it('separates initial plan expiry from the independent DNS preview lifetime', () => {
    const item = fixture()
    expect(migrationExpired(item, now)).toBe(true)
    item.status = 'released'
    expect(migrationExpired(item, now)).toBe(false)
    expect(migrationDnsReviewable(item, now)).toBe(true)
    expect(migrationDnsReviewable(item, now + 901000)).toBe(false)
  })
  it('binds DNS consent to the current migration and plan revision', () => {
    const item = fixture()
    item.dnsPreview!.planRevision = 'b'.repeat(64)
    expect(migrationDnsReviewable(item, now)).toBe(false)
    item.dnsPreview!.planRevision = item.revision
    item.dnsPreview!.migrationId = 'z'.repeat(15)
    expect(migrationDnsReviewable(item, now)).toBe(false)
  })
  it('requires dedicated sandbox coverage, disabled network and completed cleanup', () => {
    const item = fixture()
    expect(migrationDrillVerified(item)).toBe(true)
    item.plan.drill!.sandboxUid = 1001
    expect(migrationDrillVerified(item)).toBe(false)
    item.plan.drill!.sandboxUid = 1002
    item.plan.drill!.cleanedUp = false
    expect(migrationDrillVerified(item)).toBe(false)
    item.plan.drill!.cleanedUp = true
    item.plan.drill!.services = 0
    expect(migrationDrillVerified(item)).toBe(false)
  })
  it('requires the source freeze attestation to identify the exact source project', () => {
    const item = fixture()
    expect(migrationHasWriteBarrier(item)).toBe(true)
    item.plan.writeBarrier!.source = 'app3'
    expect(migrationHasWriteBarrier(item)).toBe(false)
    item.plan.writeBarrier!.source = 'app5'
    item.plan.writeBarrier!.sourceWorkersPaused = false
    expect(migrationHasWriteBarrier(item)).toBe(false)
  })
  it('never interpolates project names or invalid identifiers into the operator command', () => {
    const value = {
      projectId: 'p'.repeat(15),
      revision: 'a'.repeat(64),
      projectName: '$(touch should-never-run)',
    } as any
    const command = migrationExportCommand(value)
    expect(command).toContain('--project-id ' + 'p'.repeat(15))
    expect(command).not.toContain('touch')
    value.projectId = '../../secret'
    expect(migrationExportCommand(value)).toBe('')
  })
})
