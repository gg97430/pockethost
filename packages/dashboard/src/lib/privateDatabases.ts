export type PrivateDatabaseBinding = {
  id: string
  appId: string
  appName: string
  instanceId: string
  instanceName: string
  port: number
  enabled: boolean
  url: string
  envName: string
  status: 'pending' | 'ready' | 'unavailable'
  lastSeenAt: string
  errorCode: string
  authorized: boolean
  available: boolean
  needsRestart: boolean
  serviceCount: number
}

export type PrivateDatabaseAccess = {
  supported: boolean
  enabled: boolean
  bindings: PrivateDatabaseBinding[]
  applications: { id: string; name: string }[]
}

const problems: Record<string, string> = {
  application_stopped: 'Démarrez l’application pour utiliser cette connexion.',
  base_stopped: 'La base est arrêtée. La connexion sera disponible après son démarrage.',
  authorization_revoked: 'Un administrateur ayant accès à la base et à l’application doit renouveler la liaison.',
  broker_unavailable: 'L’accès privé est momentanément indisponible sur le serveur.',
  proxy_unavailable: 'La connexion locale de l’application est momentanément indisponible.',
  configuration: 'La configuration de la liaison doit être vérifiée.',
}

export const privateDatabaseStatus = (binding: PrivateDatabaseBinding, now = Date.now()) => {
  if (!binding.enabled) return { label: 'Désactivé', tone: 'neutral', detail: '' }
  if (!binding.authorized)
    return {
      label: 'Autorisation à renouveler',
      tone: 'warning',
      detail: problems.authorization_revoked!,
    }
  const lastSeen = Date.parse((binding.lastSeenAt || '').replace(' ', 'T'))
  const fresh = Number.isFinite(lastSeen) && now - lastSeen < 90000
  if (binding.status === 'ready' && fresh) return { label: 'Prêt', tone: 'success', detail: '' }
  if (binding.errorCode)
    return {
      label: 'Indisponible',
      tone: 'warning',
      detail: problems[binding.errorCode] || 'La connexion privée est momentanément indisponible.',
    }
  return {
    label: 'Vérification en cours',
    tone: 'neutral',
    detail: 'L’état de la connexion sera actualisé automatiquement.',
  }
}
