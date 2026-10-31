/** Tombstones remain until the worker has acknowledged every destructive step. */
export const webDeletionView = (record: core.Record, store: any = $app) => {
  const id = record.getString('deletionId')
  if (!id) return null
  try {
    const job = store.findRecordById('web_deletion_jobs', id)
    return { id, status: job.getString('status'), stage: job.getString('stage'), error: job.getString('error') }
  } catch {
    return { id, status: 'failed', stage: '', error: 'État du nettoyage indisponible.' }
  }
}
export const assertWebNotDeleting = (record: core.Record, permission = '') => {
  if (
    record.getString('deletionId') &&
    !permission.endsWith('.read') &&
    !['app.delete', 'project.delete'].includes(permission)
  )
    throw new BadRequestError('Suppression en cours : cette ressource ne peut plus être modifiée.')
}
/** Model hooks also reject requests which read a record just BEFORE its tombstone was committed. */
export const GuardWebDeletingRecord = (e: any) => {
  const record = e.record,
    name = record.collection().name,
    store = e.app || $app
  if (!name.startsWith('web_') || name === 'web_deletion_jobs') return e.next()
  if (name === 'web_apps' || name === 'web_projects') {
    let previous: any
    try {
      previous = store.findRecordById(name, record.id)
    } catch {}
    if (previous?.getString('deletionId'))
      throw new BadRequestError('La suppression de cette ressource est déjà engagée.')
    return e.next()
  }
  const appId = record.getString('appId'),
    projectId = record.getString('projectId') || (name === 'web_project_members' ? record.getString('project') : '')
  if (appId) {
    let app: any
    try {
      app = store.findRecordById('web_apps', appId)
    } catch {}
    if (!app && record.isNew()) {
      // Reject late telemetry after the parent was removed, without changing
      // historical handling of unrelated orphan records from older versions.
      const receipts = store.findRecordsByFilter('web_deletion_jobs', 'snapshot ~ {:id}', '', 0, 0, { id: appId })
      if (
        receipts.some((job: core.Record) =>
          (JSON.parse(job.getString('snapshot')).apps || []).some((item: any) => item.id === appId)
        )
      )
        throw new BadRequestError('Cette application a été supprimée.')
    }
    const terminalUpdate =
      !record.isNew() &&
      ((['web_deployments', 'web_task_runs', 'web_notification_deliveries'].includes(name) &&
        ['ready', 'failed', 'cancelled', 'sent', 'suppressed', 'expired'].includes(record.getString('status'))) ||
        ['web_alert_events', 'web_app_health_events'].includes(name))
    // Existing backup deletion leases must finish before the application cleanup.
    if (
      app?.getString('deletionId') &&
      !(
        (['web_app_backups', 'web_storage_jobs'].includes(name) &&
          !record.isNew() &&
          record.getString('operation') === 'delete') ||
        terminalUpdate
      )
    )
      throw new BadRequestError('Suppression en cours : aucune nouvelle opération sur cette application.')
  }
  if (projectId && ['web_project_members', 'web_project_variables', 'web_app_environments'].includes(name)) {
    let project: any
    try {
      project = store.findRecordById('web_projects', projectId)
    } catch {}
    if (project?.getString('deletionId')) throw new BadRequestError('Suppression du projet en cours.')
  }
  return e.next()
}
