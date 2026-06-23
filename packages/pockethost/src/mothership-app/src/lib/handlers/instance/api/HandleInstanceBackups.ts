import { mkLog } from '$util/Logger'

const BACKUP_FORMAT = 'gestion-pocketbase-instance-backup-v1'
const BACKUP_DIRS = ['pb_data', 'pb_public', 'pb_migrations', 'pb_hooks']
const MAX_STOP_WAIT_SECONDS = 120
const DIR_MODE = 0o755 as any
const PRIVATE_DIR_MODE = 0o700 as any
const PRIVATE_FILE_MODE = 0o600 as any

type BackupKind = 'manual' | 'pre-restore'
type ManagedPower = {
  shouldRestart: boolean
}

const dataRoot = () => {
  const envRoot = $os.getenv('DATA_ROOT')
  if (envRoot) return envRoot

  const appDataDir = `${$app.dataDir()}`
  const inferred = appDataDir.replace(/\/mothership\/pb_data\/?$/, '')
  if (inferred !== appDataDir) return inferred

  throw new Error("Impossible de trouver le dossier de donnees des instances.")
}

const backupRoot = () => $os.getenv('INSTANCE_BACKUP_ROOT') || `${dataRoot()}/backups/instances`

const assertSafeInstanceId = (id: string) => {
  if (!id.match(/^[a-z0-9]+$/)) {
    throw new BadRequestError("Identifiant d'instance invalide.")
  }
}

const assertSafeBackupId = (id: string) => {
  if (!id.match(/^[a-z0-9]+$/)) {
    throw new BadRequestError('Identifiant de sauvegarde invalide.')
  }
}

const assertSafeBackupFilename = (filename: string) => {
  if (!filename.match(/^[a-zA-Z0-9._-]+\.tar\.gz$/)) {
    throw new BadRequestError('Nom de sauvegarde invalide.')
  }
}

const instanceRoot = (id: string) => `${dataRoot()}/instances/${id}`
const backupDir = (instanceId: string) => `${backupRoot()}/${instanceId}`
const backupPath = (instanceId: string, filename: string) => `${backupDir(instanceId)}/${filename}`

const pathExists = (path: string) => {
  try {
    $os.stat(path)
    return true
  } catch {
    return false
  }
}

const fileSize = (path: string) => {
  try {
    return Number($os.stat(path).size())
  } catch {
    return 0
  }
}

const runCommand = (name: string, ...args: string[]) => toString($os.cmd(name, ...args).combinedOutput()).trim()

const sleepOneSecond = () => {
  $os.cmd('sleep', '1').combinedOutput()
}

const errorMessage = (error: unknown) => {
  if (error instanceof Error) return error.message
  return `${error}`
}

const slugForFilename = (value: string) => {
  const clean = value
    .toLowerCase()
    .replace(/[^a-z0-9-]/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-+|-+$/g, '')
  return clean.slice(0, 48).replace(/-+$/g, '') || 'instance'
}

const timestampForFilename = () => new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z')

const createBackupFilename = (instance: core.Record, kind: BackupKind) => {
  const suffix = kind === 'pre-restore' ? 'pre-restore' : 'manual'
  return `${timestampForFilename()}-${slugForFilename(instance.getString('subdomain'))}-${suffix}-${instance.id}.tar.gz`
}

const findInstance = (id: string) => {
  assertSafeInstanceId(id)
  const instance = $app.findRecordById('instances', id)
  if (!instance) throw new BadRequestError(`Instance ${id} introuvable.`)
  return instance
}

const requireAuthRecord = (authRecord?: core.Record) => {
  if (!authRecord) throw new BadRequestError('Session utilisateur attendue.')
  return authRecord
}

const assertInstanceAccess = (instance: core.Record, authRecord: core.Record) => {
  if (instance.getString('uid') !== authRecord.id && !authRecord.getBool('superAdmin')) {
    throw new BadRequestError('Non autorise.')
  }
}

const serializeBackup = (backup: core.Record) => ({
  id: backup.id,
  user: backup.getString('user'),
  instance: backup.getString('instance'),
  kind: backup.getString('kind'),
  status: backup.getString('status'),
  filename: backup.getString('filename'),
  remoteKey: backup.getString('remoteKey'),
  sizeBytes: Number(backup.get('sizeBytes') || 0),
  compressedBytes: Number(backup.get('compressedBytes') || 0),
  checksum: backup.getString('checksum'),
  error: backup.getString('error'),
  remoteError: backup.getString('remoteError'),
  manifest: backup.get('manifest'),
  created: backup.getString('created'),
  updated: backup.getString('updated'),
})

