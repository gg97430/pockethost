import { requireWebPermission, requireWebProjectPermission } from './access'
import { decryptWebValue, queueWebDeployment, webConfig, webEnabled, webGithub, webJson, webSitesFull } from './api'
import { createWebEnvironment } from './environments'
import { assertWebGithubRate } from './gitSource'
import { GITHUB_PRINCIPAL, resolvedConnectionId } from './githubConnections'
import { auditWebProject, webMembership, webProject } from './projects'

const activeFilter = 'environment="preview" && (state="active" || state="closing")'
const activeSql = "environment='preview' AND state IN ('active','closing')"
const timestamp = () => new Date().toISOString()
const repositoryOf = (body: any) => {
  const name = body.repository?.full_name
  if (typeof name !== 'string' || !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(name) || name.length > 250)
    throw new BadRequestError('Dépôt de pull request invalide.')
  return name
}
const closePreview = (membership: core.Record, reason: string, actorId: string, store: any) => {
  if (membership.getString('state') === 'closed') return
  const app = store.findRecordById('web_apps', membership.getString('appId'))
  if (membership.getString('environment') !== 'preview')
    throw new BadRequestError('Seule une preview peut être nettoyée automatiquement.')
  for (const job of store.findRecordsByFilter(
    'web_deployments',
    'appId={:id} && (status="queued" || status="running")',
    '',
    1000,
    0,
    { id: app.id }
  )) {
    if (job.getString('action') === 'preview-remove') continue
    if (job.getString('status') === 'queued') {
      job.set('status', 'cancelled')
      job.set('stage', 'Preview fermée')
      job.set('finishedAt', timestamp())
    } else if (job.getString('action') === 'deploy') job.set('cancelRequested', true)
    store.save(job)
  }
  membership.set('state', 'closing')
  membership.set('expiresAt', timestamp())
  store.save(membership)
  const pending = store.findRecordsByFilter(
    'web_deployments',
    'appId={:id} && action="preview-remove" && (status="queued" || status="running")',
    '',
    1,
    0,
    { id: app.id }
  )
  if (!pending.length)
    queueWebDeployment(
      app,
      'preview-remove',
      actorId,
      { env: {}, preview: { appId: app.id, sourceAppId: membership.getString('sourceAppId'), isPreview: true } },
      store
    )
  auditWebProject(
    membership.getString('projectId'),
    actorId,
    'preview-closing',
    { appId: app.id, number: membership.getInt('previewNumber'), reason },
    store
  )
}
export const assertWebPreviewRemovalJob = (app: core.Record, snapshot: any, store: any = $app) => {
  const membership = webMembership(app.id, store)
  if (
    !membership ||
    membership.getString('environment') !== 'preview' ||
    membership.getString('state') !== 'closing' ||
    snapshot?.preview?.isPreview !== true ||
    snapshot.preview.appId !== app.id ||
    snapshot.preview.sourceAppId !== membership.getString('sourceAppId') ||
    snapshot.preview.sourceAppId === app.id
  )
    throw new BadRequestError('Nettoyage refusé : cette application n’est pas une preview fermée autorisée.')
}
/** Application modèle des previews d'un projet, ou null si elle est introuvable. */
const previewTemplate = (project: core.Record, store: any = $app): core.Record | null => {
  try {
    return store.findRecordById('web_apps', project.getString('previewSourceAppId')) as core.Record
  } catch {
    return null
  }
}
/**
 * Connexion GitHub de l'application modèle d'un projet : '' pour un modèle « Dépôt Git », null pour un modèle
 * introuvable (les deux cas sont distingués).
 */
const previewSourceConnection = (project: core.Record, store: any = $app): string | null => {
  const template = previewTemplate(project, store)
  return template ? resolvedConnectionId(webConfig(template)) : null
}
/** Connexion de l'application d'une preview encore ouverte ('' : aucune, ou application « Dépôt Git »). */
const previewAppConnection = (member: core.Record | null, store: any = $app) => {
  if (!member || member.getString('state') === 'closed' || !member.getString('appId')) return ''
  try {
    return resolvedConnectionId(webConfig(store.findRecordById('web_apps', member.getString('appId'))))
  } catch {
    return ''
  }
}
/**
 * Connexion qui gouverne les previews d'un projet pour une livraison : celle du modèle GitHub App ; à défaut (modèle «
 * Dépôt Git » ou introuvable), celle de l'application de la preview elle-même. Une livraison d'une autre connexion
 * n'agit jamais sur ces previews.
 */
