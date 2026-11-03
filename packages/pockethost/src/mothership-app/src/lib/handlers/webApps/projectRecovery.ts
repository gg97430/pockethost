import { decryptWebValue, encryptWebValue, requirePlatformAdmin, webDirectLoopback, webInput, webJson } from './api'

const RUNS = 'web_project_recovery_runs',
  ID = /^[a-z0-9]{15}$/,
  now = () => new Date().toISOString().replace('T', ' ')
const object = (row: core.Record, key: string) => JSON.parse(row.getString(key) || '{}')
const metadata = (row: core.Record) => ({
  id: row.id,
  projectId: row.getString('projectId'),
  operation: row.getString('operation'),
  status: row.getString('status'),
  stage: row.getString('stage'),
  startedAt: row.getString('startedAt'),
  finishedAt: row.getString('finishedAt'),
  created: row.getString('created'),
  errorCode: row.getString('errorCode'),
  result: object(row, 'result'),
})
const snapshot = (projectId: string, store: any = $app) => {
  if (!ID.test(projectId)) throw new BadRequestError('Projet invalide.')
  const project = store.findRecordById('web_projects', projectId)
  const memberships = store.findRecordsByFilter('web_app_environments', 'projectId={:id} && appId!=""', 'id', 21, 0, {
    id: projectId,
  })
  if (!memberships.length || memberships.length > 20)
    throw new BadRequestError('Le projet doit contenir une à vingt applications.')
  const selectedAppIds = new Set(memberships.map((membership: core.Record) => membership.getString('appId')))
  const databases: any = {},
    apps: any[] = []
  for (const membership of memberships) {
    const app = store.findRecordById('web_apps', membership.getString('appId'))
    if (app.getString('status') !== 'running' || !ID.test(app.getString('activeDeployment')))
      throw new BadRequestError('Chaque application doit posséder une version publiée en service.')
    const deployment = store.findRecordById('web_deployments', app.getString('activeDeployment'))
    if (deployment.getString('appId') !== app.id) throw new BadRequestError('Publication incohérente.')
    const published = decryptWebValue(deployment.getString('snapshotEncrypted'))
    const bindings = store
      .findRecordsByFilter('web_private_db_bindings', 'appId={:id} && enabled=true', 'id', 5, 0, { id: app.id })
      .map((binding: core.Record) => {
        const instance = store.findRecordById('instances', binding.getString('instanceId'))
        databases[instance.id] = {
          id: instance.id,
          version: instance.getString('version') || instance.getString('pocketbaseVersion'),
          name: instance.getString('subdomain'),
          externalApplicationBindingsCount: store
            .findRecordsByFilter('web_private_db_bindings', 'instanceId={:id} && enabled=true', 'id', 1001, 0, {
              id: instance.id,
            })
            .filter((link: core.Record) => !selectedAppIds.has(link.getString('appId'))).length,
          configuration: {
            dev: instance.getBool('dev'),
            syncAdmin: instance.getBool('syncAdmin'),
            secrets: JSON.parse(instance.getString('secrets') || '{}'),
          },
        }
        return {
          instanceId: instance.id,
          port: binding.getInt('port'),
          publicHosts: [instance.getString('subdomain') + '.' + ($os.getenv('APEX_DOMAIN') || '')],
        }
      })
    // The API shows unresolved storage explicitly. Ordinary app-level object
    // stores have no universal format and cannot silently become "protected".
    const externalDependencies = Object.entries(published.env || {})
      .filter(([name, value]: [string, any]) => {
        const content = typeof value?.value === 'string' ? value.value : ''
        if (!content) return false
        if (/^(?:R2_|S3_|AWS_S3_)/.test(name)) return true
        if (
          !/(?:DATABASE|POCKETBASE|SUPABASE|(?:^|_)(?:PB|DB)|POSTGRES|MYSQL|MARIADB|MONGO|REDIS|LIBSQL|TURSO|COUCHDB).*(?:URL|URI|HOST(?:NAME)?|DSN|ENDPOINT|ADDR(?:ESS)?)$/i.test(
            name
          )
        )
          return false
        const parsed = /^https?:\/\/([a-z0-9.-]+)(?::([0-9]+))?(?:\/|$)/i.exec(content)
        if (!parsed) return true
        const host = parsed[1].toLowerCase(),
          port = Number(parsed[2] || 0)
        return !bindings.some(
          (binding: any) =>
            (['localhost', '127.0.0.1'].includes(host) && binding.port === port) || binding.publicHosts.includes(host)
        )
      })
      .map(([name]) => name)
    apps.push({
      id: app.id,
      name: app.getString('name'),
      deploymentId: deployment.id,
      commit: deployment.getString('commit'),
      bindings,
      externalDependencies,
      configuration: {
        config: published.config || {},
        env: published.env || {},
        runtime: published.runtime || {},
        storage: published.storage || [],
        domains: published.domains || [],
        projectEnvironment: published.projectEnvironment || {},
        checks: published.checks ? { preflight: published.checks.preflight } : undefined,
      },
      scheduledTasks: store
        .findRecordsByFilter('web_scheduled_tasks', 'appId={:id}', 'id', 100, 0, { id: app.id })
        .map((task: core.Record) => ({
          id: task.id,
          name: task.getString('name'),
          schedule: task.getString('schedule'),
          timezone: task.getString('timezone') || 'UTC',
          enabled: task.getBool('enabled'),
          command: String(decryptWebValue(task.getString('commandEncrypted')).command || ''),
          timeoutSeconds: task.getInt('timeoutSeconds'),
        })),
      domains: store
        .findRecordsByFilter('web_app_domains', 'appId={:id}', 'id', 50, 0, { id: app.id })
        .map((domain: core.Record) => ({ host: domain.getString('host'), status: domain.getString('status') })),
    })
  }
  const value = {
    version: 1,
    platform: ($os.getenv('APEX_DOMAIN') || $os.getenv('PUBLIC_HOST') || '').split('.')[0] || 'kolab-project',
    projectId,
    projectName: project.getString('name'),
    apps,
    databases: Object.values(databases),
  }
  return { ...value, revision: $security.sha256(JSON.stringify(value)) }
}
const preview = (value: any) => ({
  projectId: value.projectId,
  projectName: value.projectName,
  revision: value.revision,
  apps: value.apps.map((app: any) => ({
    id: app.id,
    name: app.name,
    services: 'published-images',
    databases: app.bindings.map((row: any) => row.instanceId),
    scheduledTasks: app.scheduledTasks.length,
    domains: app.domains,
    externalDependencies: app.externalDependencies,
  })),
  databases: value.databases.map((row: any) => ({
    id: row.id,
    name: row.name,
    version: row.version,
    externalApplicationBindingsCount: row.externalApplicationBindingsCount || 0,
  })),
  blockers: value.apps.flatMap((app: any) =>
    app.externalDependencies.map((name: string) => ({ appId: app.id, code: 'external_storage_not_captured', name }))
  ),
  withoutInterruption: true,
  copyNetwork: 'none',
  mailsDisabled: true,
  workersInert: true,
  cronsDisabled: true,
})
const workerState = () => {
  try {
    const state = JSON.parse($app.store().get('project-recovery-worker') || '{}')
    return { ...state, online: state.blocked !== true && Date.now() - Date.parse(state.lastSeenAt) < 30000 }
  } catch {
    return { online: false }
  }
}
const admissionPaused = () => {
  try {
    const value = JSON.parse(
      toString(
        $os
          .cmd(
            '/usr/bin/timeout',
            '3',
            '/usr/bin/python3',
            '-I',
            '-c',
            "import sys,json;sys.path.insert(0,'/opt/pockethost-web');from platform_maintenance import status;print(json.dumps(status(path=sys.argv[1])))",
            '/run/kolab-platform-maintenance/admission.json'
          )
          .output()
      )
    )
    return value.paused !== false
  } catch {
    return true
  }
}
export const HandleProjectRecoveryGet = (e: core.RequestEvent) => {
  requirePlatformAdmin(e)
  return webJson(e, {
    worker: workerState(),
    projects: $app
      .findRecordsByFilter('web_projects', '', 'name', 1000, 0)
      .map((row: core.Record) => ({ id: row.id, name: row.getString('name') })),
    runs: $app.findRecordsByFilter(RUNS, '', '-created,-id', 50, 0).map(metadata),
  })
}
export const HandleProjectRecoveryPreview = (e: core.RequestEvent) => {
  requirePlatformAdmin(e)
  return webJson(e, preview(snapshot(e.request!.pathValue('projectId'))))
}
export const HandleProjectRecoveryQueue = (e: core.RequestEvent) => {
  const actor = requirePlatformAdmin(e),
    body = webInput(e),
    projectId = e.request!.pathValue('projectId')
  if (
    Object.keys(body).sort().join(',') !== 'operation,revision' ||
    body.operation !== 'drill' ||
    typeof body.revision !== 'string'
  )
    throw new BadRequestError('Test isolé et révision requis.')
  if (!workerState().online || workerState().blocked)
    throw new ApiError(503, 'Le service de reprise de projet est indisponible.')
  let output: any
  $app.runInTransaction((tx) => {
    const current = snapshot(projectId, tx)
    if (current.revision !== body.revision) throw new ApiError(409, 'Le projet a changé. Préparez un nouvel aperçu.')
    if (preview(current).blockers.length)
      throw new BadRequestError('Les stockages externes signalés doivent être inclus avant une reprise complète.')
    if (tx.findRecordsByFilter(RUNS, 'status="queued" || status="running"', '', 1, 0).length)
      throw new ApiError(409, 'Un essai de reprise est déjà en attente ou en cours.')
    const row = new Record(tx.findCollectionByNameOrId(RUNS))
    for (const [key, value] of Object.entries({
      projectId,
      actorId: actor.id,
      operation: 'drill',
      status: 'queued',
      revision: current.revision,
      snapshotEncrypted: encryptWebValue(current),
    }))
      row.set(key, value)
    tx.save(row)
    output = metadata(row)
  })
  return webJson(e, { run: output }, 202)
}
const requireWorker = (e: core.RequestEvent) => {
  let expected = ''
  try {
    expected = toString($os.readFile('/etc/kolab/project-recovery-controller.key')).trim()
  } catch {}
  const received = e.request!.header.get('X-Project-Recovery') || ''
  if (!/^[a-f0-9]{64}$/.test(expected) || !$security.equal(expected, received) || !webDirectLoopback(e))
    throw new ForbiddenError('Service de reprise local requis.')
}

