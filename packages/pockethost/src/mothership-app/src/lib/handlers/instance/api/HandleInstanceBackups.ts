import { mkLog } from '$util/Logger'

const BACKUP_FORMAT = 'gestion-pocketbase-instance-backup-v1'
const BACKUP_DIRS = ['pb_data', 'pb_public', 'pb_migrations', 'pb_hooks']
const REQUIRED_RESTORE_DIR = 'pb_data'
const MAX_STOP_WAIT_SECONDS = 120
const DIR_MODE = 0o755 as any
const PRIVATE_DIR_MODE = 0o700 as any
const PRIVATE_FILE_MODE = 0o600 as any
const DEFAULT_IMPORT_CHUNK_SIZE_BYTES = 32 * 1024 * 1024
const MIN_IMPORT_CHUNK_SIZE_BYTES = 1024 * 1024
const MAX_IMPORT_CHUNKS = 20_000
const DEFAULT_BACKUP_GZIP_LEVEL = 1
const DEFAULT_BACKUP_NICE_LEVEL = 19
const DEFAULT_BACKUP_IONICE_CLASS = 3

type BackupKind = 'manual' | 'pre-restore' | 'import'
type ArchiveFormat = 'tar.gz' | 'zip'
type ManagedPower = {
  shouldRestart: boolean
}
type BackupOperationInput = {
  label?: string
  percent?: number
  sourceSizeBytes?: number
  compressedBytes?: number
}
type RestoreMode = 'in-place' | 'new-instance'
type RestoreOperationInput = BackupOperationInput & {
  mode?: RestoreMode
  targetInstanceId?: string
  targetSubdomain?: string
  error?: string
}
type RestoreArchiveOptions = {
  mode: RestoreMode
  targetInstanceId?: string
  targetSubdomain?: string
  markReady?: boolean
}
type ArchiveResourceSettings = {
  cpuLimitPercent: number
  niceLevel: number
  ioniceClass: number
  ionicePriority: number
}
type ImportedArchive = {
  filename: string
  localPath: string
}
type ChunkSession = {
  instanceId: string
  userId: string
  filename: string
  size: number
  chunkSize: number
  totalChunks: number
  createdAt: string
}

const dataRoot = () => {
  const envRoot = $os.getenv('DATA_ROOT')
  if (envRoot) return envRoot

  const appDataDir = `${$app.dataDir()}`
  const inferred = appDataDir.replace(/\/mothership\/pb_data\/?$/, '')
  if (inferred !== appDataDir) return inferred

  throw new Error('Impossible de trouver le dossier de donnees des instances.')
}

const backupRoot = () => $os.getenv('INSTANCE_BACKUP_ROOT') || `${dataRoot()}/backups/instances`
const importRoot = () => $os.getenv('INSTANCE_IMPORT_ROOT') || `${dataRoot()}/imports`
const chunkUploadRoot = () => `${importRoot()}/.chunked`
const chunkSessionDir = (instanceId: string, uploadId: string) => `${chunkUploadRoot()}/${instanceId}/${uploadId}`
const chunkPartsDir = (instanceId: string, uploadId: string) => `${chunkSessionDir(instanceId, uploadId)}/parts`
const chunkMetaPath = (instanceId: string, uploadId: string) => `${chunkSessionDir(instanceId, uploadId)}/metadata.json`
const assembledImportDir = (instanceId: string) => `${importRoot()}/assembled/${instanceId}`
const chunkPartFilename = (index: number) => `${String(index).padStart(8, '0')}.part`

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

const assertSafeUploadId = (id: string) => {
  if (!id.match(/^[a-zA-Z0-9_-]+$/)) {
    throw new BadRequestError("Identifiant d'upload invalide.")
  }
}

