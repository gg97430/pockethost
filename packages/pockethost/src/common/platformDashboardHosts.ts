/** These controller origins must never be reassigned to a tenant database or website. */
export const PLATFORM_DASHBOARD_HOSTS = [
  'monappli.re',
  'app.monappli.re',
  'app2.monappli.re',
  'app3.monappli.re',
  'app4.monappli.re',
]
export const PLATFORM_CONTROLLER_HOSTS = ['pockethost-central.monappli.re', 'pockethost-central.app2.monappli.re']
export const PLATFORM_CONTROL_HOSTS = [...PLATFORM_DASHBOARD_HOSTS, ...PLATFORM_CONTROLLER_HOSTS]

/** Exact authority from Host, never X-Forwarded-Host; malformed or nonstandard ports do not become aliases. */
export const dashboardRequestHost = (value: string | undefined) => {
  if (!value || !/^[a-z0-9.-]+(?::(?:80|443))?$/i.test(value)) return ''
  return value.replace(/:(?:80|443)$/, '').toLowerCase()
}

/** APP4 imports the two original controllers; other installations keep their own routing unchanged. */
export const legacyDashboardHosts = (dashboardHost: string) =>
  dashboardHost === 'app4.monappli.re' ? ['monappli.re', 'app.monappli.re', 'app2.monappli.re'] : []

/** Old mothership origins retain their API/admin semantics behind the same platform access policy. */
export const legacyControllerHosts = (dashboardHost: string) =>
  dashboardHost === 'app4.monappli.re' ? PLATFORM_CONTROLLER_HOSTS : []

/** Fixed destination from the reviewed offline merge, never selected from an unsigned request field. */
export const legacyGithubWebhookPath = (dashboardHost: string, host: string, path: string) =>
  dashboardHost === 'app4.monappli.re' &&
  (host === 'app2.monappli.re' || host === 'pockethost-central.app2.monappli.re') &&
  path === '/api/web-apps/github/webhook'
    ? '/api/web-apps/github/webhook/50a164e0ef1c85e'
    : path
