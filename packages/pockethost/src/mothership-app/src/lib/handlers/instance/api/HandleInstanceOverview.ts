import { refreshImportedBackupSizeMetadata, serializeInstanceBackup } from './HandleInstanceBackups'

type DockerStatsRow = {
  BlockIO?: string
  CPUPerc?: string
  MemPerc?: string
  MemUsage?: string
  Name?: string
}

export type InstanceResourceMetrics = {
  instanceId: string
  cpuPercent: number | null
  memoryBytes: number | null
  memoryLimitBytes: number | null
  memoryPercent: number | null
  diskBytes: number | null
  blockReadBytes: number | null
  blockWriteBytes: number | null
  containerName: string
}

const INSTANCE_RESOURCE_METRICS_COLLECTION = 'instance_resource_metrics'
const INSTANCE_METRICS_RETENTION_MS = 7 * 24 * 60 * 60 * 1000
const INSTANCE_METRICS_QUERY_LIMIT = 12_000

const historyRanges = {
  '30m': { durationMs: 30 * 60 * 1000, bucketMs: 60 * 1000 },
  '6h': { durationMs: 6 * 60 * 60 * 1000, bucketMs: 60 * 1000 },
  '24h': { durationMs: 24 * 60 * 60 * 1000, bucketMs: 2 * 60 * 1000 },
  '7d': { durationMs: INSTANCE_METRICS_RETENTION_MS, bucketMs: 10 * 60 * 1000 },
} as const

const formatPocketBaseDate = (timestamp: number) => new Date(timestamp).toISOString().replace('T', ' ')

type InstanceMetricHistoryRange = keyof typeof historyRanges
type StoredInstanceMetricPoint = {
  timestamp: number
  cpuPercent: number
  memoryBytes: number
  memoryLimitBytes: number
  memoryPercent: number
}

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

  throw new Error('Impossible de trouver le dossier de donnees des instances.')
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

const getDirectorySizeBytes = (path: string) => {
  try {
    const output = toString($os.cmd('du', '-sb', path).combinedOutput()).trim()
    const value = Number(output.split(/\s+/)[0] || 0)
    return Number.isFinite(value) ? value : null
  } catch {
    return null
  }
}

const parseDockerPercent = (value?: string) => {
  const normalized = `${value || ''}`.trim().replace('%', '').replace(',', '.')
  const number = Number(normalized)
  return Number.isFinite(number) ? Math.max(0, number) : null
}

const dockerByteUnits: Record<string, number> = {
  b: 1,
  kb: 1000,
  mb: 1000 ** 2,
  gb: 1000 ** 3,
  tb: 1000 ** 4,
  kib: 1024,
  mib: 1024 ** 2,
  gib: 1024 ** 3,
  tib: 1024 ** 4,
}

const parseDockerBytes = (value?: string) => {
  const match = `${value || ''}`
    .trim()
    .replace(',', '.')
    .match(/^([0-9.]+)\s*([a-zA-Z]+)$/)
  if (!match) return null

  const number = Number(match[1])
  const unit = match[2].toLowerCase()
  const factor = dockerByteUnits[unit]
  if (!Number.isFinite(number) || !factor) return null
  return Math.round(number * factor)
}

const parseDockerBytePair = (value?: string): [number | null, number | null] => {
  const parts = `${value || ''}`.split('/').map((part) => part.trim())
  return [parseDockerBytes(parts[0]), parseDockerBytes(parts[1])]
}

const readDockerStatsByName = (containerNames: string[] = []) => {
  const rows = new Map<string, DockerStatsRow>()

  try {
    const output = toString(
      $os.cmd('docker', 'stats', '--no-stream', '--format', '{{json .}}', ...containerNames).combinedOutput()
    ).trim()
    if (!output) return rows

    for (const line of output.split('\n')) {
      const trimmed = line.trim()
      if (!trimmed) continue

      try {
        const row = JSON.parse(trimmed) as DockerStatsRow
        const name = `${row.Name || ''}`
        if (name) rows.set(name, row)
      } catch {
        // Ignore one malformed stats line instead of hiding all other metrics.
      }
    }
  } catch {
    return rows
  }

  return rows
}