const assertSafeBackupFilename = (filename: string) => {
  if (!filename.match(/^[a-zA-Z0-9._-]+\.(tar\.gz|tgz|zip)$/)) {
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

const parseIntegerEnv = (name: string, fallback: number, min: number, max: number) => {
  const raw = `${$os.getenv(name) || ''}`.trim()
  if (!raw) return fallback

  const value = Number(raw)
  if (!Number.isFinite(value)) return fallback
  return Math.max(min, Math.min(max, Math.floor(value)))
}

const commandExists = (name: string) => {
  if (!name.match(/^[a-z0-9_-]+$/i)) return false
  return runCommand('sh', '-c', `command -v ${name} >/dev/null 2>&1; echo $?`) === '0'
}

const backupGzipLevel = () => parseIntegerEnv('INSTANCE_BACKUP_GZIP_LEVEL', DEFAULT_BACKUP_GZIP_LEVEL, 1, 9)
const backupCpuLimitPercent = () => parseIntegerEnv('INSTANCE_BACKUP_CPU_LIMIT_PERCENT', 0, 0, 1000)
const backupNiceLevel = () => parseIntegerEnv('INSTANCE_BACKUP_NICE_LEVEL', DEFAULT_BACKUP_NICE_LEVEL, -20, 19)
const backupIoniceClass = () => parseIntegerEnv('INSTANCE_BACKUP_IONICE_CLASS', DEFAULT_BACKUP_IONICE_CLASS, 0, 3)
const backupIonicePriority = () => parseIntegerEnv('INSTANCE_BACKUP_IONICE_PRIORITY', 7, 0, 7)
const restoreCpuLimitPercent = () =>
  parseIntegerEnv('INSTANCE_RESTORE_CPU_LIMIT_PERCENT', backupCpuLimitPercent(), 0, 1000)
const restoreNiceLevel = () => parseIntegerEnv('INSTANCE_RESTORE_NICE_LEVEL', backupNiceLevel(), -20, 19)
const restoreIoniceClass = () => parseIntegerEnv('INSTANCE_RESTORE_IONICE_CLASS', backupIoniceClass(), 0, 3)
const restoreIonicePriority = () => parseIntegerEnv('INSTANCE_RESTORE_IONICE_PRIORITY', backupIonicePriority(), 0, 7)

const backupResourceSettings = () => ({
  gzipLevel: backupGzipLevel(),
  cpuLimitPercent: backupCpuLimitPercent(),
  niceLevel: backupNiceLevel(),
  ioniceClass: backupIoniceClass(),
  ionicePriority: backupIonicePriority(),
})

const restoreResourceSettings = (): ArchiveResourceSettings => ({
  cpuLimitPercent: restoreCpuLimitPercent(),
  niceLevel: restoreNiceLevel(),
  ioniceClass: restoreIoniceClass(),
  ionicePriority: restoreIonicePriority(),
})

const withArchiveResourceLimits = (command: string[], settings: ArchiveResourceSettings, cpuLimitEnvName: string) => {
  let limited = [...command]

  if (commandExists('nice')) {
    limited = ['nice', '-n', `${settings.niceLevel}`, ...limited]
  }

  if (commandExists('ionice')) {
    const ioniceArgs = ['ionice', '-c', `${settings.ioniceClass}`]
    if (settings.ioniceClass === 2) ioniceArgs.push('-n', `${settings.ionicePriority}`)
    limited = [...ioniceArgs, ...limited]
  }

  if (settings.cpuLimitPercent > 0) {
    if (!commandExists('cpulimit')) {
      throw new Error(`${cpuLimitEnvName} requiert le paquet systeme cpulimit.`)
    }
    limited = ['cpulimit', '-q', '-m', '-f', '-l', `${settings.cpuLimitPercent}`, '--', ...limited]
  }

  return limited
}

const withBackupResourceLimits = (command: string[]) =>
  withArchiveResourceLimits(command, backupResourceSettings(), 'INSTANCE_BACKUP_CPU_LIMIT_PERCENT')

const withRestoreResourceLimits = (command: string[]) =>
  withArchiveResourceLimits(command, restoreResourceSettings(), 'INSTANCE_RESTORE_CPU_LIMIT_PERCENT')

const runBackupArchiveCommand = (tmpPath: string, root: string, stagingDir: string) => {
  const settings = backupResourceSettings()
  const command = withBackupResourceLimits([
    'tar',
    '-I',
    `gzip -${settings.gzipLevel}`,
    '-cf',
    tmpPath,
    '-C',
    root,
    ...BACKUP_DIRS,
    '-C',
    stagingDir,
    'manifest.json',
  ])

  return runCommand(command[0]!, ...command.slice(1))
}

const runArchiveCommand = (limited: boolean, command: string[]) => {
  const runnable = limited ? withRestoreResourceLimits(command) : command
  return runCommand(runnable[0]!, ...runnable.slice(1))
}

const sleepOneSecond = () => {
  $os.cmd('sleep', '1').combinedOutput()
}

const errorMessage = (error: unknown) => {
  if (error instanceof Error) return error.message
  return `${error}`
}

const realpath = (path: string) => runCommand('realpath', path)

const parentDir = (path: string) => {
  const trimmed = path.replace(/\/+$/g, '')
  const parts = trimmed.split('/')
  parts.pop()
  return parts.join('/') || '/'
}

const basename = (path: string) => path.replace(/\/+$/g, '').split('/').pop() || ''

const parsePositiveInteger = (value: unknown, field: string) => {
  const numeric = Number(value)
  if (!Number.isFinite(numeric) || numeric <= 0) {
    throw new BadRequestError(`${field} invalide.`)
  }
  return Math.floor(numeric)
}

const parseNonNegativeInteger = (value: unknown, field: string) => {
  const numeric = Number(value)
  if (!Number.isFinite(numeric) || numeric < 0) {
    throw new BadRequestError(`${field} invalide.`)
  }
  return Math.floor(numeric)
}

const slugForFilename = (value: string) => {
  const clean = value
    .toLowerCase()
    .replace(/[^a-z0-9-]/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-+|-+$/g, '')
  return clean.slice(0, 48).replace(/-+$/g, '') || 'instance'
}

const timestampForFilename = () =>
  new Date()
    .toISOString()
    .replace(/[-:]/g, '')
    .replace(/\.\d{3}Z$/, 'Z')

const createBackupFilename = (instance: core.Record, kind: BackupKind) => {
  const suffix = kind === 'pre-restore' ? 'pre-restore' : kind === 'import' ? 'import' : 'manual'
  return `${timestampForFilename()}-${slugForFilename(instance.getString('subdomain'))}-${suffix}-${instance.id}.tar.gz`
}

const extensionForImport = (filename: string) => {
  const lower = filename.toLowerCase()
  if (lower.endsWith('.tar.gz')) return 'tar.gz'
  if (lower.endsWith('.tgz')) return 'tgz'
  if (lower.endsWith('.zip')) return 'zip'
  throw new BadRequestError('Archive non prise en charge. Utilisez .zip, .tgz ou .tar.gz.')
}

const archiveFormatForFilename = (filename: string): ArchiveFormat => {
  const ext = extensionForImport(filename)
  return ext === 'zip' ? 'zip' : 'tar.gz'
}

const createImportBackupFilename = (instance: core.Record, sourceFilename: string) => {
  const extension = extensionForImport(sourceFilename)
  return `${timestampForFilename()}-${slugForFilename(instance.getString('subdomain'))}-import-${instance.id}.${extension}`
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

const assertServerImportAllowed = (authRecord: core.Record, requestedPath: string) => {
  if (!authRecord.getBool('superAdmin')) {
    throw new BadRequestError("L'import depuis un chemin serveur est reserve au superadmin.")
  }

  if (!requestedPath.trim()) {
    throw new BadRequestError('Chemin serveur manquant.')
  }

  $os.mkdirAll(importRoot(), DIR_MODE)

  const root = realpath(importRoot())
  const source = realpath(requestedPath.trim())
  if (source !== root && !source.startsWith(`${root}/`)) {
    throw new BadRequestError(`Archive hors du dossier autorise (${root}).`)
  }
  return source
}

const assertBackupImportAllowed = (authRecord: core.Record) => {
  if (!authRecord.getBool('superAdmin')) {
    throw new BadRequestError("L'import d'archive est reserve au superadmin.")
  }
}

export const serializeInstanceBackup = (backup: core.Record) => ({
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

const normalizeBaseSubdomain = (subdomain: string) => {
  const clean = subdomain
    .toLowerCase()
    .replace(/[^a-z0-9-]/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-+|-+$/g, '')

  const base = clean.match(/^[a-z]/) ? clean : `base-${clean}`
  return base.slice(0, 34).replace(/-+$/g, '') || 'base'
}

const assertValidSubdomain = (subdomain: string) => {
  if (!subdomain.match(/^[a-z][a-z0-9-]{2,39}$/)) {
    throw new BadRequestError('Nom de nouvelle instance invalide.')
  }
}

const subdomainExists = (subdomain: string) => {
  try {
    $app.findFirstRecordByData('instances', 'subdomain', subdomain)
    return true
  } catch {
    return false
  }
}

const suggestRestoreSubdomain = (sourceSubdomain: string) => {
  const base = normalizeBaseSubdomain(sourceSubdomain)
  const fixed = `${base.slice(0, 31).replace(/-+$/g, '')}-restore`
  if (fixed.match(/^[a-z][a-z0-9-]{2,39}$/) && !subdomainExists(fixed)) return fixed

  for (let i = 0; i < 25; i++) {
    const suffix = $security.randomStringWithAlphabet(5 + Math.min(i, 4), 'abcdefghijklmnopqrstuvwxyz0123456789')
    const candidate = `${base.slice(0, 39 - suffix.length).replace(/-+$/g, '')}-${suffix}`
    if (candidate.match(/^[a-z][a-z0-9-]{2,39}$/) && !subdomainExists(candidate)) return candidate
  }

  throw new BadRequestError("Impossible de generer un nom d'instance disponible.")
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
    running = $app.findFirstRecordByFilter('instance_backups', 'instance = {:instance} && status = "running"', {
      instance: instanceId,
    })
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
  const now = new Date().toISOString()
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
  backup.set('manifest', {
    operation: {
      phase: 'queued',
      label:
        kind === 'manual'
          ? 'Sauvegarde demandee'
          : kind === 'pre-restore'
            ? 'Sauvegarde de securite demandee'
            : 'Import demande',
      percent: 2,
      startedAt: now,
      updatedAt: now,
    },
  })
  $app.save(backup)
  return backup
}

const recordObject = (value: unknown) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return JSON.parse(JSON.stringify(value)) as Record<string, any>
}

const updateBackupOperation = (backup: core.Record, phase: string, input: BackupOperationInput = {}) => {
  const manifest = recordObject(backup.get('manifest'))
  const currentOperation =
    manifest.operation && typeof manifest.operation === 'object' && !Array.isArray(manifest.operation)
      ? manifest.operation
      : {}
  const now = new Date().toISOString()

  backup.set('manifest', {
    ...manifest,
    operation: {
      ...currentOperation,
      phase,
      label: input.label || currentOperation.label || phase,
      percent:
        typeof input.percent === 'number'
          ? Math.max(0, Math.min(99, Math.round(input.percent)))
          : currentOperation.percent || 0,
      sourceSizeBytes:
        typeof input.sourceSizeBytes === 'number' ? input.sourceSizeBytes : currentOperation.sourceSizeBytes || 0,
      compressedBytes:
        typeof input.compressedBytes === 'number' ? input.compressedBytes : currentOperation.compressedBytes || 0,
      startedAt: currentOperation.startedAt || now,
      updatedAt: now,
    },
  })
  $app.save(backup)
}

const updateRestoreOperation = (backup: core.Record, phase: string, input: RestoreOperationInput = {}) => {
  const manifest = recordObject(backup.get('manifest'))
  const currentOperation =
    manifest.restoreOperation &&
    typeof manifest.restoreOperation === 'object' &&
    !Array.isArray(manifest.restoreOperation)
      ? manifest.restoreOperation
      : {}
  const now = new Date().toISOString()
  const isComplete = phase === 'ready' || phase === 'failed'
  const rawPercent =
    typeof input.percent === 'number'
      ? input.percent
      : typeof currentOperation.percent === 'number'
        ? currentOperation.percent
        : 0

  backup.set('manifest', {
    ...manifest,
    restoreOperation: {
      ...currentOperation,
      phase,
      label: input.label || currentOperation.label || phase,
      percent: Math.max(0, Math.min(isComplete ? 100 : 99, Math.round(rawPercent))),
      mode: input.mode || currentOperation.mode || 'in-place',
      targetInstanceId: input.targetInstanceId || currentOperation.targetInstanceId || '',
      targetSubdomain: input.targetSubdomain || currentOperation.targetSubdomain || '',
      sourceSizeBytes:
        typeof input.sourceSizeBytes === 'number' ? input.sourceSizeBytes : currentOperation.sourceSizeBytes || 0,
      compressedBytes:
        typeof input.compressedBytes === 'number' ? input.compressedBytes : currentOperation.compressedBytes || 0,
      error: input.error || (phase === 'failed' ? currentOperation.error || '' : ''),
      startedAt: currentOperation.startedAt || now,
      updatedAt: now,
    },
  })
  $app.save(backup)
}

type BackupDetails = {
  filename: string
  localPath: string
  sizeBytes: number
  compressedBytes: number
  checksum: string
  manifest: unknown
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
  const resourceSettings = backupResourceSettings()

  assertSafeBackupFilename(filename)
  $os.mkdirAll(dir, DIR_MODE)
  $os.removeAll(stagingDir)
  $os.mkdirAll(stagingDir, PRIVATE_DIR_MODE)
  $os.removeAll(tmpPath)

  ensureInstanceDirs(root)

  updateBackupOperation(backup, 'scanning', {
    label: 'Analyse de la taille des fichiers',
    percent: 18,
  })
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
    compression: {
      format: 'tar.gz',
      gzipLevel: resourceSettings.gzipLevel,
      cpuLimitPercent: resourceSettings.cpuLimitPercent,
      niceLevel: resourceSettings.niceLevel,
      ioniceClass: resourceSettings.ioniceClass,
      ionicePriority: resourceSettings.ionicePriority,
    },
  }

  try {
    $os.writeFile(manifestPath, JSON.stringify(manifest, null, 2), PRIVATE_FILE_MODE)
    updateBackupOperation(backup, 'compressing', {
      label:
        resourceSettings.cpuLimitPercent > 0
          ? `Compression limitee a ${resourceSettings.cpuLimitPercent} % CPU`
          : "Compression de l'archive en cours",
      percent: 36,
      sourceSizeBytes: sizeBytes,
    })
    runBackupArchiveCommand(tmpPath, root, stagingDir)
    $os.rename(tmpPath, finalPath)

    const compressedBytes = fileSize(finalPath)
    updateBackupOperation(backup, 'checksum', {
      label: "Calcul de l'empreinte SHA-256",
      percent: 86,
      sourceSizeBytes: sizeBytes,
      compressedBytes,
    })
    const checksum = sha256(finalPath)
    updateBackupOperation(backup, 'finalizing', {
      label: 'Finalisation de la sauvegarde',
      percent: 92,
      sourceSizeBytes: sizeBytes,
      compressedBytes,
    })

    return {
      filename,
      localPath: finalPath,
      sizeBytes,
      compressedBytes,
      checksum,
      manifest,
    }
  } finally {
    try {
      $os.remove(tmpPath)
    } catch {}
    try {
      $os.removeAll(stagingDir)
    } catch {}
  }
}

const markBackupReady = (backup: core.Record, details: BackupDetails) => {
  backup.set('filename', details.filename)
  backup.set('localPath', details.localPath)
  backup.set('sizeBytes', details.sizeBytes)
  backup.set('compressedBytes', details.compressedBytes)
  backup.set('checksum', details.checksum)
  backup.set('manifest', details.manifest)
  backup.set('error', '')

  try {
    if (s3Config()) {
      backup.set('manifest', {
        ...details.manifest,
        operation: {
          phase: 'remote',
          label: 'Copie distante R2/S3 en cours',
          percent: 95,
          sourceSizeBytes: details.sizeBytes,
          compressedBytes: details.compressedBytes,
          startedAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        },
      })
      updateBackupOperation(backup, 'remote', {
        label: 'Copie distante R2/S3 en cours',
        percent: 95,
        sourceSizeBytes: details.sizeBytes,
        compressedBytes: details.compressedBytes,
      })
    }
    const remoteKey = uploadBackupToS3(backup.getString('instance'), details.filename, details.localPath)
    backup.set('remoteKey', remoteKey)
    backup.set('remoteError', '')
  } catch (error) {
    backup.set('remoteError', errorMessage(error))
  }

  backup.set('status', 'ready')
  backup.set('manifest', details.manifest)
  $app.save(backup)
}

const markBackupFailed = (backup: core.Record, error: unknown) => {
  updateBackupOperation(backup, 'failed', {
    label: 'Sauvegarde en echec',
  })
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
      updateBackupOperation(backup, 'stopping', {
        label: instance.getBool('power') ? "Arret de l'instance avant sauvegarde" : "Verification de l'instance",
        percent: 8,
      })
      power = stopForFilesystemOperation(instance)
    } else {
      updateBackupOperation(backup, 'waiting', {
        label: "Attente de l'arret de l'instance",
        percent: 8,
      })
      waitUntilIdle(instance.id)
    }

    updateBackupOperation(backup, 'snapshot', {
      label: 'Instance arretee, preparation des fichiers',
      percent: 14,
    })
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

const normalizeArchiveEntry = (entry: string) => entry.replace(/^\.\/+/, '')

const entryParts = (entry: string) => normalizeArchiveEntry(entry).split('/').filter(Boolean)

const archiveHasRequiredRestoreData = (entries: string[]) => {
  return entries.some((entry) => {
    const parts = entryParts(entry)
    return parts.includes(REQUIRED_RESTORE_DIR) || parts[parts.length - 1] === 'data.db'
  })
}

const archiveIncludedDirs = (entries: string[]) => {
  const included = entries
    .map(entryParts)
    .flat()
    .filter((part, index, parts) => BACKUP_DIRS.includes(part) && parts.indexOf(part) === index)

  if (included.includes(REQUIRED_RESTORE_DIR)) return included
  if (archiveHasRequiredRestoreData(entries)) return [REQUIRED_RESTORE_DIR, ...included]
  return included
}

const zipListedSizeBytes = (archivePath: string) => {
  const output = runCommand('unzip', '-l', archivePath)
  let total = 0

  for (const line of output.split('\n')) {
    const summary = line.match(/^\s*(\d+)\s+\d+\s+files?\s*$/i)
    if (summary) return Number(summary[1])

    const entry = line.match(/^\s*(\d+)\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s+/)
    if (!entry) continue

    const bytes = Number(entry[1])
    if (Number.isFinite(bytes)) total += bytes
  }

  return total
}

const tarListedSizeBytes = (archivePath: string) => {
  const output = runCommand('tar', '--numeric-owner', '-tvzf', archivePath)
  let total = 0

  for (const line of output.split('\n')) {
    const parts = line.trim().split(/\s+/)
    const mode = parts[0] || ''
    if (!mode.startsWith('-')) continue

    const sizeToken = parts.slice(1).find((part) => part.match(/^\d+$/))
    if (!sizeToken) continue

    const bytes = Number(sizeToken)
    if (Number.isFinite(bytes)) total += bytes
  }

  return total
}

const archiveSourceSizeBytes = (archivePath: string, filename: string) => {
  try {
    const format = archiveFormatForFilename(filename)
    const sourceBytes = format === 'zip' ? zipListedSizeBytes(archivePath) : tarListedSizeBytes(archivePath)
    return Number.isFinite(sourceBytes) && sourceBytes > 0 ? sourceBytes : 0
  } catch {
    return 0
  }
}

const listArchiveEntries = (archivePath: string, filename: string, limited = false) => {
  const format = archiveFormatForFilename(filename)
  const output =
    format === 'zip'
      ? runArchiveCommand(limited, ['unzip', '-Z1', archivePath])
      : runArchiveCommand(limited, ['tar', '-tzf', archivePath])
  return output
    .split('\n')
    .map((entry) => entry.trim())
    .filter(Boolean)
}

const validateArchiveListing = (archivePath: string, filename: string, limited = false) => {
  const entries = listArchiveEntries(archivePath, filename, limited)
  if (!entries.length) throw new BadRequestError('Archive vide.')

  for (const entry of entries) {
    if (!safeTarEntry(entry)) throw new BadRequestError('Archive invalide.')
  }

  if (!archiveHasRequiredRestoreData(entries)) {
    throw new BadRequestError(`Archive invalide: dossier ${REQUIRED_RESTORE_DIR} ou fichier data.db manquant.`)
  }

  return entries
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

const readManifest = (extractDir: string, backup?: core.Record) => {
  const manifestPath = `${extractDir}/manifest.json`
  if (!pathExists(manifestPath)) {
    return {
      format: BACKUP_FORMAT,
      imported: true,
      originalFilename: backup?.getString('filename') || '',
      createdAt: new Date().toISOString(),
      included: BACKUP_DIRS.filter((dir) => pathExists(`${extractDir}/${dir}`)),
      sourceSizeBytes: 0,
    }
  }

  const raw = toString($os.readFile(manifestPath))
  const manifest = JSON.parse(raw)
  if (!manifest || manifest.format !== BACKUP_FORMAT) {
    throw new BadRequestError('Format de sauvegarde non pris en charge.')
  }
  return manifest
}

const extractArchive = (archivePath: string, filename: string, extractDir: string) => {
  const format = archiveFormatForFilename(filename)
  if (format === 'zip') {
    runArchiveCommand(true, ['unzip', '-q', archivePath, '-d', extractDir])
    return
  }
  runArchiveCommand(true, ['tar', '-xzf', archivePath, '-C', extractDir])
}

const moveDirectoryContents = (sourceDir: string, targetDir: string) => {
  $os.mkdirAll(targetDir, DIR_MODE)
  runCommand('find', sourceDir, '-mindepth', '1', '-maxdepth', '1', '-exec', 'mv', '{}', targetDir, ';')
}

const findLoosePbDataRoot = (extractDir: string) => {
  const found = runCommand('find', extractDir, '-type', 'f', '-name', 'data.db', '-print', '-quit')
  if (!found) return ''
  return parentDir(found.split('\n')[0]!)
}

const findArchiveContentRoot = (extractDir: string, normalizedDir: string) => {
  if (pathExists(`${extractDir}/${REQUIRED_RESTORE_DIR}`)) return extractDir

  const found = runCommand('find', extractDir, '-type', 'd', '-name', REQUIRED_RESTORE_DIR, '-print', '-quit')
  if (found) return parentDir(found.split('\n')[0]!)

  const loosePbDataRoot = findLoosePbDataRoot(extractDir)
  if (!loosePbDataRoot) {
    throw new BadRequestError(`Archive invalide: dossier ${REQUIRED_RESTORE_DIR} ou fichier data.db manquant.`)
  }

  $os.removeAll(normalizedDir)
  moveDirectoryContents(loosePbDataRoot, `${normalizedDir}/${REQUIRED_RESTORE_DIR}`)
  return normalizedDir
}

const ensureRestorableDirs = (sourceRoot: string) => {
  if (!pathExists(`${sourceRoot}/${REQUIRED_RESTORE_DIR}`)) {
    throw new BadRequestError(`Archive incomplete: ${REQUIRED_RESTORE_DIR} manquant.`)
  }

  for (const dir of BACKUP_DIRS) {
    $os.mkdirAll(`${sourceRoot}/${dir}`, DIR_MODE)
  }
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

const restoreArchive = (
  instance: core.Record,
  backup: core.Record,
  archiveInstance = instance,
  options: RestoreArchiveOptions = { mode: 'in-place' }
) => {
  const filename = backup.getString('filename')
  const target = {
    mode: options.mode,
    targetInstanceId: options.targetInstanceId || instance.id,
    targetSubdomain: options.targetSubdomain || instance.getString('subdomain'),
  }
  const compressedBytes = Number(backup.get('compressedBytes') || 0)
  const sourceBytes = Number(backup.get('sizeBytes') || 0)

  updateRestoreOperation(backup, 'preparing', {
    ...target,
    label: "Préparation de l'archive",
    percent: 12,
    sourceSizeBytes: sourceBytes,
    compressedBytes,
  })

  let archivePath = ''
  try {
    archivePath = ensureLocalArchive(archiveInstance, backup)
    updateRestoreOperation(backup, 'validating', {
      ...target,
      label: "Validation de l'archive",
      percent: 18,
      sourceSizeBytes: sourceBytes,
      compressedBytes: compressedBytes || fileSize(archivePath),
    })
    validateArchiveListing(archivePath, filename, true)
  } catch (error) {
    updateRestoreOperation(backup, 'failed', {
      ...target,
      label: 'Restauration échouée',
      percent: 100,
      sourceSizeBytes: sourceBytes,
      compressedBytes: compressedBytes || (archivePath ? fileSize(archivePath) : 0),
      error: errorMessage(error),
    })
    throw error
  }

  const extractDir = `${instanceRoot(instance.id)}/.restore-extract-${backup.id}`
  const normalizedDir = `${instanceRoot(instance.id)}/.restore-normalized-${backup.id}`
  $os.mkdirAll(instanceRoot(instance.id), DIR_MODE)
  $os.removeAll(extractDir)
  $os.removeAll(normalizedDir)
  $os.mkdirAll(extractDir, PRIVATE_DIR_MODE)

  try {
    const resourceSettings = restoreResourceSettings()
    updateRestoreOperation(backup, 'extracting', {
      ...target,
      label:
        resourceSettings.cpuLimitPercent > 0
          ? `Extraction limitée à ${resourceSettings.cpuLimitPercent} % CPU`
          : "Extraction de l'archive",
      percent: 32,
      sourceSizeBytes: sourceBytes,
      compressedBytes: compressedBytes || fileSize(archivePath),
    })
    extractArchive(archivePath, filename, extractDir)

    updateRestoreOperation(backup, 'normalizing', {
      ...target,
      label: 'Préparation des dossiers restaurés',
      percent: 68,
      sourceSizeBytes: sourceBytes,
      compressedBytes: compressedBytes || fileSize(archivePath),
    })
    const contentRoot = findArchiveContentRoot(extractDir, normalizedDir)
    ensureRestorableDirs(contentRoot)
    const manifest = readManifest(contentRoot, backup)

    updateRestoreOperation(backup, 'replacing', {
      ...target,
      label: "Remplacement des fichiers de l'instance",
      percent: 78,
      sourceSizeBytes: Number(manifest.sourceSizeBytes || sourceBytes || 0),
      compressedBytes: compressedBytes || fileSize(archivePath),
    })
    restoreExtractedDirs(instance, contentRoot)

    updateRestoreOperation(backup, 'finalizing', {
      ...target,
      label: 'Finalisation de la restauration',
      percent: 92,
      sourceSizeBytes: Number(manifest.sourceSizeBytes || sourceBytes || 0),
      compressedBytes: compressedBytes || fileSize(archivePath),
    })
    if (manifest.instance?.version) {
      const current = findInstance(instance.id)
      current.set('version', manifest.instance.version)
      $app.save(current)
    }

    if (options.markReady !== false) {
      updateRestoreOperation(backup, 'ready', {
        ...target,
        label: 'Restauration terminée',
        percent: 100,
        sourceSizeBytes: Number(manifest.sourceSizeBytes || sourceBytes || 0),
        compressedBytes: compressedBytes || fileSize(archivePath),
      })
    }
  } catch (error) {
    updateRestoreOperation(backup, 'failed', {
      ...target,
      label: 'Restauration échouée',
      percent: 100,
      sourceSizeBytes: sourceBytes,
      compressedBytes: compressedBytes || (archivePath ? fileSize(archivePath) : 0),
      error: errorMessage(error),
    })
    throw error
  } finally {
    try {
      $os.removeAll(extractDir)
    } catch {}
    try {
      $os.removeAll(normalizedDir)
    } catch {}
  }
}

const createRestoredInstanceFromBackup = (
  source: core.Record,
  authRecord: core.Record,
  backup: core.Record,
  e: core.RequestEvent
) => {
  const { subdomain } = readRestoreNewInput(e, source)
  updateRestoreOperation(backup, 'creating', {
    mode: 'new-instance',
    targetSubdomain: subdomain,
    label: 'Création de la nouvelle instance',
    percent: 8,
    sourceSizeBytes: Number(backup.get('sizeBytes') || 0),
    compressedBytes: Number(backup.get('compressedBytes') || 0),
  })

  const collection = $app.findCollectionByNameOrId('instances')
  const target = new Record(collection)

  target.set('uid', authRecord.id)
  target.set('subdomain', subdomain)
  target.set('status', 'idle')
  target.set('power', false)
  target.set('version', source.getString('version'))
  target.set('dev', false)
  target.set('syncAdmin', source.getBool('syncAdmin'))
  target.set('autoVacuum', source.getBool('autoVacuum'))
  target.set('secrets', source.get('secrets'))
  target.set('webhooks', source.get('webhooks'))

  try {
    $app.save(target)

    // The create hook defaults autoVacuum. Save once more so the target preserves the source option.
    target.set('autoVacuum', source.getBool('autoVacuum'))
    $app.save(target)

    updateRestoreOperation(backup, 'created', {
      mode: 'new-instance',
      targetInstanceId: target.id,
      targetSubdomain: subdomain,
      label: 'Nouvelle instance créée',
      percent: 14,
      sourceSizeBytes: Number(backup.get('sizeBytes') || 0),
      compressedBytes: Number(backup.get('compressedBytes') || 0),
    })
    restoreArchive(target, backup, source, {
      mode: 'new-instance',
      targetInstanceId: target.id,
      targetSubdomain: subdomain,
    })
    return target
  } catch (error) {
    updateRestoreOperation(backup, 'failed', {
      mode: 'new-instance',
      targetInstanceId: target.id || '',
      targetSubdomain: subdomain,
      label: 'Restauration échouée',
      percent: 100,
      sourceSizeBytes: Number(backup.get('sizeBytes') || 0),
      compressedBytes: Number(backup.get('compressedBytes') || 0),
      error: errorMessage(error),
    })
    try {
      if (target.id) $app.delete(target)
    } catch {}
    try {
      if (target.id) $os.removeAll(instanceRoot(target.id))
    } catch {}
    throw new ApiError(500, 'Impossible de restaurer vers une nouvelle instance.', { error })
  }
}

const importBackupFromServerPath = (
  instance: core.Record,
  authRecord: core.Record,
  serverPath: string
): ImportedArchive => {
  const source = assertServerImportAllowed(authRecord, serverPath)
  const filename = createImportBackupFilename(instance, basename(source))
  const dir = backupDir(instance.id)
  const finalPath = backupPath(instance.id, filename)
  const tmpPath = `${finalPath}.tmp`

  assertSafeBackupFilename(filename)
  $os.mkdirAll(dir, DIR_MODE)
  $os.removeAll(tmpPath)

  try {
    runCommand('cp', source, tmpPath)
    validateArchiveListing(tmpPath, filename)
    $os.rename(tmpPath, finalPath)
  } catch (error) {
    try {
      $os.remove(tmpPath)
    } catch {}
    throw error
  }

  return { filename, localPath: finalPath }
}

const importBackupFromUpload = (instance: core.Record, uploaded: filesystem.File): ImportedArchive => {
  const originalName = uploaded.originalName || uploaded.name || 'archive.zip'
  const filename = createImportBackupFilename(instance, originalName)
  const dir = backupDir(instance.id)
  const finalPath = backupPath(instance.id, filename)
  const tmpName = `${filename}.tmp`
  const tmpPath = `${dir}/${tmpName}`

  assertSafeBackupFilename(filename)
  $os.mkdirAll(dir, DIR_MODE)
  $os.removeAll(tmpPath)

  const fs = $filesystem.local(dir)
  try {
    fs.uploadFile(uploaded, tmpName)
    validateArchiveListing(tmpPath, filename)
    $os.rename(tmpPath, finalPath)
  } catch (error) {
    try {
      $os.remove(tmpPath)
    } catch {}
    throw error
  } finally {
    fs.close()
  }

  return { filename, localPath: finalPath }
}

const readServerPathInput = (e: core.RequestEvent) => {
  try {
    const value = e.request.formValue('serverPath')
    if (value) return value
  } catch {}

  try {
    let data = new DynamicModel({
      serverPath: '',
    })
    e.bindBody(data)
    data = JSON.parse(JSON.stringify(data))
    return data.serverPath || ''
  } catch {}

  return ''
}

const readRestoreNewInput = (e: core.RequestEvent, source: core.Record) => {
  let data = new DynamicModel({
    subdomain: '',
  }) as { subdomain?: string }

  try {
    e.bindBody(data)
    data = JSON.parse(JSON.stringify(data))
  } catch {
    data = { subdomain: '' }
  }

  const requested = (data.subdomain || '').trim().toLowerCase()
  const subdomain = requested || suggestRestoreSubdomain(source.getString('subdomain'))
  assertValidSubdomain(subdomain)
  if (subdomainExists(subdomain)) throw new BadRequestError('Ce nom de nouvelle instance est deja utilise.')
  return { subdomain }
}

const backupManifestObject = (backup: core.Record) => {
  return recordObject(backup.get('manifest')) as Record<string, unknown>
}

export const refreshImportedBackupSizeMetadata = (backup: core.Record) => {
  if (backup.getString('kind') !== 'import') return backup
  if (backup.getString('status') !== 'ready') return backup

  const manifest = backupManifestObject(backup)
  if (manifest.sourceSizeComputedAt) return backup

  const filename = backup.getString('filename')
  if (!filename) return backup

  try {
    assertSafeBackupFilename(filename)
    const localPath = backupPath(backup.getString('instance'), filename)
    if (!pathExists(localPath)) return backup

    const compressedBytes = fileSize(localPath)
    const sourceBytes = archiveSourceSizeBytes(localPath, filename) || compressedBytes
    backup.set('sizeBytes', sourceBytes)
    backup.set('compressedBytes', compressedBytes)
    backup.set('manifest', {
      ...manifest,
      sourceSizeBytes: sourceBytes,
      compressedSizeBytes: compressedBytes,
      sourceSizeComputedAt: new Date().toISOString(),
    })
    $app.save(backup)
  } catch {}

  return backup
}

const createImportedBackupFromImporter = (
  instance: core.Record,
  authRecord: core.Record,
  importer: () => ImportedArchive | null
) => {
  assertBackupImportAllowed(authRecord)
  assertNoRunningOperation(instance.id)

  const backup = createBackupRecord(instance, authRecord, 'import')

  try {
    const imported = importer()

    if (!imported) {
      throw new BadRequestError('Archive manquante. Envoyez un fichier archive ou renseignez un chemin serveur.')
    }

    const compressedBytes = fileSize(imported.localPath)
    const entries = listArchiveEntries(imported.localPath, imported.filename)
    const sourceBytes = archiveSourceSizeBytes(imported.localPath, imported.filename) || compressedBytes
    markBackupReady(backup, {
      filename: imported.filename,
      localPath: imported.localPath,
      sizeBytes: sourceBytes,
      compressedBytes,
      checksum: sha256(imported.localPath),
      manifest: {
        format: BACKUP_FORMAT,
        imported: true,
        createdAt: new Date().toISOString(),
        included: archiveIncludedDirs(entries),
        sourceSizeBytes: sourceBytes,
        compressedSizeBytes: compressedBytes,
        sourceSizeComputedAt: new Date().toISOString(),
      },
    })

    return backup
  } catch (error) {
    markBackupFailed(backup, error)
    throw error
  }
}

const createImportedBackup = (instance: core.Record, authRecord: core.Record, e: core.RequestEvent) => {
  return createImportedBackupFromImporter(instance, authRecord, () => {
    const serverPath = readServerPathInput(e)
    const uploaded = !serverPath
      ? e.findUploadedFiles('archive').filter((file): file is filesystem.File => !!file)[0]
      : null

    return serverPath
      ? importBackupFromServerPath(instance, authRecord, serverPath)
      : uploaded
        ? importBackupFromUpload(instance, uploaded)
        : null
  })
}

const createImportedBackupFromServerArchive = (instance: core.Record, authRecord: core.Record, serverPath: string) => {
  return createImportedBackupFromImporter(instance, authRecord, () =>
    importBackupFromServerPath(instance, authRecord, serverPath)
  )
}

const readChunkStartInput = (e: core.RequestEvent) => {
  let data = new DynamicModel({
    filename: '',
    size: 0,
    chunkSize: 0,
  })
  e.bindBody(data)
  return JSON.parse(JSON.stringify(data)) as {
    filename?: string
    size?: number | string
    chunkSize?: number | string
  }
}

const normalizeChunkSize = (requested: number) => {
  if (!Number.isFinite(requested) || requested <= 0) return DEFAULT_IMPORT_CHUNK_SIZE_BYTES
  return Math.max(MIN_IMPORT_CHUNK_SIZE_BYTES, Math.min(Math.floor(requested), DEFAULT_IMPORT_CHUNK_SIZE_BYTES))
}

const startChunkSession = (instance: core.Record, authRecord: core.Record, e: core.RequestEvent) => {
  assertBackupImportAllowed(authRecord)
  assertNoRunningOperation(instance.id)

  const input = readChunkStartInput(e)
  const filename = `${input.filename || ''}`.trim() || 'archive.zip'
  extensionForImport(filename)

  const size = parsePositiveInteger(input.size, 'Taille du fichier')
  const chunkSize = normalizeChunkSize(Number(input.chunkSize || DEFAULT_IMPORT_CHUNK_SIZE_BYTES))
  const totalChunks = Math.ceil(size / chunkSize)
  if (totalChunks <= 0 || totalChunks > MAX_IMPORT_CHUNKS) {
    throw new BadRequestError('Nombre de morceaux invalide.')
  }

  const uploadId = $security.randomStringWithAlphabet(24, 'abcdefghijklmnopqrstuvwxyz0123456789')
  const session: ChunkSession = {
    instanceId: instance.id,
    userId: authRecord.id,
    filename,
    size,
    chunkSize,
    totalChunks,
    createdAt: new Date().toISOString(),
  }

  const sessionDir = chunkSessionDir(instance.id, uploadId)
  $os.mkdirAll(chunkPartsDir(instance.id, uploadId), PRIVATE_DIR_MODE)
  $os.writeFile(chunkMetaPath(instance.id, uploadId), JSON.stringify(session, null, 2), PRIVATE_FILE_MODE)

  return { uploadId, session, sessionDir }
}

const readChunkSession = (instanceId: string, uploadId: string) => {
  assertSafeInstanceId(instanceId)
  assertSafeUploadId(uploadId)

  try {
    const raw = toString($os.readFile(chunkMetaPath(instanceId, uploadId)))
    const session = JSON.parse(raw) as ChunkSession
    if (session.instanceId !== instanceId) throw new Error('Instance mismatch')
    extensionForImport(session.filename)
    parsePositiveInteger(session.size, 'Taille du fichier')
    parsePositiveInteger(session.chunkSize, 'Taille de morceau')
    parsePositiveInteger(session.totalChunks, 'Nombre de morceaux')
    return session
  } catch {
    throw new BadRequestError("Session d'upload introuvable ou invalide.")
  }
}

const assertChunkSessionOwner = (session: ChunkSession, authRecord: core.Record) => {
  if (session.userId !== authRecord.id) {
    throw new BadRequestError("Session d'upload non autorisee.")
  }
}

const expectedChunkBytes = (session: ChunkSession, index: number) => {
  if (index === session.totalChunks - 1) {
    return session.size - session.chunkSize * (session.totalChunks - 1)
  }
  return session.chunkSize
}

const uploadedChunkCount = (instanceId: string, uploadId: string, totalChunks: number) => {
  let count = 0
  for (let index = 0; index < totalChunks; index++) {
    if (pathExists(`${chunkPartsDir(instanceId, uploadId)}/${chunkPartFilename(index)}`)) count++
  }
  return count
}

const storeChunk = (instance: core.Record, authRecord: core.Record, uploadId: string, e: core.RequestEvent) => {
  const session = readChunkSession(instance.id, uploadId)
  assertChunkSessionOwner(session, authRecord)
  assertBackupImportAllowed(authRecord)

  const rawIndex = e.request.formValue('index')
  const index = parseNonNegativeInteger(rawIndex, 'Index de morceau')
  if (index >= session.totalChunks) throw new BadRequestError('Index de morceau hors limite.')

  const uploaded = e.findUploadedFiles('chunk').filter((file): file is filesystem.File => !!file)[0]
  if (!uploaded) throw new BadRequestError('Morceau manquant.')

  const partsDir = chunkPartsDir(instance.id, uploadId)
  const partFilename = chunkPartFilename(index)
  const partPath = `${partsDir}/${partFilename}`
  $os.mkdirAll(partsDir, PRIVATE_DIR_MODE)
  try {
    $os.remove(partPath)
  } catch {}

  const fs = $filesystem.local(partsDir)
  try {
    fs.uploadFile(uploaded, partFilename)
  } finally {
    fs.close()
  }

  const uploadedBytes = fileSize(partPath)
  const expectedBytes = expectedChunkBytes(session, index)
  if (uploadedBytes !== expectedBytes) {
    try {
      $os.remove(partPath)
    } catch {}
    throw new BadRequestError('Taille de morceau invalide.')
  }

  return {
    index,
    uploadedBytes,
    uploadedChunks: uploadedChunkCount(instance.id, uploadId, session.totalChunks),
    totalChunks: session.totalChunks,
  }
}

const assertAllChunksPresent = (instanceId: string, uploadId: string, session: ChunkSession) => {
  let totalBytes = 0
  for (let index = 0; index < session.totalChunks; index++) {
    const partPath = `${chunkPartsDir(instanceId, uploadId)}/${chunkPartFilename(index)}`
    if (!pathExists(partPath)) {
      throw new BadRequestError(`Morceau ${index + 1}/${session.totalChunks} manquant.`)
    }
    const partBytes = fileSize(partPath)
    const expectedBytes = expectedChunkBytes(session, index)
    if (partBytes !== expectedBytes) {
      throw new BadRequestError(`Morceau ${index + 1}/${session.totalChunks} invalide.`)
    }
    totalBytes += partBytes
  }

  if (totalBytes !== session.size) {
    throw new BadRequestError('Taille totale assemblee invalide.')
  }
}

const assembleChunkSessionArchive = (instance: core.Record, uploadId: string, session: ChunkSession) => {
  assertAllChunksPresent(instance.id, uploadId, session)

  const filename = createImportBackupFilename(instance, session.filename)
  const dir = assembledImportDir(instance.id)
  const finalPath = `${dir}/${uploadId}-${filename}`
  const tmpPath = `${finalPath}.tmp`

  assertSafeBackupFilename(filename)
  $os.mkdirAll(dir, PRIVATE_DIR_MODE)
  $os.removeAll(tmpPath)
  $os.removeAll(finalPath)

  try {
    runCommand(
      'sh',
      '-c',
      'set -e; : > "$3"; i=0; while [ "$i" -lt "$2" ]; do part=$(printf "%s/%08d.part" "$1" "$i"); cat "$part" >> "$3"; i=$((i + 1)); done',
      'sh',
      chunkPartsDir(instance.id, uploadId),
      `${session.totalChunks}`,
      tmpPath
    )

    if (fileSize(tmpPath) !== session.size) {
      throw new BadRequestError('Archive assemblee invalide.')
    }

    $os.rename(tmpPath, finalPath)
    return finalPath
  } catch (error) {
    try {
      $os.remove(tmpPath)
    } catch {}
    throw error
  }
}

const completeChunkSession = (instance: core.Record, authRecord: core.Record, uploadId: string) => {
  const session = readChunkSession(instance.id, uploadId)
  assertChunkSessionOwner(session, authRecord)
  assertBackupImportAllowed(authRecord)

  let assembledPath = ''
  try {
    assembledPath = assembleChunkSessionArchive(instance, uploadId, session)
    return createImportedBackupFromServerArchive(instance, authRecord, assembledPath)
  } finally {
    if (assembledPath) {
      try {
        $os.remove(assembledPath)
      } catch {}
    }
    try {
      $os.removeAll(chunkSessionDir(instance.id, uploadId))
    } catch {}
  }
}

const cancelChunkSession = (instance: core.Record, authRecord: core.Record, uploadId: string) => {
  const session = readChunkSession(instance.id, uploadId)
  assertChunkSessionOwner(session, authRecord)
  assertBackupImportAllowed(authRecord)
  $os.removeAll(chunkSessionDir(instance.id, uploadId))
}

export const HandleInstanceBackupCreate = (e: core.RequestEvent) => {
  const log = mkLog('POST:instance:backup')
  const authRecord = requireAuthRecord(e.auth)
  const instance = findInstance(pathValue(e, 'id'))
  assertInstanceAccess(instance, authRecord)

  const backup = createBackupForInstance(instance, authRecord, 'manual', true)
  log(`created ${backup.id} for ${instance.id}`)

  return e.json(200, { backup: serializeInstanceBackup(backup) })
}

export const HandleInstanceBackupImport = (e: core.RequestEvent) => {
  const log = mkLog('POST:instance:backup:import')
  const authRecord = requireAuthRecord(e.auth)
  const instance = findInstance(pathValue(e, 'id'))
  assertInstanceAccess(instance, authRecord)

  const backup = createImportedBackup(instance, authRecord, e)
  log(`imported ${backup.id} for ${instance.id}`)

  return e.json(200, { backup: serializeInstanceBackup(backup) })
}

export const HandleInstanceBackupChunkedStart = (e: core.RequestEvent) => {
  const authRecord = requireAuthRecord(e.auth)
  const instance = findInstance(pathValue(e, 'id'))
  assertInstanceAccess(instance, authRecord)

  const { uploadId, session } = startChunkSession(instance, authRecord, e)
  return e.json(200, {
    uploadId,
    chunkSize: session.chunkSize,
    totalChunks: session.totalChunks,
  })
}

export const HandleInstanceBackupChunkedUpload = (e: core.RequestEvent) => {
  const authRecord = requireAuthRecord(e.auth)
  const instance = findInstance(pathValue(e, 'id'))
  assertInstanceAccess(instance, authRecord)

  const result = storeChunk(instance, authRecord, pathValue(e, 'uploadId'), e)
  return e.json(200, result)
}

export const HandleInstanceBackupChunkedComplete = (e: core.RequestEvent) => {
  const log = mkLog('POST:instance:backup:import:chunked:complete')
  const authRecord = requireAuthRecord(e.auth)
  const instance = findInstance(pathValue(e, 'id'))
  assertInstanceAccess(instance, authRecord)

  const backup = completeChunkSession(instance, authRecord, pathValue(e, 'uploadId'))
  log(`imported ${backup.id} for ${instance.id} from chunked upload`)

  return e.json(200, { backup: serializeInstanceBackup(backup) })
}

export const HandleInstanceBackupChunkedCancel = (e: core.RequestEvent) => {
  const authRecord = requireAuthRecord(e.auth)
  const instance = findInstance(pathValue(e, 'id'))
  assertInstanceAccess(instance, authRecord)

  cancelChunkSession(instance, authRecord, pathValue(e, 'uploadId'))
  return e.json(200, { status: 'ok' })
}

export const HandleInstanceBackupsList = (e: core.RequestEvent) => {
  const authRecord = requireAuthRecord(e.auth)
  const instance = findInstance(pathValue(e, 'id'))
  assertInstanceAccess(instance, authRecord)

  const backups = findInstanceBackups(instance.id).map(refreshImportedBackupSizeMetadata).map(serializeInstanceBackup)

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

  let power: ManagedPower = { shouldRestart: false }

  try {
    updateRestoreOperation(backup, 'stopping', {
      mode: 'in-place',
      targetInstanceId: instance.id,
      targetSubdomain: instance.getString('subdomain'),
      label: instance.getBool('power') ? "Arrêt de l'instance avant restauration" : "Vérification de l'instance",
      percent: 4,
      sourceSizeBytes: Number(backup.get('sizeBytes') || 0),
      compressedBytes: Number(backup.get('compressedBytes') || 0),
    })
    power = stopForFilesystemOperation(instance)
    updateRestoreOperation(backup, 'safety-backup', {
      mode: 'in-place',
      targetInstanceId: instance.id,
      targetSubdomain: instance.getString('subdomain'),
      label: 'Sauvegarde de sécurité avant restauration',
      percent: 8,
      sourceSizeBytes: Number(backup.get('sizeBytes') || 0),
      compressedBytes: Number(backup.get('compressedBytes') || 0),
    })
    createBackupForInstance(findInstance(instance.id), authRecord, 'pre-restore', false, true)
    restoreArchive(findInstance(instance.id), backup, instance, {
      mode: 'in-place',
      targetInstanceId: instance.id,
      targetSubdomain: instance.getString('subdomain'),
      markReady: !power.shouldRestart,
    })

    if (power.shouldRestart) {
      updateRestoreOperation(backup, 'restarting', {
        mode: 'in-place',
        targetInstanceId: instance.id,
        targetSubdomain: instance.getString('subdomain'),
        label: "Redémarrage de l'instance",
        percent: 96,
        sourceSizeBytes: Number(backup.get('sizeBytes') || 0),
        compressedBytes: Number(backup.get('compressedBytes') || 0),
      })
      restartIfNeeded(instance.id, power)
      updateRestoreOperation(backup, 'ready', {
        mode: 'in-place',
        targetInstanceId: instance.id,
        targetSubdomain: instance.getString('subdomain'),
        label: 'Restauration terminée',
        percent: 100,
        sourceSizeBytes: Number(backup.get('sizeBytes') || 0),
        compressedBytes: Number(backup.get('compressedBytes') || 0),
      })
    }

    log(`restored ${backup.id} into ${instance.id}`)
  } catch (error) {
    updateRestoreOperation(backup, 'failed', {
      mode: 'in-place',
      targetInstanceId: instance.id,
      targetSubdomain: instance.getString('subdomain'),
      label: 'Restauration échouée',
      percent: 100,
      sourceSizeBytes: Number(backup.get('sizeBytes') || 0),
      compressedBytes: Number(backup.get('compressedBytes') || 0),
      error: errorMessage(error),
    })
    throw error
  }

  return e.json(200, { status: 'ok' })
}

export const HandleInstanceBackupRestoreNew = (e: core.RequestEvent) => {
  const log = mkLog('POST:instance:backup:restore:new')
  const authRecord = requireAuthRecord(e.auth)
  const instance = findInstance(pathValue(e, 'id'))
  assertInstanceAccess(instance, authRecord)

  const backup = getBackupRecord(instance, pathValue(e, 'backupId'))
  if (backup.getString('status') !== 'ready') {
    throw new BadRequestError("Cette sauvegarde n'est pas prete.")
  }

  const target = createRestoredInstanceFromBackup(instance, authRecord, backup, e)
  log(`restored ${backup.id} from ${instance.id} into new instance ${target.id}`)

  return e.json(200, { instance: target })
}
