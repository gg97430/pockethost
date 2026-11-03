import { describe, expect, it } from 'vitest'
import { monitoringReport } from '../../../../../common/platformMonitoring'
import { readOperatorDiagnostics } from './diagnostics'

const report = () => monitoringReport('fixture', 'fixture.invalid', [])
const record = (data: Record<string, string>) => ({ id: data.id, getString: (key: string) => data[key] || '' })
describe('operator diagnostic metadata boundary', () => {
  it('compares recent event dates using the native PocketBase storage format', () => {
    let cutoff = ''
    readOperatorDiagnostics(report(), {
      findRecordsByFilter: (
        collection: string,
        _filter: string,
        _sort: string,
        _limit: number,
        _offset: number,
        params: any
      ) => {
        if (collection === 'web_deployments') cutoff = params.cutoff
        return []
      },
    })
    expect(cutoff).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}Z$/)
    expect(Math.abs(Date.now() - Date.parse(cutoff) - 3600000)).toBeLessThan(1000)
  })
  it('never returns deployment error text, bridge tokens or actor secrets', () => {
    const calls: string[] = []
    const store = {
      findRecordsByFilter: (collection: string) => {
        calls.push(collection)
        return collection === 'web_deployments'
          ? [
              record({
                id: 'a'.repeat(15),
                appId: 'b'.repeat(15),
                status: 'failed',
                finishedAt: new Date().toISOString(),
                error: 'La commande de construction a échoué. secret-value',
              }),
            ]
          : [
              record({
                id: 'c'.repeat(15),
                appId: 'b'.repeat(15),
                instanceId: 'd'.repeat(15),
                status: 'unavailable',
                errorCode: 'secret-value',
                tokenEncrypted: 'secret-value',
              }),
            ]
      },
    }
    const result = readOperatorDiagnostics(report(), store)
    expect(calls).toEqual(['web_private_db_bindings', 'web_deployments'])
    expect(result.events[0]?.category).toBe('build')
    expect(JSON.stringify(result)).not.toContain('secret-value')
  })
  it('explicitly marks unavailable inventories while retaining the valid health snapshot', () => {
    const value = report()
    const result = readOperatorDiagnostics(value, {
      findRecordsByFilter: () => {
        throw Error('unavailable')
      },
    })
    expect(result.checkedAt).toBe(value.checkedAt)
    expect(result.dependencyInventoryAvailable).toBe(false)
    expect(result.eventInventoryAvailable).toBe(false)
    expect(result.items).toEqual([])
  })
  it('rejects an oversized dependency inventory as incomplete rather than claiming it complete', () => {
    const result = readOperatorDiagnostics(report(), {
      findRecordsByFilter: (collection: string) =>
        collection === 'web_private_db_bindings' ? Array(5001).fill({}) : [],
    })
    expect(result.dependencyInventoryAvailable).toBe(false)
    expect(result.eventInventoryAvailable).toBe(true)
  })
})
