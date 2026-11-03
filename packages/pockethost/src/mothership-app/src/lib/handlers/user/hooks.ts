routerAdd(
  'POST',
  '/api/web-apps/_platform/delegate',
  (e) => require(`${__hooks}/mothership`).HandleDashboardServiceDelegate(e),
  $apis.requireAuth('_superusers')
)
onBootstrap((e) => {
  e.next()
  require(`${__hooks}/mothership`).InitializeDashboardTotp()
})
routerUse((e) => require(`${__hooks}/mothership`).GuardDashboardTotp(e))
onRecordAuthRequest((e) => require(`${__hooks}/mothership`).HandleDashboardTotpAuth(e), 'users')
routerAdd(
  'GET',
  '/api/account/totp',
  (e) => require(`${__hooks}/mothership`).HandleDashboardTotpStatus(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/account/totp/setup',
  (e) => require(`${__hooks}/mothership`).HandleDashboardTotpSetup(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/account/totp/confirm',
  (e) => require(`${__hooks}/mothership`).HandleDashboardTotpConfirm(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/account/totp/disable',
  (e) => require(`${__hooks}/mothership`).HandleDashboardTotpDisable(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/account/totp/recovery',
  (e) => require(`${__hooks}/mothership`).HandleDashboardTotpRegenerate(e),
  $apis.requireAuth('users')
)

routerAdd(
  'GET',
  '/api/userToken/{id}',
  (e) => {
    return require(`${__hooks}/mothership`).HandleUserTokenRequest(e)
  },
  $apis.requireSuperuserAuth()
)