const sortBackupsNewestFirst = (backups: core.Record[]) => {
  return backups.sort((a, b) => {
    const aValue = a.getString('updated') || a.getString('created') || a.getString('filename') || a.id
    const bValue = b.getString('updated') || b.getString('created') || b.getString('filename') || b.id
    return bValue.localeCompare(aValue)
  })
}

const findInstanceBackups = (instanceId: string) => {
  const records = $app.findRecordsByFilter('instance_backups', 'instance = {:instance}', '', 100, 0, {
    instance: instanceId,
  })
  return sortBackupsNewestFirst(records.filter((record): record is core.Record => !!record))
}

const getBackupRecord = (instance: core.Record, backupId: string) => {
  assertSafeBackupId(backupId)
  const backup = $app.findRecordById('instance_backups', backupId)
  if (!backup || backup.getString('instance') !== instance.id) {
    throw new BadRequestError('Sauvegarde introuvable.')
  }
  return backup
}

const pathValue = (e: core.RequestEvent, name: string) => {
  if (!e.request) throw new BadRequestError('Requete invalide.')
  return e.request.pathValue(name)
}

const assertNoRunningOperation = (instanceId: string) => {
  let running: core.Record | null = null
  try {
    running = $app.findFirstRecordByFilter(
      'instance_backups',
      'instance = {:instance} && status = "running"',
      { instance: instanceId }
    )
  } catch (error) {
    running = null
  }

  if (running) throw new BadRequestError('Une operation de sauvegarde est deja en cours pour cette instance.')
}

const setInstancePower = (instanceId: string, power: boolean) => {
  const record = findInstance(instanceId)
  record.set('power', power)
  $app.save(record)
  return record
}

const waitUntilIdle = (instanceId: string) => {
  for (let i = 0; i < MAX_STOP_WAIT_SECONDS; i++) {
    const current = findInstance(instanceId)
    if (!current.getBool('power') && current.getString('status').toLowerCase() === 'idle') return current
    sleepOneSecond()
  }

  throw new BadRequestError("L'instance ne s'est pas arretee a temps.")
}

const stopForFilesystemOperation = (instance: core.Record): ManagedPower => {
  const shouldRestart = instance.getBool('power')

  if (shouldRestart) {
    setInstancePower(instance.id, false)
  }

  waitUntilIdle(instance.id)
  return { shouldRestart }
}

const restartIfNeeded = (instanceId: string, managedPower: ManagedPower) => {
  if (!managedPower.shouldRestart) return

  try {
    setInstancePower(instanceId, true)
  } catch {}
}

const createBackupRecord = (instance: core.Record, authRecord: core.Record, kind: BackupKind) => {
  const collection = $app.findCollectionByNameOrId('instance_backups')
  const backup = new Record(collection)
  backup.set('user', instance.getString('uid') || authRecord.id)
  backup.set('instance', instance.id)
  backup.set('kind', kind)
  backup.set('status', 'running')
  backup.set('filename', '')
  backup.set('sizeBytes', 0)
  backup.set('compressedBytes', 0)
  backup.set('checksum', '')
  backup.set('error', '')
  backup.set('remoteError', '')
  backup.set('manifest', null)
  $app.save(backup)
  return backup
}

const sourceSizeBytes = (root: string) => {
  const dirs = BACKUP_DIRS.map((dir) => `${root}/${dir}`)
  const output = runCommand('du', '-sb', ...dirs)
  return output
    .split('\n')
    .map((line) => Number(line.trim().split(/\s+/)[0] || 0))
    .filter((value) => Number.isFinite(value))
    .reduce((sum, value) => sum + value, 0)
}

const sha256 = (path: string) => {
  const output = runCommand('sha256sum', path)
  return output.split(/\s+/)[0] || ''
}

const s3Config = () => {
  const enabled = ($os.getenv('INSTANCE_BACKUP_S3_ENABLED') || '').toLowerCase() === 'true'
  if (!enabled) return null

  const endpoint = $os.getenv('INSTANCE_BACKUP_S3_ENDPOINT')
  const bucket = $os.getenv('INSTANCE_BACKUP_S3_BUCKET')
  const prefix = ($os.getenv('INSTANCE_BACKUP_S3_PREFIX') || 'instances').replace(/^\/+|\/+$/g, '')
  const region = $os.getenv('AWS_DEFAULT_REGION') || 'auto'

  if (!endpoint || !bucket) {
    throw new Error('Configuration R2/S3 incomplete: endpoint et bucket requis.')
  }

  return { endpoint, bucket, prefix, region }
}

