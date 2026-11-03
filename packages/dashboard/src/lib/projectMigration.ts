export const migrationPeers = ['app3', 'app4', 'app5'] as const
export type MigrationPeer = (typeof migrationPeers)[number]
export const migrationPeer = (value: unknown): MigrationPeer | null =>
  migrationPeers.includes(value as MigrationPeer) ? (value as MigrationPeer) : null
export const migrationHost = (peer: MigrationPeer) => `${peer}.monappli.re`
export const migrationResourceHref = (peer: unknown, kind: 'application' | 'database', id: unknown) => {
  const server = migrationPeer(peer)
  return server && typeof id === 'string' && /^[a-z0-9]{15}$/.test(id)
    ? `https://${migrationHost(server)}/${kind === 'database' ? 'instances' : 'applications'}/${id}`
    : undefined
}
export type MigrationBlocker = { code: string; appId: string; name: string }
export type MigrationSourcePreview = {
  projectId: string
  projectName: string
  revision: string
  apps: {
    id: string
    name: string
    databases: string[]
    scheduledTasks: number
    domains: { host: string; status: string }[]
    externalDependencies: string[]
  }[]
  databases: { id: string; name: string; version: string }[]
  blockers: MigrationBlocker[]
}
export type MigrationDnsRecord = {
  id?: string
  type: string
  name: string
  content: string
  proxied?: boolean
  ttl?: number
}
export type MigrationDnsPreview = {
  version: 1
  migrationId: string
  planRevision: string
  revision: string
  createdAt: number
  expiresAt: number
  changes: { host: string; zoneId?: string; before: MigrationDnsRecord[]; after: MigrationDnsRecord }[]
}
export type MigrationPlan = {
  id: string
  revision: string
  projectId: string
  targetProjectId: string
  projectName: string
  source: MigrationPeer
  target: MigrationPeer
  ownerId: string
  expiresAt: number
  requiredBytes: number
  requiredMemoryMiB: number
  restoreBytes: number
  archiveSha256: string
  writeBarrier?: {
    version: 1
    projectId: string
    source: MigrationPeer
    attestedAt: string
    sourceWritesPaused: boolean
    sourceCronsPaused: boolean
    sourceWorkersPaused: boolean
    attestationOnly: true
  } | null
  drill?: {
    verified: boolean
    sandboxUid: number
    noExternalNetwork: boolean
    cleanedUp: boolean
    services: number
  } | null
  applications: { sourceId: string; targetId: string; name: string; hasFront: boolean }[]
  databases: {
    sourceId: string
    targetId: string
    subdomain: string
    version: string
    targetHost: string
    memoryReservationMiB?: number
    memoryRequestMiB?: number
  }[]
  environment: { appTargetId: string; name: string; action: string; build: boolean; runtime: boolean }[]
  bindings: { appTargetId: string; instanceTargetId: string; port: number }[]
  scheduledTasks: {
    name: string
    appTargetId: string
    schedule: string
    timezone: string
    sourceEnabled: boolean
    enabled: false
  }[]
  domains: { host: string; appTargetId: string; targetStatus: string; proxied: true }[]
  volumes: { appTargetId: string; name: string; bytes: number }[]
  workers: { appTargetId: string; service: string; enabled: false }[]
  blockers: MigrationBlocker[]
  limitations: string[]
}
export type MigrationItem = {
  id: string
  revision: string
  status: string
  projectName: string
  source: MigrationPeer
  target: MigrationPeer
  approvedAt: string
  importedAt: string
  releasedAt: string
  dnsRevision: string
  dnsApprovedAt: string
  dnsPreview?: MigrationDnsPreview | null
  plan: MigrationPlan
}
export const migrationStatusLabels: Record<string, string> = {
  review: 'À examiner',
  approved: 'Import autorisé',
  importing: 'Import en cours',
  imported_paused: 'Copie suspendue',
  released: 'Déverrouillée pour recette',
  failed: 'Import interrompu',
  rolling_back: 'Retrait de la copie',
  rolled_back: 'Copie retirée',
  cutover: 'Bascule enregistrée',
}
export const migrationBlockerLabel = (code: string) =>
  ({
    build_url_requires_rebuild: 'Une URL de construction change : une nouvelle image est nécessaire.',
    native_hooks_require_review: 'Les hooks ou migrations de cette base nécessitent une revue.',
    shared_database_writers: 'Cette base est partagée avec des applications hors du projet.',
    shared_database_dependency: 'Cette base est partagée avec une autre application.',
    external_dependency: 'Une dépendance externe doit être incluse dans le périmètre.',
    external_storage_not_captured: 'Un stockage ou accès BDD externe manque dans la capture.',
    isolated_drill_required: 'La reconstruction isolée de cette capsule doit être vérifiée.',
  })[code] || 'Un point du périmètre nécessite une vérification.'
export const migrationExpired = (item: MigrationItem, now = Date.now()) =>
  ['review', 'approved'].includes(item.status) &&
  (!Number.isFinite(item.plan.expiresAt) || item.plan.expiresAt * 1000 <= now)
export const migrationDnsReviewable = (item: MigrationItem, now = Date.now()) => {
  const value = item.dnsPreview
  return (
    !!value &&
    value.version === 1 &&
    value.migrationId === item.id &&
    value.planRevision === item.revision &&
    /^[a-f0-9]{64}$/.test(value.revision) &&
    Number.isSafeInteger(value.expiresAt) &&
    value.expiresAt * 1000 > now &&
    Array.isArray(value.changes) &&
    value.changes.length > 0 &&
    value.changes.length <= 1000
  )
}
export const migrationDrillVerified = (item: MigrationItem) =>
  item.plan.drill?.verified === true &&
  item.plan.drill.sandboxUid === 1002 &&
  item.plan.drill.noExternalNetwork === true &&
  item.plan.drill.cleanedUp === true &&
  Number.isSafeInteger(item.plan.drill.services) &&
  item.plan.drill.services > 0
/** An operator attestation is displayed as such; it does not prove business writes stopped. */
export const migrationHasWriteBarrier = (item: MigrationItem) => {
  const value = item.plan.writeBarrier
  return (
    !!value &&
    value.version === 1 &&
    value.projectId === item.plan.projectId &&
    value.source === item.source &&
    value.sourceWritesPaused === true &&
    value.sourceCronsPaused === true &&
    value.sourceWorkersPaused === true
  )
}
export const migrationExportCommand = (value: MigrationSourcePreview) => {
  if (!/^[a-z0-9]{15}$/.test(value.projectId) || !/^[a-f0-9]{64}$/.test(value.revision)) return ''
  const destination = `/var/lib/kolab-project-migration/exports/${value.projectId}-${value.revision.slice(0, 12)}`
  return `sudo install -d -m 0700 /var/lib/kolab-project-migration/exports\nsudo /usr/bin/python3 -I /opt/pockethost-web/web_project_migration.py export --project-id ${value.projectId} --revision ${value.revision} --config /etc/kolab/project-migration.json --output ${destination}`
}
