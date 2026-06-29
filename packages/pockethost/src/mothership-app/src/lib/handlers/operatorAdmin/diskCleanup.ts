const SAFE_INSTANCE_ID = /^[a-z0-9]+$/

export type DiskCleanupKind = 'instance-data' | 'backup-data' | 'import-data'

export type DiskCleanupEntry = {
  id: string
  kind: DiskCleanupKind
  path: string
  sizeBytes: number
  hasRecord: boolean
  hasContainer: boolean
  removed: boolean
  error: string
}

export type DiskCleanupResult = {
  scannedAt: string
  dataRoot: string
  orphanCount: number
  totalBytes: number
  removedCount: number
  freedBytes: number
  entries: DiskCleanupEntry[]
}

const dataRoot = () => {
  const envRoot = $os.getenv('DATA_ROOT')
  if (envRoot) return envRoot

  const appDataDir = `${$app.dataDir()}`
  const inferred = appDataDir.replace(/\/mothership\/pb_data\/?$/, '')
  if (inferred !== appDataDir) return inferred

  throw new Error('Impossible de trouver le dossier de donnees des instances.')
}

const instanceRoot = (id: string) => `${dataRoot()}/instances/${id}`
const backupRoot = () => $os.getenv('INSTANCE_BACKUP_ROOT') || `${dataRoot()}/backups/instances`
const importRoot = () => $os.getenv('INSTANCE_IMPORT_ROOT') || `${dataRoot()}/imports`

const pathExists = (path: string) => {
  try {
    $os.stat(path)
    return true
  } catch {
    return false
  }
}

const runCommand = (name: string, ...args: string[]) => toString($os.cmd(name, ...args).combinedOutput()).trim()
const basename = (path: string) => path.replace(/\/+$/g, '').split('/').pop() || ''

const assertSafeInstanceId = (id: string) => {
  if (!SAFE_INSTANCE_ID.test(id)) {
    throw new BadRequestError("Identifiant d'instance invalide.")
  }
}

const directorySizeBytes = (path: string) => {
  if (!pathExists(path)) return 0
  try {
    const output = runCommand('du', '-sb', path)
    const value = Number(output.split(/\s+/)[0] || 0)
    return Number.isFinite(value) ? Math.max(0, value) : 0
  } catch {
    return 0
  }
}

const listChildDirs = (root: string) => {
  if (!pathExists(root)) return []
  try {
    return runCommand('find', root, '-mindepth', '1', '-maxdepth', '1', '-type', 'd', '-print')
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
  } catch {
    return []
  }
}

const dockerContainerNames = () => {
  try {
    return new Set(
      runCommand('docker', 'ps', '-a', '--format', '{{.Names}}')
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean)
    )
  } catch {
    return new Set<string>()
  }
}

const listInstanceRecordIds = () => {
  const ids = new Set<string>()
  let offset = 0
  const pageSize = 500

  for (;;) {
    const records = $app.findRecordsByFilter('instances', 'id != ""', '', pageSize, offset)
    for (const record of records) ids.add(record.id)
    if (records.length < pageSize) break
    offset += pageSize
  }

  return ids
}

const removeDirectory = (path: string) => {
  if (!pathExists(path)) return false
  $os.removeAll(path)
  return true
}

const scanRoot = (
  root: string,
  kind: DiskCleanupKind,
  recordIds: Set<string>,
  containers: Set<string>,
  remove: boolean,
  requirePbData = false
) => {
  const entries: DiskCleanupEntry[] = []

  for (const path of listChildDirs(root)) {
    const id = basename(path)
    if (!SAFE_INSTANCE_ID.test(id)) continue
    if (requirePbData && !pathExists(`${path}/pb_data`)) continue

    const hasRecord = recordIds.has(id)
    const hasContainer = containers.has(id)
    if (hasRecord || hasContainer) continue

    const entry: DiskCleanupEntry = {
      id,
      kind,
      path,
      sizeBytes: directorySizeBytes(path),
      hasRecord,
      hasContainer,
      removed: false,
      error: '',
    }

    if (remove) {
      try {
        removeDirectory(path)
        entry.removed = true
      } catch (error) {
        entry.error = error instanceof Error ? error.message : `${error}`
      }
    }

    entries.push(entry)
  }

  return entries
}

export const scanOrphanInstanceStorage = (remove = false): DiskCleanupResult => {
  const recordIds = listInstanceRecordIds()
  const containers = dockerContainerNames()
  const entries = [
    ...scanRoot(`${dataRoot()}/instances`, 'instance-data', recordIds, containers, remove, true),
    ...scanRoot(backupRoot(), 'backup-data', recordIds, containers, remove),
    ...scanRoot(`${importRoot()}/assembled`, 'import-data', recordIds, containers, remove),
    ...scanRoot(`${importRoot()}/.chunked`, 'import-data', recordIds, containers, remove),
  ]

  return {
    scannedAt: new Date().toISOString(),
    dataRoot: dataRoot(),
    orphanCount: entries.length,
    totalBytes: entries.reduce((total, entry) => total + entry.sizeBytes, 0),
    removedCount: entries.filter((entry) => entry.removed).length,
    freedBytes: entries.filter((entry) => entry.removed).reduce((total, entry) => total + entry.sizeBytes, 0),
    entries,
  }
}

export const removeInstanceLocalStorage = (id: string) => {
  assertSafeInstanceId(id)

  const targets = [
    instanceRoot(id),
    `${backupRoot()}/${id}`,
    `${importRoot()}/assembled/${id}`,
    `${importRoot()}/.chunked/${id}`,
  ]
  let removedCount = 0
  let freedBytes = 0
  const errors: string[] = []

  for (const target of targets) {
    try {
      const bytes = directorySizeBytes(target)
      if (removeDirectory(target)) {
        removedCount++
        freedBytes += bytes
      }
    } catch (error) {
      errors.push(error instanceof Error ? error.message : `${error}`)
    }
  }

  return { removedCount, freedBytes, errors }
}