const remoteKeyFor = (instanceId: string, filename: string) => {
  const config = s3Config()
  if (!config) return ''
  return `${config.prefix}/${instanceId}/${filename}`.replace(/^\/+/, '')
}

const uploadBackupToS3 = (instanceId: string, filename: string, localPath: string) => {
  const config = s3Config()
  if (!config) return ''

  const remoteKey = remoteKeyFor(instanceId, filename)
  runCommand(
    'aws',
    's3',
    'cp',
    localPath,
    `s3://${config.bucket}/${remoteKey}`,
    '--endpoint-url',
    config.endpoint,
    '--region',
    config.region
  )
  return remoteKey
}

const downloadBackupFromS3 = (remoteKey: string, localPath: string) => {
  const config = s3Config()
  if (!config) throw new Error('La sauvegarde locale est absente et R2/S3 est desactive.')

  runCommand(
    'aws',
    's3',
    'cp',
    `s3://${config.bucket}/${remoteKey}`,
    localPath,
    '--endpoint-url',
    config.endpoint,
    '--region',
    config.region
  )
}

const deleteBackupFromS3 = (remoteKey: string) => {
  const config = s3Config()
  if (!config || !remoteKey) return ''

  return runCommand(
    'aws',
    's3',
    'rm',
    `s3://${config.bucket}/${remoteKey}`,
    '--endpoint-url',
    config.endpoint,
    '--region',
    config.region
  )
}

const ensureInstanceDirs = (root: string) => {
  $os.mkdirAll(root, DIR_MODE)
  for (const dir of BACKUP_DIRS) {
    $os.mkdirAll(`${root}/${dir}`, DIR_MODE)
  }
}

const createArchive = (instance: core.Record, backup: core.Record, kind: BackupKind) => {
  assertSafeInstanceId(instance.id)

  const root = instanceRoot(instance.id)
  const dir = backupDir(instance.id)
  const filename = createBackupFilename(instance, kind)
  const finalPath = backupPath(instance.id, filename)
  const tmpPath = `${finalPath}.tmp`
  const stagingDir = `${dir}/.staging-${backup.id}`
  const manifestPath = `${stagingDir}/manifest.json`

  assertSafeBackupFilename(filename)
  $os.mkdirAll(dir, DIR_MODE)
  $os.removeAll(stagingDir)
  $os.mkdirAll(stagingDir, PRIVATE_DIR_MODE)
  $os.removeAll(tmpPath)

  ensureInstanceDirs(root)

  const sizeBytes = sourceSizeBytes(root)
  const manifest = {
    format: BACKUP_FORMAT,
    createdAt: new Date().toISOString(),
    kind,
    instance: {
      id: instance.id,
      subdomain: instance.getString('subdomain'),
      version: instance.getString('version'),
      dev: instance.getBool('dev'),
      syncAdmin: instance.getBool('syncAdmin'),
      autoVacuum: instance.getBool('autoVacuum'),
    },
    included: BACKUP_DIRS,
    excluded: ['logs'],
    sourceSizeBytes: sizeBytes,
  }

  try {
    $os.writeFile(manifestPath, JSON.stringify(manifest, null, 2), PRIVATE_FILE_MODE)
    runCommand('tar', '-czf', tmpPath, '-C', root, ...BACKUP_DIRS, '-C', stagingDir, 'manifest.json')
    $os.rename(tmpPath, finalPath)
  } finally {
    try {
      $os.remove(tmpPath)
    } catch {}
    try {
      $os.removeAll(stagingDir)
    } catch {}
  }

  return {
    filename,
    localPath: finalPath,
    sizeBytes,
    compressedBytes: fileSize(finalPath),
    checksum: sha256(finalPath),
    manifest,
  }
}

