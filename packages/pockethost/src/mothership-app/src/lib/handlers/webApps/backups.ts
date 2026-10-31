import type { WebPermission } from '../../../../../common/webAccess'
import { type WebAccess, canSetWebRepository, requireWebAppPermission } from './access'
import {
  decryptWebValue,
  encryptWebValue,
  queueWebDeployment,
  serializeWebDeployment,
  webConfig,
  webDirectLoopback,
  webEnabled,
  webInput,
  webJson,
  webRoot,
} from './api'
import { resolvedConnectionId } from './githubConnections'
import { emitWebAlert, webAlertSummary } from './notifications'
import {
  assertWebDeploymentInScope,
  assertWebEnvironmentBackupAllowed,
  exportWebEnvironmentBackup,
  resolveWebEnvironment,
  webHistoryInScope,
} from './projects'
import { currentWebRuntimeRevision, runtimeWorkerSnapshot, validateRuntimeProfile } from './runtimes'
import { storageWorkerSnapshot } from './storage'

const BACKUPS = 'web_app_backups',
  SETTINGS = 'web_app_backup_settings'
/** Baux interrompus (crash, OOM, redémarrage) avant l'abandon : la sauvegarde passe alors en échec et alerte. */
const MAX_LEASES = 3
/** Suppression abandonnée après une boucle de crash : retentée automatiquement une fois par jour. */
const DELETE_RETRY_AFTER = 86400000
const FAILURE_CODES = ['attempts_exhausted', 'backup_error', 'engine_error']
const ARCHIVE_READ = String.raw`import os,pathlib,stat,sys
root=pathlib.Path(sys.argv[1]).absolute()
if root.resolve()!=root:raise SystemExit(1)
directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
 for name in ['backups',sys.argv[2]]:
  child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory);os.close(directory);directory=child
 descriptor=os.open(sys.argv[3]+'.phwb',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)
 try:
  info=os.fstat(descriptor)
  if not stat.S_ISREG(info.st_mode)or info.st_size>524288:raise SystemExit(1)
  with os.fdopen(os.dup(descriptor),'rb')as stream:raw=stream.read(524289)
  if len(raw)>524288:raise SystemExit(1)
  sys.stdout.buffer.write(raw)
 finally:os.close(descriptor)
finally:os.close(directory)
`
const ARCHIVE_LIST = String.raw`import hashlib,json,os,pathlib,stat,sys
root=pathlib.Path(sys.argv[1]).absolute();app=sys.argv[2];items=json.loads(sys.argv[3]);result={i['id']:False for i in items}
if root.resolve()==root:
 for item in items:
  p=root/'backups'/app/(item['id']+'.phwb')
  try:
   if p.resolve()!=p:continue
   descriptor=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
   try:
    info=os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode)or info.st_size>524288:continue
    with os.fdopen(os.dup(descriptor),'rb')as stream:raw=stream.read(524289)
    result[item['id']]=len(raw)<=524288 and hashlib.sha256(raw).hexdigest()==item['checksum']
   finally:os.close(descriptor)
  except OSError:pass
print(json.dumps(result))
`
const now = () => new Date().toISOString()
const id = (value: string) => {
  if (!/^[a-z0-9]{15}$/.test(value || '')) throw new BadRequestError('Sauvegarde invalide.')
  return value
}
const imageRef = (value: string, appId: string) =>
  /^sha256:[a-f0-9]{64}$/.test(value || '') || new RegExp(`^ph-web/${appId}:[a-z0-9]{15}$`).test(value || '')
const valueOf = (row: core.Record, field: string) => JSON.parse(row.getString(field) || '{}')
const imageMap = (value: any) => {
  if (value === undefined || value === null) return {} as Record<string, string>
  if (
    typeof value !== 'object' ||
    Array.isArray(value) ||
    Object.keys(value).length > 4 ||
    Object.entries(value).some(
      ([name, ref]) =>
        !/^[a-z][a-z0-9-]{0,30}$/.test(name) || typeof ref !== 'string' || !/^sha256:[a-f0-9]{64}$/.test(ref)
    )
  )
    throw new BadRequestError('Inventaire des images de sauvegarde invalide.')
  return value as Record<string, string>
}
const imageSizes = (value: any) => {
  if (value === undefined || value === null) return {} as Record<string, number>
  if (
    typeof value !== 'object' ||
    Array.isArray(value) ||
    Object.keys(value).length > 4 ||
    Object.entries(value).some(
      ([ref, size]) => !/^sha256:[a-f0-9]{64}$/.test(ref) || !Number.isSafeInteger(size) || Number(size) < 0
    )
  )
    throw new BadRequestError('Tailles des images invalides.')
  return value as Record<string, number>
}
const imageObjects = (value: any) => {
  if (value === undefined || value === null) return {} as Record<string, string>
  if (
    typeof value !== 'object' ||
    Array.isArray(value) ||
    Object.keys(value).length > 4 ||
    Object.entries(value).some(
      ([ref, key]) =>
        !/^sha256:[a-f0-9]{64}$/.test(ref) || typeof key !== 'string' || key.length > 500 || /[\r\n?]/.test(key)
    )
  )
    throw new BadRequestError('Références des images S3 invalides.')
  return value as Record<string, string>
}
const equalMap = (a: any, b: any) =>
  JSON.stringify(Object.entries(a || {}).sort()) === JSON.stringify(Object.entries(b || {}).sort())

