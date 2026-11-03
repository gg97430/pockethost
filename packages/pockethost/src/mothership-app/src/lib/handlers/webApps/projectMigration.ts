import { parseWebCron } from '../../../../../common/webCron'
import {
  assertWebSiteCapacity,
  decryptWebValue,
  encryptWebValue,
  requirePlatformAdmin,
  webDirectLoopback,
  webInput,
  webJson,
} from './api'
import { assertNoWebDomainConflict } from './domains'
import { projectRecoveryPreview, projectRecoverySnapshot } from './projectRecovery'
import { validateRuntimeProfile } from './runtimes'

const COLLECTION = 'web_project_migrations',
  ID = /^[a-z0-9]{15}$/,
  SHA = /^[a-f0-9]{64}$/
const stamp = () => new Date().toISOString().replace('T', ' ')
const assertAdmission = () => {
  try {
    const status = JSON.parse(
      toString(
        $os
          .cmd(
            '/usr/bin/timeout',
            '3',
            '/usr/bin/python3',
            '-I',
            '-c',
            "import sys,json;sys.path.insert(0,'/opt/pockethost-web');from platform_maintenance import status;print(json.dumps(status(path=sys.argv[1])))",
            $os.getenv('WEB_PROJECT_MIGRATION_ADMISSION_FILE') || '/run/kolab-platform-maintenance/admission.json'
          )
          .output()
      )
    )
    if (status.paused !== false) throw Error('Paused')
  } catch {
    throw new ApiError(503, 'Les nouvelles opérations sont suspendues pour maintenance. Le plan est conservé.')
  }
}
const webCronScheduleValid = (value: unknown) => {
  try {
    parseWebCron(value)
    return true
  } catch {
    return false
  }
}
/** Same canonical encoding as Python json(sort_keys=True, ensure_ascii=True). */
const canonical = (value: any): string => {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value && typeof value === 'object')
    return (
      '{' +
      Object.keys(value)
        .sort()
        .map((key) => canonical(key) + ':' + canonical(value[key]))
        .join(',') +
      '}'
    )
  return JSON.stringify(value).replace(
    /[\u007f-\uffff]/g,
    (char) => '\\u' + char.charCodeAt(0).toString(16).padStart(4, '0')
  )
}
const planOf = (row: core.Record) => decryptWebValue(row.getString('planEncrypted'))
const view = (row: core.Record) => ({
  id: row.id,
  revision: row.getString('revision'),
  status: row.getString('status'),
  projectName: row.getString('projectName'),
  source: row.getString('source'),
  target: row.getString('target'),
  approvedAt: row.getString('approvedAt'),
  importedAt: row.getString('importedAt'),
  releasedAt: row.getString('releasedAt'),
  dnsRevision: row.getString('dnsRevision'),
  dnsPreview: JSON.parse(row.getString('dnsPreview') || 'null'),
  dnsApprovedAt: row.getString('dnsApprovedAt'),
  plan: JSON.parse(row.getString('publicPlan') || '{}'),
})
const operator = (e: core.RequestEvent) => {
  const key = toString(
    $os.readFile($os.getenv('WEB_PROJECT_MIGRATION_CONTROL_KEY_FILE') || '/etc/kolab/project-migration-control.key')
  ).trim()
  if (
    !/^[A-Za-z0-9]{80}$/.test(key) ||
    !$security.equal(key, e.request!.header.get('X-Project-Migration') || '') ||
    !webDirectLoopback(e)
  )
    throw new ForbiddenError('Opérateur local de migration requis.')
}
const find = (store: any, collection: string, id: string) => {
  try {
    return store.findRecordById(collection, id) as core.Record
  } catch {
    return null
  }
}
const rowFrom = (body: any, store: any) => {
  if (!ID.test(body.id || '') || !SHA.test(body.revision || '')) throw new BadRequestError('Plan révisé requis.')
  const row = store.findRecordById(COLLECTION, body.id)
  if (row.getString('revision') !== body.revision) throw new ApiError(409, 'Le plan a changé.')
  return row as core.Record
}
const projection = (plan: any) => ({
  id: plan.id,
  revision: plan.revision,
  projectId: plan.projectId,
  targetProjectId: plan.targetProjectId,
  projectName: plan.projectName,
  source: plan.source,
  target: plan.target,
  ownerId: plan.ownerId,
  expiresAt: plan.expiresAt,
  requiredBytes: plan.requiredBytes,
  requiredMemoryMiB: plan.requiredMemoryMiB,
  writeBarrier: plan.writeBarrier || null,
  restoreBytes: plan.restoreBytes,
  archiveSha256: plan.archiveSha256,
  applications: plan.applications.map((row: any) => ({
    sourceId: row.sourceId,
    targetId: row.targetId,
    name: row.name,
    hasFront: row.hasFront,
  })),
  databases: plan.databases.map((row: any) => ({
    sourceId: row.sourceId,
    targetId: row.targetId,
    subdomain: row.subdomain,
    version: row.version,
    memoryReservationMiB: row.memoryReservationMiB,
    memoryRequestMiB: row.memoryRequestMiB,
    targetHost: row.targetHost,
  })),
  environment: plan.environment.map((row: any) => ({
    appTargetId: row.appTargetId,
    name: row.name,
    action: row.action,
    build: row.build,
    runtime: row.runtime,
  })),
  bindings: plan.bindings.map((row: any) => ({
    appTargetId: row.appTargetId,
    instanceTargetId: row.instanceTargetId,
    port: row.port,
  })),
  scheduledTasks: plan.scheduledTasks.map((row: any) => ({
    name: row.name,
    appTargetId: row.appTargetId,
    schedule: row.schedule,
    timezone: row.timezone,
    sourceEnabled: row.sourceEnabled,
    enabled: false,
  })),
  domains: plan.domains.map((row: any) => ({
    host: row.host,
    appTargetId: row.appTargetId,
    targetStatus: 'pending',
    proxied: true,
  })),
  volumes: plan.volumes.map((row: any) => ({ appTargetId: row.appTargetId, name: row.name, bytes: row.state.bytes })),
  workers: plan.workers.map((row: any) => ({ appTargetId: row.appTargetId, service: row.service, enabled: false })),
  blockers: plan.blockers.map((row: any) => ({ code: row.code, appId: row.appId, name: row.name })),
  limitations: plan.limitations,
  sourcePreserved: true,
  importedPaused: true,
  mailsEnabled: false,
  cronsEnabled: false,
  autoDeploy: false,
})
const validatePlan = (plan: any, store: any) => {
  if (
    !plan ||
    typeof plan !== 'object' ||
    plan.version !== 1 ||
    plan.mode !== 'paused-import' ||
    JSON.stringify(plan).length > 4000000 ||
    !ID.test(plan.id || '') ||
    !ID.test(plan.ownerId || '') ||
    !ID.test(plan.projectId || '') ||
    !ID.test(plan.targetProjectId || '') ||
    !SHA.test(plan.revision || '') ||
    !SHA.test(plan.archiveSha256 || '')
  )
    throw new BadRequestError('Plan privé invalide.')
  const review = { ...plan }
  delete review.revision
  if ($security.sha256(canonical(review)) !== plan.revision) throw new BadRequestError('Empreinte du plan invalide.')
  const host = $os.getenv('PUBLIC_HOST') || $os.getenv('APEX_DOMAIN')
  if (
    !['app3', 'app4', 'app5'].includes(plan.source) ||
    !['app3', 'app4', 'app5'].includes(plan.target) ||
    plan.source === plan.target ||
    host !== plan.target + '.monappli.re'
  )
    throw new BadRequestError('Le plan doit viser ce serveur.')
  if (
    !Number.isSafeInteger(plan.expiresAt) ||
    plan.expiresAt * 1000 < Date.now() ||
    plan.expiresAt * 1000 > Date.now() + 1900000
  )
    throw new BadRequestError('Plan expiré ou durée excessive.')
  const owner = find(store, 'users', plan.ownerId)
  if (!owner || owner.getString('suspension') || !owner.getBool('superAdmin'))
    throw new BadRequestError('Un propriétaire superadmin existant sur la cible est requis.')
  if (typeof plan.projectName !== 'string' || !plan.projectName.trim() || plan.projectName.length > 80)
    throw new BadRequestError('Nom cible invalide.')
  for (const [key, max] of [
    ['applications', 20],
    ['databases', 20],
    ['environment', 10000],
    ['bindings', 80],
    ['scheduledTasks', 2000],
    ['domains', 1000],
    ['volumes', 80],
    ['workers', 80],
    ['blockers', 10000],
  ] as const)
    if (!Array.isArray(plan[key]) || plan[key].length > max) throw new BadRequestError('Inventaire hors bornes.')
  if (
    store.countRecords('instances', $dbx.exp('uid={:id}', { id: plan.ownerId })) + plan.databases.length >
    owner.getInt('subscription_quantity')
  )
    throw new BadRequestError('Le quota de bases du propriétaire cible est insuffisant.')
  if (!plan.applications.length || !Number.isSafeInteger(plan.requiredBytes) || plan.requiredBytes < 15 * 1024 ** 3)
    throw new BadRequestError('Capacité du plan invalide.')
  const ids = new Set<string>(),
    applications = new Set<string>(),
    databases = new Set<string>()
  const fresh = (collection: string, id: any) => {
    if (typeof id !== 'string' || !ID.test(id) || ids.has(id) || find(store, collection, id))
      throw new BadRequestError('Un identifiant cible est déjà utilisé.')
    ids.add(id)
  }
  fresh('web_projects', plan.targetProjectId)
  for (const row of plan.applications) {
    fresh('web_apps', row.targetId)
    fresh('web_deployments', row.targetDeployment)
    applications.add(row.targetId)
    if (
      !ID.test(row.sourceId || '') ||
      row.sourceId === row.targetId ||
      typeof row.hasFront !== 'boolean' ||
      typeof row.name !== 'string' ||
      !row.name ||
      row.name.length > 80
    )
      throw new BadRequestError('Mapping application invalide.')
  }
  for (const row of plan.databases) {
    fresh('instances', row.targetId)
    databases.add(row.targetId)
    if (
      !/^0\.\d{1,3}\.\d{1,4}$/.test(row.version || '') ||
      String(row.subdomain).startsWith('web-') ||
      !/^[a-z][a-z0-9-]{1,58}[a-z0-9]$/.test(row.subdomain || '') ||
      row.targetHost !== row.subdomain + '.' + host ||
      store.findRecordsByFilter('instances', 'subdomain={:name}', '', 1, 0, { name: row.subdomain }).length
    )
      throw new BadRequestError('Mapping base invalide.')
    assertNoWebDomainConflict(row.targetHost)
  }
  for (const category of ['environment', 'bindings', 'scheduledTasks', 'domains', 'volumes', 'workers'])
    for (const row of plan[category])
      if (!applications.has(row.appTargetId)) throw new BadRequestError('Mapping hors application cible.')
  for (const row of plan.bindings) {
    fresh('web_private_db_bindings', row.targetId)
    if (!databases.has(row.instanceTargetId) || !Number.isInteger(row.port) || row.port < 1024 || row.port > 65535)
      throw new BadRequestError('Liaison cible invalide.')
  }
  for (const row of plan.scheduledTasks) {
    fresh('web_scheduled_tasks', row.targetId)
    if (
      typeof row.command !== 'string' ||
      !row.command ||
      row.command.length > 10000 ||
      !webCronScheduleValid(row.schedule) ||
      !['UTC', 'Indian/Reunion'].includes(row.timezone) ||
      row.enabled !== false
    )
      throw new BadRequestError('Tâche cible invalide.')
  }
  for (const row of plan.domains) {
    fresh('web_app_domains', row.targetId)
    if (
      !/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(row.host || '') ||
      store.findRecordsByFilter('web_app_domains', 'host={:host}', '', 1, 0, { host: row.host }).length
    )
      throw new BadRequestError('Domaine cible déjà réservé ou invalide.')
  }
  for (const row of plan.environment)
    if (
      !/^[A-Za-z_][A-Za-z0-9_]{0,119}$/.test(row.name || '') ||
      typeof row.value !== 'string' ||
      row.value.includes('\0') ||
      typeof row.build !== 'boolean' ||
      typeof row.runtime !== 'boolean'
    )
      throw new BadRequestError('Variable cible invalide.')
  return plan
}
const recordsOf = (row: core.Record) =>
  JSON.parse(row.getString('importedRecords') || '[]') as { collection: string; id: string }[]
