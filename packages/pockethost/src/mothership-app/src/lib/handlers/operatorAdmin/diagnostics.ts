import {
  deploymentDiagnosticCategory,
  platformDiagnostics,
  type DiagnosticBinding,
  type DiagnosticDeployment,
} from '../../../../../common/platformDiagnostics'
import type { MonitoringReport } from '../../../../../common/platformMonitoring'

/** Bounded metadata reads only. Never decrypt configuration or load deployment logs for explanations. */
export const readOperatorDiagnostics = (report: MonitoringReport, store: any = $app) => {
  const bindings: DiagnosticBinding[] = [],
    events: DiagnosticDeployment[] = []
  let dependencyInventoryAvailable = true,
    eventInventoryAvailable = true
  try {
    const rows = store.findRecordsByFilter('web_private_db_bindings', 'enabled=true', 'id', 5001, 0) as core.Record[]
    if (rows.length > 5000) throw Error('Dependency inventory bound exceeded')
    for (const row of rows)
      bindings.push({
        id: row.id,
        appId: row.getString('appId'),
        instanceId: row.getString('instanceId'),
        status: row.getString('status'),
        errorCode: row.getString('errorCode'),
        lastSeenAt: row.getString('lastSeenAt'),
      })
  } catch {
    dependencyInventoryAvailable = false
  }
  try {
    const rows = store.findRecordsByFilter(
      'web_deployments',
      '(status="failed" || status="cancelled") && finishedAt>{:cutoff}',
      '-finishedAt',
      20,
      0,
      { cutoff: new Date(Date.now() - 3600000).toISOString().replace('T', ' ') }
    ) as core.Record[]
    for (const row of rows)
      events.push({
        id: row.id,
        appId: row.getString('appId'),
        status: row.getString('status') as DiagnosticDeployment['status'],
        finishedAt: row.getString('finishedAt'),
        category: deploymentDiagnosticCategory(row.getString('status'), row.getString('error')),
      })
  } catch {
    eventInventoryAvailable = false
  }
  return platformDiagnostics(report, bindings, events, { dependencyInventoryAvailable, eventInventoryAvailable })
}
