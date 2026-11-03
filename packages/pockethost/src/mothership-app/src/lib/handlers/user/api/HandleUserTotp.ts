import { createTotpSecurity } from '../../../security/totpSecurity'

const security = createTotpSecurity({
  authCollection: 'users',
  issuer: `PocketHost ${$os.getenv('APP_URL') || ''}`.trim(),
})
export const InitializeDashboardTotp = security.initialize
export const HandleDashboardTotpStatus = security.status
export const HandleDashboardTotpSetup = security.setup
export const HandleDashboardTotpConfirm = security.confirm
export const HandleDashboardTotpDisable = security.disable
export const HandleDashboardTotpRegenerate = security.regenerate
export const HandleDashboardTotpAuth = security.authenticate
export const GuardDashboardTotp = security.guard

export const HandleDashboardServiceDelegate = security.delegateService
