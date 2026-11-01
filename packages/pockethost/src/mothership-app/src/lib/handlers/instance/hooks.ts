routerAdd(
  'GET',
  '/api/instance/{id}/litestream/status',
  (e) => require(`${__hooks}/mothership`).HandleInstanceLitestreamTelemetry(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/instance/{id}/litestream/verify',
  (e) => require(`${__hooks}/mothership`).HandleInstanceLitestreamVerify(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/instance/{id}/diagnostics',
  (e) => require(`${__hooks}/mothership`).HandleInstanceDiagnostics(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/instance/{id}/performance',
  (e) => require(`${__hooks}/mothership`).HandleInstancePerformance(e),
  $apis.requireAuth('users')
)
const DEFAULT_BACKUP_IMPORT_LIMIT_BYTES = 12 * 1024 * 1024 * 1024
routerAdd('GET', '/api/instance/{id}/console', (e) => require(`${__hooks}/mothership`).HandleInstanceConsoleInfo(e))
routerAdd(
  'POST',
  '/api/instance/{id}/console',
  (e) => require(`${__hooks}/mothership`).HandleInstanceConsoleTicket(e),
  $apis.requireAuth('users')
)
routerAdd('POST', '/api/instance/{id}/console/consume', (e) =>
  require(`${__hooks}/mothership`).HandleInstanceConsoleConsume(e)
)
cronAdd('console_ticket_cleanup', '*/5 * * * *', () => require(`${__hooks}/mothership`).PruneInstanceConsoleTickets())
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
  '/api/instance/{id}/transfers',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceTransferCreate(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'GET',
  '/api/instance-transfers',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceTransfersList(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'POST',
  '/api/instance-transfers/inspect',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceTransferInspect(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'POST',
  '/api/instance-transfers/import',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceTransferImport(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'POST',
  '/api/instance-transfers/{transferId}/cancel',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceTransferCancel(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'POST',
  '/api/instance-transfers/{transferId}/retry',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceTransferRetry(e)
  },
  $apis.requireAuth()
)
routerAdd(
  'POST',
  '/api/instance-transfers/{transferId}/link',
  (e) => {
    return require(`${__hooks}/mothership`).HandleInstanceTransferRenewLink(e)
  },
  $apis.requireAuth()
)
routerAdd('GET', '/api/instance-transfers/{transferId}/metadata', (e) => {
  return require(`${__hooks}/mothership`).HandleInstanceTransferMetadata(e)
})
routerAdd('POST', '/api/instance-transfers/{transferId}/claim', (e) => {
  return require(`${__hooks}/mothership`).HandleInstanceTransferClaim(e)
})
routerAdd('GET', '/api/instance-transfers/{transferId}/archive', (e) => {
  return require(`${__hooks}/mothership`).HandleInstanceTransferArchive(e)
})
routerAdd('POST', '/api/instance-transfers/{transferId}/complete', (e) => {
  return require(`${__hooks}/mothership`).HandleInstanceTransferComplete(e)
})
routerAdd('POST', '/api/instance-transfers/{transferId}/progress', (e) => {
  return require(`${__hooks}/mothership`).HandleInstanceTransferProgress(e)
})
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

/** Protect instances while a transfer is running and clean its private workspace after deletion. */
onRecordDelete((e) => {
  require(`${__hooks}/mothership`).BeforeDelete_instanceTransfer(e)
  e.next()
}, 'instances')

onRecordAfterDeleteSuccess((e) => {
  require(`${__hooks}/mothership`).AfterDelete_instanceTransfer(e)
  e.next()
}, 'instance_transfers')

/** Validate instance version */
onRecordUpdate((e) => {
  require(`${__hooks}/mothership`).BeforeUpdate_instanceTransferPower(e)
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

onBootstrap((e) => {
  e.next()
  require(`${__hooks}/mothership`).HandleInstanceTransferBootstrap()
})
cronAdd('instance-transfer-dispatcher', '* * * * *', () => {
  require(`${__hooks}/mothership`).HandleInstanceTransferDispatcher()
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

routerUse((e) => require(`${__hooks}/mothership`).AuditInstanceRequest(e))
routerAdd(
  'GET',
  '/api/instance/{id}/actions',
  (e) => require(`${__hooks}/mothership`).HandleInstanceActionLog(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/instance/{id}/backup-checks',
  (e) => require(`${__hooks}/mothership`).HandleInstanceBackupChecksGet(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/instance/{id}/backup-checks',
  (e) => require(`${__hooks}/mothership`).HandleInstanceBackupCheckCreate(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PUT',
  '/api/instance/{id}/backup-checks/policy',
  (e) => require(`${__hooks}/mothership`).HandleInstanceBackupCheckPolicy(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/instance/{id}/service-accounts',
  (e) => require(`${__hooks}/mothership`).HandleInstanceServiceAccounts(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/instance/{id}/service-accounts',
  (e) => require(`${__hooks}/mothership`).HandleInstanceServiceAccounts(e),
  $apis.requireAuth('users')
)
routerAdd('POST', '/api/instance/{id}/operations/consume', (e) =>
  require(`${__hooks}/mothership`).HandleInstanceOperationConsume(e)
)
onBootstrap((e) => {
  e.next()
  require(`${__hooks}/mothership`).BackupCheckBootstrap()
})
cronAdd('instance-backup-integrity-checks', '*/1 * * * *', () =>
  require(`${__hooks}/mothership`).BackupCheckDispatcher()
)

routerAdd(
  'GET',
  '/api/actions',
  (e) => require(`${__hooks}/mothership`).HandleAccountActionLog(e),
  $apis.requireAuth('users')
)

// Native collection endpoints must not bypass the platform-only migration-address controls.
onRecordCreateRequest((e) => require(`${__hooks}/mothership`).GuardInstanceHostnamesRequest(e), 'instances')
onRecordUpdateRequest((e) => require(`${__hooks}/mothership`).GuardInstanceHostnamesRequest(e), 'instances')
onRecordCreate((e) => require(`${__hooks}/mothership`).ValidateInstanceHostnames(e), 'instances')
onRecordUpdate((e) => require(`${__hooks}/mothership`).ValidateInstanceHostnames(e), 'instances')
