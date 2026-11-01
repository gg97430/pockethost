import { consoleSsoConfig, exchangeConsoleTicket } from './lib/security/consoleSso'
import { consoleTotpPage } from './lib/security/consoleTotpPage'
import {
  assertServiceAccountActive,
  initializeServiceAccounts,
  trackServiceAuthentication,
} from './lib/security/serviceAccounts'
import { createTotpSecurity } from './lib/security/totpSecurity'

const security = createTotpSecurity({
  authCollection: '_superusers',
  issuer: `PocketBase ${$os.getenv('PH_INSTANCE_URL') || $os.getenv('PH_APP_NAME') || ''}`.trim(),
})
export { executeServiceAccountCommand } from './lib/security/serviceAccounts'
export const initialize = () => {
  security.initialize()
  initializeServiceAccounts()
}
export const status = security.status
export const setup = security.setup
export const confirm = security.confirm
export const disable = security.disable
export const regenerate = security.regenerate
export const authenticate = (e: core.RecordAuthRequestEvent) => {
  if (e.record) assertServiceAccountActive(e.record)
  security.authenticate(e)
  if (e.record) trackServiceAuthentication(e.record)
}
export const guard = security.guard
export const consoleLogin = (e: core.RequestEvent) => exchangeConsoleTicket(e, security.trustedToken)
export const page = (e: core.RequestEvent) => {
  e.response.header().set('Cache-Control', 'no-store')
  e.response.header().set('Referrer-Policy', 'no-referrer')
  const config = JSON.stringify(consoleSsoConfig()).replace(/</g, '\\u003c')
  return e.html(200, consoleTotpPage.replace('__PH_CONSOLE_SSO_CONFIG__', config))
}
