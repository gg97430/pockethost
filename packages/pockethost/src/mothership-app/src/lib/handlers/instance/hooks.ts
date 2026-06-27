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
