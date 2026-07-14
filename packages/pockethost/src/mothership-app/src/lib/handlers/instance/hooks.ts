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
  'GET',
  '/api/instance/{id}/metrics',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceMetrics(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'GET',
  '/api/instance/{id}/metrics/history',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceMetricsHistory(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'GET',
  '/api/instances/metrics',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstancesMetrics(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'GET',
  '/api/instance/{id}/monitoring',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceMonitoringGet(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'PUT',
  '/api/instance/{id}/monitoring',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceMonitoringUpdate(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'GET',
  '/api/instance/{id}/monitoring/history',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceMonitoringHistory(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'GET',
  '/api/instance/{id}/monitoring/incidents',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceMonitoringIncidents(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'POST',
  '/api/instance/{id}/monitoring/test',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceMonitoringTest(e)
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
  '/api/instance/{id}/backups/litestream',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceLitestreamPolicyGet(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'PUT',
  '/api/instance/{id}/backups/litestream',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceLitestreamPolicyUpdate(e)
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

/** Create or resolve backup monitoring incidents after terminal backup updates. */
onRecordAfterUpdateSuccess((e) => {
  e.next()
  require(`${__hooks}/mothership`).HandleInstanceBackupMonitoringUpdate(e)
}, 'instance_backups')

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

cronAdd('instance-backup-policy-dispatcher', '* * * * *', () => {
  require(`${__hooks}/mothership`).HandleInstanceBackupPolicyCronDispatcher()
})

/** Persist metrics and evaluate opt-in monitoring with a shared Docker snapshot. */
cronAdd('instance-resource-metrics-sampler', '* * * * *', () => {
  require(`${__hooks}/mothership`).CollectInstanceMetricsAndMonitoring()
})

/** Keep the metrics collection bounded to seven days. */
cronAdd('instance-resource-metrics-retention', '17 * * * *', () => {
  require(`${__hooks}/mothership`).PurgeExpiredInstanceResourceMetrics()
})

/** Keep detailed uptime checks for exactly thirty days. */
cronAdd('instance-health-checks-retention', '29 * * * *', () => {
  require(`${__hooks}/mothership`).PurgeExpiredInstanceHealthChecks()
})

/** Reconcile optional Litestream replication */
onBootstrap((e) => {
  e.next()
  return require(`${__hooks}/mothership`).HandleInstanceLitestreamBootstrap(e)
})