const markBackupReady = (backup: core.Record, details: ReturnType<typeof createArchive>) => {
  backup.set('status', 'ready')
  backup.set('filename', details.filename)
  backup.set('localPath', details.localPath)
  backup.set('sizeBytes', details.sizeBytes)
  backup.set('compressedBytes', details.compressedBytes)
  backup.set('checksum', details.checksum)
  backup.set('manifest', details.manifest)
  backup.set('error', '')

  try {
    const remoteKey = uploadBackupToS3(backup.getString('instance'), details.filename, details.localPath)
    backup.set('remoteKey', remoteKey)
    backup.set('remoteError', '')
  } catch (error) {
    backup.set('remoteError', errorMessage(error))
  }

  $app.save(backup)
}

const markBackupFailed = (backup: core.Record, error: unknown) => {
  backup.set('status', 'failed')
  backup.set('error', errorMessage(error))
  $app.save(backup)
}

const createBackupForInstance = (
  instance: core.Record,
  authRecord: core.Record,
  kind: BackupKind,
  managePower: boolean,
  skipRunningCheck = false
) => {
  if (!skipRunningCheck) assertNoRunningOperation(instance.id)

  const backup = createBackupRecord(instance, authRecord, kind)
  let power: ManagedPower = { shouldRestart: false }

  try {
    if (managePower) {
      power = stopForFilesystemOperation(instance)
    } else {
      waitUntilIdle(instance.id)
    }

    const stoppedInstance = findInstance(instance.id)
    const details = createArchive(stoppedInstance, backup, kind)
    markBackupReady(backup, details)
    return backup
  } catch (error) {
    markBackupFailed(backup, error)
    throw error
  } finally {
    if (managePower) restartIfNeeded(instance.id, power)
  }
}

const safeTarEntry = (entry: string) => {
  const normalized = entry.replace(/^\.\/+/, '')
  const parts = normalized.split('/')
  return !!normalized && !normalized.startsWith('/') && !normalized.startsWith('../') && !parts.includes('..')
}

const validateArchiveListing = (archivePath: string) => {
  const output = runCommand('tar', '-tzf', archivePath)
  const entries = output.split('\n').map((entry) => entry.trim()).filter(Boolean)
  if (!entries.length) throw new BadRequestError('Archive vide.')

  for (const entry of entries) {
    if (!safeTarEntry(entry)) throw new BadRequestError('Archive invalide.')
  }
}

const ensureLocalArchive = (instance: core.Record, backup: core.Record) => {
  const filename = backup.getString('filename')
  assertSafeBackupFilename(filename)

  const localPath = backupPath(instance.id, filename)
  if (pathExists(localPath)) return localPath

  const remoteKey = backup.getString('remoteKey')
  if (!remoteKey) throw new BadRequestError('Archive locale introuvable.')

  $os.mkdirAll(backupDir(instance.id), DIR_MODE)
  downloadBackupFromS3(remoteKey, localPath)
  backup.set('localPath', localPath)
  backup.set('remoteError', '')
  $app.save(backup)
  return localPath
}

const readManifest = (extractDir: string) => {
  const raw = toString($os.readFile(`${extractDir}/manifest.json`))
  const manifest = JSON.parse(raw)
  if (!manifest || manifest.format !== BACKUP_FORMAT) {
    throw new BadRequestError('Format de sauvegarde non pris en charge.')
  }
  return manifest
}

const restoreExtractedDirs = (instance: core.Record, extractDir: string) => {
  const root = instanceRoot(instance.id)
  const rollbackDir = `${root}/.restore-rollback-${Date.now()}-${instance.id}`

  $os.mkdirAll(root, DIR_MODE)
  $os.mkdirAll(rollbackDir, PRIVATE_DIR_MODE)

  let movedOldDirs = false

  try {
    for (const dir of BACKUP_DIRS) {
      const source = `${extractDir}/${dir}`
      if (!pathExists(source)) throw new BadRequestError(`Archive incomplete: ${dir} manquant.`)
    }

    for (const dir of BACKUP_DIRS) {
      const current = `${root}/${dir}`
      if (pathExists(current)) {
        $os.rename(current, `${rollbackDir}/${dir}`)
      }
    }

    movedOldDirs = true

    for (const dir of BACKUP_DIRS) {
      $os.rename(`${extractDir}/${dir}`, `${root}/${dir}`)
    }

    $os.removeAll(rollbackDir)
  } catch (error) {
    if (movedOldDirs) {
      for (const dir of BACKUP_DIRS) {
        try {
          $os.removeAll(`${root}/${dir}`)
        } catch {}
        try {
          if (pathExists(`${rollbackDir}/${dir}`)) {
            $os.rename(`${rollbackDir}/${dir}`, `${root}/${dir}`)
          }
        } catch {}
      }
    }
    try {
      $os.removeAll(rollbackDir)
    } catch {}
    throw error
  }
}

