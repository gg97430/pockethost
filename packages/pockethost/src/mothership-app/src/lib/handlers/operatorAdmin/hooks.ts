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