export const currentBackupAppRevision = (app: core.Record, store: any = $app) =>
  $security.sha256(
    JSON.stringify([
      app.getString('name'),
      app.getString('config'),
      app.getString('envEncrypted'),
      app.getString('desiredCommit'),
      app.getString('activeDeployment'),
      resolveWebEnvironment(app, store).context.revision,
      currentWebRuntimeRevision(app, store),
      storageWorkerSnapshot(app, store),
    ])
  )
export const assertBackupAppRevision = (app: core.Record, expected: string, store: any = $app) => {
  if (typeof expected !== 'string' || !$security.equal(currentBackupAppRevision(app, store), expected))
    throw new BadRequestError(
      'L’application a changé depuis la préparation de la restauration. Préparez un nouvel aperçu ; les réglages actuels sont conservés.'
    )
}
const selected = (e: core.RequestEvent, permission: WebPermission) => {
  const access = requireWebAppPermission(e, permission)
  return { user: access.user, app: access.app!, access }
}
/**
 * Une restauration réécrit la configuration et le rattachement de projet sauvegardés. Hors superAdmin, elle ne doit ni
 * passer à un dépôt Git hors de la liste autorisée du projet (app.repository) ni sortir l'application de tout projet
 * (app.attach/app.detach).
 */
/**
 * Sauvegarde produite dans le projet actuel (ou ses origines) ; hors projet seulement si l'application n'en a jamais
 * quitté.
 */
const backupInScope = (app: core.Record, payload: any) => {
  const state = payload?.projectState
  if (!state) return webHistoryInScope(app, '')
  return typeof state.project?.id === 'string' && !!state.project.id && webHistoryInScope(app, state.project.id)
}
const FOREIGN_BACKUP =
  'Cette sauvegarde a été prise dans un autre projet. Son téléchargement et sa restauration sont réservés aux administrateurs de la plateforme.'
const assertRestoreScope = (access: WebAccess, app: core.Record, payload: any) => {
  if (access.platform) return
  if (!backupInScope(app, payload)) throw new ForbiddenError(FOREIGN_BACKUP)
  const current = `${webConfig(app).repository || ''}`
  if (
    [payload.app?.config, payload.published?.config].some(
      (config) => !canSetWebRepository(access.project, access.user, `${config?.repository || ''}`, current)
    )
  )
    throw new ForbiddenError(
      'Cette sauvegarde utilise un autre dépôt Git. Sa restauration est réservée aux administrateurs de la plateforme.'
    )
  const connection = resolvedConnectionId(webConfig(app))
  if (
    [payload.app?.config, payload.published?.config].some(
      (config) => !!config && resolvedConnectionId(config) !== connection
    )
  )
    throw new ForbiddenError(
      'Cette sauvegarde utilise une autre connexion GitHub. Sa restauration est réservée aux administrateurs de la plateforme.'
    )
  if (!payload.projectState)
    throw new ForbiddenError(
      'Cette sauvegarde a été prise hors projet. Sa restauration est réservée aux administrateurs de la plateforme.'
    )
}
const backupOf = (e: core.RequestEvent, appId: string, store: any = $app) => {
  const row = store.findRecordById(BACKUPS, id(e.request!.pathValue('backupId')))
  if (row.getString('appId') !== appId) throw new NotFoundError('Sauvegarde introuvable.')
  return row
}
const settingOf = (appId: string, store: any = $app): core.Record | null => {
  try {
    return store.findFirstRecordByFilter(SETTINGS, 'appId={:id}', { id: appId })
  } catch {
    return null
  }
}
const settingView = (row: core.Record | null) => ({
  enabled: row?.getBool('enabled') || false,
  hourUTC: row ? row.getInt('hourUTC') : 3,
  minuteUTC: row ? row.getInt('minuteUTC') : 0,
  retention: row ? row.getInt('retention') : 3,
  nextRunAt: row?.getString('nextRunAt') || '',
  lastRunAt: row?.getString('lastRunAt') || '',
  lastError: row?.getString('lastError') || '',
})
const nextRun = (hour: number, minute: number, date = new Date()) => {
  const candidate = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate(), hour, minute))
  if (candidate.getTime() <= date.getTime()) candidate.setUTCDate(candidate.getUTCDate() + 1)
  return candidate.toISOString()
}
const locallyReady = (row: core.Record) =>
  ['ready', 'running'].includes(row.getString('status')) &&
  row.getFloat('archiveBytes') > 0 &&
  /^[a-f0-9]{64}$/.test(row.getString('checksum'))
