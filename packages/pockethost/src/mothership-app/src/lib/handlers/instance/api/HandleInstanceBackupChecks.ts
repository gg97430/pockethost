import { instanceTransferBackupTools as backups } from './HandleInstanceBackups'
import { ReportBackupCheckIncident } from './HandleInstanceMonitoring'
import { BACKUP_VERIFIER } from './backupVerification'
import { operationJson, operationsAccess, recordInstanceAction } from './instanceOperationsAccess'
const CHECKS = 'instance_backup_checks',
  POLICY = 'instance_backup_check_policies',
  LOCK = 'ph_backup_check_worker'
const policyFor = (id: string) => {
  try {
    return $app.findFirstRecordByFilter(POLICY, 'instanceId = {:id}', { id })
  } catch {
    return null
  }
}
const progressPath = (id: string) => `${backups.dataRoot()}/backup-checks/${id}.json`
const serialize = (r: core.Record) => {
  let result = JSON.parse(r.getString('result') || '{}')
  if (r.getString('status') === 'running')
    try {
      result = JSON.parse(toString($os.readFile(progressPath(r.id))))
    } catch {}
  return {
    id: r.id,
    backupId: r.getString('backupId'),
    status: r.getString('status'),
    created: r.getString('created'),
    startedAt: r.getString('startedAt'),
    finishedAt: r.getString('finishedAt'),
    result,
  }
}
const enqueue = (instance: core.Record, user?: core.Record, requested = '') => {
  const pending = $app.findRecordsByFilter(
    CHECKS,
    'instanceId = {:id} && (status = "queued" || status = "running")',
    '',
    1,
    0,
    { id: instance.id }
  )[0]
  if (pending) return pending
  const backup = requested ? $app.findRecordById('instance_backups', requested) : backups.latestReady(instance.id)
  if (!backup || backup.getString('instance') !== instance.id || backup.getString('status') !== 'ready')
    throw new BadRequestError('Aucune sauvegarde prête à vérifier.')
  const row = new Record($app.findCollectionByNameOrId(CHECKS))
  row.set('instanceId', instance.id)
  row.set('backupId', backup.id)
  row.set('status', 'queued')
  try {
    $app.save(row)
  } catch (error) {
    const existing = $app.findRecordsByFilter(
      CHECKS,
      'instanceId = {:id} && (status = "queued" || status = "running")',
      '',
      1,
      0,
      { id: instance.id }
    )[0]
    if (existing) return existing
    throw error
  }
  recordInstanceAction(instance, user, 'backup-check.queued', { check: row.id, backup: backup.id })
  return row
}
export const HandleInstanceBackupChecksGet = (e: core.RequestEvent) => {
  const { instance } = operationsAccess(e),
    policy = policyFor(instance.id)
  const page = Math.max(1, Math.floor(Number(e.request!.url!.query().get('page')) || 1))
  const rows = $app.findRecordsByFilter(CHECKS, 'instanceId = {:id}', '-created,-id', 26, (page - 1) * 25, {
    id: instance.id,
  })
  return operationJson(e, {
    policy: {
      enabled: policy?.getBool('enabled') || false,
      intervalDays: policy?.getInt('intervalDays') || 7,
      nextRunAt: policy?.getString('nextRunAt') || '',
    },
    items: rows.slice(0, 25).map(serialize),
    hasMore: rows.length > 25,
    page,
  })
}
export const HandleInstanceBackupCheckCreate = (e: core.RequestEvent) => {
  const { instance, user } = operationsAccess(e),
    input = JSON.parse(JSON.stringify(e.requestInfo().body || {}))
  if (input.backupId && !/^[a-z0-9]{15}$/.test(input.backupId)) throw new BadRequestError('Sauvegarde invalide.')
  return operationJson(e, { check: serialize(enqueue(instance, user, input.backupId || '')) }, 202)
}
export const HandleInstanceBackupCheckPolicy = (e: core.RequestEvent) => {
  const { instance, user } = operationsAccess(e),
    input = JSON.parse(JSON.stringify(e.requestInfo().body || {}))
  const interval = Number(input.intervalDays)
  if (typeof input.enabled !== 'boolean' || !Number.isInteger(interval) || interval < 1 || interval > 30)
    throw new BadRequestError('Fréquence attendue entre 1 et 30 jours.')
  const policy = policyFor(instance.id) || new Record($app.findCollectionByNameOrId(POLICY))
  policy.set('instanceId', instance.id)
  policy.set('enabled', input.enabled)
  policy.set('intervalDays', interval)
  if (!policy.getString('nextRunAt') || input.enabled)
    policy.set('nextRunAt', new Date(Date.now() + interval * 86400000).toISOString())
  $app.save(policy)
  recordInstanceAction(instance, user, 'backup-check.policy', { enabled: input.enabled, intervalDays: interval })
  return operationJson(e, { ok: true })
}
export const BackupCheckBootstrap = () => {
  // onBootstrap runs before automatic migrations during an upgrade.
  try {
    $app.findCollectionByNameOrId(CHECKS)
  } catch {
    return
  }
  // Interrupted checks are never silently reported as successful or resumed against changed archives.
  for (const row of $app.findRecordsByFilter(CHECKS, 'status = "running"', '', 1000, 0)) {
    if (!row) continue
    row.set('status', 'failed')
    row.set('finishedAt', new Date().toISOString())
    row.set('result', { message: 'Contrôle interrompu par le redémarrage du serveur.' })
    $app.save(row)
  }
}
export const BackupCheckDispatcher = () => {
  let claimed = false
  $app.store().setFunc(LOCK, (raw: unknown) => {
    if (raw) return raw
    claimed = true
    return 'running'
  })
  if (!claimed) return
  try {
    for (const policy of $app.findRecordsByFilter(
      POLICY,
      'enabled = true && nextRunAt <= {:now}',
      'nextRunAt',
      100,
      0,
      { now: new Date().toISOString() }
    )) {
      if (!policy) continue
      let instance: core.Record
      try {
        instance = $app.findRecordById('instances', policy.getString('instanceId'))
      } catch {
        policy.set('enabled', false)
        $app.save(policy)
        continue
      }
      try {
        enqueue(instance)
      } catch {
        const failed = new Record($app.findCollectionByNameOrId(CHECKS))
        failed.set('instanceId', instance.id)
        failed.set('status', 'failed')
        failed.set('finishedAt', new Date().toISOString())
        failed.set('result', { message: 'Aucune sauvegarde prête disponible pour le contrôle automatique.' })
        $app.save(failed)
        recordInstanceAction(instance, undefined, 'backup-check.failed', { check: failed.id })
        ReportBackupCheckIncident(instance, false)
      }
      policy.set(
        'nextRunAt',
        new Date(Date.now() + Math.max(1, policy.getInt('intervalDays')) * 86400000).toISOString()
      )
      $app.save(policy)
    }
    const row = $app.findRecordsByFilter(CHECKS, 'status = "queued"', 'created', 1, 0)[0]
    if (!row) return
    row.set('status', 'running')
    row.set('startedAt', new Date().toISOString())
    $app.save(row)
    let instance: core.Record | undefined
    try {
      instance = $app.findRecordById('instances', row.getString('instanceId'))
      backups.assertNoRunningOperation(instance.id)
      const backup = $app.findRecordById('instance_backups', row.getString('backupId'))
      if (backup.getString('status') !== 'ready' || backup.getString('instance') !== instance.id)
        throw new Error('Unavailable')
      const archive = backups.archivePath(instance, backup),
        workspace = `${backups.dataRoot()}/backup-checks`
      $os.mkdirAll(workspace, 0o700 as any)
      const raw = toString(
        $os
          .cmd('python3', '-c', BACKUP_VERIFIER, archive, backup.getString('checksum'), progressPath(row.id), workspace)
          .output()
      )
      const result = JSON.parse(raw)
      row.set('status', result.ok ? 'passed' : 'failed')
      row.set('result', result)
    } catch {
      row.set('status', 'failed')
      row.set('result', {
        message: 'Archive indisponible ou contrôle interrompu. Réessayez après la fin des opérations en cours.',
      })
    }
    row.set('finishedAt', new Date().toISOString())
    $app.save(row)
    if (instance) {
      recordInstanceAction(instance, undefined, `backup-check.${row.getString('status')}`, {
        check: row.id,
        backup: row.getString('backupId'),
      })
      ReportBackupCheckIncident(instance, row.getString('status') === 'passed')
    }
    try {
      $os.remove(progressPath(row.id))
    } catch {}
  } finally {
    $app.store().remove(LOCK)
  }
}
