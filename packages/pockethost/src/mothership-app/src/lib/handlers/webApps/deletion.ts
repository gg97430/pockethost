import { requireWebAppPermission, requireWebProjectPermission } from './access'
import { requireWorker, webInput, webJson } from './api'
import { webDeletionView } from './deletionState'
import { auditWebProject, webMembership } from './projects'

const JOBS = 'web_deletion_jobs',
  stamp = () => new Date().toISOString()
const assertStorageSettled = () => {
  let pending = false
  try {
    $os.stat(($os.getenv('WEB_APPS_ROOT') || '/home/pockethost-web/data') + '/storage-operation.json')
    pending = true
  } catch {}
  if (pending)
    throw new BadRequestError('Une opération de stockage doit encore être réconciliée. Réessayez après sa fin.')
}
type DeletionSnapshot = {
  kind: 'app' | 'project'
  targetId: string
  name: string
  apps: { id: string; deployments: string[]; taskRuns: string[] }[]
}
const rows = (store: any, collection: string, appId: string, filter = '') =>
  store.findRecordsByFilter(collection, 'appId={:id}' + (filter ? ' && (' + filter + ')' : ''), '', 0, 0, {
    id: appId,
  }) as core.Record[]
const disableAndDeleteBackups = (app: core.Record, store: any) => {
  if (!app.getString('deletionId')) {
    for (const collection of ['web_app_backup_settings', 'web_scheduled_tasks'])
      for (const row of rows(store, collection, app.id)) {
        row.set('enabled', false)
        store.save(row)
      }
  }
  for (const row of rows(store, 'web_app_backups', app.id, 'status!="deleted" && status!="deleting"')) {
    row.set('status', 'deleting')
    row.set('operation', 'delete')
    row.set('leaseHash', '')
    row.set('leaseAt', '')
    row.set('leaseAttempts', 0)
    row.set('error', '')
    store.save(row)
  }
  for (const row of rows(
    store,
    'web_storage_jobs',
    app.id,
    '(operation="backup" || operation="delete") && status!="deleted" && status!="running" && status!="deleting"'
  )) {
    row.set('operation', 'delete')
    row.set('status', 'deleting')
    row.set('leaseHash', '')
    row.set('leaseAt', '')
    store.save(row)
  }
}
const requestDeletion = (kind: 'app' | 'project', found: core.Record, actorId: string, confirmation: unknown) => {
  let result: any
  $app.runInTransaction((tx) => {
    const target = tx.findRecordById(kind === 'app' ? 'web_apps' : 'web_projects', found.id)
    if (confirmation !== target.getString('name'))
      throw new BadRequestError('Saisissez le nom exact pour confirmer la suppression.')
    const existing = target.getString('deletionId')
    if (existing) {
      const job = tx.findRecordById(JOBS, existing)
      if (job.getString('kind') !== kind || job.getString('targetId') !== target.id)
        throw new BadRequestError('La suppression du projet entier est déjà engagée.')
      if (job.getString('status') === 'failed') {
        assertStorageSettled()
        const snapshot = JSON.parse(job.getString('snapshot'))
        for (const item of snapshot.apps) disableAndDeleteBackups(tx.findRecordById('web_apps', item.id), tx)
        job.set('status', 'queued')
        job.set('error', '')
        job.set('stage', 'Nouvel essai de nettoyage')
        job.set('leaseHash', '')
        tx.save(job)
      }
      result = { accepted: true, deletion: webDeletionView(target, tx) }
      return
    }
    assertStorageSettled()
    const members = (
      kind === 'project'
        ? tx.findRecordsByFilter('web_app_environments', 'projectId={:id} && appId!=""', '', 501, 0, { id: target.id })
        : []
    ) as core.Record[]
    if (members.length > 500)
      throw new BadRequestError('Retirez des applications avant de supprimer un projet de plus de 500 sites.')
    const apps: core.Record[] =
      kind === 'app' ? [target] : members.map((m) => tx.findRecordById('web_apps', m.getString('appId')))
    const snapshot: any = { kind, targetId: target.id, name: target.getString('name'), apps: [] }
    for (const app of apps) {
      if (app.getString('deletionId'))
        throw new BadRequestError('Une application est déjà en cours de suppression. Attendez la fin du nettoyage.')
      const membership = webMembership(app.id, tx)
      if (membership?.getString('state') === 'closing')
        throw new BadRequestError('Une preview est déjà en cours de fermeture. Réessayez après son nettoyage.')
      for (const collection of ['web_deployments', 'web_storage_jobs', 'web_app_backups'])
        if (
          rows(
            tx,
            collection,
            app.id,
            collection === 'web_app_backups' ? 'status="running"' : 'status="queued" || status="running"'
          ).length
        )
          throw new BadRequestError(
            'Un déploiement, une sauvegarde ou une opération de stockage est en cours. Attendez sa fin ou annulez le déploiement avant de supprimer.'
          )
      snapshot.apps.push({
        id: app.id,
        deployments: rows(tx, 'web_deployments', app.id).map((r) => r.id),
        taskRuns: rows(tx, 'web_task_runs', app.id).map((r) => r.id),
      })
    }
    const job = new Record(tx.findCollectionByNameOrId(JOBS))
    job.set('kind', kind)
    job.set('targetId', target.id)
    job.set('actorId', actorId)
    job.set('status', 'queued')
    job.set('snapshot', snapshot)
    job.set('stage', 'Préparation du nettoyage')
    tx.save(job)
    // Disable schedules BEFORE installing the write tombstones. Existing backup
    // deletion leases continue through their normal S3/local cleanup machinery.
    for (const app of apps) {
      disableAndDeleteBackups(app, tx)
      app.set('config', { ...JSON.parse(app.getString('config') || '{}'), autoDeploy: false })
      app.set('deletionId', job.id)
      app.set('status', 'deleting')
      app.set('lastError', '')
      tx.save(app)
      const membership = webMembership(app.id, tx)
      if (membership)
        auditWebProject(
          membership.getString('projectId'),
          actorId,
          'application-deletion-requested',
          { appId: app.id, deletionId: job.id },
          tx
        )
    }
    if (kind === 'project') {
      target.set('previewEnabled', false)
      target.set('deletionId', job.id)
      tx.save(target)
      auditWebProject(
        target.id,
        actorId,
        'project-deletion-requested',
        { deletionId: job.id, siteCount: apps.length },
        tx
      )
    }
    result = { accepted: true, deletion: webDeletionView(target, tx) }
  })
  return result
}
export const HandleWebAppDelete = (e: core.RequestEvent) => {
  const { user, app } = requireWebAppPermission(e, 'app.delete')
  return webJson(e, requestDeletion('app', app!, user.id, webInput(e).confirmation), 202)
}
export const HandleWebProjectDeleteContents = (e: core.RequestEvent) => {
  const { user, project } = requireWebProjectPermission(e, 'project.delete')
  return webJson(e, requestDeletion('project', project!, user.id, webInput(e).confirmation), 202)
}
const snapshotOf = (job: core.Record, store: any) => {
  const snapshot = JSON.parse(job.getString('snapshot')) as DeletionSnapshot
  if (
    snapshot.kind !== job.getString('kind') ||
    snapshot.targetId !== job.getString('targetId') ||
    !Array.isArray(snapshot.apps)
  )
    throw new BadRequestError('Périmètre du nettoyage invalide.')
  for (const item of snapshot.apps)
    if (!/^[a-z0-9]{15}$/.test(item.id) || store.findRecordById('web_apps', item.id).getString('deletionId') !== job.id)
      throw new BadRequestError('Périmètre du nettoyage modifié.')
  if (
    snapshot.kind === 'project' &&
    store.findRecordById('web_projects', snapshot.targetId).getString('deletionId') !== job.id
  )
    throw new BadRequestError('Suppression du projet non autorisée.')
  return snapshot
}
export const HandleWebDeletionClaim = (e: core.RequestEvent) => {
  requireWorker(e)
  let claimed: any = null
  $app.runInTransaction((tx) => {
    for (const job of tx.findRecordsByFilter(
      JOBS,
      'status="queued" || (status="running" && heartbeatAt<{:stale})',
      'created',
      20,
      0,
      { stale: new Date(Date.now() - 5 * 60000).toISOString().replace('T', ' ') }
    ) as core.Record[]) {
      const snapshot = snapshotOf(job, tx)
      const backups = snapshot.apps.flatMap((app) => [
        ...rows(tx, 'web_app_backups', app.id, 'status!="deleted"'),
        ...rows(tx, 'web_storage_jobs', app.id, 'operation="delete" && status!="deleted"'),
      ])
      if (backups.length) {
        job.set('stage', 'Suppression des sauvegardes')
        if (backups.some((b) => b.getString('status') === 'failed')) {
          job.set('status', 'failed')
          job.set('error', 'La suppression d’une sauvegarde a échoué. Réessayez le nettoyage.')
        }
        tx.save(job)
        continue
      }
      const lease = $security.randomString(40)
      job.set('status', 'running')
      job.set('leaseHash', $security.sha256(lease))
      job.set('heartbeatAt', stamp())
      job.set('stage', 'Nettoyage des applications')
      tx.save(job)
      claimed = { id: job.id, lease, ...snapshot }
      break
    }
  })
  return webJson(e, { job: claimed })
}
const eraseRows = (store: any, collection: string, field: string, id: string) => {
  for (;;) {
    const records = store.findRecordsByFilter(collection, field + '={:id}', '', 200, 0, { id })
    for (const record of records) store.delete(record)
    if (records.length < 200) return
  }
}
export const HandleWebDeletionReport = (e: core.RequestEvent) => {
  requireWorker(e)
  const input = webInput(e)
  if (!/^[a-z0-9]{15}$/.test(input.id || '') || typeof input.lease !== 'string')
    throw new BadRequestError('Nettoyage invalide.')
  $app.runInTransaction((tx) => {
    const job = tx.findRecordById(JOBS, input.id)
    if (!$security.equal(job.getString('leaseHash'), $security.sha256(input.lease)))
      throw new ForbiddenError('Bail de nettoyage invalide.')
    if (job.getString('status') === 'ready') return // Idempotent acknowledgement after a lost HTTP response.
    if (job.getString('status') !== 'running' || !['running', 'ready', 'failed'].includes(input.status))
      throw new BadRequestError('Nettoyage non actif.')
    const snapshot = snapshotOf(job, tx)
    if (input.status === 'ready') {
      if (input.removed !== true) throw new BadRequestError('Nettoyage non confirmé.')
      const collections = (tx.findAllCollections() as core.Collection[]).filter(
        (c) => c.type === 'base' && c.name.startsWith('web_') && c.name !== JOBS && c.fields.getByName('appId')
      )
      for (const item of snapshot.apps) {
        if (rows(tx, 'web_app_backups', item.id, 'status!="deleted"').length)
          throw new BadRequestError('Les sauvegardes ne sont pas encore supprimées.')
        if (rows(tx, 'web_storage_jobs', item.id, 'operation="delete" && status!="deleted"').length)
          throw new BadRequestError('Les snapshots de volumes ne sont pas encore supprimés.')
        // Clear preview templates, even when deleting just one application.
        for (const project of tx.findRecordsByFilter('web_projects', 'previewSourceAppId={:id}', '', 0, 0, {
          id: item.id,
        }) as core.Record[]) {
          if (project.getString('deletionId') === job.id) continue // This project is removed below.
          project.set('previewEnabled', false)
          project.set('previewSourceAppId', '')
          tx.save(project)
        }
        for (const collection of collections) eraseRows(tx, collection.name, 'appId', item.id)
        tx.delete(tx.findRecordById('web_apps', item.id))
      }
      if (snapshot.kind === 'project') {
        // Git providers are platform configuration; keep their credentials and
        // remote repositories, just as the existing empty-project deletion does.
        for (const source of tx.findRecordsByFilter('web_git_sources', 'projectId={:id}', '', 0, 0, {
          id: snapshot.targetId,
        }) as core.Record[]) {
          source.set('projectId', '')
          tx.save(source)
        }
        for (const name of ['web_app_environments', 'web_project_variables'])
          eraseRows(tx, name, 'projectId', snapshot.targetId)
        auditWebProject(
          snapshot.targetId,
          job.getString('actorId'),
          'project-deleted',
          { deletionId: job.id, siteCount: snapshot.apps.length },
          tx
        )
        tx.delete(tx.findRecordById('web_projects', snapshot.targetId))
      }
    }
    job.set('status', input.status)
    if (input.status === 'ready')
      job.set('snapshot', {
        kind: snapshot.kind,
        targetId: snapshot.targetId,
        name: snapshot.name,
        apps: snapshot.apps.map((app) => ({ id: app.id })),
      })
    job.set('heartbeatAt', stamp())
    job.set('stage', String(input.stage || (input.status === 'ready' ? 'Suppression terminée' : '')).slice(0, 150))
    job.set(
      'error',
      input.status === 'failed' ? String(input.error || 'Le nettoyage a échoué. Réessayez.').slice(0, 1000) : ''
    )
    tx.save(job)
  })
  return webJson(e, { ok: true })
}
