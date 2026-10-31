/** Alertes Applications web : liste et libellés partagés par les hooks mothership (Goja) et le dashboard. */
export const WEB_ALERT_BRAND = 'Kolab Hosting'
export const WEB_NOTIFICATION_EVENTS = [
  'health_degraded',
  'health_recovered',
  'deployment_failed',
  'backup_failed',
  'runtime_stopped',
  'task_failed',
  'certificate_expiring',
  'certificate_failed',
  'disk_low',
  'disk_recovered',
] as const
/** Événements par défaut d'un nouveau canal d'application (disque : canal plateforme, opt-in ici). */
export const WEB_NOTIFICATION_DEFAULT_EVENTS: string[] = WEB_NOTIFICATION_EVENTS.filter(
  (event) => !event.startsWith('disk_')
)
export const WEB_PLATFORM_EVENTS = [
  'disk_low',
  'disk_recovered',
  'worker_unresponsive',
  'worker_recovered',
  'controller_unreachable',
  'recovery_failed',
  'recovery_service_offline',
  'recovery_capture_stale',
  'backup_cleanup_failed',
  'runtime_stopped',
  'app_deployment_failed',
  'app_backup_failed',
  'app_health_degraded',
] as const
/** Par défaut, le canal plateforme ne reçoit pas les copies d'événements des applications. */
export const WEB_PLATFORM_DEFAULT_EVENTS: string[] = WEB_PLATFORM_EVENTS.filter((event) => !event.startsWith('app_'))
export const WEB_WEBHOOK_FORMATS = ['generic', 'slack', 'discord'] as const
export type WebWebhookFormat = (typeof WEB_WEBHOOK_FORMATS)[number]
export type WebNotificationEvent =
  | (typeof WEB_NOTIFICATION_EVENTS)[number]
  | (typeof WEB_PLATFORM_EVENTS)[number]
  | 'test'
  | 'alerts_grouped'
/** Title : libellé court (UI, objet email) ; sentence : phrase du message ; tab : onglet du lien profond. */
export const WEB_NOTIFICATION_LABELS: Record<WebNotificationEvent, { title: string; sentence: string; tab: string }> = {
  health_degraded: {
    title: 'Site indisponible',
    sentence: 'Le site ne répond plus correctement au contrôle de santé.',
    tab: 'health',
  },
  health_recovered: {
    title: 'Site rétabli',
    sentence: 'Le site répond à nouveau au contrôle de santé.',
    tab: 'health',
  },
  deployment_failed: {
    title: 'Échec de déploiement',
    sentence: 'Un déploiement a échoué ; la version publiée précédente est conservée si elle existe.',
    tab: 'deployments',
  },
  backup_failed: {
    title: 'Échec de sauvegarde',
    sentence: 'Une sauvegarde, sa planification ou sa copie S3 a échoué.',
    tab: 'backups',
  },
  runtime_stopped: {
    title: 'Site arrêté par la plateforme',
    sentence: 'Le site a été arrêté : limite de cache ou espace disque insuffisant.',
    tab: 'deployments',
  },
  task_failed: {
    title: 'Échec de tâche planifiée',
    sentence: 'Une tâche planifiée a échoué, dépassé sa durée maximale ou été suspendue.',
    tab: 'tasks',
  },
  certificate_expiring: {
    title: 'Certificat bientôt expiré',
    sentence: 'Un certificat expire dans moins de 30 jours.',
    tab: 'domains',
  },
  certificate_failed: {
    title: 'Échec de certificat',
    sentence: 'La création ou le renouvellement d’un certificat a échoué.',
    tab: 'domains',
  },
  disk_low: {
    title: 'Espace disque faible',
    sentence: 'L’espace disque du serveur est inférieur à 15 Gio.',
    tab: 'notifications',
  },
  disk_recovered: {
    title: 'Espace disque rétabli',
    sentence: 'La réserve disque du serveur est rétablie.',
    tab: 'notifications',
  },
  worker_unresponsive: {
    title: 'Worker web injoignable',
    sentence:
      'Le worker des applications web ne contacte plus le contrôleur depuis plus de 3 minutes : sondes, sauvegardes et webhooks sont à l’arrêt.',
    tab: '',
  },
  worker_recovered: {
    title: 'Worker web rétabli',
    sentence: 'Le worker des applications web contacte à nouveau le contrôleur.',
    tab: '',
  },
  controller_unreachable: {
    title: 'Contrôleur injoignable',
    sentence: 'Le worker ne parvient plus à joindre le contrôleur de la plateforme.',
    tab: '',
  },
  recovery_failed: {
    title: 'Échec de capture ou de test de reprise',
    sentence: 'Une capture ou un test de reprise de la plateforme a échoué.',
    tab: '',
  },
  recovery_service_offline: {
    title: 'Service de reprise hors ligne',
    sentence: 'Le service de reprise ne publie plus son état.',
    tab: '',
  },
  recovery_capture_stale: {
    title: 'Capture de reprise trop ancienne',
    sentence: 'Aucune capture de reprise vérifiée depuis plus de 36 heures alors que la planification est active.',
    tab: '',
  },
  backup_cleanup_failed: {
    title: 'Suppression de sauvegarde bloquée',
    sentence:
      'Une sauvegarde de site n’a pas pu être supprimée (rétention ou suppression manuelle) : son archive et ses objets S3 peuvent rester en place. Nouvel essai automatique sous 24 heures.',
    tab: '',
  },
  app_deployment_failed: {
    title: 'Échec de déploiement (copie plateforme)',
    sentence: 'Un déploiement d’application a échoué.',
    tab: 'deployments',
  },
  app_backup_failed: {
    title: 'Échec de sauvegarde (copie plateforme)',
    sentence: 'Une sauvegarde d’application a échoué.',
    tab: 'backups',
  },
  app_health_degraded: {
    title: 'Site indisponible (copie plateforme)',
    sentence: 'Un site ne répond plus au contrôle de santé.',
    tab: 'health',
  },
  test: {
    title: 'Test d’alerte',
    sentence: 'Notification de test demandée depuis le dashboard.',
    tab: 'notifications',
  },
  alerts_grouped: {
    title: 'Alertes regroupées',
    sentence: 'Des alertes ont été regroupées par la limite de fréquence pendant l’heure écoulée.',
    tab: 'notifications',
  },
}
export const webNotificationTitle = (event: string) =>
  (WEB_NOTIFICATION_LABELS as Record<string, { title: string }>)[event]?.title || event
