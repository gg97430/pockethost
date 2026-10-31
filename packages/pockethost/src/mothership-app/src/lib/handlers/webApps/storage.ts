import { requireWebAppPermission } from './access'
import {
  decryptWebValue,
  encryptWebValue,
  queueWebDeployment,
  webDirectLoopback,
  webEnabled,
  webInput,
  webJson,
  webRoot,
} from './api'
import { assertWebDeploymentInScope } from './projects'
const SETTINGS = 'web_storage_settings',
  JOBS = 'web_storage_jobs'
const name = /^[a-z][a-z0-9-]{0,30}$/,
  id = /^[a-z0-9]{15}$/
const timestamp = () => new Date().toISOString()
const setting = (appId: string, store: any = $app) =>
  store.findRecordsByFilter(SETTINGS, 'appId={:id}', '', 1, 0, { id: appId })[0]
export const storageWorkerSnapshot = (app: core.Record, store: any = $app) => {
  const row = setting(app.id, store)
  return row ? JSON.parse(row.getString('volumes') || '[]') : []
}
const volumesOf = (row?: core.Record) => (row ? JSON.parse(row.getString('volumes') || '[]') : [])
const validate = (value: any) => {
  if (!Array.isArray(value) || value.length > 4) throw new BadRequestError('Quatre volumes au maximum par site.')
  const seen = new Set<string>(),
    targets: string[] = []
  const volumes = value.map((item) => {
    if (
      !item ||
      typeof item !== 'object' ||
      Object.keys(item).some((k) => !['name', 'target', 'sizeMiB', 'readOnly'].includes(k)) ||
      typeof item.name !== 'string' ||
      !name.test(item.name) ||
      seen.has(item.name)
    )
      throw new BadRequestError('Nom de volume invalide ou dupliqué.')
    if (
      typeof item.target !== 'string' ||
      (!/^\/(?:data|mnt|app\/(?:data|uploads|storage))(?:\/[A-Za-z0-9_.-]+)*$/.test(item.target) &&
        item.target !== '/app/.next/cache') ||
      item.target.split('/').some((part: string) => part === '..' || part === '.') ||
      targets.some(
        (target) =>
          item.target === target || item.target.startsWith(target + '/') || target.startsWith(item.target + '/')
      )
    )
      throw new BadRequestError('Cible de volume invalide ou recouvrant un autre volume.')
    const sizeMiB = item.sizeMiB ?? 256,
      readOnly = item.readOnly ?? false
    if (!Number.isInteger(sizeMiB) || sizeMiB < 16 || sizeMiB > 2048 || typeof readOnly !== 'boolean')
      throw new BadRequestError('Volume attendu entre 16 et 2048 Mio.')
    seen.add(item.name)
    targets.push(item.target)
    return { name: item.name, target: item.target, sizeMiB, readOnly }
  })
  if (volumes.reduce((n, item) => n + item.sizeMiB, 0) > 4096)
    throw new BadRequestError('Le budget total des volumes est limité à 4 Gio.')
  return volumes
}
const runtime = (appId: string) => {
  try {
    const value = JSON.parse(toString($os.readFile(webRoot() + '/storage-status.json'))).applications?.[appId]
    if (value && Date.now() - Date.parse(value.checkedAt) < 60000) return value
  } catch {}
  return { available: false, volumes: [] }
}
const view = (app: core.Record) => {
  const row = setting(app.id)
  return {
    volumes: volumesOf(row),
    revision: row?.getString('revision') || '',
    retention: row?.getInt('retention') || 7,
    runtime: runtime(app.id),
    limits: { maxVolumes: 4, maxVolumeMiB: 2048, maxTotalMiB: 4096, hardQuota: false },
    snapshotRequiresPause: true,
  }
}
const serialize = (row: core.Record) => ({
  id: row.id,
  name: row.getString('name'),
  operation: row.getString('operation'),
  status: row.getString('status'),
  created: row.getString('created'),
  startedAt: row.getString('startedAt'),
  finishedAt: row.getString('finishedAt'),
  error: row.getString('error'),
  result: JSON.parse(row.getString('result') || '{}'),
})
const worker = (e: core.RequestEvent) => {
  if (!webEnabled()) throw new NotFoundError()
  const secret = $os.getenv('WEB_APPS_WORKER_SECRET')
  if (secret.length < 32 || !$security.equal(secret, e.request!.header.get('X-Web-Worker') || ''))
    throw new UnauthorizedError()
  if (!webDirectLoopback(e)) throw new ForbiddenError()
}
const currentVolume = (app: core.Record, volumeName: string, store: any = $app) => {
  if (!name.test(volumeName)) throw new BadRequestError('Volume invalide.')
  const row = setting(app.id, store),
    volume = volumesOf(row).find((item: any) => item.name === volumeName)
  if (!volume) throw new BadRequestError('Volume non enregistré.')
  return { row, volume }
}
const busy = (appId: string, store: any = $app) =>
  store.findRecordsByFilter(JOBS, 'appId={:id} && (status="queued" || status="running")', '', 1, 0, { id: appId })
    .length > 0