const importedUntouched = (row: core.Record, store: any) => {
  const plan = planOf(row)
  for (const mapped of plan.applications) {
    const app = store.findRecordById('web_apps', mapped.targetId)
    if (
      app.getString('migrationLock') !== row.id ||
      app.getString('status') !== 'stopped' ||
      app.getString('activeDeployment') !== mapped.targetDeployment ||
      store.findRecordsByFilter('web_deployments', 'appId={:id} && (status="queued" || status="running")', '', 1, 0, {
        id: app.id,
      }).length
    )
      throw new BadRequestError('Une application importée est déjà utilisée ou déverrouillée.')
  }
  for (const mapped of plan.databases) {
    const instance = store.findRecordById('instances', mapped.targetId)
    if (instance.getString('migrationLock') !== row.id || instance.getBool('power'))
      throw new BadRequestError('Une base importée est déjà utilisée ou déverrouillée.')
  }
}
export const HandleProjectMigrationsGet = (e: core.RequestEvent) => {
  requirePlatformAdmin(e)
  return webJson(e, {
    items: $app.findRecordsByFilter(COLLECTION, '', '-created,-id', 50, 0).map(view),
    ownerId: e.auth!.id,
  })
}
export const HandleProjectMigrationSources = (e: core.RequestEvent) => {
  requirePlatformAdmin(e)
  return webJson(e, {
    items: $app
      .findRecordsByFilter('web_projects', '', 'name', 1000, 0)
      .map((row: core.Record) => ({ id: row.id, name: row.getString('name') })),
  })
}
export const HandleProjectMigrationSourcePreview = (e: core.RequestEvent) => {
  requirePlatformAdmin(e)
  return webJson(e, projectRecoveryPreview(projectRecoverySnapshot(e.request!.pathValue('projectId'))))
}
export const HandleProjectMigrationApprove = (e: core.RequestEvent) => {
  const actor = requirePlatformAdmin(e),
    body = webInput(e)
  let saved: any
  $app.runInTransaction((tx) => {
    const row = rowFrom({ id: e.request!.pathValue('id'), revision: body.revision }, tx),
      plan = planOf(row)
    if (row.getString('status') !== 'review' || body.confirmation !== row.getString('projectName'))
      throw new BadRequestError('Confirmez le nom exact du projet pour autoriser son import suspendu.')
    validatePlan(plan, tx)
    row.set('status', 'approved')
    row.set('actorId', actor.id)
    row.set('approvedAt', stamp())
    tx.save(row)
    saved = view(row)
  })
  return webJson(e, { item: saved })
}
export const HandleProjectMigrationRelease = (e: core.RequestEvent) => {
  requirePlatformAdmin(e)
  const body = webInput(e)
  let saved: any
  $app.runInTransaction((tx) => {
    const row = rowFrom({ id: e.request!.pathValue('id'), revision: body.revision }, tx),
      plan = planOf(row)
    if (
      row.getString('status') !== 'imported_paused' ||
      body.confirmation !== row.getString('projectName') ||
      body.keepDeliveryDisabled !== true
    )
      throw new BadRequestError('Import suspendu et confirmation explicite requis.')
    if (plan.blockers.length)
      throw new BadRequestError(
        'Résolvez les blocages du plan avant une recette en service. Les artefacts restent suspendus.'
      )
    if (plan.workers.length && body.sourceWorkersPaused !== true)
      throw new BadRequestError('Confirmez que les workers source sont arrêtés avant de déverrouiller leur copie.')
    importedUntouched(row, tx)
    for (const name of ['web_apps', 'web_projects', 'instances'])
      for (const item of recordsOf(row).filter((item) => item.collection === name)) {
        const record = tx.findRecordById(name, item.id)
        record.set('migrationLock', '')
        tx.save(record)
      }
    for (const binding of plan.bindings) {
      const record = tx.findRecordById('web_private_db_bindings', binding.targetId)
      record.set('enabled', true)
      record.set('status', 'pending')
      tx.save(record)
    }
    row.set('status', 'released')
    row.set('releasedAt', stamp())
    tx.save(row)
    saved = view(row)
  })
  return webJson(e, { item: saved, servicesStarted: false, mailsEnabled: false, cronsEnabled: false })
}
export const HandleProjectMigrationDnsApprove = (e: core.RequestEvent) => {
  requirePlatformAdmin(e)
  const body = webInput(e)
  let saved: any
  $app.runInTransaction((tx) => {
    const row = rowFrom({ id: e.request!.pathValue('id'), revision: body.revision }, tx),
      plan = planOf(row)
    if (
      row.getString('status') !== 'released' ||
      body.confirmation !== row.getString('projectName') ||
      !SHA.test(body.dnsRevision || '') ||
      ['sourceWritesPaused', 'sourceCronsPaused', 'targetVerified', 'nonAtomicAccepted'].some(
        (key) => body[key] !== true
      )
    )
      throw new BadRequestError('Les validations de bascule et la révision DNS exacte sont requises.')
    if (plan.blockers.length) throw new BadRequestError('Le plan contient encore des blocages.')
    const dns = JSON.parse(row.getString('dnsPreview') || 'null')
    if (!dns || dns.revision !== body.dnsRevision || dns.expiresAt * 1000 < Date.now())
      throw new BadRequestError('Examinez un aperçu DNS actuel avant de valider.')
    if (
      !plan.writeBarrier ||
      plan.writeBarrier.projectId !== plan.projectId ||
      plan.writeBarrier.source !== plan.source ||
      plan.writeBarrier.sourceWritesPaused !== true ||
      plan.writeBarrier.sourceCronsPaused !== true ||
      plan.writeBarrier.sourceWorkersPaused !== true
    )
      throw new BadRequestError(
        'La capture doit avoir été précédée du gel explicite des écritures, crons et workers source. Exportez une nouvelle capsule après ce gel.'
      )
    for (const mapped of plan.applications) {
      const app = tx.findRecordById('web_apps', mapped.targetId)
      if (mapped.hasFront && app.getString('status') !== 'running')
        throw new BadRequestError('Une application cible n’est pas encore en service.')
    }
    row.set('dnsRevision', body.dnsRevision)
    row.set('dnsApprovedAt', stamp())
    tx.save(row)
    saved = view(row)
  })
  return webJson(e, { item: saved, dnsChanged: false })
}
export const HandleProjectMigrationOperator = (e: core.RequestEvent) => {
  operator(e)
  const body = webInput(e),
    action = e.request!.pathValue('action')
  let result: any = {}
  $app.runInTransaction((tx) => {
    if (['take', 'rollback'].includes(action)) {
      if (!ID.test(body.id || '')) throw new BadRequestError('Plan invalide.')
      tx.db().newQuery('UPDATE web_project_migrations SET status=status WHERE id={:id}').bind({ id: body.id }).execute()
    }
    if (action === 'export-snapshot') {
      const snapshot: any = projectRecoverySnapshot(body.projectId, tx)
      if (snapshot.revision !== body.revision) throw new ApiError(409, 'Le projet source a changé depuis l’aperçu.')
      if (body.sourceWritesPaused === true) {
        if (
          body.confirmation !== snapshot.projectId ||
          body.sourceCronsPaused !== true ||
          body.sourceWorkersPaused !== true
        )
          throw new BadRequestError('Confirmation du gel source incomplète.')
        for (const app of snapshot.apps)
          if (app.scheduledTasks.some((task: any) => task.enabled))
            throw new BadRequestError('Désactivez les crons source avant une capture destinée à une bascule.')
        snapshot.migrationSourceBarrier = {
          version: 1,
          projectId: snapshot.projectId,
          source: snapshot.platform,
          attestedAt: stamp(),
          sourceWritesPaused: true,
          sourceCronsPaused: true,
          sourceWorkersPaused: true,
          attestationOnly: true,
        }
      }
      result = { snapshot }
      return
    }
    if (action === 'prepare') {
      const plan = validatePlan(body.plan, tx)
      if (find(tx, COLLECTION, plan.id)) throw new ApiError(409, 'Plan déjà enregistré.')
      const row = new Record(tx.findCollectionByNameOrId(COLLECTION))
      row.id = plan.id
      for (const [key, value] of Object.entries({
        projectName: plan.projectName,
        ownerId: plan.ownerId,
        source: plan.source,
        target: plan.target,
        revision: plan.revision,
        status: 'review',
        planEncrypted: encryptWebValue(plan),
        publicPlan: projection(plan),
        expiresAt: new Date(plan.expiresAt * 1000).toISOString(),
      }))
        row.set(key, value)
      tx.save(row)
      result = { item: view(row) }
      return
    }
    const row = rowFrom(body, tx),
      plan = planOf(row)
    if (action === 'state') {
      result = { item: view(row) }
      return
    }
    if (action === 'dns-preview') {
      const preview = body.preview,
        review = { ...preview }
      delete review.revision
      if (
        !['imported_paused', 'released'].includes(row.getString('status')) ||
        !preview ||
        preview.migrationId !== row.id ||
        preview.planRevision !== row.getString('revision') ||
        preview.revision !== $security.sha256(canonical(review)) ||
        !Array.isArray(preview.changes) ||
        preview.changes.length !== plan.domains.length ||
        preview.expiresAt * 1000 < Date.now() ||
        preview.expiresAt * 1000 > Date.now() + 910000 ||
        plan.domains.some(
          (domain: any) =>
            preview.changes.filter(
              (change: any) =>
                change.host === domain.host &&
                change.after?.proxied === true &&
                change.after?.type === 'A' &&
                change.after?.name === domain.host
            ).length !== 1
        )
      )
        throw new BadRequestError('Aperçu DNS non conforme au plan.')
      row.set('dnsPreview', preview)
      row.set('dnsRevision', '')
      row.set('dnsApprovedAt', '')
      tx.save(row)
      result = { item: view(row) }
      return
    }
    if (action === 'take') {
      if (row.getString('status') !== 'approved')
        throw new BadRequestError('L’import suspendu doit être approuvé dans Kolab.')
      const actor = find(tx, 'users', row.getString('actorId'))
      if (!actor?.getBool('superAdmin') || actor.getString('suspension'))
        throw new ForbiddenError('L’approbateur n’est plus autorisé.')
      validatePlan(plan, tx)
      assertAdmission()
      row.set('status', 'importing')
      tx.save(row)
      result = { taken: true }
      return
    }
    if (action === 'fail') {
      if (row.getString('status') === 'importing') {
        row.set('status', 'failed')
        tx.save(row)
      }
      result = { saved: true }
      return
    }
    if (action === 'rollback') {
      if (!['imported_paused', 'failed', 'rolling_back'].includes(row.getString('status')))
        throw new BadRequestError('Seule une copie suspendue peut être retirée automatiquement.')
      if (row.getString('status') !== 'rolling_back' && recordsOf(row).length) importedUntouched(row, tx)
      assertAdmission()
      row.set('status', 'rolling_back')
      tx.save(row)
      result = { allowed: true }
      return
    }
    if (action === 'rolled-back') {
      if (row.getString('status') !== 'rolling_back') throw new BadRequestError('Retour arrière non engagé.')
      for (const item of recordsOf(row).reverse()) {
        const record = find(tx, item.collection, item.id)
        if (record) tx.delete(record)
      }
      row.set('status', 'rolled_back')
      row.set('importedRecords', [])
      tx.save(row)
      result = { rolledBack: true }
      return
    }
    if (action === 'dns-ready') {
      if (
        row.getString('status') !== 'released' ||
        row.getString('dnsRevision') !== body.dnsRevision ||
        Date.now() - Date.parse(row.getString('dnsApprovedAt')) > 900000
      )
        throw new ForbiddenError('Aucune approbation récente pour cet aperçu DNS.')
      result = { approved: true }
      return
    }
    if (action === 'dns-result') {
      if (row.getString('dnsRevision') !== body.dnsRevision || body.status !== 'cutover')
        throw new ForbiddenError('Révision DNS différente.')
      row.set('status', 'cutover')
      tx.save(row)
      result = { recorded: true }
      return
    }
    if (action !== 'commit' || row.getString('status') !== 'importing' || canonical(body.plan) !== canonical(plan))
      throw new BadRequestError('Import non engagé ou plan modifié.')
    const captured = body.capsule
    if (
      !captured ||
      captured.projectId !== plan.projectId ||
      captured.revision !== plan.capsuleRevision ||
      !Array.isArray(captured.apps) ||
      captured.apps.length !== plan.applications.length
    )
      throw new BadRequestError('Capsule différente du plan.')
    const probe = body.drill
    if (
      !probe ||
      probe.sandboxUid !== 1002 ||
      probe.capsuleRevision !== plan.capsuleRevision ||
      probe.manifestSha256 !== plan.manifestSha256 ||
      probe.noExternalNetwork !== true ||
      probe.cleanedUp !== true ||
      !Array.isArray(probe.services)
    )
      throw new BadRequestError('Une recette isolée de cette capsule doit être terminée et nettoyée avant import.')
    const expected = captured.apps.flatMap((app: any) =>
      app.services.map((service: any) => ({
        appId: app.id,
        service: service.name,
        status: service.front ? 'running-isolated' : 'inert-worker-preserved',
      }))
    )
    if (
      probe.services.length !== expected.length ||
      expected.some(
        (service: any) =>
          probe.services.filter(
            (item: any) =>
              item.appId === service.appId && item.service === service.service && item.status === service.status
          ).length !== 1
      )
    )
      throw new BadRequestError('La recette ne couvre pas exactement les services capturés.')
    const records: { collection: string; id: string }[] = []
    const save = (collection: string, id: string, values: any) => {
      const record = new Record(tx.findCollectionByNameOrId(collection))
      record.id = id
      for (const [key, value] of Object.entries(values)) record.set(key, value)
      tx.save(record)
      records.push({ collection, id: record.id })
      return record
    }
    save('web_projects', plan.targetProjectId, {
      name: plan.projectName,
      repository: 'migration/' + plan.projectId,
      actorId: plan.ownerId,
      multiRepository: true,
      allowedRepositories: [],
      previewEnabled: false,
      previewMax: 3,
      previewTtlHours: 24,
      migrationLock: row.id,
    })
    save('web_project_members', $security.randomStringWithAlphabet(15, 'abcdefghijklmnopqrstuvwxyz0123456789'), {
      project: plan.targetProjectId,
      user: plan.ownerId,
      role: 'owner',
      invitedBy: plan.ownerId,
    })
    for (const mapped of plan.databases) {
      if (find(tx, 'instances', mapped.targetId)) throw new ApiError(409, 'Base cible déjà présente.')
      const description = captured.databases[mapped.sourceId]
      if (!description || description.version !== mapped.version) throw new BadRequestError('Binaire natif incohérent.')
      save('instances', mapped.targetId, {
        uid: plan.ownerId,
        subdomain: mapped.subdomain,
        version: mapped.version,
        power: false,
        status: 'idle',
        dev: false,
        syncAdmin: false,
        autoVacuum: false,
        secrets: description.configuration?.secrets || [],
        migrationLock: row.id,
      })
    }
    for (const mapped of plan.applications) {
      assertWebSiteCapacity(tx)
      const original = captured.apps.find((app: any) => app.id === mapped.sourceId)
      if (
        !original ||
        original.deploymentId !== mapped.sourceDeployment ||
        !Array.isArray(original.services) ||
        !original.services.length ||
        mapped.hasFront !== original.services.some((service: any) => service.front === true) ||
        original.services.some(
          (service: any) => !/^sha256:[a-f0-9]{64}$/.test(body.images?.[mapped.targetId]?.[service.name] || '')
        )
      )
        throw new BadRequestError('Images importées incomplètes.')
      const input = { ...(original.configuration.runtime || { kind: 'nextjs' }) }
      delete input.registry
      if (input.compose) input.compose = { front: input.compose.front, services: input.compose.services }
      const runtime = validateRuntimeProfile(input),
        config = {
          ...original.configuration.config,
          repository: '',
          repositoryId: 0,
          autoDeploy: false,
          generatedDomain: `web-${mapped.targetId}.${$os.getenv('APEX_DOMAIN') || 'monappli.re'}`,
        }
      for (const name of ['source', 'gitSourceId', 'githubConnectionId']) delete config[name]
      const environment: any = {}
      for (const item of plan.environment.filter((item: any) => item.appTargetId === mapped.targetId))
        environment[item.name] = { value: item.value, build: item.build, runtime: item.runtime }
      // Image ENV is also overridden: a baked delivery secret must not activate mail during preview.
      for (const service of original.services)
        for (const text of service.environment || []) {
          const name = String(text).split('=', 1)[0] || ''
          if (/SMTP|BREVO|SENDGRID|MAILGUN|RESEND|SES_|STRIPE|WEBHOOK|CRON_SECRET/i.test(name))
            environment[name] = { value: '', build: false, runtime: true }
        }
      for (const [name, value] of Object.entries({
        KOLAB_MIGRATION_PAUSED: '1',
        DISABLE_CRON: 'true',
        CRON_ENABLED: 'false',
        SMTP_ENABLED: 'false',
        MAIL_ENABLED: 'false',
      }))
        environment[name] = { value, build: false, runtime: true }
      save('web_apps', mapped.targetId, {
        name: mapped.name,
        uid: plan.ownerId,
        config,
        envEncrypted: encryptWebValue(environment),
        status: 'stopped',
        activeDeployment: mapped.targetDeployment,
        desiredCommit: original.commit || '',
        migrationLock: row.id,
      })
      const snapshot = { config, env: environment, runtime, storage: original.configuration.storage || [], domains: [] }
      save('web_runtime_profiles', $security.randomStringWithAlphabet(15, 'abcdefghijklmnopqrstuvwxyz0123456789'), {
        appId: mapped.targetId,
        profile: runtime,
        registryEncrypted: '',
        revision: $security.randomString(24),
      })
      save('web_storage_settings', $security.randomStringWithAlphabet(15, 'abcdefghijklmnopqrstuvwxyz0123456789'), {
        appId: mapped.targetId,
        volumes: snapshot.storage,
        retention: 7,
        revision: $security.randomString(24),
      })
      save('web_app_environments', $security.randomStringWithAlphabet(15, 'abcdefghijklmnopqrstuvwxyz0123456789'), {
        projectId: plan.targetProjectId,
        appId: mapped.targetId,
        environment: 'prod',
        slug: 'migration-' + mapped.targetId,
        state: 'active',
      })
      const images = body.images[mapped.targetId],
        first = images[original.services[0].name]
      save('web_deployments', mapped.targetDeployment, {
        appId: mapped.targetId,
        actorId: plan.ownerId,
        action: 'migration-import',
        commit: original.commit || '',
        status: 'ready',
        stage: 'Importée suspendue',
        snapshotEncrypted: encryptWebValue(snapshot),
        result:
          runtime.kind === 'compose'
            ? { images, runtimeKind: runtime.kind }
            : { image: first, runtimeKind: runtime.kind },
        finishedAt: stamp(),
      })
    }
    for (const binding of plan.bindings)
      save('web_private_db_bindings', binding.targetId, {
        appId: binding.appTargetId,
        instanceId: binding.instanceTargetId,
        actorId: plan.ownerId,
        port: binding.port,
        enabled: false,
        tokenEncrypted: encryptWebValue({ token: $security.randomString(64) }),
        revision: $security.randomString(24),
        status: 'unavailable',
        needsRestart: true,
      })
    for (const task of plan.scheduledTasks)
      save('web_scheduled_tasks', task.targetId, {
        appId: task.appTargetId,
        actorId: plan.ownerId,
        name: task.name,
        commandEncrypted: encryptWebValue({ command: task.command }),
        schedule: task.schedule,
        timezone: task.timezone,
        enabled: false,
        timeoutSeconds: task.timeoutSeconds,
        catchUp: 'skip',
        maxCatchUpDelayMinutes: 15,
      })
    for (const domain of plan.domains)
      save('web_app_domains', domain.targetId, {
        appId: domain.appTargetId,
        host: domain.host,
        status: 'pending',
        ownershipToken: $security.randomString(48),
        autoDns: false,
        lastError: 'Migration suspendue : DNS et certificat à valider avant bascule.',
      })
    row.set('status', 'imported_paused')
    row.set('publicPlan', {
      ...projection(plan),
      drill: { verified: true, sandboxUid: 1002, noExternalNetwork: true, cleanedUp: true, services: expected.length },
    })
    row.set('importedAt', stamp())
    row.set('importedRecords', records)
    tx.save(row)
    result = { item: view(row), servicesStarted: false }
  })
  return webJson(e, result)
}

