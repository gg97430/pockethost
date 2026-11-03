type ReplicationReport = {
  enabled: boolean
  positionState: string
  service: { state: string }
  local: { status?: string }
  metrics: { disk_full?: number }
  remote: { ok: boolean; partial?: boolean }
}
export const litestreamStatus = (report: ReplicationReport) => {
  if (!report.enabled)
    return {
      label: 'Synchro désactivée',
      tone: 'neutral',
      message: 'Activez la sauvegarde continue pour protéger cette base.',
    }
  if (report.service.state !== 'online')
    return { label: 'À vérifier', tone: 'warning', message: 'Le service de sauvegarde ne fonctionne pas actuellement.' }
  if (report.metrics.disk_full === 1)
    return {
      label: 'À vérifier',
      tone: 'warning',
      message: 'Le disque est plein. La sauvegarde peut être interrompue.',
    }
  if (report.local.status !== 'ok')
    return { label: 'À vérifier', tone: 'warning', message: 'La lecture de la base locale n’est pas confirmée.' }
  if (!report.remote.ok)
    return {
      label: 'À vérifier',
      tone: 'warning',
      message: 'Le stockage S3 n’est pas accessible ou son contrôle a échoué.',
    }
  if (report.positionState === 'caught-up' && !report.remote.partial)
    return {
      label: 'Synchro OK',
      tone: 'success',
      message: 'Les dernières modifications observées sont présentes sur S3.',
    }
  if (report.positionState === 'pending')
    return {
      label: 'Synchronisation en cours',
      tone: 'info',
      message: 'Les dernières modifications attendent leur confirmation sur S3.',
    }
  if (report.remote.partial)
    return {
      label: 'Synchro active',
      tone: 'info',
      message:
        'Le service est actif et S3 est accessible. Le contrôle partiel ne confirme pas encore que tout est à jour.',
    }
  return {
    label: 'À vérifier',
    tone: 'warning',
    message: 'Les mesures ne permettent pas encore de confirmer la synchronisation.',
  }
}