const serializeInstanceRuntimeMetrics = (instance: core.Record, dockerStatsByName: Map<string, DockerStatsRow>) => {
  const row = dockerStatsByName.get(instance.id)
  const [memoryBytes, memoryLimitBytes] = parseDockerBytePair(row?.MemUsage)
  const [blockReadBytes, blockWriteBytes] = parseDockerBytePair(row?.BlockIO)

  return {
    instanceId: instance.id,
    cpuPercent: parseDockerPercent(row?.CPUPerc),
    memoryBytes,
    memoryLimitBytes,
    memoryPercent: parseDockerPercent(row?.MemPerc),
    diskBytes: null,
    blockReadBytes,
    blockWriteBytes,
    containerName: row?.Name || '',
  }
}

const serializeInstanceResourceMetrics = (instance: core.Record, dockerStatsByName: Map<string, DockerStatsRow>) => ({
  ...serializeInstanceRuntimeMetrics(instance, dockerStatsByName),
  diskBytes: getDirectorySizeBytes(instanceRoot(instance.id)),
})

const findMetricsHistoryRange = (e: core.RequestEvent): InstanceMetricHistoryRange => {
  const requested = `${e.request.url.query().get('range') || '30m'}`
  return requested in historyRanges ? (requested as InstanceMetricHistoryRange) : '30m'
}

const serializeStoredMetricPoint = (record: core.Record): StoredInstanceMetricPoint => ({
  timestamp: record.getDateTime('created').unix() * 1000,
  cpuPercent: record.getFloat('cpuPercent'),
  memoryBytes: record.getInt('memoryBytes'),
  memoryLimitBytes: record.getInt('memoryLimitBytes'),
  memoryPercent: record.getFloat('memoryPercent'),
})

const aggregateMetricPoints = (points: StoredInstanceMetricPoint[], bucketMs: number) => {
  const buckets = new Map<
    number,
    {
      count: number
      cpuPercent: number
      memoryBytes: number
      memoryLimitBytes: number
      memoryPercent: number
    }
  >()

  for (const point of points) {
    if (!Number.isFinite(point.timestamp)) continue
    const timestamp = Math.floor(point.timestamp / bucketMs) * bucketMs
    const bucket = buckets.get(timestamp) || {
      count: 0,
      cpuPercent: 0,
      memoryBytes: 0,
      memoryLimitBytes: 0,
      memoryPercent: 0,
    }
    bucket.count += 1
    bucket.cpuPercent += point.cpuPercent
    bucket.memoryBytes += point.memoryBytes
    bucket.memoryLimitBytes += point.memoryLimitBytes
    bucket.memoryPercent += point.memoryPercent
    buckets.set(timestamp, bucket)
  }

  return Array.from(buckets.entries())
    .sort(([a], [b]) => a - b)
    .map(([timestamp, bucket]) => ({
      collectedAt: new Date(timestamp).toISOString(),
      cpuPercent: bucket.cpuPercent / bucket.count,
      memoryBytes: Math.round(bucket.memoryBytes / bucket.count),
      memoryLimitBytes: Math.round(bucket.memoryLimitBytes / bucket.count),
      memoryPercent: bucket.memoryPercent / bucket.count,
    }))
}

const findAccessibleInstances = (authRecord: core.Record) => {
  const records = authRecord.getBool('superAdmin')
    ? $app.findRecordsByFilter('instances', '1=1', 'subdomain', 500, 0)
    : $app.findRecordsByFilter('instances', 'uid = {:uid}', 'subdomain', 500, 0, { uid: authRecord.id })

  return records.filter((record): record is core.Record => !!record)
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

  const backups = findInstanceBackups(instance.id).map(refreshImportedBackupSizeMetadata).map(serializeInstanceBackup)
  const totalCompressedBytes = backups.reduce((total, backup) => total + backup.compressedBytes, 0)
  const runtime = serializeInstanceResourceMetrics(instance, readDockerStatsByName())

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
      instanceBytes: runtime.diskBytes,
    },
    runtime,
    collectedAt: new Date().toISOString(),
  })
}

