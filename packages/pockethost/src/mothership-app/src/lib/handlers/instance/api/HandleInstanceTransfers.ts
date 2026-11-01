import {
  assertTransferPeerAllowed,
  INSTANCE_TRANSFER_PROTOCOL,
  parseInstanceTransferLink,
  validateTransferMetadata,
  type TransferMetadata,
} from '$common/instanceTransfer'
import { listVersions } from '$util/versions'
import { instanceTransferBackupTools as backups } from './HandleInstanceBackups'

const COLLECTION = 'instance_transfers'
const ACTIVE_EXPORT = 'direction = "export" && (status = "queued" || status = "preparing" || status = "ready")'
const now = () => new Date().toISOString()
const object = (raw: any): Record<string, any> =>
  typeof raw === 'string' ? JSON.parse(raw || '{}') : raw ? JSON.parse(JSON.stringify(raw)) : {}
const root = () => `${backups.dataRoot()}/transfers`
const directory = (id: string) => `${root()}/${id}`
const credentialsPath = (id: string) => `${directory(id)}/curl-auth.conf`
const partialPath = (id: string) => `${directory(id)}/archive.tar.gz.part`
const maxBytes = () => {
  const value = Number($os.getenv('INSTANCE_TRANSFER_MAX_BYTES') || 100 * 1024 ** 3)
  if (!Number.isSafeInteger(value) || value <= 0) throw new Error('INSTANCE_TRANSFER_MAX_BYTES invalide.')
  return value
}
const exists = (path: string) => {
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
const removeCredentials = (id: string) => {
  try {
    $os.remove(credentialsPath(id))
  } catch {}
}
const command = (name: string, ...args: string[]) => toString($os.cmd(name, ...args).combinedOutput()).trim()
const find = (id: string, app: Pick<core.App, 'findRecordById'> = $app) => {
  if (!/^[a-z0-9]{15}$/.test(id)) throw new BadRequestError('Transfert introuvable.')
  return app.findRecordById(COLLECTION, id)
}
const requireAdmin = (e: core.RequestEvent) => {
  if (!e.auth?.getBool('superAdmin')) throw new ForbiddenError('Le transfert de serveur est réservé au superadmin.')
  return e.auth
}
const body = (e: core.RequestEvent) => object(e.requestInfo().body)
const pathId = (e: core.RequestEvent) => e.request!.pathValue('transferId')
const saveStatus = (record: core.Record, status: string, error = '') => {
  record.set('status', status)
  record.set('error', error)
  $app.save(record)
}
const serialize = (record: core.Record) => {
  const metadata = object(record.getString('metadata'))
  const status = record.getString('status')
  const exporting = record.getString('direction') === 'export'
  const totalBytes = Number(metadata.peer?.archive?.compressedBytes || metadata.compressedBytes || 0)
  let preparation = { phase: '', compressedBytes: 0, sourceBytes: 0 }
  if (exporting && record.getString('backup')) {
    try {
      preparation = backups.progress($app.findRecordById('instance_backups', record.getString('backup')))
    } catch {}
  }
  const phase = ['complete', 'failed', 'revoked', 'queued'].includes(status)
    ? status
    : status === 'preparing'
      ? 'preparing'
      : metadata.progress?.phase || status
  const downloadedBytes =
    status === 'complete' || (!exporting && record.getString('backup'))
      ? totalBytes
      : exporting
        ? Number(metadata.progress?.downloadedBytes || 0)
        : fileSize(partialPath(record.id))
  let subdomain = metadata.subdomain || ''
  try {
    subdomain = $app.findRecordById('instances', record.getString('instance')).getString('subdomain')
  } catch {}
  return {
    id: record.id,
    direction: record.getString('direction'),
    instance: record.getString('instance'),
    subdomain,
    backup: record.getString('backup'),
    status,
    phase,
    claimed: !!record.getString('claimHash'),
    preparedBytes: preparation.compressedBytes,
    preparationPhase: preparation.phase,
    sourceBytes: preparation.sourceBytes,
    progressUpdatedAt: metadata.progress?.updatedAt || '',
    expiresAt: record.getString('expiresAt'),
    error: record.getString('error'),
    created: record.getString('created'),
    totalBytes,
    downloadedBytes: Math.min(totalBytes, downloadedBytes),
  }
}
const assertOwner = (record: core.Record, auth: core.Record) => {
  if (record.getString('user') !== auth.id)
    throw new ForbiddenError('Ce transfert appartient à un autre administrateur.')
}
const assertFresh = (record: core.Record) => {
  if (Date.parse(record.getString('expiresAt')) <= Date.now())
    throw new BadRequestError('Le lien de transfert a expiré.')
}
const peerRecord = (e: core.RequestEvent, allowComplete = false) => {
  const record = find(pathId(e))
  const code = e.request!.header.get('X-PocketHost-Transfer') || ''
  if (
    record.getString('direction') !== 'export' ||
    !/^[A-Za-z0-9]{48}$/.test(code) ||
    !$security.equal($security.sha256(code), record.getString('codeHash'))
  ) {
    throw new ForbiddenError('Code de transfert invalide.')
  }
  if (record.getString('status') === 'revoked' || (!allowComplete && record.getString('status') === 'complete')) {
    throw new BadRequestError('Ce lien a été révoqué ou déjà utilisé.')
  }
  if (!(allowComplete && record.getString('status') === 'complete')) assertFresh(record)
  return record
}
const assertClaim = (e: core.RequestEvent, record: core.Record) => {
  const claim = e.request!.header.get('X-PocketHost-Claim') || ''
  if (!/^[A-Za-z0-9]{48}$/.test(claim) || !$security.equal($security.sha256(claim), record.getString('claimHash'))) {
    throw new ForbiddenError('Ce transfert est réservé à un autre serveur cible.')
  }
}
const metadataForPeer = (record: core.Record): TransferMetadata => {
  const instance = $app.findRecordById('instances', record.getString('instance'))
  const frozen = object(record.getString('metadata')).instance as TransferMetadata['instance'] | undefined
  const result: TransferMetadata = {
    protocol: INSTANCE_TRANSFER_PROTOCOL,
    id: record.id,
    status: record.getString('status') as TransferMetadata['status'],
    expiresAt: record.getString('expiresAt'),
    instance: frozen || {
      subdomain: instance.getString('subdomain'),
      version: instance.getString('version'),
      syncAdmin: instance.getBool('syncAdmin'),
      autoVacuum: instance.getBool('autoVacuum'),
      secrets: JSON.parse(instance.getString('secrets') || '[]'),
      webhooks: JSON.parse(instance.getString('webhooks') || '[]'),
    },
  }
  if (record.getString('backup')) {
    const backup = $app.findRecordById('instance_backups', record.getString('backup'))
    result.archive = {
      filename: backup.getString('filename'),
      checksum: backup.getString('checksum'),
      compressedBytes: Number(backup.get('compressedBytes')),
      sizeBytes: Number(backup.get('sizeBytes')),
    }
  }
  return result
}
const parsePeer = (link: string, code = '') => {
  const peer = parseInstanceTransferLink(link, code)
  assertTransferPeerAllowed(
    peer,
    $os.getenv('INSTANCE_TRANSFER_ALLOWED_HOSTS') || '',
    $os.getenv('INSTANCE_TRANSFER_ALLOW_HTTP') === '1'
  )
  return peer
}
const writeCredentials = (id: string, code: string, claim: string) => {
  $os.mkdirAll(directory(id), 0o700 as any)
  $os.writeFile(
    credentialsPath(id),
    `header = "X-PocketHost-Transfer: ${code}"\nheader = "X-PocketHost-Claim: ${claim}"\n`,
    0o600 as any
  )
}
const curlBase = (config: string) => [
  '--silent',
  '--show-error',
  '--fail',
  '--connect-timeout',
  '10',
  '--max-redirs',
  '0',
  '--proto',
  $os.getenv('INSTANCE_TRANSFER_ALLOW_HTTP') === '1' ? '=http,https' : '=https',
  '--config',
  config,
]
const peerRequest = (origin: string, id: string, config: string, action: string, method = 'GET', data?: unknown) => {
  const output = command(
    'curl',
    ...curlBase(config),
    '--max-time',
    action === 'progress' ? '3' : '30',
    '--max-filesize',
    '1048576',
    '--request',
    method,
    ...(data === undefined ? [] : ['--header', 'Content-Type: application/json', '--data-raw', JSON.stringify(data)]),
    `${origin}/api/instance-transfers/${id}/${action}`
  )
  return JSON.parse(output)
}
// Keep credentials out of argv. A separate process reports actual bytes while curl streams to disk.
// If PocketBase exits, stop the writer before a future worker resumes the same archive.
const DOWNLOAD_SCRIPT = String.raw`
transfer_config=$1
transfer_path=$2
transfer_archive=$3
transfer_total=$4
transfer_progress=$5
transfer_proto=$6
transfer_parent=$PPID
transfer_pid=
transfer_cleanup() {
  if [ -n "$transfer_pid" ]; then kill "$transfer_pid" 2>/dev/null; wait "$transfer_pid" 2>/dev/null; fi
}
trap transfer_cleanup EXIT
trap 'exit 143' TERM HUP INT
transfer_report() {
  transfer_bytes=$(stat -c %s "$transfer_path" 2>/dev/null || printf 0)
  transfer_payload=$(printf '{"phase":"downloading","downloadedBytes":%s}' "$transfer_bytes")
  curl --silent --fail --connect-timeout 1 --max-time 2 --max-redirs 0 --proto "$transfer_proto" \
    --config "$transfer_config" --request POST --header 'Content-Type: application/json' \
    --data "$transfer_payload" "$transfer_progress" >/dev/null 2>&1 || true
}
curl --silent --show-error --fail --connect-timeout 10 --max-redirs 0 --proto "$transfer_proto" \
  --config "$transfer_config" --retry 3 --retry-delay 3 --speed-time 120 --speed-limit 1024 \
  --max-time 43200 --max-filesize "$transfer_total" --continue-at - --output "$transfer_path" \
  "$transfer_archive" &
transfer_pid=$!
while kill -0 "$transfer_pid" 2>/dev/null; do
  if ! kill -0 "$transfer_parent" 2>/dev/null; then exit 143; fi
  transfer_report
  sleep 3
done
wait "$transfer_pid"
transfer_status=$?
transfer_pid=
transfer_report
exit "$transfer_status"
`
const downloadArchive = (record: core.Record, origin: string, id: string, total: number) =>
  command(
    'bash',
    '-c',
    DOWNLOAD_SCRIPT,
    'pockethost-transfer',
    credentialsPath(record.id),
    partialPath(record.id),
    `${origin}/api/instance-transfers/${id}/archive`,
    `${total}`,
    `${origin}/api/instance-transfers/${id}/progress`,
    $os.getenv('INSTANCE_TRANSFER_ALLOW_HTTP') === '1' ? '=http,https' : '=https'
  )

const reportImportProgress = (record: core.Record, phase: string) => {
  const metadata = object(record.getString('metadata'))
  const total = Number(metadata.peer?.archive?.compressedBytes || 0)
  const progress = {
    phase,
    downloadedBytes: record.getString('backup') ? total : Math.min(total, fileSize(partialPath(record.id))),
    updatedAt: now(),
  }
  record.set('metadata', { ...metadata, progress })
  $app.save(record)
  try {
    peerRequest(
      record.getString('sourceUrl'),
      record.getString('remoteId'),
      credentialsPath(record.id),
      'progress',
      'POST',
      progress
    )
  } catch {
    // Reporting is advisory and remains compatible with peers without this endpoint.
  }
}

const inspectPeer = (link: string, code = '') => {
  const peer = parsePeer(link, code)
  const tempId = $security.randomStringWithAlphabet(15, 'abcdefghijklmnopqrstuvwxyz0123456789')
  writeCredentials(tempId, peer.code, $security.randomString(48))
  try {
    const metadata = validateTransferMetadata(
      peerRequest(peer.origin, peer.id, credentialsPath(tempId), 'metadata'),
      peer.id,
      maxBytes()
    )
    return { peer, metadata }
  } finally {
    $os.removeAll(directory(tempId))
  }
}

export const HandleInstanceTransferCreate = (e: core.RequestEvent) => {
  const auth = requireAdmin(e)
  const instance = $app.findRecordById('instances', e.request!.pathValue('id'))
  backups.assertNoRunningOperation(instance.id)
  const base = ($os.getenv('APP_URL') || '').replace(/\/+$/, '')
  const code = $security.randomString(48)
  const record = new Record($app.findCollectionByNameOrId(COLLECTION))
  record.set('direction', 'export')
  record.set('status', 'queued')
  record.set('user', auth.id)
  record.set('instance', instance.id)
  record.set('codeHash', $security.sha256(code))
  record.set('expiresAt', new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString())
  record.set('metadata', { wasRunning: instance.getBool('power'), subdomain: instance.getString('subdomain') })
  $app.runInTransaction((app) => {
    if (
      app.findRecordsByFilter(COLLECTION, `${ACTIVE_EXPORT} && instance = {:id}`, '', 1, 0, { id: instance.id }).length
    ) {
      throw new BadRequestError('Un transfert est déjà en cours pour cette instance.')
    }
    app.save(record)
    parseInstanceTransferLink(`${base}/api/instance-transfers/${record.id}#${code}`)
    const current = app.findRecordById('instances', instance.id)
    current.set('power', false)
    app.save(current)
  })
  e.response.header().set('Cache-Control', 'no-store')
  return e.json(202, { transfer: serialize(record), link: `${base}/api/instance-transfers/${record.id}#${code}`, code })
}

export const HandleInstanceTransfersList = (e: core.RequestEvent) => {
  const auth = requireAdmin(e)
  const instanceId = e.request!.url!.query().get('instance') || ''
  const records = $app.findRecordsByFilter(
    COLLECTION,
    'user = {:user}' + (instanceId ? ' && instance = {:instance}' : ''),
    '-created',
    100,
    0,
    { user: auth.id, instance: instanceId }
  )
  e.response.header().set('Cache-Control', 'no-store')
  return e.json(200, { transfers: records.map(serialize) })
}

export const HandleInstanceTransferMetadata = (e: core.RequestEvent) => {
  const record = peerRecord(e)
  e.response.header().set('Cache-Control', 'no-store')
  return e.json(200, metadataForPeer(record))
}

export const HandleInstanceTransferRenewLink = (e: core.RequestEvent) => {
  const auth = requireAdmin(e)
  const code = $security.randomString(48)
  const record = find(pathId(e))
  assertOwner(record, auth)
  $app.runInTransaction((app) => {
    const current = find(record.id, app)
    if (
      current.getString('direction') !== 'export' ||
      !['queued', 'preparing', 'ready'].includes(current.getString('status')) ||
      current.getString('claimHash')
    )
      throw new BadRequestError('Le transfert est déjà réservé ou terminé.')
    current.set('codeHash', $security.sha256(code))
    current.set('expiresAt', new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString())
    app.save(current)
  })
  const link = `${($os.getenv('APP_URL') || '').replace(/\/+$/, '')}/api/instance-transfers/${record.id}#${code}`
  e.response.header().set('Cache-Control', 'no-store')
  return e.json(200, { transfer: serialize(find(record.id)), link, code })
}

export const HandleInstanceTransferClaim = (e: core.RequestEvent) => {
  const record = peerRecord(e)
  const claim = e.request!.header.get('X-PocketHost-Claim') || ''
  if (!/^[A-Za-z0-9]{48}$/.test(claim)) throw new BadRequestError('Identifiant cible invalide.')
  $app.runInTransaction((app) => {
    const current = find(record.id, app)
    assertFresh(current)
    if (
      !$security.equal(
        current.getString('codeHash'),
        $security.sha256(e.request!.header.get('X-PocketHost-Transfer') || '')
      )
    ) {
      throw new ForbiddenError('Ce code a été remplacé.')
    }
    if (current.getString('status') !== 'ready') throw new BadRequestError('Le transfert n’est pas disponible.')
    const hash = $security.sha256(claim)
    if (current.getString('claimHash') && !$security.equal(current.getString('claimHash'), hash)) {
      throw new BadRequestError('Ce lien est déjà utilisé par un autre transfert.')
    }
    current.set('claimHash', hash)
    app.save(current)
  })
  return e.json(200, { status: 'ok' })
}

export const HandleInstanceTransferArchive = (e: core.RequestEvent) => {
  const record = peerRecord(e)
  assertClaim(e, record)
  if (record.getString('status') !== 'ready') throw new BadRequestError('Archive indisponible.')
  const instance = $app.findRecordById('instances', record.getString('instance'))
  if (instance.getBool('power')) throw new BadRequestError('La source doit rester arrêtée pendant le transfert.')
  const backup = $app.findRecordById('instance_backups', record.getString('backup'))
  const path = backups.archivePath(instance, backup)
  const filename = path.split('/').pop()!
  e.response.header().set('Cache-Control', 'no-store')
  e.response.header().set('Content-Disposition', `attachment; filename="${filename}"`)
  return e.fileFS($os.dirFS(path.slice(0, -filename.length)), filename)
}

export const HandleInstanceTransferProgress = (e: core.RequestEvent) => {
  const record = peerRecord(e)
  const input = body(e)
  if (!['downloading', 'verifying', 'restoring', 'failed'].includes(input.phase)) {
    throw new BadRequestError('Étape de transfert invalide.')
  }
  $app.runInTransaction((app) => {
    const current = find(record.id, app)
    assertClaim(e, current)
    assertFresh(current)
    if (
      current.getString('status') !== 'ready' ||
      !$security.equal(
        current.getString('codeHash'),
        $security.sha256(e.request!.header.get('X-PocketHost-Transfer') || '')
      )
    ) {
      throw new BadRequestError('Transfert indisponible.')
    }
    const metadata = object(current.getString('metadata'))
    const total = Number(metadata.compressedBytes || 0)
    if (!Number.isSafeInteger(input.downloadedBytes) || input.downloadedBytes < 0 || input.downloadedBytes > total) {
      throw new BadRequestError('Volume transféré invalide.')
    }
    current.set('metadata', {
      ...metadata,
      progress: { phase: input.phase, downloadedBytes: input.downloadedBytes, updatedAt: now() },
    })
    app.save(current)
  })
  return e.json(200, { status: 'ok' })
}

export const HandleInstanceTransferComplete = (e: core.RequestEvent) => {
  const record = peerRecord(e, true)
  $app.runInTransaction((app) => {
    const current = find(record.id, app)
    assertClaim(e, current)
    if (
      !['ready', 'complete'].includes(current.getString('status')) ||
      !$security.equal(
        current.getString('codeHash'),
        $security.sha256(e.request!.header.get('X-PocketHost-Transfer') || '')
      )
    ) {
      throw new BadRequestError('Transfert indisponible.')
    }
    if (current.getString('status') !== 'complete') assertFresh(current)
    const instance = app.findRecordById('instances', current.getString('instance'))
    if (instance.getBool('power')) throw new BadRequestError('La source doit rester arrêtée.')
    current.set('status', 'complete')
    app.save(current)
  })
  return e.json(200, { status: 'ok' })
}

export const HandleInstanceTransferInspect = (e: core.RequestEvent) => {
  requireAdmin(e)
  const input = body(e)
  try {
    const { metadata } = inspectPeer(`${input.link || ''}`, `${input.code || ''}`)
    return e.json(200, {
      ...metadata,
      instance: { subdomain: metadata.instance.subdomain, version: metadata.instance.version },
    })
  } catch (error) {
    throw new BadRequestError(`${error}`)
  }
}

export const HandleInstanceTransferImport = (e: core.RequestEvent) => {
  const auth = requireAdmin(e)
  const input = body(e)
  const { peer, metadata } = inspectPeer(`${input.link || ''}`, `${input.code || ''}`)
  const subdomain = `${input.subdomain || metadata.instance.subdomain}`.trim().toLowerCase()
  if (!/^[a-z][a-z0-9-]{2,39}$/.test(subdomain)) throw new BadRequestError('Nom de nouvelle instance invalide.')
  const versions = listVersions()
  if (versions.length && !versions.includes(metadata.instance.version)) {
    throw new BadRequestError(`La version ${metadata.instance.version} doit être disponible sur ce serveur.`)
  }
  const instance = new Record($app.findCollectionByNameOrId('instances'))
  instance.set('uid', auth.id)
  instance.set('subdomain', subdomain)
  instance.set('status', 'idle')
  instance.set('power', false)
  instance.set('version', metadata.instance.version)
  instance.set('syncAdmin', metadata.instance.syncAdmin)
  instance.set('secrets', metadata.instance.secrets || [])
  instance.set('webhooks', metadata.instance.webhooks || [])
  instance.set('dev', false)
  const record = new Record($app.findCollectionByNameOrId(COLLECTION))
  const claim = $security.randomString(48)
  try {
    $app.runInTransaction((app) => {
      app.save(instance)
      instance.set('power', false)
      instance.set('autoVacuum', metadata.instance.autoVacuum)
      app.save(instance)
      record.set('direction', 'import')
      record.set('status', 'preparing')
      record.set('user', auth.id)
      record.set('instance', instance.id)
      record.set('expiresAt', metadata.expiresAt)
      record.set('sourceUrl', peer.origin)
      record.set('remoteId', peer.id)
      record.set('metadata', { peer: metadata, subdomain })
      app.save(record)
      writeCredentials(record.id, peer.code, claim)
    })
    peerRequest(peer.origin, peer.id, credentialsPath(record.id), 'claim', 'POST')
    saveStatus(record, 'queued')
    return e.json(202, { transfer: serialize(record) })
  } catch (error) {
    if (record.id) {
      try {
        $app.delete(record)
      } catch {}
      $os.removeAll(directory(record.id))
    }
    if (instance.id) {
      try {
        $app.delete(instance)
      } catch {}
    }
    throw new BadRequestError(`Impossible de réserver ce transfert : ${error}`)
  }
}

export const HandleInstanceTransferCancel = (e: core.RequestEvent) => {
  const auth = requireAdmin(e)
  const record = find(pathId(e))
  assertOwner(record, auth)
  if (
    record.getString('direction') !== 'export' ||
    !['queued', 'ready', 'failed'].includes(record.getString('status'))
  ) {
    throw new BadRequestError(
      'Attendez la fin de la préparation avant d’annuler. Un transfert terminé ne peut pas être annulé.'
    )
  }
  $app.runInTransaction((app) => {
    const current = find(record.id, app)
    if (!['queued', 'ready', 'failed'].includes(current.getString('status')))
      throw new BadRequestError('Transfert en cours de préparation.')
    current.set('status', 'revoked')
    app.save(current)
    if (object(current.getString('metadata')).wasRunning) {
      const instance = app.findRecordById('instances', current.getString('instance'))
      instance.set('power', true)
      app.save(instance)
    }
  })
  return e.json(200, { transfer: serialize(find(record.id)) })
}

export const HandleInstanceTransferRetry = (e: core.RequestEvent) => {
  const auth = requireAdmin(e)
  const record = find(pathId(e))
  assertOwner(record, auth)
  assertFresh(record)
  if (
    record.getString('status') !== 'failed' ||
    record.getString('direction') !== 'import' ||
    !exists(credentialsPath(record.id))
  ) {
    throw new BadRequestError('Ce transfert ne peut pas être repris. Préparez un nouveau lien sur la source.')
  }
  const instance = $app.findRecordById('instances', record.getString('instance'))
  if (instance.getBool('power')) throw new BadRequestError('Arrêtez l’instance cible avant de reprendre le transfert.')
  $app.runInTransaction((app) => {
    const current = find(record.id, app)
    if (current.getString('status') !== 'failed') throw new BadRequestError('Ce transfert est déjà en cours.')
    current.set('status', 'queued')
    current.set('error', '')
    app.save(current)
  })
  return e.json(202, { transfer: serialize(find(record.id)) })
}

export const BeforeUpdate_instanceTransferPower = (e: core.RecordEvent) => {
  if (!e.record) return
  if (!e.record.getBool('power')) return
  // Bootstrap resets existing running instances before custom migrations create the transfer collection.
  try {
    e.app.findCollectionByNameOrId(COLLECTION)
  } catch {
    return
  }
  const active = e.app.findRecordsByFilter(
    COLLECTION,
    'instance = {:id} && (' +
      ACTIVE_EXPORT +
      ' || (direction = "import" && status != "complete" && status != "revoked"))',
    '',
    1,
    0,
    { id: e.record.id }
  )
  if (active.length) throw new BadRequestError('Terminez ou annulez le transfert avant de démarrer cette instance.')
}

export const BeforeDelete_instanceTransfer = (e: core.RecordEvent) => {
  if (!e.record) return
  try {
    e.app.findCollectionByNameOrId(COLLECTION)
  } catch {
    return
  }
  const active = e.app.findRecordsByFilter(
    COLLECTION,
    'instance = {:id} && (status = "queued" || status = "preparing" || status = "ready" || status = "downloading" || status = "restoring")',
    '',
    1,
    0,
    { id: e.record.id }
  )
  if (active.length) throw new BadRequestError('Terminez ou annulez le transfert avant de supprimer cette instance.')
}

export const AfterDelete_instanceTransfer = (e: core.RecordEvent) => {
  if (e.record) $os.removeAll(directory(e.record.id))
}

const prepareExport = (record: core.Record) => {
  const instance = $app.findRecordById('instances', record.getString('instance'))
  const auth = $app.findRecordById('users', record.getString('user'))
  const backup = backups.create(instance, auth, record.id, (created) => {
    record.set('backup', created.id)
    $app.save(record)
  })
  record.set('metadata', {
    ...object(record.getString('metadata')),
    instance: metadataForPeer(record).instance,
    compressedBytes: Number(backup.get('compressedBytes')),
  })
  saveStatus(record, 'ready')
}

const performImport = (record: core.Record) => {
  assertFresh(record)
  const instance = $app.findRecordById('instances', record.getString('instance'))
  if (instance.getBool('power')) throw new Error('L’instance cible doit rester arrêtée.')
  const metadata = object(record.getString('metadata')).peer as TransferMetadata
  const peer = parsePeer(
    `${record.getString('sourceUrl')}/api/instance-transfers/${record.getString('remoteId')}#${'x'.repeat(48)}`
  )
  const archive = metadata.archive!
  let backup: core.Record
  if (!record.getString('backup')) {
    peerRequest(peer.origin, peer.id, credentialsPath(record.id), 'claim', 'POST')
    const path = partialPath(record.id)
    reportImportProgress(record, 'downloading')
    if (fileSize(path) > archive.compressedBytes) $os.remove(path)
    if (!exists(path) || fileSize(path) !== archive.compressedBytes) {
      downloadArchive(record, peer.origin, peer.id, archive.compressedBytes)
    }
    reportImportProgress(record, 'verifying')
    if (fileSize(path) !== archive.compressedBytes || backups.checksum(path) !== archive.checksum) {
      $os.remove(path)
      throw new Error(
        'L’archive reçue est incomplète ou son empreinte SHA-256 ne correspond pas. Relancez le transfert.'
      )
    }
    const auth = $app.findRecordById('users', record.getString('user'))
    backup = backups.receive(instance, auth, path, archive)
    record.set('backup', backup.id)
    $app.save(record)
  } else backup = $app.findRecordById('instance_backups', record.getString('backup'))
  saveStatus(record, 'restoring')
  reportImportProgress(record, 'restoring')
  backups.restore(instance, backup)
  // The target stays off until the operator starts it; no duplicate writers on acknowledgement failure.
  peerRequest(peer.origin, peer.id, credentialsPath(record.id), 'complete', 'POST')
  saveStatus(record, 'complete')
  removeCredentials(record.id)
}

export const HandleInstanceTransferDispatcher = () => {
  let record: core.Record | undefined
  $app.runInTransaction((app) => {
    const busy = app.findRecordsByFilter(
      COLLECTION,
      'status = "preparing" || status = "downloading" || status = "restoring"',
      '',
      1,
      0
    )
    if (busy.length) return
    const queued = app.findRecordsByFilter(COLLECTION, 'status = "queued"', 'created', 1, 0)
    if (!queued.length) return
    record = queued[0]!
    record.set('status', record.getString('direction') === 'export' ? 'preparing' : 'downloading')
    app.save(record)
  })
  if (!record) return
  try {
    assertFresh(record)
    if (record.getString('direction') === 'export') prepareExport(record)
    else performImport(record)
  } catch (error) {
    if (record.getString('direction') === 'import') reportImportProgress(record, 'failed')
    saveStatus(record, 'failed', `${error}`.slice(0, 1800))
  }
}

export const HandleInstanceTransferBootstrap = () => {
  try {
    $app.findCollectionByNameOrId(COLLECTION)
  } catch {
    return
  }
  // A server restart interrupts workers. Never restart an instance or replay an incomplete restore automatically.
  const interrupted = $app.findRecordsByFilter(
    COLLECTION,
    'status = "preparing" || status = "downloading" || status = "restoring"',
    '',
    500,
    0
  )
  for (const record of interrupted) {
    if (!record) continue
    if (record.getString('backup')) {
      const backup = $app.findRecordById('instance_backups', record.getString('backup'))
      if (backup.getString('status') === 'running') {
        backup.set('status', 'failed')
        backup.set('error', 'Préparation interrompue par le redémarrage du serveur.')
      }
      if (backup.getString('restoreState') === 'running') {
        backup.set('restoreState', 'failed')
        backup.set('restoreError', 'Restauration interrompue. Le transfert peut être repris.')
        backup.set('restoreUpdatedAt', now())
      }
      $app.save(backup)
    }
    saveStatus(record, 'failed', 'Transfert interrompu par le redémarrage du serveur. La source est conservée.')
  }
}
