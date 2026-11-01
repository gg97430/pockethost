import { client } from '$src/pocketbase-client'
import type { WebAppAccess } from './webAccess'

export type WebAppEnvironment = {
  name: string
  build: boolean
  runtime: boolean
  hasValue?: boolean
  public?: boolean
}
export type WebAppEnvironmentDraft = WebAppEnvironment & { value?: string; remove?: boolean }
export type WebApp = {
  id: string
  name: string
  runtimeKind?: 'nextjs' | 'auto' | 'dockerfile' | 'image' | 'compose' | 'worker'
  sourceDeployable?: boolean
  repositoryId: string | number
  /** 'owner/repo' (GitHub App) ou 'git:'+canonical (Dépôt Git) : afficher via webGitRepositoryLabel(). */
  repository: string
  /** Source du code : GitHub App (défaut, absent) ou Dépôt Git avec clé de déploiement. */
  source?: 'github' | 'git'
  /** Source « Dépôt Git » rattachée (web_git_sources). */
  gitSourceId?: string
  /** Connexion GitHub App de l'application ; absente = connexion principale ('default'). */
  githubConnectionId?: string
  branch: string
  rootDirectory: string
  healthPath: string
  generatedDomain: string
  url: string
  status: string
  deletion?: { id: string; status: string; stage: string; error: string } | null
  autoDeploy: boolean
  activeDeployment: string
  desiredCommit: string
  lastError: string
  created: string
  updated: string
  env: WebAppEnvironment[]
  /** Droits de l'utilisateur courant sur cette application (projet, rôle, capacités). */
  access?: WebAppAccess
}
export type WebDeployment = {
  id: string
  appId: string
  action: string
  commit: string
  status: string
  stage: string
  error: string
  created: string
  startedAt: string
  finishedAt: string
  elapsedSeconds: number
  active: boolean
  available?: boolean
  cancelRequested?: boolean
  cancellable?: boolean
  retryable?: boolean
  stageKey?: string
  heartbeatAt?: string
  phases?: WebDeploymentPhase[]
  retriedFrom?: string
}
export type WebCapabilities = {
  workerReady: boolean
  githubConfigured: boolean
  rootlessReady: boolean
  /** Git et openssh-client présents sur le worker (source « Dépôt Git »). */
  gitAvailable?: boolean
  /** Railpack (binaire, mise et frontend vérifiés) présent sur le worker (construction automatique). */
  railpackAvailable?: boolean
  /** Nombre de connexions GitHub (jamais leurs libellés : visibles par tous les lecteurs). */
  githubConnectionCount?: number
  error?: string
}
export type WebAppDomain = {
  id: string
  hostname: string
  status: string
  ownershipRequired?: boolean
  ownershipName: string
  ownershipValue: string
  dnsTarget: string
  lastError: string
  certificateExpiresAt: string
  created: string
  cloudflare?: {
    enabled: boolean
    status: 'pending_validation' | 'pending_https' | 'active' | 'disabled' | 'error'
    lastError: string
  }
}
export type WebRepository = { id: string | number; fullName: string; defaultBranch: string; private: boolean }
export type WebGitHubSettings = {
  githubAppId: string
  githubAppSlug: string
  githubInstallationId: string
  hasPrivateKey: boolean
  hasWebhookSecret: boolean
  webhookUrl: string
}
/** Élément de GET /github/connections (plateforme) : aucune clé privée ni secret n'est jamais renvoyé. */
export type WebGitHubConnectionSummary = {
  /** 'default' pour la connexion principale, sinon 15 caractères [a-z0-9]. */
  id: string
  label: string
  isPrincipal: boolean
  disabled: boolean
  configured: boolean
  app: { id: string; slug: string; owner?: string }
  installationId: string
  hasPrivateKey: boolean
  hasWebhookSecret: boolean
  webhookUrl: string
  appCount: number
  lastDelivery?: { at: string; status: string } | null
}
export type WebGitHubConnectionList = { items: WebGitHubConnectionSummary[]; max: number }
/** Saisie manuelle d'une connexion : un secret laissé vide est conservé lors d'une modification. */
export type WebGitHubConnectionInput = {
  label?: string
  disabled?: boolean
  githubAppId?: string
  githubAppSlug?: string
  githubInstallationId?: string
  privateKey?: string
  webhookSecret?: string
}
export type WebGitHubConnectionDeleted = { deleted: boolean; notice?: string; manageUrl?: string }
export const webRequest = <T>(path: string, options: Record<string, unknown> = {}) =>
  client().client.send<T>(
    path === '/projects' || path.startsWith('/projects/')
      ? `/api/web-projects${path.slice(9)}`
      : `/api/web-apps${path}`,
    { ...options, requestKey: null }
  )