export const HandleInstancesMetrics = (e: core.RequestEvent) => {
  const authRecord = requireAuthRecord(e.auth)
  const dockerStatsByName = readDockerStatsByName()
  const metrics: Record<string, InstanceResourceMetrics> = {}

  for (const instance of findAccessibleInstances(authRecord)) {
    metrics[instance.id] = serializeInstanceResourceMetrics(instance, dockerStatsByName)
  }

  return e.json(200, {
    instances: metrics,
    collectedAt: new Date().toISOString(),
  })
}

export const HandleInstanceMetrics = (e: core.RequestEvent) => {
  const authRecord = requireAuthRecord(e.auth)
  const instance = findInstance(pathValue(e, 'id'))
  assertInstanceAccess(instance, authRecord)

  return e.json(200, {
    metric: serializeInstanceRuntimeMetrics(instance, readDockerStatsByName([instance.id])),
    collectedAt: new Date().toISOString(),
  })
}

export const HandleInstanceMetricsHistory = (e: core.RequestEvent) => {
  const authRecord = requireAuthRecord(e.auth)
  const instance = findInstance(pathValue(e, 'id'))
  assertInstanceAccess(instance, authRecord)

  const range = findMetricsHistoryRange(e)
  const config = historyRanges[range]
  const cutoff = formatPocketBaseDate(Date.now() - config.durationMs)
  const records = $app.findRecordsByFilter(
    INSTANCE_RESOURCE_METRICS_COLLECTION,
    'instance = {:instance} && created >= {:cutoff}',
    'created',
    INSTANCE_METRICS_QUERY_LIMIT,
    0,
    { instance: instance.id, cutoff }
  )

  return e.json(200, {
    range,
    historyEnabled: instance.getBool('metricsHistoryEnabled'),
    bucketSeconds: config.bucketMs / 1000,
    points: aggregateMetricPoints(records.map(serializeStoredMetricPoint), config.bucketMs),
    collectedAt: new Date().toISOString(),
  })
}

export const CollectInstanceResourceMetrics = () => {
  const instances = $app.findRecordsByFilter('instances', 'metricsHistoryEnabled = true', '', 500, 0)
  if (instances.length === 0) return { saved: 0 }

  const dockerStatsByName = readDockerStatsByName()
  const collection = $app.findCollectionByNameOrId(INSTANCE_RESOURCE_METRICS_COLLECTION)
  let saved = 0

  for (const instance of instances) {
    const metric = serializeInstanceRuntimeMetrics(instance, dockerStatsByName)
    if (
      metric.cpuPercent === null ||
      metric.memoryBytes === null ||
      metric.memoryLimitBytes === null ||
      metric.memoryPercent === null
    ) {
      continue
    }

    try {
      const record = new Record(collection)
      record.set('instance', instance.id)
      record.set('cpuPercent', metric.cpuPercent)
      record.set('memoryBytes', metric.memoryBytes)
      record.set('memoryLimitBytes', metric.memoryLimitBytes)
      record.set('memoryPercent', metric.memoryPercent)
      $app.save(record)
      saved += 1
    } catch (error) {
      console.warn(`Impossible d'enregistrer les métriques de l'instance ${instance.id}: ${error}`)
    }
  }

  return { saved }
}

export const PurgeExpiredInstanceResourceMetrics = () => {
  const cutoff = formatPocketBaseDate(Date.now() - INSTANCE_METRICS_RETENTION_MS)
  let deleted = 0

  for (;;) {
    const records = $app.findRecordsByFilter(
      INSTANCE_RESOURCE_METRICS_COLLECTION,
      'created < {:cutoff}',
      'created',
      1000,
      0,
      { cutoff }
    )
    if (records.length === 0) break

    for (const record of records) {
      $app.delete(record)
      deleted += 1
    }
  }

  return { deleted }
}
