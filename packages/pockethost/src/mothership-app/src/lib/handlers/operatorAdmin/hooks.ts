routerAdd(
  'GET',
  '/api/admin/fleet',
  (e) => require(`${__hooks}/mothership`).HandleOperatorFleet(e),
  $apis.requireAuth('users')
)

routerAdd(
  'GET',
  '/api/admin/controls',
  (e) => require(`${__hooks}/mothership`).HandleOperatorControls(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PUT',
  '/api/admin/controls/memory',
  (e) => require(`${__hooks}/mothership`).HandleOperatorMemory(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PUT',
  '/api/admin/controls/maintenance',
  (e) => require(`${__hooks}/mothership`).HandleOperatorDeploymentMaintenance(e),
  $apis.requireAuth('users')
)

routerAdd(
  'GET',
  '/api/admin/monitoring/resources',
  (e) => require(`${__hooks}/mothership`).HandleOperatorResourceMetrics(e),
  $apis.requireAuth('users')
)

routerAdd(
  'GET',
  '/api/admin/_monitoring/config',
  (e) => require(`${__hooks}/mothership`).HandleOperatorMonitoringDeliveryConfig(e),
  $apis.requireAuth('_superusers')
)

routerAdd(
  'PATCH',
  '/api/admin/monitoring',
  (e) => require(`${__hooks}/mothership`).HandleOperatorMonitoringUpdate(e),
  $apis.requireAuth('users')
)

routerAdd(
  'GET',
  '/api/admin/monitoring',
  (e) => require(`${__hooks}/mothership`).HandleOperatorMonitoring(e),
  $apis.requireAuth('users')
)

routerAdd(
  'GET',
  '/api/admin/overview',
  (e) => {
    return require(`${__hooks}/mothership`).HandleOperatorAdminOverview(e)
  },
  $apis.requireAuth()
)

routerAdd(
  'POST',
  '/api/admin/users',
  (e) => {
    return require(`${__hooks}/mothership`).HandleOperatorAdminCreateUser(e)
  },
  $apis.requireAuth()
)

routerAdd(
  'PATCH',
  '/api/admin/users/{id}',
  (e) => {
    return require(`${__hooks}/mothership`).HandleOperatorAdminUpdateUser(e)
  },
  $apis.requireAuth()
)

routerAdd(
  'PUT',
  '/api/admin/settings',
  (e) => {
    return require(`${__hooks}/mothership`).HandleOperatorAdminUpdateSettings(e)
  },
  $apis.requireAuth()
)

routerAdd(
  'POST',
  '/api/admin/settings/backup-s3/test',
  (e) => {
    return require(`${__hooks}/mothership`).HandleOperatorAdminTestBackupS3(e)
  },
  $apis.requireAuth()
)

routerAdd(
  'POST',
  '/api/admin/settings/smtp/test',
  (e) => {
    return require(`${__hooks}/mothership`).HandleOperatorAdminTestSMTP(e)
  },
  $apis.requireAuth()
)

routerAdd(
  'GET',
  '/api/admin/disk-cleanup',
  (e) => {
    return require(`${__hooks}/mothership`).HandleOperatorAdminDiskCleanupPreview(e)
  },
  $apis.requireAuth()
)

routerAdd(
  'POST',
  '/api/admin/disk-cleanup',
  (e) => {
    return require(`${__hooks}/mothership`).HandleOperatorAdminDiskCleanupRun(e)
  },
  $apis.requireAuth()
)

routerAdd(
  'GET',
  '/api/workspace/overview',
  (e) => require(`${__hooks}/mothership`).HandleWorkspaceOverview(e),
  $apis.requireAuth('users')
)

routerAdd(
  'GET',
  '/api/admin/request-limits',
  (e) => require(`${__hooks}/mothership`).HandleOperatorRequestLimits(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/admin/request-limits/web',
  (e) => require(`${__hooks}/mothership`).HandleOperatorWebRequestLimit(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PUT',
  '/api/admin/request-limits/web',
  (e) => require(`${__hooks}/mothership`).HandleOperatorWebRequestLimit(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PUT',
  '/api/admin/request-limits',
  (e) => require(`${__hooks}/mothership`).HandleOperatorRequestLimitsPut(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/admin/request-limits/databases/{id}',
  (e) => require(`${__hooks}/mothership`).HandleOperatorDatabaseRequestLimits(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PUT',
  '/api/admin/request-limits/databases/{id}',
  (e) => require(`${__hooks}/mothership`).HandleOperatorDatabaseRequestLimits(e),
  $apis.requireAuth('users')
)