const metadata = (row: core.Record, localAvailable = locallyReady(row)) => ({
  id: row.id,
  status: row.getString('status'),
  trigger: row.getString('trigger'),
  commit: row.getString('commit'),
  sourceDeploymentId: row.getString('sourceDeploymentId'),
  imageId: row.getString('imageId'),
  imageBytes: row.getFloat('imageBytes'),
  totalImageBytes: row.getFloat('totalImageBytes') || row.getFloat('imageBytes'),
  imageCount: Math.max(1, new Set(Object.values(imageMap(valueOf(row, 'runtimeImages')))).size),
  archiveBytes: row.getFloat('archiveBytes'),
  checksum: row.getString('checksum'),
  error: row.getString('error'),
  created: row.getString('created'),
  finishedAt: row.getString('finishedAt'),
  s3State: row.getString('s3State') || 'pending',
  s3Error: row.getString('s3Error'),
  s3CheckedAt: row.getString('s3CheckedAt'),
  s3Bytes: row.getFloat('s3Bytes'),
  s3TotalBytes: row.getFloat('s3TotalBytes'),
  localAvailable,
  downloadable: locallyReady(row) && localAvailable,
  restorable: locallyReady(row) && localAvailable,
  recoverable:
    ['ready', 'failed'].includes(row.getString('status')) && row.getString('s3State') === 'ready' && !localAvailable,
})
const queueBackup = (app: core.Record, actorId: string, trigger: string, store: any) => {
  assertWebEnvironmentBackupAllowed(app, store)
  if (
    store.findRecordsByFilter(BACKUPS, 'appId={:id} && (status="queued" || status="running")', '', 1, 0, { id: app.id })
      .length
  )
    throw new BadRequestError('Une sauvegarde de cette application est déjà en attente ou en cours.')
  let published: core.Record
  try {
    published = store.findRecordById('web_deployments', app.getString('activeDeployment'))
  } catch {
    throw new BadRequestError('Publiez une première version avant de créer une sauvegarde restaurable.')
  }
  const result = valueOf(published, 'result'),
    snapshot = decryptWebValue(published.getString('snapshotEncrypted')),
    images = imageMap(result.images),
    front =
      result.services?.find((service: any) => service.front === true)?.name ||
      snapshot?.runtime?.compose?.front ||
      'web',
    frontImage = images[front] || result.image
  if (
    published.getString('appId') !== app.id ||
    published.getString('status') !== 'ready' ||
    // Availability reports can lag restored/stopped containers. The backup
    // worker must still inspect every immutable image and its owner before it
    // can produce a ready archive; active publications retain the early guard.
    (result.available === false && app.getString('status') !== 'stopped') ||
    !imageRef(frontImage, app.id) ||
    !snapshot?.config ||
    !snapshot?.env
  )
    throw new BadRequestError(
      'L’image de la version publiée n’est plus disponible. Publiez une nouvelle version avant de sauvegarder.'
    )
  if (
    snapshot.runtime?.kind === 'compose' &&
    JSON.stringify(Object.keys(snapshot.runtime.compose?.services || {}).sort()) !==
      JSON.stringify(Object.keys(images).sort())
  )
    throw new BadRequestError(
      'L’inventaire des images Compose est incomplet. Publiez une version complète avant de sauvegarder.'
    )
  // L'archive porterait le projet actuel : une version produite dans un autre projet n'y est jamais réétiquetée.
  assertWebDeploymentInScope(app, published, store)
  const payload = {
    version: 1,
    appId: app.id,
    projectState: exportWebEnvironmentBackup(app, store),
    app: {
      name: app.getString('name'),
      config: webConfig(app),
      env: decryptWebValue(app.getString('envEncrypted')),
      desiredCommit: app.getString('desiredCommit'),
      runtime: runtimeWorkerSnapshot(app, store),
      storage: storageWorkerSnapshot(app, store),
    },
    published: {
      config: snapshot.config,
      env: snapshot.env,
      commit: published.getString('commit'),
      imageRef: frontImage,
      images,
      runtime: snapshot.runtime || validateRuntimeProfile(),
      storage: snapshot.storage || [],
    },
  }
  const encrypted = encryptWebValue(payload)
  if (encrypted.length > 400000) throw new BadRequestError('Les réglages dépassent la taille maximale d’une archive.')
  const row = new Record(store.findCollectionByNameOrId(BACKUPS))
  for (const [key, value] of Object.entries({
    appId: app.id,
    actorId,
    status: 'queued',
    trigger,
    operation: 'create',
    payloadEncrypted: encrypted,
    sourceDeploymentId: published.id,
    commit: published.getString('commit'),
    imageRef: frontImage,
    runtimeImages: images,
    s3State: 'pending',
  }))
    row.set(key, value)
  store.save(row)
  return row
}
const activeRestores = (appId: string, store: any) => {
  const rows = store.findRecordsByFilter(
    'web_deployments',
    'appId={:id} && action="restore-backup" && (status="queued" || status="running")',
    '',
    1001,
    0,
    { id: appId }
  )
  if (rows.length > 1000) throw new BadRequestError('Trop de restaurations en attente.')
  return new Set(
    rows
      .map((row: core.Record) => decryptWebValue(row.getString('snapshotEncrypted'))?.restoreBackup?.id)
      .filter(Boolean)
  )
}
export const HandleWebBackupsList = (e: core.RequestEvent) => {
  const { app } = selected(e, 'app.read'),
    page = Math.max(1, Math.min(100000, Math.floor(Number(e.request!.url!.query().get('page')) || 1))),
    perPage = 20
  const items = $app.findRecordsByFilter(BACKUPS, 'appId={:id}', '-created,-id', perPage, (page - 1) * perPage, {
    id: app.id,
  })
  const totalItems = $app.countRecords(BACKUPS, $dbx.exp('appId={:id}', { id: app.id }))
  let available: Record<string, boolean> = {}
  try {
    available = JSON.parse(
      toString(
        $os
          .cmd(
            'python3',
            '-c',
            ARCHIVE_LIST,
            webRoot(),
            app.id,
            JSON.stringify(
              items.filter(locallyReady).map((row) => ({ id: row.id, checksum: row.getString('checksum') }))
            )
          )
          .output()
      )
    )
  } catch {}
  return webJson(e, {
    items: items.map((row) => metadata(row, available[row.id] === true)),
    page,
    perPage,
    totalItems,
    totalPages: Math.max(1, Math.ceil(totalItems / perPage)),
    settings: settingView(settingOf(app.id)),
    limits: { minRetention: 1, maxRetention: 14, minFreeBytes: 15 * 1024 ** 3, pinnedImageBudgetBytes: 6 * 1024 ** 3 },
    scope: 'local-config-and-image',
    // Capacité lue par le service de reprise : les archives trigger=platform sont hors rétention du client.
    platformRetention: 'excluded',
  })
}
export const HandleWebBackupCreate = (e: core.RequestEvent) => {
  const { app, user } = selected(e, 'backups.create'),
    input = webInput(e)
  if (
    !input ||
    typeof input !== 'object' ||
    Array.isArray(input) ||
    Object.keys(input).some((key) => key !== 'trigger')
  )
    throw new BadRequestError('Options de sauvegarde invalides.')
  if (input.trigger !== undefined && input.trigger !== 'platform')
    throw new BadRequestError('Déclencheur de sauvegarde invalide.')
  // Archive de la reprise de la plateforme : hors quota de rétention du client, réservée aux superAdmins.
  if (input.trigger === 'platform' && user.getBool('superAdmin') !== true)
    throw new ForbiddenError('Réservé à la plateforme.')
  let backup: any
  $app.runInTransaction((tx) => {
    backup = metadata(
      queueBackup(
        tx.findRecordById('web_apps', app.id),
        user.id,
        input.trigger === 'platform' ? 'platform' : 'manual',
        tx
      )
    )
  })
  return webJson(e, { backup }, 202)
}
export const HandleWebBackupSettings = (e: core.RequestEvent) => {
  const { app } = selected(e, 'backups.manage'),
    input = webInput(e)
  if (input.enabled) assertWebEnvironmentBackupAllowed(app)
  if (
    typeof input.enabled !== 'boolean' ||
    !Number.isInteger(input.hourUTC) ||
    input.hourUTC < 0 ||
    input.hourUTC > 23 ||
    !Number.isInteger(input.minuteUTC) ||
    input.minuteUTC < 0 ||
    input.minuteUTC > 59 ||
    !Number.isInteger(input.retention) ||
    input.retention < 1 ||
    input.retention > 14
  )
    throw new BadRequestError('Horaire UTC invalide ou rétention hors de la plage 1 à 14 sauvegardes.')
  let settings: any
  $app.runInTransaction((tx) => {
    const row = settingOf(app.id, tx) || new Record(tx.findCollectionByNameOrId(SETTINGS))
    row.set('appId', app.id)
    for (const name of ['enabled', 'hourUTC', 'minuteUTC', 'retention']) row.set(name, input[name])
    row.set('nextRunAt', input.enabled ? nextRun(input.hourUTC, input.minuteUTC) : '')
    row.set('lastError', '')
    tx.save(row)
    settings = settingView(row)
  })
  return webJson(e, { settings })
}
export const HandleWebBackupDelete = (e: core.RequestEvent) => {
  const { app } = selected(e, 'backups.manage')
  let backup: any
  $app.runInTransaction((tx) => {
    const row = backupOf(e, app.id, tx)
    if (!['ready', 'failed'].includes(row.getString('status')))
      throw new BadRequestError('Cette sauvegarde ne peut pas être supprimée maintenant.')
    if (activeRestores(app.id, tx).has(row.id))
      throw new BadRequestError('Une restauration utilise cette sauvegarde. Attendez sa fin.')
    row.set('status', 'deleting')
    row.set('operation', 'delete')
    row.set('leaseHash', '')
    row.set('leaseAt', '')
    row.set('leaseAttempts', 0)
    row.set('error', '')
    tx.save(row)
    backup = metadata(row)
  })
  return webJson(e, { backup }, 202)
}
export const HandleWebBackupRecover = (e: core.RequestEvent) => {
  const { app } = selected(e, 'backups.manage')
  let backup: any
  $app.runInTransaction((tx) => {
    const row = backupOf(e, app.id, tx)
    if (
      !['ready', 'failed'].includes(row.getString('status')) ||
      row.getString('s3State') !== 'ready' ||
      !row.getString('s3Object') ||
      !row.getString('checksum')
    )
      throw new BadRequestError('Aucune copie S3 complète n’est disponible pour cette sauvegarde.')
    row.set('operation', 'recover')
    row.set('status', 'queued')
    row.set('leaseHash', '')
    row.set('leaseAt', '')
    row.set('leaseAttempts', 0)
    row.set('error', '')
    tx.save(row)
    backup = metadata(row, false)
  })
  return webJson(e, { backup }, 202)
}
const readArchive = (app: core.Record, row: core.Record) => {
  if (!locallyReady(row)) throw new BadRequestError('Archive indisponible.')
  try {
    const raw = toString($os.cmd('python3', '-c', ARCHIVE_READ, webRoot(), id(app.id), id(row.id)).output())
    if (!$security.equal($security.sha256(raw), row.getString('checksum'))) throw new Error()
    const archive = JSON.parse(raw)
    if (
      archive.format !== 'pockethost-web-backup' ||
      archive.version !== 1 ||
      archive.appId !== app.id ||
      archive.id !== row.id ||
      archive.imageId !== row.getString('imageId') ||
      !$security.equal(archive.payloadEncrypted, row.getString('payloadEncrypted')) ||
      $security.sha256(archive.payloadEncrypted) !== archive.payloadSha256
    )
      throw new Error()
    const payload = decryptWebValue(archive.payloadEncrypted)
    if (
      payload?.version !== 1 ||
      payload.appId !== app.id ||
      !payload.app?.config ||
      !payload.app?.env ||
      !payload.published?.config ||
      !payload.published?.env ||
      !/^sha256:[a-f0-9]{64}$/.test(archive.imageId) ||
      !equalMap(imageMap(archive.images), imageMap(valueOf(row, 'runtimeImages'))) ||
      (payload.published.images && !equalMap(imageMap(payload.published.images), imageMap(archive.images))) ||
      (Object.keys(imageMap(archive.images)).length > 0 &&
        !Object.values(imageMap(archive.images)).includes(archive.imageId))
    )
      throw new Error()
    return { raw, archive, payload }
  } catch {
    throw new BadRequestError(
      'Archive absente ou contrôle d’intégrité incorrect. La configuration actuelle est conservée.'
    )
  }
}
export const HandleWebBackupDownload = (e: core.RequestEvent) => {
  const { app, access } = selected(e, 'backups.manage'),
    row = backupOf(e, app.id),
    { raw, payload } = readArchive(app, row)
  if (!access.platform && !backupInScope(app, payload)) throw new ForbiddenError(FOREIGN_BACKUP)
  e.response.header().set('Cache-Control', 'no-store')
  e.response.header().set('Content-Disposition', `attachment; filename="web-${app.id}-${row.id}.phwb"`)
  e.response.header().set('X-Archive-SHA256', row.getString('checksum'))
  return e.blob(200, 'application/octet-stream', raw)
}
const token = (userId: string, appId: string, row: core.Record, revision: string, expiresAt: string) =>
  $security.hs256(
    JSON.stringify([userId, appId, row.id, row.getString('checksum'), revision, expiresAt]),
    $os.getenv('WEB_APPS_MASTER_KEY')
  )
