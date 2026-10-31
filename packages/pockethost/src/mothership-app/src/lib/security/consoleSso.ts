/** Platform-owned settings only; never accept a control-plane URL from a browser request. */
export const consoleSsoConfig = () => {
  const instance = $os.getenv('PH_INSTANCE_ID')
  const dashboard = ($os.getenv('PH_MOTHERSHIP_URL') || '').replace(/\/+$/, '')
  const allowed = $os.getenv('PH_CONSOLE_SSO_ALLOW_HTTP') === '1' ? /^https?:\/\// : /^https:\/\//
  if (
    !/^[a-z0-9]{15}$/.test(instance) ||
    !allowed.test(dashboard) ||
    !/^https?:\/\/[a-z0-9.-]+(?::\d+)?$/i.test(dashboard)
  )
    return null
  return { instance, dashboard }
}

export const exchangeConsoleTicket = (e: core.RequestEvent, mint: (record: core.Record) => string) => {
  const config = consoleSsoConfig()
  if (!config) throw new BadRequestError('La connexion dashboard n’est pas configurée sur cette instance.')
  const input = JSON.parse(JSON.stringify(e.requestInfo().body || {}))
  if (!/^[A-Za-z0-9]{64}$/.test(input.ticket || '')) throw new UnauthorizedError('Autorisation de console invalide.')
  const response = $http.send({
    url: `${config.dashboard}/api/instance/${config.instance}/console/consume`,
    method: 'POST',
    timeout: 10,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ticket: input.ticket }),
  })
  const identity = JSON.parse(toString(response.raw))
  if (response.statusCode !== 200 || identity.instance !== config.instance || !identity.email || !identity.user) {
    throw new UnauthorizedError('La session dashboard est expirée ou l’autorisation a déjà été utilisée.')
  }
  let record: core.Record
  try {
    record = $app.findAuthRecordByEmail('_superusers', identity.email)
  } catch {
    // Admin sync may be disabled. Give the authorized owner a separate console identity, without changing passwords.
    const email = `dashboard-${identity.user}@pockethost.internal`
    try {
      record = $app.findAuthRecordByEmail('_superusers', email)
    } catch {
      record = new Record($app.findCollectionByNameOrId('_superusers'))
      record.set('email', email)
      record.setPassword($security.randomString(64))
      $app.save(record)
    }
  }
  record.ignoreEmailVisibility(true)
  e.response.header().set('Cache-Control', 'no-store')
  return e.json(200, { token: mint(record), record })
}
