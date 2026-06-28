const DEFAULT_BACKUP_IMPORT_LIMIT_BYTES = 12 * 1024 * 1024 * 1024
const DEFAULT_BACKUP_IMPORT_CHUNK_LIMIT_BYTES = 64 * 1024 * 1024

const backupImportBodyLimitBytes = () => {
  const raw = $os.getenv('INSTANCE_BACKUP_UPLOAD_LIMIT_BYTES') || ''
  if (!raw) return DEFAULT_BACKUP_IMPORT_LIMIT_BYTES

  const value = Number(raw)
  if (!Number.isFinite(value) || value < 0) {
    return DEFAULT_BACKUP_IMPORT_LIMIT_BYTES
  }

  return Math.floor(value)
}

const backupImportChunkLimitBytes = () => {
  const raw = $os.getenv('INSTANCE_BACKUP_CHUNK_LIMIT_BYTES') || ''
  if (!raw) return DEFAULT_BACKUP_IMPORT_CHUNK_LIMIT_BYTES

  const value = Number(raw)
  if (!Number.isFinite(value) || value <= 0) {
    return DEFAULT_BACKUP_IMPORT_CHUNK_LIMIT_BYTES
  }

  return Math.floor(value)
}

routerAdd(
  'PUT',
  '/api/instance/{id}',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceUpdate(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'POST',
  '/api/instance',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceCreate(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'GET',
  '/api/instance/{id}/overview',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceOverview(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'POST',
  '/api/instance/{id}/duplicate',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceDuplicate(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'POST',
  '/api/instance/{id}/backups',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceBackupCreate(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'POST',
  '/api/instance/{id}/backups/import',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceBackupImport(e)
  },
  $apis.requireAuth(),
  $apis.bodyLimit(backupImportBodyLimitBytes())
)
routerAdd(
  'POST',
  '/api/instance/{id}/backups/import/chunked/start',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceBackupChunkedStart(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'POST',
  '/api/instance/{id}/backups/import/chunked/{uploadId}/chunk',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceBackupChunkedUpload(e)
  },
  $apis.requireAuth(),
  $apis.bodyLimit(backupImportChunkLimitBytes())
)
routerAdd(
  'POST',
  '/api/instance/{id}/backups/import/chunked/{uploadId}/complete',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceBackupChunkedComplete(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'DELETE',
  '/api/instance/{id}/backups/import/chunked/{uploadId}',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceBackupChunkedCancel(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'GET',
  '/api/instance/{id}/backups',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceBackupsList(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'GET',
  '/api/instance/{id}/backups/policy',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceBackupPolicyGet(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'PUT',
  '/api/instance/{id}/backups/policy',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceBackupPolicyUpdate(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'POST',
  '/api/instance/{id}/backups/policy/run',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceBackupPolicyRun(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'GET',
  '/api/instance/{id}/backups/{backupId}/download',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceBackupDownload(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'DELETE',
  '/api/instance/{id}/backups/{backupId}',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceBackupDelete(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'POST',
  '/api/instance/{id}/backups/{backupId}/restore',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceBackupRestore(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'POST',
  '/api/instance/{id}/backups/{backupId}/restore-new',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceBackupRestoreNew(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'DELETE',
  '/api/instance/{id}',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceDelete(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'POST',
  '/api/instances/runtime/reset',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstancesRuntimeReset(e)
  },
  $apis.requireSuperuserAuth()
)
/** Default autoVacuum to true (PocketBase bool zero-default is false) */
onRecordCreate((e) => {
  require(`${__hooks}/mothership`).BeforeCreate_autoVacuum(e)
  e.next()
}, 'instances')

/** Validate instance version */
onRecordUpdate((e) => {
  require(`${__hooks}/mothership`).BeforeUpdate_version(e)
  e.next()
}, 'instances')

/** Validate cname */
onRecordUpdate((e) => {
  require(`${__hooks}/mothership`).BeforeUpdate_cname(e)
  e.next()
}, 'instances')

/** Notify discord on instance create */
// onRecordAfterCreateSuccess((e) => {
//   e.next()
//   return require(`${__hooks}/mothership`).AfterCreate_notify_discord(e)
// }, 'instances')

onBootstrap((e) => {
  e.next()
  // return require(`${__hooks}/mothership`).HandleMigrateInstanceVersions(e)
})

/** Reset instance status to idle on start */
onBootstrap((e) => {
  e.next()
  return require(`${__hooks}/mothership`).HandleInstancesResetIdle(e)
})

/** Register automatic instance backup policies */
onBootstrap((e) => {
  e.next()
  return require(`${__hooks}/mothership`).HandleInstanceBackupPoliciesBootstrap(e)
})