const environmentChanges = (current: any, before: any) => {
  const changes: { added: string[]; updated: string[]; removed: string[] } = { added: [], updated: [], removed: [] }
  for (const name of [...new Set([...Object.keys(current), ...Object.keys(before)])].sort()) {
    const a = current[name],
      b = before[name]
    if (!a) changes.added.push(name)
    else if (!b) changes.removed.push(name)
    else if (a.value !== b.value || a.build !== b.build || a.runtime !== b.runtime) changes.updated.push(name)
  }
  return changes
}
export const HandleWebBackupRestorePreview = (e: core.RequestEvent) => {
  const { app, user, access } = selected(e, 'backups.manage'),
    row = backupOf(e, app.id),
    { payload } = readArchive(app, row)
  assertRestoreScope(access, app, payload)
  const revision = currentBackupAppRevision(app),
    expiresAt = new Date(Date.now() + 10 * 60000).toISOString()
  return webJson(e, {
    revision,
    expiresAt,
    previewToken: token(user.id, app.id, row, revision, expiresAt),
    backup: metadata(row),
    appName: payload.app.name,
    configurationFields: Object.keys(payload.app.config).filter(
      (name) => JSON.stringify(payload.app.config[name]) !== JSON.stringify(webConfig(app)[name])
    ),
    environmentChanges: environmentChanges(decryptWebValue(app.getString('envEncrypted')), payload.app.env),
    storedConfigurationIncludesPendingChanges:
      JSON.stringify(payload.app.config) !== JSON.stringify(payload.published.config) ||
      JSON.stringify(payload.app.env) !== JSON.stringify(payload.published.env),
    warning:
      'Cette restauration redémarre la version publiée sauvegardée et remplace les réglages et variables enregistrés. Les données externes, bases de données, fichiers et caches ne sont pas restaurés.',
  })
}
export const HandleWebBackupRestore = (e: core.RequestEvent) => {
  const { app, user, access } = selected(e, 'backups.manage'),
    input = webInput(e),
    row = backupOf(e, app.id),
    { payload, archive } = readArchive(app, row)
  assertRestoreScope(access, app, payload)
  assertBackupAppRevision(app, input.revision)
  if (
    typeof input.expiresAt !== 'string' ||
    Date.parse(input.expiresAt) <= Date.now() ||
    Date.parse(input.expiresAt) > Date.now() + 10 * 60000 ||
    typeof input.previewToken !== 'string' ||
    !$security.equal(input.previewToken, token(user.id, app.id, row, input.revision, input.expiresAt))
  )
    throw new BadRequestError('Préparez un nouvel aperçu avant de confirmer la restauration.')
  let deployment: any
  $app.runInTransaction((tx) => {
    const current = tx.findRecordById('web_apps', app.id),
      saved = backupOf(e, app.id, tx)
    assertBackupAppRevision(current, input.revision)
    if (!locallyReady(saved) || saved.getString('checksum') !== row.getString('checksum'))
      throw new BadRequestError('La sauvegarde n’est plus disponible.')
    if (
      tx.findRecordsByFilter('web_deployments', 'appId={:id} && (status="queued" || status="running")', '', 1, 0, {
        id: app.id,
      }).length
    )
      throw new BadRequestError('Attendez la fin du traitement en cours avant de restaurer cette application.')
    const job = queueWebDeployment(
      current,
      'restore-backup',
      user.id,
      {
        config: payload.published.config,
        env: payload.published.env,
        commit: payload.published.commit,
        runtime: payload.published.runtime || validateRuntimeProfile(),
        storage: payload.published.storage || [],
        rollbackResult: { image: archive.imageId, images: imageMap(archive.images), available: true },
        restoreBackup: {
          id: row.id,
          expectedAppRevision: input.revision,
          app: payload.app,
          projectState: payload.projectState || null,
          runtime: payload.app.runtime || validateRuntimeProfile(),
          storage: payload.app.storage || [],
          checksum: row.getString('checksum'),
          s3Object: row.getString('s3Object'),
          s3ImageObject: row.getString('s3ImageObject'),
          s3ImageObjects: imageObjects(valueOf(row, 's3ImageObjects')),
        },
      },
      tx
    )
    deployment = serializeWebDeployment(job)
  })
  return webJson(e, { deployment }, 202)
}
const worker = (e: core.RequestEvent) => {
  if (!webEnabled()) throw new NotFoundError()
  const expected = $os.getenv('WEB_APPS_WORKER_SECRET'),
    actual = e.request!.header.get('X-Web-Worker') || ''
  if (expected.length < 32 || !$security.equal(expected, actual)) throw new UnauthorizedError('Worker non autorisé.')
  if (!webDirectLoopback(e)) throw new ForbiddenError('Accès local requis.')
}
export const HandleWebBackupPins = (e: core.RequestEvent) => {
  worker(e)
  const input = webInput(e),
    filter =
      '(status="queued" || status="running" || status="ready" || status="deleting" || (status="failed" && archiveBytes>0))' +
      (input.appId ? ' && appId={:appId}' : '')
  if (input.appId) id(input.appId)
  const rows = $app.findRecordsByFilter(BACKUPS, filter, '', 10001, 0, { appId: input.appId || '' })
  if (rows.length > 10000) throw new BadRequestError('Trop de sauvegardes pour confirmer le nettoyage des images.')
  return webJson(e, {
    items: rows.map((row) => ({
      id: row.id,
      appId: row.getString('appId'),
      imageRef: row.getString('imageRef'),
      imageId: row.getString('imageId'),
      imageBytes: row.getFloat('imageBytes'),
      images: imageMap(valueOf(row, 'runtimeImages')),
      imageSizes: imageSizes(valueOf(row, 'runtimeImageSizes')),
      totalImageBytes: row.getFloat('totalImageBytes') || row.getFloat('imageBytes'),
      s3Object: row.getString('s3Object'),
      s3ImageObject: row.getString('s3ImageObject'),
      s3ImageObjects: imageObjects(valueOf(row, 's3ImageObjects')),
    })),
  })
}
const maintenance = (store: any) => {
  const timestamp = now(),
    due = store.findRecordsByFilter(
      SETTINGS,
      'enabled=true && nextRunAt!="" && nextRunAt<={:now}',
      'nextRunAt',
      50,
      0,
      { now: timestamp.replace('T', ' ') }
    )
  for (const setting of due) {
    try {
      queueBackup(store.findRecordById('web_apps', setting.getString('appId')), '', 'scheduled', store)
      setting.set('lastError', '')
    } catch (error) {
      // Les erreurs d'API PocketBase (BadRequestError) ne sont pas des instances d'Error dans Goja.
      const raw = (error as any)?.message,
        message =
          typeof raw === 'string' && raw.trim() ? raw.slice(0, 500) : 'La sauvegarde quotidienne n’a pas pu démarrer.'
      setting.set('lastError', message)
      // Planification en échec (aucune ligne de sauvegarde créée) : alerte poussée, une par occurrence.
      emitWebAlert(
        store,
        'app',
        setting.getString('appId'),
        'backup_failed',
        'backupsched:' + setting.getString('appId') + ':' + setting.getString('nextRunAt'),
        { trigger: 'scheduled', reason: 'queue_failed', summary: webAlertSummary(message, null) }
      )
    }
    setting.set('lastRunAt', timestamp)
    setting.set('nextRunAt', nextRun(setting.getInt('hourUTC'), setting.getInt('minuteUTC')))
    store.save(setting)
  }
  // Suppression abandonnée (boucle de crash) : nouvel essai, avec un budget de baux neuf, au plus une fois par jour.
  for (const row of store.findRecordsByFilter(
    BACKUPS,
    'status="failed" && operation="delete" && finishedAt!="" && finishedAt<{:before}',
    'finishedAt',
    50,
    0,
    { before: new Date(Date.now() - DELETE_RETRY_AFTER).toISOString().replace('T', ' ') }
  )) {
    row.set('status', 'deleting')
    row.set('leaseHash', '')
    row.set('leaseAt', '')
    row.set('leaseAttempts', 0)
    store.save(row)
  }
  const settings = store.findRecordsByFilter(SETTINGS, '', '', 1000, 0)
  const appIds = new Set(settings.map((row: core.Record) => row.getString('appId')))
  // Applications without a schedule still use the default retention for manual archives.
  for (const row of store.findRecordsByFilter(BACKUPS, 'status="ready"', '-created', 10000, 0))
    appIds.add(row.getString('appId'))
  for (const appId of appIds) {
    const retain = settingOf(appId as string, store)?.getInt('retention') || 3,
      active = activeRestores(appId as string, store)
    // Les archives de la reprise de la plateforme ne comptent pas dans la rétention du client.
    const ready = store.findRecordsByFilter(
      BACKUPS,
      'appId={:id} && status="ready" && trigger!="platform"',
      '-created,-id',
      10000,
      0,
      { id: appId }
    )
    // La capture recopie ses objets : seule la plus récente archive « platform » de l'application est gardée.
    const platform = store.findRecordsByFilter(
      BACKUPS,
      'appId={:id} && status="ready" && trigger="platform"',
      '-created,-id',
      1000,
      0,
      { id: appId }
    )
    for (const row of [...ready.slice(retain), ...platform.slice(1)])
      if (!active.has(row.id)) {
        row.set('status', 'deleting')
        row.set('operation', 'delete')
        row.set('leaseHash', '')
        row.set('leaseAt', '')
        row.set('leaseAttempts', 0)
        store.save(row)
      }
  }
}
export const HandleWebBackupClaim = (e: core.RequestEvent) => {
  worker(e)
  let job: any = null
  $app.runInTransaction((tx) => {
    maintenance(tx)
    const stale = new Date(Date.now() - 5 * 60000).toISOString().replace('T', ' '),
      retry = new Date(Date.now() - 60000).toISOString().replace('T', ' ')
    // Bail expiré sans compte rendu : le worker a planté ou redémarré. Au troisième bail perdu, la sauvegarde est en
    // échec (alerte backup_failed par le scan) au lieu de boucler indéfiniment.
    for (const row of tx.findRecordsByFilter(BACKUPS, 'status="running" && leaseAt<{:stale}', '', 100, 0, { stale })) {
      row.set('leaseHash', '')
      if (row.getInt('leaseAttempts') >= MAX_LEASES) {
        row.set('status', 'failed')
        row.set('finishedAt', now())
        row.set(
          'error',
          'La sauvegarde a été interrompue trois fois sans compte rendu du worker (redémarrage ou ressources insuffisantes).'
        )
      } else row.set('status', 'queued')
      tx.save(row)
    }
    const deleting = tx.findRecordsByFilter(
      BACKUPS,
      'status="deleting" && (leaseAt="" || leaseAt<{:retry})',
      'created,id',
      1,
      0,
      { retry }
    )
    const rows = deleting.length ? deleting : tx.findRecordsByFilter(BACKUPS, 'status="queued"', 'created,id', 1, 0)
    if (!rows.length) return
    const row = rows[0]!,
      operation =
        row.getString('status') === 'deleting'
          ? 'delete'
          : row.getString('operation') === 'recover'
            ? 'recover'
            : 'create',
      lease = $security.randomString(40)
    row.set('status', operation === 'delete' ? 'deleting' : 'running')
    row.set('leaseAt', now())
    row.set('leaseHash', $security.sha256(lease))
    row.set('leaseAttempts', Math.min(100, row.getInt('leaseAttempts') + 1))
    row.set('startedAt', row.getString('startedAt') || now())
    tx.save(row)
    job = {
      id: row.id,
      appId: row.getString('appId'),
      operation,
      lease,
      payloadEncrypted: operation !== 'delete' ? row.getString('payloadEncrypted') : undefined,
      imageRef: row.getString('imageRef'),
      imageId: row.getString('imageId'),
      imageBytes: row.getFloat('imageBytes'),
      images: imageMap(valueOf(row, 'runtimeImages')),
      imageSizes: imageSizes(valueOf(row, 'runtimeImageSizes')),
      totalImageBytes: row.getFloat('totalImageBytes') || row.getFloat('imageBytes'),
      archiveBytes: row.getFloat('archiveBytes'),
      checksum: row.getString('checksum'),
      s3Object: row.getString('s3Object'),
      s3ImageObject: row.getString('s3ImageObject'),
      s3ImageObjects: imageObjects(valueOf(row, 's3ImageObjects')),
      commit: row.getString('commit'),
      createdAt: row.getString('created'),
      attempt: row.getInt('leaseAttempts'),
      maxAttempts: MAX_LEASES,
    }
  })
  return webJson(e, { job })
}
export const HandleWebBackupReport = (e: core.RequestEvent) => {
  worker(e)
  const input = webInput(e)
  $app.runInTransaction((tx) => {
    const row = tx.findRecordById(BACKUPS, id(input.id))
    if (
      typeof input.lease !== 'string' ||
      !$security.equal($security.sha256(input.lease), row.getString('leaseHash')) ||
      !['running', 'deleting'].includes(row.getString('status'))
    )
      throw new BadRequestError('Traitement de sauvegarde non actif.')
    const deleting = row.getString('status') === 'deleting'
    if (input.images !== undefined) {
      const images = imageMap(input.images),
        expected = imageMap(valueOf(row, 'runtimeImages'))
      if (!equalMap(images, expected))
        throw new BadRequestError('Les images retournées ne correspondent pas à cette sauvegarde.')
      row.set('runtimeImages', images)
    }
    if (input.imageSizes !== undefined) {
      const sizes = imageSizes(input.imageSizes),
        images = imageMap(valueOf(row, 'runtimeImages'))
      if (Object.keys(sizes).some((ref) => !Object.values(images).includes(ref)))
        throw new BadRequestError('Une taille d’image est étrangère à cette sauvegarde.')
      row.set('runtimeImageSizes', sizes)
    }
    if (input.totalImageBytes !== undefined) {
      const sizes = imageSizes(valueOf(row, 'runtimeImageSizes'))
      if (
        !Number.isSafeInteger(input.totalImageBytes) ||
        input.totalImageBytes < 0 ||
        Object.values(sizes).reduce((n, size) => n + size, 0) !== input.totalImageBytes
      )
        throw new BadRequestError('Taille totale des images incohérente.')
      row.set('totalImageBytes', input.totalImageBytes)
    }
    if (input.s3ImageObjects !== undefined) {
      const objects = imageObjects(input.s3ImageObjects),
        images = imageMap(valueOf(row, 'runtimeImages'))
      if (Object.keys(objects).some((ref) => !Object.values(images).includes(ref)))
        throw new BadRequestError('Une référence S3 est étrangère à cette sauvegarde.')
      row.set('s3ImageObjects', objects)
    }
    if (input.s3State !== undefined) {
      if (!['pending', 'uploading', 'ready', 'failed', 'disabled', 'deleted'].includes(input.s3State))
        throw new BadRequestError('État S3 invalide.')
      row.set('s3State', input.s3State)
      for (const name of ['s3Object', 's3ImageObject'])
        if (input[name] !== undefined) {
          if (typeof input[name] !== 'string' || input[name].length > 500 || /[\r\n?]/.test(input[name]))
            throw new BadRequestError('Référence S3 invalide.')
          row.set(name, input[name])
        }
      if (input.s3CheckedAt) {
        if (!Number.isFinite(Date.parse(input.s3CheckedAt))) throw new BadRequestError('Date S3 invalide.')
        row.set('s3CheckedAt', input.s3CheckedAt)
      }
      row.set('s3Error', `${input.s3Error || ''}`.slice(0, 500))
      for (const name of ['s3Bytes', 's3TotalBytes'])
        if (input[name] !== undefined) {
          if (!Number.isSafeInteger(input[name]) || input[name] < 0)
            throw new BadRequestError('Progression S3 invalide.')
          row.set(name, input[name])
        }
    }
    const expectedImages = imageMap(valueOf(row, 'runtimeImages')),
      expectedRefs = [...new Set(Object.values(expectedImages))].sort()
    if (input.imageId !== undefined && expectedRefs.length) {
      const sizes = imageSizes(valueOf(row, 'runtimeImageSizes'))
      if (
        !expectedRefs.includes(input.imageId) ||
        JSON.stringify(Object.keys(sizes).sort()) !== JSON.stringify(expectedRefs) ||
        sizes[input.imageId] !== input.imageBytes ||
        Object.values(sizes).reduce((sum, size) => sum + size, 0) !== row.getFloat('totalImageBytes')
      )
        throw new BadRequestError('Inventaire des images locales incomplet ou incohérent.')
    }
    if (input.s3State === 'ready' && expectedRefs.length) {
      const objects = imageObjects(valueOf(row, 's3ImageObjects'))
      if (JSON.stringify(Object.keys(objects).sort()) !== JSON.stringify(expectedRefs))
        throw new BadRequestError('La copie S3 ne contient pas toutes les images du site.')
    }
    if (input.status === 'running' && !deleting) {
      if (input.imageId !== undefined) {
        if (
          !/^sha256:[a-f0-9]{64}$/.test(input.imageId) ||
          !/^[a-f0-9]{64}$/.test(input.checksum || '') ||
          !Number.isInteger(input.archiveBytes) ||
          input.archiveBytes <= 0 ||
          input.archiveBytes > 512 * 1024 ||
          !Number.isInteger(input.imageBytes) ||
          input.imageBytes < 0
        )
          throw new BadRequestError('Métadonnées locales invalides.')
        for (const name of ['imageId', 'imageBytes', 'archiveBytes', 'checksum']) row.set(name, input[name])
      }
      row.set('leaseAt', now())
      tx.save(row)
      return
    }
    if (input.status === 'ready' && !deleting) {
      if (
        !/^sha256:[a-f0-9]{64}$/.test(input.imageId || '') ||
        !/^[a-f0-9]{64}$/.test(input.checksum || '') ||
        !Number.isInteger(input.archiveBytes) ||
        input.archiveBytes <= 0 ||
        input.archiveBytes > 512 * 1024 ||
        !Number.isInteger(input.imageBytes) ||
        input.imageBytes < 0
      )
        throw new BadRequestError('Métadonnées de sauvegarde invalides.')
      for (const name of ['imageId', 'imageBytes', 'archiveBytes', 'checksum']) row.set(name, input[name])
      row.set('status', 'ready')
      row.set('error', '')
      row.set('finishedAt', now())
    } else if (input.status === 'deleted' && deleting) {
      row.set('status', 'deleted')
      row.set('payloadEncrypted', '')
      row.set('archiveBytes', 0)
      row.set('error', '')
      row.set('finishedAt', now())
    } else if (input.status === 'failed') {
      if (input.failureCode !== undefined && !FAILURE_CODES.includes(input.failureCode))
        throw new BadRequestError('Code d’échec de sauvegarde invalide.')
      if (
        input.attempts !== undefined &&
        (!Number.isInteger(input.attempts) || input.attempts < 0 || input.attempts > 1000)
      )
        throw new BadRequestError('Nombre de tentatives invalide.')
      const exhausted = input.failureCode === 'attempts_exhausted',
        message = `${input.error || 'Échec de la sauvegarde locale.'}`
      if (deleting && !exhausted) {
        // Échec signalé (S3 ou bucket indisponible…) : ce n'est pas un bail perdu. La suppression reste en file et
        // garde tout son budget ; seule une boucle de crash sans compte rendu l'épuise.
        row.set('status', 'deleting')
        row.set('leaseAttempts', 0)
        row.set('error', message.slice(0, 500))
      } else if (deleting) {
        // Suppression abandonnée après une boucle de crash : jamais une alerte « Échec de sauvegarde » chez le client
        // (operation="delete" est exclu du scan). Canal plateforme, puis nouvel essai automatique le lendemain.
        row.set('status', 'failed')
        row.set(
          'error',
          'La suppression de cette sauvegarde a été interrompue plusieurs fois sans compte rendu du worker. Elle sera retentée automatiquement.'
        )
        emitWebAlert(
          tx,
          'platform',
          row.getString('appId'),
          'backup_cleanup_failed',
          'backup-delete:' + row.id + ':' + now().slice(0, 10),
          {
            appId: row.getString('appId'),
            backupId: row.id,
            attempts: Number.isInteger(input.attempts) ? input.attempts : MAX_LEASES,
          }
        )
      } else {
        row.set('status', 'failed')
        row.set(
          'error',
          (exhausted && !/tentatives/.test(message)
            ? `Sauvegarde abandonnée après ${Number.isInteger(input.attempts) ? input.attempts : MAX_LEASES} tentatives interrompues. `
            : '') + message
        )
        row.set('error', row.getString('error').slice(0, 500))
      }
      row.set('finishedAt', now())
    } else throw new BadRequestError('État de sauvegarde invalide.')
    if (!deleting || input.status === 'deleted' || row.getString('status') === 'failed') row.set('leaseHash', '')
    tx.save(row)
  })
  return webJson(e, { ok: true })
}