const previewGoverningConnection = (template: core.Record | null, member: core.Record | null, store: any) => {
  const config = template ? webConfig(template) : null
  return config && config.source !== 'git' ? resolvedConnectionId(config) : previewAppConnection(member, store)
}
/** Une preview ouverte du projet est servie par cette connexion (modèle « Dépôt Git » ou introuvable). */
const openPreviewOnConnection = (projectId: string, connectionId: string, store: any = $app) =>
  (
    store.findRecordsByFilter(
      'web_app_environments',
      'projectId={:project} && environment="preview" && state!="closed"',
      '',
      100,
      0,
      { project: projectId }
    ) as core.Record[]
  ).some((member) => previewAppConnection(member, store) === connectionId)
/**
 * Only call after GitHub signature and installation checks in the public webhook. `onlyProjectId` limits a manual
 * refresh to the requesting member's project (other teams may share the repository). `connectionId` : connexion qui a
 * reçu la livraison ; seuls les projets dont le modèle est sur cette connexion sont concernés, et la PR est relue avec
 * ses identifiants.
 */
export const handleWebPreviewWebhook = (
  body: any,
  delivery: string,
  onlyProjectId = '',
  connectionId = GITHUB_PRINCIPAL
) => {
  if (!webEnabled()) throw new NotFoundError()
  if (!/^[A-Za-z0-9-]{1,100}$/.test(delivery || '')) throw new BadRequestError('Livraison GitHub invalide.')
  const repository = repositoryOf(body),
    number = Number(body.number || body.pull_request?.number)
  if (!Number.isSafeInteger(number) || number < 1 || number > 100000000)
    throw new BadRequestError('Numéro de pull request invalide.')
  try {
    $app.findFirstRecordByFilter('web_preview_deliveries', 'deliveryId={:delivery}', { delivery })
    return { accepted: true, duplicate: true, queued: 0, closed: 0 }
  } catch {}
  if (!['opened', 'reopened', 'synchronize', 'ready_for_review', 'closed', 'converted_to_draft'].includes(body.action))
    return { accepted: true, ignored: true, queued: 0, closed: 0 }
  const projects = $app.findRecordsByFilter('web_projects', 'previewEnabled=true', '', 1000, 0).filter((row) => {
    if (row.getString('repository').toLowerCase() !== repository.toLowerCase()) return false
    if (onlyProjectId && row.id !== onlyProjectId) return false
    const source = previewSourceConnection(row)
    if (source) return source === connectionId
    // Modèle « Dépôt Git » ou introuvable : seulement si une preview ouverte est servie par cette connexion.
    return openPreviewOnConnection(row.id, connectionId)
  })
  if (!projects.length) return { accepted: true, ignored: true, queued: 0, closed: 0 }
  // Resolve current PR state before the transaction: late synchronize/close deliveries cannot roll back a newer head.
  const pr = webGithub('pull-request', { repository, number }, connectionId),
    base = pr.base?.repo,
    head = pr.head?.repo
  const baseMatches = String(base?.full_name || '').toLowerCase() === repository.toLowerCase()
  const sameRepository =
    baseMatches &&
    Number(base?.id) > 0 &&
    Number(base?.id) === Number(head?.id) &&
    String(head?.full_name || '').toLowerCase() === repository.toLowerCase()
  const trusted = sameRepository && ['OWNER', 'MEMBER'].includes(pr.author_association || pr.authorAssociation)
  const closed = pr.state === 'closed' || pr.draft === true
  const commit = pr.head?.sha,
    branch = pr.head?.ref
  if (
    !baseMatches ||
    (!closed &&
      trusted &&
      (!/^[a-f0-9]{40}$/.test(commit || '') || !/^[A-Za-z0-9_./-]{1,200}$/.test(branch || '') || branch.includes('..')))
  )
    throw new BadRequestError('État autoritatif de la pull request invalide.')
  let queued = 0,
    removed = 0,
    wasDuplicate = false,
    note = trusted ? '' : 'Preview refusée : auteur OWNER/MEMBER et même dépôt requis.'
  $app.runInTransaction((tx) => {
    try {
      tx.findFirstRecordByFilter('web_preview_deliveries', 'deliveryId={:delivery}', { delivery })
      wasDuplicate = true
      return
    } catch {}
    for (const original of projects) {
      const project = webProject(original.id, tx)
      if (
        !project.getBool('previewEnabled') ||
        project.getString('repository').toLowerCase() !== repository.toLowerCase()
      )
        continue
      let member: core.Record | null = null
      try {
        member = tx.findFirstRecordByFilter(
          'web_app_environments',
          'projectId={:project} && environment="preview" && previewNumber={:number}',
          { project: project.id, number }
        )
      } catch {}
      // Avant toute action : seules les previews servies par la connexion qui a reçu la livraison sont concernées.
      const template = previewTemplate(project, tx)
      if (previewGoverningConnection(template, member, tx) !== connectionId) {
        if (!template) note = 'Le modèle de preview est introuvable.'
        else if (webConfig(template).source === 'git')
          note = 'Les aperçus de pull request nécessitent la source GitHub App.'
        else note = 'Le modèle de preview utilise une autre connexion GitHub.'
        continue
      }
      if (closed) {
        if (member && member.getString('state') !== 'closed') {
          closePreview(member, pr.draft ? 'draft' : 'pull-request-closed', '', tx)
          removed++
        }
        continue
      }
      if (!trusted) {
        if (member && member.getString('state') === 'active') {
          closePreview(member, 'trust-revoked', '', tx)
          removed++
        }
        continue
      }
      if (member?.getString('state') === 'closing') {
        note = 'Nettoyage de la preview en cours. Relancez la synchronisation après sa fermeture.'
        continue
      }
      if (!template) {
        note = 'Le modèle de preview est introuvable.'
        continue
      }
      const source = template,
        sourceMember = webMembership(source.id, tx)
      if (sourceMember?.getString('projectId') !== project.id || sourceMember.getString('environment') === 'preview') {
        note = 'Le modèle de preview doit être un environnement permanent du projet.'
        continue
      }
      // Les previews suivent les pull requests de la GitHub App : jamais une application « Dépôt Git ».
      if (webConfig(source).source === 'git') {
        note = 'Les aperçus de pull request nécessitent la source GitHub App.'
        continue
      }
      // Modèle rattaché entre-temps à une autre connexion : cette livraison ne le concerne plus.
      if (resolvedConnectionId(webConfig(source)) !== connectionId) {
        note = 'Le modèle de preview utilise une autre connexion GitHub.'
        continue
      }
      const expiresAt = new Date(Date.now() + (project.getInt('previewTtlHours') || 24) * 3600000).toISOString()
      let app: core.Record
      if (!member || member.getString('state') === 'closed') {
        const projectCount = tx.countRecords(
            'web_app_environments',
            $dbx.exp(`${activeSql} AND projectId={:id}`, { id: project.id })
          ),
          globalCount = tx.countRecords('web_app_environments', $dbx.exp(activeSql))
        if (projectCount >= (project.getInt('previewMax') || 3) || globalCount >= 10 || webSitesFull(tx)) {
          note = 'Quota de previews atteint. Fermez une preview ou attendez son nettoyage.'
          continue
        }
        app = createWebEnvironment(
          project,
          source,
          { branch, name: `${source.getString('name').slice(0, 60)} · PR #${number}`, copyVariables: false },
          '',
          tx,
          { number, expiresAt, existing: member || undefined }
        )
        member = webMembership(app.id, tx)
      } else {
        app = tx.findRecordById('web_apps', member.getString('appId'))
        const config = {
          ...webConfig(source),
          branch,
          generatedDomain: webConfig(app).generatedDomain,
          autoDeploy: false,
        }
        app.set('config', config)
        tx.save(app)
        member.set('expiresAt', expiresAt)
        tx.save(member)
      }
      if (app.getString('desiredCommit') === commit) continue
      for (const running of tx.findRecordsByFilter(
        'web_deployments',
        'appId={:id} && action="deploy" && status="running"',
        '',
        100,
        0,
        { id: app.id }
      )) {
        running.set('cancelRequested', true)
        tx.save(running)
      }
      queueWebDeployment(app, 'deploy', '', { commit }, tx)
      queued++
      auditWebProject(project.id, '', 'preview-synchronized', { appId: app.id, number, commit }, tx)
    }
    const row = new Record(tx.findCollectionByNameOrId('web_preview_deliveries'))
    row.set('deliveryId', delivery)
    row.set('repository', repository)
    row.set('previewNumber', number)
    row.set('connectionId', connectionId)
    row.set('status', queued ? 'queued' : removed ? 'closing' : 'ignored')
    row.set(
      'note',
      note ||
        (removed ? 'Preview fermée.' : queued ? 'Preview isolée ajoutée à la file.' : 'Aucun changement à déployer.')
    )
    tx.save(row)
  })
  return { accepted: true, duplicate: wasDuplicate, queued, closed: removed, note }
}
export const HandleWebPreviewRefresh = (e: core.RequestEvent) => {
  const access = requireWebProjectPermission(e, 'previews.manage'),
    project = access.project!,
    number = Number(e.request!.pathValue('number'))
  assertWebGithubRate(project.id, access.user.id)
  return webJson(
    e,
    handleWebPreviewWebhook(
      { repository: { full_name: project.getString('repository') }, number, action: 'synchronize' },
      'manual-' + $security.randomStringWithAlphabet(30, 'abcdefghijklmnopqrstuvwxyz0123456789'),
      access.platform ? '' : project.id,
      // Connexion de l'application modèle de CE projet (principale pour un modèle « Dépôt Git »).
      previewSourceConnection(project) || GITHUB_PRINCIPAL
    ),
    202
  )
}
export const HandleWebPreviewClose = (e: core.RequestEvent) => {
  // Cible { projectId, environmentId } : une preview d'un autre projet donne le même 404 qu'un identifiant inconnu.
  const { user, project } = requireWebPermission(
    e,
    { projectId: e.request!.pathValue('projectId'), environmentId: e.request!.pathValue('previewId') },
    'previews.manage'
  )
  $app.runInTransaction((tx) => {
    const member = tx.findRecordById('web_app_environments', e.request!.pathValue('previewId'))
    if (member.getString('projectId') !== project!.id || member.getString('environment') !== 'preview')
      throw new NotFoundError('Preview introuvable.')
    closePreview(member, 'manual', user.id, tx)
  })
  return webJson(e, { closing: true }, 202)
}
export const HandleWebPreviewMaintenance = () => {
  if (!webEnabled()) return
  $app.runInTransaction((tx) => {
    const rows = tx.findRecordsByFilter('web_app_environments', activeFilter, '', 100, 0)
    for (const row of rows) {
      if (
        row.getString('state') === 'active' &&
        (!Number.isFinite(Date.parse(row.getString('expiresAt'))) ||
          Date.parse(row.getString('expiresAt')) <= Date.now())
      )
        closePreview(row, 'ttl-expired', '', tx)
      if (row.getString('state') !== 'closing') continue
      const appId = row.getString('appId'),
        finished = tx.findRecordsByFilter(
          'web_deployments',
          'appId={:id} && action="preview-remove" && status="ready"',
          '-created',
          1,
          0,
          { id: appId }
        )[0]
      if (!finished || JSON.parse(finished.getString('result') || '{}').removed !== true) continue
      const app = tx.findRecordById('web_apps', appId)
      assertWebPreviewRemovalJob(app, decryptWebValue(finished.getString('snapshotEncrypted')), tx)
      if (tx.findRecordsByFilter('web_app_backups', 'appId={:id} && status!="deleted"', '', 1, 0, { id: appId }).length)
        continue
      for (const collection of [
        'web_app_domains',
        'web_app_health',
        'web_app_health_events',
        'web_scheduled_tasks',
        'web_task_runs',
        'web_terminal_sessions',
        'web_environment_audit',
        'web_app_backup_settings',
        'web_app_backups',
        'web_runtime_profiles',
        'web_storage_settings',
        'web_storage_jobs',
        'web_deployments',
      ]) {
        for (const record of tx.findRecordsByFilter(collection, 'appId={:id}', '', 10000, 0, { id: appId }))
          tx.delete(record)
      }
      tx.delete(app)
      row.set('state', 'closed')
      row.set('appId', '')
      tx.save(row)
      auditWebProject(
        row.getString('projectId'),
        '',
        'preview-removed',
        { appId, number: row.getInt('previewNumber') },
        tx
      )
    }
  })
}