/** Model-level locks cover metadata edits and queued operations admitted before import commit. */
const lockOf = (record: core.Record, store: any) => {
  const name = record.collection().name
  if (['instances', 'web_apps', 'web_projects'].includes(name))
    return record.getString('migrationLock') || find(store, name, record.id)?.getString('migrationLock') || ''
  if (!name.startsWith('web_') || name === COLLECTION) return ''
  const appId = record.getString('appId')
  if (appId) return find(store, 'web_apps', appId)?.getString('migrationLock') || ''
  const projectId = record.getString('projectId') || (name === 'web_project_members' ? record.getString('project') : '')
  return projectId ? find(store, 'web_projects', projectId)?.getString('migrationLock') || '' : ''
}
export const GuardProjectMigrationRecord = (e: any) => {
  const record = e.record as core.Record,
    name = record.collection().name,
    store = e.app || $app,
    lock = lockOf(record, store)
  if (!lock) return e.next()
  const migration = find(store, COLLECTION, lock)
  if (migration?.getString('status') === 'importing') return e.next()
  if (['instances', 'web_apps', 'web_projects'].includes(name) && !record.isNew()) {
    const previous = find(store, name, record.id)
    const keys =
      name === 'instances'
        ? ['uid', 'subdomain', 'version', 'power', 'dev', 'syncAdmin', 'autoVacuum', 'secrets', 'cname']
        : name === 'web_apps'
          ? ['name', 'uid', 'config', 'envEncrypted', 'activeDeployment', 'desiredCommit', 'deletionId', 'status']
          : [
              'name',
              'repository',
              'actorId',
              'multiRepository',
              'allowedRepositories',
              'previewEnabled',
              'previewMax',
              'previewTtlHours',
              'deletionId',
            ]
    if (previous && keys.every((key) => record.getString(key) === previous.getString(key))) return e.next() // unlock alone, or harmless telemetry
  }
  throw new BadRequestError('Import suspendu : déverrouillez son plan avant de modifier cette ressource.')
}
export const GuardProjectMigrationDelete = (e: any) => {
  const lock = lockOf(e.record, e.app || $app)
  if (lock && find(e.app || $app, COLLECTION, lock)?.getString('status') !== 'rolling_back')
    throw new BadRequestError('Import suspendu : utilisez le retour arrière de son assistant.')
  return e.next()
}
export const GuardProjectMigrationRequest = (e: any) => {
  const body = e.requestInfo().body || {}
  if (
    Object.keys(body).some(
      (key) => key === 'migrationLock' || key.startsWith('migrationLock:') || key.startsWith('migrationLock+')
    )
  )
    throw new ForbiddenError('Le verrou de migration est réservé à son assistant.')
  if (e.request?.method === 'DELETE' && lockOf(e.record, e.app || $app))
    throw new ForbiddenError('Import suspendu : retour arrière opérateur requis.')
  return e.next()
}