export const HandleProjectRecoveryClaim = (e: core.RequestEvent) => {
  requireWorker(e)
  const body = webInput(e)
  if (
    Object.keys(body).some((key) => !['cleanupOnly', 'blocked', 'recoverOrphans'].includes(key)) ||
    (body.cleanupOnly !== undefined && typeof body.cleanupOnly !== 'boolean') ||
    (body.blocked !== undefined && typeof body.blocked !== 'boolean') ||
    (body.recoverOrphans !== undefined && typeof body.recoverOrphans !== 'boolean') ||
    (body.recoverOrphans === true && body.cleanupOnly !== true)
  )
    throw new BadRequestError('Demande invalide.')
  $app
    .store()
    .set('project-recovery-worker', JSON.stringify({ online: true, blocked: body.blocked === true, lastSeenAt: now() }))
  let job: any = null
  $app.runInTransaction((tx) => {
    // Serialize with the release guard's writer barrier before reading the
    // root-owned admission gate. A request started earlier cannot slip through.
    tx.db().newQuery('UPDATE web_project_recovery_runs SET status=status WHERE id={:id}').bind({ id: '' }).execute()
    if (body.recoverOrphans === true) {
      // Root coordinator only, after exclusive daemon lock and verified cleanup.
      for (const orphan of tx.findRecordsByFilter(RUNS, 'status="running"', '', 100, 0)) {
        orphan.set('status', 'failed')
        orphan.set('errorCode', 'worker_lost')
        orphan.set('finishedAt', now())
        orphan.set('snapshotEncrypted', '')
        orphan.set('leaseHash', '')
        tx.save(orphan)
      }
    }
    for (const expired of tx.findRecordsByFilter(RUNS, 'status="running" && deadline<{:now}', '', 100, 0, {
      now: now(),
    })) {
      expired.set('status', 'failed')
      expired.set('errorCode', 'lease_expired')
      expired.set('finishedAt', now())
      expired.set('snapshotEncrypted', '')
      expired.set('leaseHash', '')
      tx.save(expired)
    }
    if (
      body.cleanupOnly ||
      body.blocked ||
      admissionPaused() ||
      tx.findRecordsByFilter(RUNS, 'status="running"', '', 1, 0).length
    )
      return
    const row = tx.findRecordsByFilter(RUNS, 'status="queued"', 'created,id', 1, 0)[0]
    if (!row) return
    let changed: any = false
    try {
      const actor = tx.findRecordById('users', row.getString('actorId'))
      changed = actor.getString('suspension') || !actor.getBool('superAdmin')
    } catch {
      changed = true
    }
    try {
      changed = changed || snapshot(row.getString('projectId'), tx).revision !== row.getString('revision')
    } catch {
      changed = true
    }
    if (changed) {
      row.set('status', 'cancelled')
      row.set('errorCode', 'project_changed')
      row.set('snapshotEncrypted', '')
      row.set('finishedAt', now())
      tx.save(row)
      return
    }
    const proof = $security.randomString(80)
    row.set('status', 'running')
    row.set('startedAt', now())
    row.set('deadline', new Date(Date.now() + 7200000).toISOString())
    row.set('leaseHash', $security.sha256(proof))
    row.set('stage', 'capture')
    tx.save(row)
    job = { id: row.id, lease: proof, snapshot: decryptWebValue(row.getString('snapshotEncrypted')) }
  })
  return webJson(e, { job })
}
export const HandleProjectRecoveryReport = (e: core.RequestEvent) => {
  requireWorker(e)
  const body = webInput(e)
  if (!ID.test(body.id || '') || typeof body.lease !== 'string' || body.lease.length > 100)
    throw new BadRequestError('Bail invalide.')
  let output: any
  $app.runInTransaction((tx) => {
    const row = tx.findRecordById(RUNS, body.id)
    if (
      row.getString('status') !== 'running' ||
      !$security.equal(row.getString('leaseHash'), $security.sha256(body.lease)) ||
      Date.parse(row.getString('deadline')) < Date.now()
    )
      throw new ForbiddenError('Bail expiré ou déjà consommé.')
    if (body.status === 'running' && ['capture', 'restore', 'probe', 'report'].includes(body.stage)) {
      row.set('stage', body.stage)
      tx.save(row)
      output = metadata(row)
      return
    }
    const result = body.result
    if (
      !['ready', 'partial', 'failed'].includes(body.status) ||
      !result ||
      typeof result !== 'object' ||
      JSON.stringify(result).length > 50000
    )
      throw new BadRequestError('Résultat invalide.')
    const clean = {
      completeArtifacts: result.completeArtifacts === true,
      applicationVerified: result.applicationVerified === true,
      runtimeStarted: result.runtimeStarted === true,
      noExternalNetwork: result.noExternalNetwork === true,
      mailsDisabled: result.mailsDisabled === true,
      scheduledTasksDisabled: result.scheduledTasksDisabled === true,
      workersInert: result.workersInert === true,
      cleanedUp: result.cleanedUp === true,
      scope: result.scope === 'isolated-http-health' ? 'isolated-http-health' : 'artifacts-only',
      apps: Number(result.apps) || 0,
      databases: Number(result.databases) || 0,
      images: Number(result.images) || 0,
      archiveBytes: Number(result.archiveBytes) || 0,
      durationSeconds: Number(result.durationSeconds) || 0,
      sha256: /^[a-f0-9]{64}$/.test(result.sha256 || '') ? result.sha256 : '',
      services: Array.isArray(result.services)
        ? result.services.slice(0, 80).map((service: any) => ({
            appId: ID.test(service.appId || '') ? service.appId : '',
            name: typeof service.service === 'string' ? service.service.slice(0, 31) : '',
            status: ['running-isolated', 'inert-worker-preserved', 'failed'].includes(service.status)
              ? service.status
              : 'failed',
          }))
        : [],
    }
    const captured = decryptWebValue(row.getString('snapshotEncrypted'))
    const appIds = new Set(captured.apps.map((app: any) => app.id))
    if (
      clean.apps < 0 ||
      clean.databases < 0 ||
      clean.images < 0 ||
      clean.archiveBytes < 0 ||
      !Number.isSafeInteger(clean.apps) ||
      !Number.isSafeInteger(clean.databases) ||
      !Number.isSafeInteger(clean.images) ||
      !Number.isSafeInteger(clean.archiveBytes) ||
      clean.services.some((service: any) => !appIds.has(service.appId))
    )
      throw new BadRequestError('Inventaire de résultat invalide.')
    if (
      body.status === 'ready' &&
      (clean.apps !== captured.apps.length ||
        clean.databases !== captured.databases.length ||
        clean.images < 1 ||
        clean.archiveBytes < 1 ||
        clean.services.some((service: any) => service.status === 'failed') ||
        captured.apps.some((app: any) => !clean.services.some((service: any) => service.appId === app.id)))
    )
      throw new BadRequestError('Le résultat ne couvre pas tout le projet.')
    // A process/HTTP rehearsal does not prove complete business workflows.
    clean.applicationVerified = false
    if (
      body.status === 'ready' &&
      !(
        clean.completeArtifacts &&
        clean.scope === 'isolated-http-health' &&
        clean.services.some((service: any) => service.status === 'running-isolated') &&
        clean.runtimeStarted &&
        clean.noExternalNetwork &&
        clean.mailsDisabled &&
        clean.scheduledTasksDisabled &&
        clean.workersInert &&
        clean.cleanedUp
      )
    )
      throw new BadRequestError('Une recette incomplète ne peut pas être déclarée réussie.')
    row.set('status', body.status)
    row.set('stage', 'complete')
    row.set('finishedAt', now())
    row.set('result', clean)
    row.set(
      'errorCode',
      typeof body.errorCode === 'string' && /^[a-z_]{1,60}$/.test(body.errorCode) ? body.errorCode : ''
    )
    row.set('snapshotEncrypted', '')
    row.set('leaseHash', '')
    tx.save(row)
    output = metadata(row)
  })
  return webJson(e, { run: output })
}

export const projectRecoverySnapshot = snapshot
export const projectRecoveryPreview = preview
