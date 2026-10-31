/**
 * Livraisons de webhooks acceptées hors de la liste d'adresses du dashboard : chemins exacts uniquement (aucun préfixe,
 * aucun slash final). Le contrôleur vérifie ensuite la signature HMAC (ou le jeton GitLab) sur le corps brut.
 *
 * - `/api/web-apps/github/webhook` : connexion GitHub principale (chemin legacy, inchangé).
 * - `/api/web-apps/github/webhook/<15 car. [a-z0-9]>` : connexions GitHub supplémentaires (pas de `/webhook/default`).
 * - `/api/web-git-sources/webhook/<24 car.>` : sources « Dépôt Git ».
 */
import { dashboardRequestHost } from '../../../../../common/platformDashboardHosts'

const GITHUB_WEBHOOK = '/api/web-apps/github/webhook'
const GITHUB_CONNECTION_WEBHOOK = /^\/api\/web-apps\/github\/webhook\/[a-z0-9]{15}$/
const GIT_WEBHOOK = /^\/api\/web-git-sources\/webhook\/[A-Za-z0-9]{24}$/

export const isWebhookException = (method: string, path: string, host: string, dashboardHosts: Set<string>) =>
  method === 'POST' &&
  (path === GITHUB_WEBHOOK || GITHUB_CONNECTION_WEBHOOK.test(path) || GIT_WEBHOOK.test(path)) &&
  dashboardHosts.has(dashboardRequestHost(host))
