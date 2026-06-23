const assertSafeInstanceId = (id: string) => {
  if (!id.match(/^[a-z0-9]+$/)) {
    throw new BadRequestError("Identifiant d'instance invalide.")
  }
}

const dataRoot = () => {
  const envRoot = $os.getenv('DATA_ROOT')
  if (envRoot) return envRoot

  const appDataDir = `${$app.dataDir()}`
  const inferred = appDataDir.replace(/\/mothership\/pb_data\/?$/, '')
  if (inferred !== appDataDir) return inferred

  throw new Error("Impossible de trouver le dossier de donnees des instances.")
}

const instanceRoot = (id: string) => `${dataRoot()}/instances/${id}`

const requireAuthRecord = (authRecord?: core.Record) => {
  if (!authRecord) throw new BadRequestError('Session utilisateur attendue.')
  return authRecord
}

const findInstance = (id: string) => {
  assertSafeInstanceId(id)
  const instance = $app.findRecordById('instances', id)
  if (!instance) throw new BadRequestError(`Instance ${id} introuvable.`)
  return instance
}

const assertInstanceAccess = (instance: core.Record, authRecord: core.Record) => {
  if (instance.getString('uid') !== authRecord.id && !authRecord.getBool('superAdmin')) {
    throw new BadRequestError('Non autorise.')
  }
}

const pathValue = (e: core.RequestEvent, name: string) => {
  if (!e.request) throw new BadRequestError('Requete invalide.')
  return e.request.pathValue(name)
}

const serializeBackup = (backup: core.Record) => ({
  id: backup.id,
  kind: backup.getString('kind'),
  status: backup.getString('status'),
  filename: backup.getString('filename'),
  remoteKey: backup.getString('remoteKey'),
  sizeBytes: Number(backup.get('sizeBytes') || 0),
  compressedBytes: Number(backup.get('compressedBytes') || 0),
  error: backup.getString('error'),
  remoteError: backup.getString('remoteError'),
  created: backup.getString('created'),
  updated: backup.getString('updated'),
})

const getDirectorySizeBytes = (path: string) => {
  try {
    const output = toString($os.cmd('du', '-sb', path).combinedOutput()).trim()
    const value = Number(output.split(/\s+/)[0] || 0)
    return Number.isFinite(value) ? value : null
  } catch {
    return null
  }
}

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

export const HandleInstanceOverview = (e: core.RequestEvent) => {
  const authRecord = requireAuthRecord(e.auth)
  const instance = findInstance(pathValue(e, 'id'))
  assertInstanceAccess(instance, authRecord)

  const backups = findInstanceBackups(instance.id).map(serializeBackup)
  const totalCompressedBytes = backups.reduce((total, backup) => total + backup.compressedBytes, 0)

  return e.json(200, {
    instance,
    backups: {
      count: backups.length,
      readyCount: backups.filter((backup) => backup.status === 'ready').length,
      runningCount: backups.filter((backup) => backup.status === 'running').length,
      failedCount: backups.filter((backup) => backup.status === 'failed').length,
      totalCompressedBytes,
      latest: backups[0] || null,
    },
    storage: {
      instanceBytes: getDirectorySizeBytes(instanceRoot(instance.id)),
    },
    collectedAt: new Date().toISOString(),
  })
}