const restoreArchive = (instance: core.Record, backup: core.Record) => {
  const archivePath = ensureLocalArchive(instance, backup)
  validateArchiveListing(archivePath)

  const extractDir = `${instanceRoot(instance.id)}/.restore-extract-${backup.id}`
  $os.mkdirAll(instanceRoot(instance.id), DIR_MODE)
  $os.removeAll(extractDir)
  $os.mkdirAll(extractDir, PRIVATE_DIR_MODE)

  try {
    runCommand('tar', '-xzf', archivePath, '-C', extractDir)
    const manifest = readManifest(extractDir)
    restoreExtractedDirs(instance, extractDir)

    if (manifest.instance?.version) {
      const current = findInstance(instance.id)
      current.set('version', manifest.instance.version)
      $app.save(current)
    }
  } finally {
    try {
      $os.removeAll(extractDir)
    } catch {}
  }
}

export const HandleInstanceBackupCreate = (e: core.RequestEvent) => {
  const log = mkLog('POST:instance:backup')
  const authRecord = requireAuthRecord(e.auth)
  const instance = findInstance(pathValue(e, 'id'))
  assertInstanceAccess(instance, authRecord)

  const backup = createBackupForInstance(instance, authRecord, 'manual', true)
  log(`created ${backup.id} for ${instance.id}`)

  return e.json(200, { backup: serializeBackup(backup) })
}

export const HandleInstanceBackupsList = (e: core.RequestEvent) => {
  const authRecord = requireAuthRecord(e.auth)
  const instance = findInstance(pathValue(e, 'id'))
  assertInstanceAccess(instance, authRecord)

  const backups = findInstanceBackups(instance.id).map(serializeBackup)

  return e.json(200, { backups })
}

export const HandleInstanceBackupDownload = (e: core.RequestEvent) => {
  const authRecord = requireAuthRecord(e.auth)
  const instance = findInstance(pathValue(e, 'id'))
  assertInstanceAccess(instance, authRecord)

  const backup = getBackupRecord(instance, pathValue(e, 'backupId'))
  if (backup.getString('status') !== 'ready') {
    throw new BadRequestError("Cette sauvegarde n'est pas prete.")
  }

  const localPath = ensureLocalArchive(instance, backup)
  const filename = backup.getString('filename')
  e.response.header().set('Content-Disposition', `attachment; filename="${filename}"`)
  e.response.header().set('Content-Length', `${fileSize(localPath)}`)

  return e.fileFS($os.dirFS(backupDir(instance.id)), filename)
}

export const HandleInstanceBackupDelete = (e: core.RequestEvent) => {
  const authRecord = requireAuthRecord(e.auth)
  const instance = findInstance(pathValue(e, 'id'))
  assertInstanceAccess(instance, authRecord)

  const backup = getBackupRecord(instance, pathValue(e, 'backupId'))
  const filename = backup.getString('filename')
  if (filename) {
    assertSafeBackupFilename(filename)
    try {
      $os.remove(backupPath(instance.id, filename))
    } catch {}
  }

  try {
    deleteBackupFromS3(backup.getString('remoteKey'))
  } catch {}

  $app.delete(backup)
  return e.json(200, { status: 'ok' })
}

export const HandleInstanceBackupRestore = (e: core.RequestEvent) => {
  const log = mkLog('POST:instance:backup:restore')
  const authRecord = requireAuthRecord(e.auth)
  const instance = findInstance(pathValue(e, 'id'))
  assertInstanceAccess(instance, authRecord)
  assertNoRunningOperation(instance.id)

  const backup = getBackupRecord(instance, pathValue(e, 'backupId'))
  if (backup.getString('status') !== 'ready') {
    throw new BadRequestError("Cette sauvegarde n'est pas prete.")
  }

  const power = stopForFilesystemOperation(instance)
  let restored = false

  try {
    createBackupForInstance(findInstance(instance.id), authRecord, 'pre-restore', false, true)
    restoreArchive(findInstance(instance.id), backup)
    restored = true
    log(`restored ${backup.id} into ${instance.id}`)
  } finally {
    if (restored) restartIfNeeded(instance.id, power)
  }

  return e.json(200, { status: 'ok' })
}
