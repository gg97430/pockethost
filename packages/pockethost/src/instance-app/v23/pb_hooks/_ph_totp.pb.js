onBootstrap((e) => {
  e.next()
  require(`${__hooks}/_ph_totp.js`).initialize()
})
routerUse((e) => require(`${__hooks}/_ph_totp.js`).guard(e))
onRecordAuthRequest((e) => require(`${__hooks}/_ph_totp.js`).authenticate(e), '_superusers')
routerAdd('GET', '/_/ph-auth', (e) => require(`${__hooks}/_ph_totp.js`).page(e))
routerAdd('POST', '/api/ph-console/login', (e) => require(`${__hooks}/_ph_totp.js`).consoleLogin(e))
routerAdd(
  'GET',
  '/api/ph-totp/status',
  (e) => require(`${__hooks}/_ph_totp.js`).status(e),
  $apis.requireSuperuserAuth()
)
routerAdd('POST', '/api/ph-totp/setup', (e) => require(`${__hooks}/_ph_totp.js`).setup(e), $apis.requireSuperuserAuth())
routerAdd(
  'POST',
  '/api/ph-totp/confirm',
  (e) => require(`${__hooks}/_ph_totp.js`).confirm(e),
  $apis.requireSuperuserAuth()
)
routerAdd(
  'POST',
  '/api/ph-totp/disable',
  (e) => require(`${__hooks}/_ph_totp.js`).disable(e),
  $apis.requireSuperuserAuth()
)
routerAdd(
  'POST',
  '/api/ph-totp/recovery',
  (e) => require(`${__hooks}/_ph_totp.js`).regenerate(e),
  $apis.requireSuperuserAuth()
)

routerAdd('POST', '/api/ph-service-accounts/execute', (e) =>
  require(`${__hooks}/_ph_totp.js`).executeServiceAccountCommand(e)
)