export const HandleWebStorageGet = (e: core.RequestEvent) =>
  webJson(e, view(requireWebAppPermission(e, 'app.read').app!))
export const HandleWebStorageUpdate = (e: core.RequestEvent) => {
  const app = requireWebAppPermission(e, 'storage.manage').app!,
    input = webInput(e),
    volumes = validate(input.volumes),
    retention = input.retention ?? 7
  if (!Number.isInteger(retention) || retention < 1 || retention > 14)
    throw new BadRequestError('Rétention attendue de 1 à 14 snapshots.')
  $app.runInTransaction((tx) => {
    let row = setting(app.id, tx)
    if (input.revision !== undefined && input.revision !== (row?.getString('revision') || ''))
      throw new BadRequestError('La configuration du stockage a changé. Rechargez la page.')
    if (busy(app.id, tx)) throw new BadRequestError('Une opération de stockage est en cours.')
    const status = runtime(app.id)
    for (const item of volumes) {
      const measured = status.volumes?.find((value: any) => value.name === item.name)
      if (measured?.usedBytes > item.sizeMiB * 1024 ** 2)
        throw new BadRequestError('La limite choisie est inférieure aux données déjà présentes.')
    }
    if (!row) {
      row = new Record(tx.findCollectionByNameOrId(SETTINGS))
      row.set('appId', app.id)
    }
    row.set('volumes', volumes)
    row.set('retention', retention)
    row.set('revision', $security.randomString(24))
    tx.save(row)
  })
  return webJson(e, view(app))
}
export const HandleWebStorageRestorePreview = (e: core.RequestEvent) => {
  const access = requireWebAppPermission(e, 'storage.manage'),
    actor = access.user,
    app = access.app!,
    volumeName = e.request!.pathValue('name'),
    input = webInput(e),
    current = currentVolume(app, volumeName)
  if (!id.test(input.backupId || '')) throw new BadRequestError('Snapshot invalide.')
  const source = $app.findRecordById(JOBS, input.backupId)
  if (
    source.getString('appId') !== app.id ||
    source.getString('name') !== volumeName ||
    source.getString('operation') !== 'backup' ||
    source.getString('status') !== 'ready'
  )
    throw new BadRequestError('Snapshot indisponible pour ce volume.')
  const result = JSON.parse(source.getString('result') || '{}'),
    token = $security.randomString(64),
    expiresAt = new Date(Date.now() + 10 * 60000).toISOString(),
    row = new Record($app.findCollectionByNameOrId(JOBS))
  row.set('appId', app.id)
  row.set('actorId', actor.id)
  row.set('name', volumeName)
  row.set('operation', 'restore')
  row.set('status', 'preview')
  row.set(
    'snapshotEncrypted',
    encryptWebValue({
      volume: current.volume,
      backup: result,
      revision: current.row.getString('revision'),
      deploymentId: app.getString('activeDeployment'),
      previewHash: $security.sha256(token),
      expiresAt,
    })
  )
  $app.save(row)
  return webJson(e, {
    previewId: row.id,
    previewToken: token,
    expiresAt,
    backup: { id: source.id, name: volumeName, created: source.getString('created'), dataBytes: result.dataBytes },
    willPause: true,
  })
}
export const HandleWebStorageAction = (e: core.RequestEvent) => {
  const access = requireWebAppPermission(e, 'storage.manage'),
    actor = access.user,
    app = access.app!,
    volumeName = e.request!.pathValue('name'),
    operation = e.request!.pathValue('operation'),
    input = webInput(e)
  if (!['backup', 'restore'].includes(operation)) throw new BadRequestError('Opération de stockage inconnue.')
  let job: any
  $app.runInTransaction((tx) => {
    const current = currentVolume(app, volumeName, tx)
    if (busy(app.id, tx)) throw new BadRequestError('Une opération de stockage est déjà en cours sur ce site.')
    let row: core.Record
    if (operation === 'restore') {
      if (
        !id.test(input.previewId || '') ||
        typeof input.previewToken !== 'string' ||
        !/^[A-Za-z0-9]{64}$/.test(input.previewToken)
      )
        throw new BadRequestError('Prévisualisez la restauration avant de la confirmer.')
      row = tx.findRecordById(JOBS, input.previewId)
      const snapshot = decryptWebValue(row.getString('snapshotEncrypted'))
      if (
        row.getString('status') !== 'preview' ||
        row.getString('actorId') !== actor.id ||
        row.getString('appId') !== app.id ||
        row.getString('name') !== volumeName ||
        snapshot.backup?.backupId !== input.backupId ||
        snapshot.revision !== current.row.getString('revision') ||
        snapshot.deploymentId !== app.getString('activeDeployment') ||
        Date.parse(snapshot.expiresAt) < Date.now() ||
        !$security.equal(snapshot.previewHash, $security.sha256(input.previewToken))
      )
        throw new BadRequestError('Aperçu expiré ou configuration modifiée.')
      // Le redémarrage après restauration rejoue la publication active : jamais celle d'un autre projet.
      if (!access.platform)
        assertWebDeploymentInScope(app, tx.findRecordById('web_deployments', app.getString('activeDeployment')), tx)
      delete snapshot.previewHash
      delete snapshot.expiresAt
      row.set('snapshotEncrypted', encryptWebValue(snapshot))
    } else {
      row = new Record(tx.findCollectionByNameOrId(JOBS))
      row.set('appId', app.id)
      row.set('actorId', actor.id)
      row.set('name', volumeName)
      row.set('operation', 'backup')
      row.set(
        'snapshotEncrypted',
        encryptWebValue({ volume: current.volume, revision: current.row.getString('revision') })
      )
    }
    row.set('status', 'queued')
    tx.save(row)
    job = serialize(row)
  })
  return webJson(e, { job }, 201)
}
export const HandleWebStorageHistory = (e: core.RequestEvent) => {
  const app = requireWebAppPermission(e, 'app.read').app!,
    query = e.request!.url!.query(),
    page = Math.max(1, Math.min(10000, Math.floor(Number(query.get('page')) || 1))),
    perPage = Math.max(1, Math.min(100, Math.floor(Number(query.get('perPage')) || 20))),
    rows = $app.findRecordsByFilter(
      JOBS,
      'appId={:id} && status!="preview"',
      '-created,-id',
      perPage,
      (page - 1) * perPage,
      { id: app.id }
    ),
    totalItems = $app.countRecords(JOBS, $dbx.exp('appId={:id} AND status!="preview"', { id: app.id }))
  return webJson(e, {
    items: rows.map(serialize),
    page,
    perPage,
    totalItems,
    totalPages: Math.ceil(totalItems / perPage),
    hasMore: page * perPage < totalItems,
  })
}
export const HandleWebStorageClaim = (e: core.RequestEvent) => {
  worker(e)
  let job: any = null
  $app.runInTransaction((tx) => {
    for (const row of tx.findRecordsByFilter(JOBS, 'status="running" && leaseAt<{:cutoff}', '', 100, 0, {
      cutoff: new Date(Date.now() - 5 * 60000).toISOString().replace('T', ' '),
    })) {
      row.set('status', 'queued')
      row.set('leaseHash', '')
      tx.save(row)
    }
    const rows = tx.findRecordsByFilter(JOBS, 'status="queued" || status="deleting"', 'created,id', 1, 0)
    if (!rows.length) return
    const row = rows[0],
      lease = $security.randomString(64),
      snapshot = decryptWebValue(row.getString('snapshotEncrypted'))
    row.set('status', 'running')
    row.set('leaseHash', $security.sha256(lease))
    row.set('leaseAt', timestamp())
    row.set('startedAt', timestamp())
    tx.save(row)
    job = {
      id: row.id,
      appId: row.getString('appId'),
      name: row.getString('name'),
      operation: row.getString('operation'),
      lease,
      ...snapshot,
      ...(row.getString('operation') === 'delete' ? { backup: JSON.parse(row.getString('result') || '{}') } : {}),
    }
  })
  return webJson(e, { job })
}
export const HandleWebStorageReport = (e: core.RequestEvent) => {
  let state: any = null
  worker(e)
  const input = webInput(e)
  if (!id.test(input.id || '') || typeof input.lease !== 'string') throw new BadRequestError('Opération invalide.')
  $app.runInTransaction((tx) => {
    const row = tx.findRecordById(JOBS, input.id)
    if (!row.getString('leaseHash') || !$security.equal(row.getString('leaseHash'), $security.sha256(input.lease)))
      throw new UnauthorizedError()
    if (input.inspect === true) {
      state = { status: row.getString('status'), result: JSON.parse(row.getString('result') || '{}') }
      return
    }
    if (row.getString('status') === input.status && ['ready', 'failed'].includes(input.status)) return
    if (row.getString('status') === 'deleted' && input.status === 'ready' && row.getString('operation') === 'delete')
      return
    if (row.getString('status') !== 'running') throw new BadRequestError('Opération déjà terminée.')
    if (input.status === 'running') {
      row.set('leaseAt', timestamp())
      const progress: any = {}
      for (const key of ['s3Bytes', 's3TotalBytes'])
        if (input[key] !== undefined) {
          if (!Number.isSafeInteger(input[key]) || input[key] < 0 || input[key] > 2 * 1024 ** 3)
            throw new BadRequestError('Progression invalide.')
          progress[key] = input[key]
        }
      row.set('result', { ...JSON.parse(row.getString('result') || '{}'), ...progress })
      tx.save(row)
      return
    }
    if (!['ready', 'failed'].includes(input.status)) throw new BadRequestError('État invalide.')
    if (input.status === 'ready') {
      const result = input.result || {},
        app = tx.findRecordById('web_apps', row.getString('appId')),
        volumeName = row.getString('name'),
        prefix = 'ph-web-data-' + app.id + '-' + volumeName + '-'
      if (row.getString('operation') === 'backup') {
        if (
          typeof result.object !== 'string' ||
          !result.object.endsWith('/apps/' + app.id + '/volumes/' + volumeName + '/' + row.id + '.phwv') ||
          !Number.isSafeInteger(result.bytes) ||
          result.bytes < 1 ||
          result.bytes > 2 * 1024 ** 3 ||
          !Number.isSafeInteger(result.dataBytes) ||
          result.dataBytes < 0 ||
          result.dataBytes > 2048 * 1024 ** 2
        )
          throw new BadRequestError('Résultat de snapshot invalide.')
        row.set('result', {
          object: result.object,
          bytes: result.bytes,
          dataBytes: result.dataBytes,
          files: result.files,
          sourceVolume: result.sourceVolume,
          backupId: row.id,
          checkedAt: timestamp(),
        })
      } else if (row.getString('operation') === 'restore') {
        if (
          result.activeVolume !== prefix + 'r' + row.id ||
          typeof result.previousVolume !== 'string' ||
          !result.previousVolume.startsWith(prefix) ||
          !Array.isArray(result.previousContainers) ||
          result.previousContainers.length > 4 ||
          result.previousContainers.some((value: any) => typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value))
        )
          throw new BadRequestError('Résultat de restauration invalide.')
        const prepared = decryptWebValue(row.getString('snapshotEncrypted')),
          activeId = app.getString('activeDeployment')
        if (!id.test(activeId) || activeId !== prepared.deploymentId)
          throw new BadRequestError('La publication a changé depuis l’aperçu de restauration.')
        const active = tx.findRecordById('web_deployments', activeId),
          activeResult = JSON.parse(active.getString('result') || '{}'),
          published = decryptWebValue(active.getString('snapshotEncrypted'))
        if (
          active.getString('appId') !== app.id ||
          active.getString('status') !== 'ready' ||
          !activeResult ||
          typeof activeResult.image !== 'string' ||
          !activeResult.image ||
          activeResult.available === false ||
          !published?.config ||
          !published?.env
        )
          throw new BadRequestError('La publication active ne permet plus le redémarrage après restauration.')
        if (
          tx.findRecordsByFilter('web_deployments', 'appId={:id} && (status="queued" || status="running")', '', 1, 0, {
            id: app.id,
          }).length
        )
          throw new BadRequestError('Un autre déploiement est en attente. Recommencez la restauration après sa fin.')
        const deployment = queueWebDeployment(
          app,
          'apply-runtime',
          row.getString('actorId'),
          {
            commit: active.getString('commit'),
            config: published.config,
            env: published.env,
            runtime: published.runtime || { kind: 'nextjs' },
            storage: published.storage || [],
            rollbackResult: activeResult,
            storageRestore: { ...result, name: volumeName },
          },
          tx
        )
        const clean = decryptWebValue(deployment.getString('snapshotEncrypted'))
        for (const key of ['ci', 'promotion', 'restoreBackup']) delete clean[key]
        deployment.set('snapshotEncrypted', encryptWebValue(clean))
        tx.save(deployment)
        row.set('result', { ...result, restartDeploymentId: deployment.id })
      } else row.set('result', result)
    }
    row.set('status', row.getString('operation') === 'delete' && input.status === 'ready' ? 'deleted' : input.status)
    row.set(
      'error',
      input.status === 'failed' ? 'L’opération de stockage a échoué. Les données précédentes sont conservées.' : ''
    )
    row.set('finishedAt', timestamp())
    tx.save(row)
  })
  HandleWebStorageMaintenance()
  return webJson(e, state || { ok: true })
}
export const HandleWebStorageMaintenance = () => {
  if (!webEnabled()) return
  try {
    $app.findCollectionByNameOrId(JOBS)
  } catch {
    return
  }
  const cutoff = new Date(Date.now() - 30 * 86400000).toISOString().replace('T', ' ')
  for (const row of $app.findRecordsByFilter(
    JOBS,
    '(status="preview" || status="deleted" || status="failed") && created<{:cutoff}',
    'created',
    500,
    0,
    { cutoff }
  ))
    $app.delete(row)
  const rows = $app.findRecordsByFilter(JOBS, 'operation="backup" && status="ready"', '-created,-id', 10000, 0),
    counts: Record<string, number> = {}
  const active = new Set<string>()
  for (const row of $app.findRecordsByFilter(
    JOBS,
    'operation="restore" && (status="queued" || status="running" || status="preview")',
    '',
    1000,
    0
  )) {
    try {
      const backup = decryptWebValue(row.getString('snapshotEncrypted')).backup?.backupId
      if (backup) active.add(backup)
    } catch {}
  }
  for (const row of rows) {
    const key = row.getString('appId') + '/' + row.getString('name'),
      retain = setting(row.getString('appId'))?.getInt('retention') || 7
    counts[key] = (counts[key] || 0) + 1
    if (counts[key] > retain && !active.has(row.id)) {
      row.set('operation', 'delete')
      row.set('status', 'deleting')
      row.set('leaseHash', '')
      row.set('leaseAt', '')
      $app.save(row)
    }
  }
}

export const restoreWebStorageBackup = (app: core.Record, value: any, store: any = $app) => {
  const volumes = validate(value || [])
  let row = setting(app.id, store)
  if (!row) {
    row = new Record(store.findCollectionByNameOrId(SETTINGS))
    row.set('appId', app.id)
    row.set('retention', 7)
  }
  row.set('volumes', volumes)
  row.set('revision', $security.randomString(24))
  store.save(row)
}