export const webError = (error: unknown) => {
  if (error instanceof Error) {
    try {
      return client().parseError(error)[0] || error.message || 'La demande n’a pas pu aboutir.'
    } catch {
      return error.message || 'La demande n’a pas pu aboutir.'
    }
  }
  return String(error) || 'La demande n’a pas pu aboutir.'
}
export const webDate = (value?: string) => (value ? new Date(value).toLocaleString('fr-FR') : '—')
export const webCommit = (value?: string) => (value ? value.slice(0, 8) : '—')
export const webLabels: Record<string, string> = {
  deleting: 'Suppression en cours',
  stopped: 'Arrêtée',
  starting: 'Démarrage',
  running: 'En cours',
  ready: 'Prête',
  failed: 'Échec',
  cancelled: 'Annulé',
  queued: 'En attente',
  fetching: 'Récupération du code',
  checkout: 'Récupération du code',
  building: 'Construction',
  build: 'Construction',
  starting_container: 'Démarrage',
  checking: 'Contrôle de santé',
  health: 'Contrôle de santé',
  publishing: 'Publication',
  publish: 'Publication',
  complete: 'Terminée',
  deploy: 'Déploiement',
  domain: 'Domaine',
  'apply-runtime': 'Variables d’exécution',
  'restore-backup': 'Restauration de sauvegarde',
  'ci-deploy': 'Image construite en CI',
  promote: 'Promotion d’environnement',
  'preview-remove': 'Fermeture de preview',
  rollback: 'Retour arrière',
  start: 'Démarrage',
  stop: 'Arrêt',
  pending_dns: 'DNS à configurer',
  verified: 'Propriété vérifiée',
  pending_certificate: 'Certificat en préparation',
  active: 'Actif',
  error: 'À vérifier',
}

export type WebGitHubPending = {
  sessionId: string
  status: 'created' | 'awaiting_installation' | 'manifest_failed' | 'installation_failed' | 'completed'
  expiresAt: string
  app?: { id: string; slug: string; owner?: string }
  error?: string
  /** Connexion visée par l'assistant (nouvelle en mode 'add', existante en mode 'replace'). */
  connectionId?: string
  mode?: 'add' | 'replace'
  label?: string
}
export type WebGitHubConnection = { configured: boolean; connections?: number; pending: WebGitHubPending | null }
/** Réponse de POST /github/connect/install (et /install/check) une fois la connexion activée. */
export type WebGitHubConnectResult = {
  connected: boolean
  installation?: { id: string; account: string }
  repositoryCount: number
  connectionId?: string
  label?: string
  mode?: 'add' | 'replace'
}
export type WebGitHubManifestStart = {
  sessionId: string
  state: string
  createUrl: string
  manifest: Record<string, unknown>
  expiresAt: string
}
export type WebGitHubInstallStart = {
  sessionId: string
  installUrl: string
  state: string
  expiresAt: string
}
export { githubDestination, updateWebLogBuffer } from './webAppFlow'

export type WebDeploymentPhase = {
  key: string
  label: string
  startedAt: string
  finishedAt?: string
  durationMs: number
}
export type WebRuntime = {
  available: boolean
  checkedAt?: string
  scope: 'application-container' | 'application-services'
  serviceCount?: number
  state?: string
  cpuPercent?: number
  memoryBytes?: number
  memoryLimitBytes?: number
  pids?: number
  lastError?: string
}
export type WebLogChunk = { text: string; cursor?: string; reset?: boolean; truncated: boolean; hasMore?: boolean }

export type WebWebhookDelivery = {
  id: string
  deliveryId: string
  event: string
  repository: string
  branch: string
  payloadCommit: string
  resolvedCommit: string
  status: 'queued' | 'ignored'
  queued: number
  duplicateCount: number
  note: string
  created: string
  lastReceivedAt: string
}

export type WebTerminalGrant = {
  sessionId: string
  token: string
  expiresAt: string
  wsPath: '/api/web-terminal/connect'
  shell: '/bin/sh' | '/bin/bash'
  idleTimeoutSeconds: number
  maxDurationSeconds: number
}

export type WebGitHubHealth = {
  configured: boolean
  checkedAt: string
  ok: boolean
  app?: { id: string; slug: string; owner?: string }
  installation?: { id: string; account?: string; repositorySelection?: string }
  permissions?: { contents?: string; metadata?: string; pull_requests?: string }
  repositoryCount?: number
  previewSupported?: boolean
  webhook?: {
    url?: string
    lastReceivedAt?: string
    lastStatus?: string
    /** URL lue sur GitHub (action `hook-config`) ; `urlMismatch` si elle diffère de celle de cette connexion. */
    configuredUrl?: string
    urlMismatch?: boolean
  }
  manageUrl?: string
  error?: string
  connectionId?: string
  label?: string
  disabled?: boolean
}
export type WebRepositoryPage = {
  items: WebRepository[]
  page: number
  hasMore: boolean
  totalItems?: number
  configured?: boolean
  truncated?: boolean
}
export type WebBranchPage = { items: { name: string }[]; page: number; hasMore: boolean; totalItems?: number }

const githubConnectionPath = (id: string) => `/github/connections/${encodeURIComponent(id)}`
export const webGithubConnections = () => webRequest<WebGitHubConnectionList>('/github/connections')
export const webGithubConnectionCreate = (body: WebGitHubConnectionInput) =>
  webRequest<{ connection?: WebGitHubConnectionSummary }>('/github/connections', { method: 'POST', body })
export const webGithubConnectionUpdate = (id: string, body: WebGitHubConnectionInput) =>
  webRequest<{ connection?: WebGitHubConnectionSummary }>(githubConnectionPath(id), { method: 'PATCH', body })
export const webGithubConnectionDelete = (id: string) =>
  webRequest<WebGitHubConnectionDeleted>(githubConnectionPath(id), { method: 'DELETE' })
