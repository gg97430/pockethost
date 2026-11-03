import { monitoringStale, type MonitorCheck, type MonitoringReport, type MonitorState } from './platformMonitoring'

export type DiagnosticBinding = {
  id: string
  appId: string
  instanceId: string
  status: string
  errorCode: string
  lastSeenAt: string
}
export type DiagnosticDeployment = {
  id: string
  appId: string
  status: 'failed' | 'cancelled'
  finishedAt: string
  category: 'resources' | 'build' | 'health' | 'superseded' | 'interrupted' | 'other'
}
export type DiagnosticEvidence = {
  id: string
  name: string
  detail: string
  state: MonitorState
  href?: string
  checkedAt: string
}
export type PlatformDiagnostic = {
  id: string
  title: string
  state: 'error' | 'warning' | 'unknown'
  explanation: string
  evidence: DiagnosticEvidence[]
  checkIds: string[]
  additionalEvidence: number
  hypotheses: string[]
  nextAction: string
}
export type PlatformDiagnostics = {
  checkedAt: string
  stale: boolean
  dependencyInventoryAvailable: boolean
  eventInventoryAvailable: boolean
  items: PlatformDiagnostic[]
  omittedCount: number
  events: (DiagnosticDeployment & { appName: string })[]
}

const ID = /^[a-z0-9]{15}$/
const issue = (row?: MonitorCheck) => !!row && ['error', 'warning', 'unknown'].includes(row.state)
const severity = (rows: MonitorCheck[]): PlatformDiagnostic['state'] =>
  rows.some((row) => row.state === 'error')
    ? 'error'
    : rows.some((row) => row.state === 'warning')
      ? 'warning'
      : 'unknown'
const fresh = (date: string, now: number, max: number) => {
  const age = now - Date.parse(date)
  return Number.isFinite(age) && age >= -30000 && age <= max
}

/** Only a known category crosses the API boundary; deployment errors/logs can contain project secrets. */
export function deploymentDiagnosticCategory(status: string, error: string): DiagnosticDeployment['category'] {
  if (/Un commit plus récent est en attente|Remplacé par un déploiement plus récent/.test(error)) return 'superseded'
  if (status === 'cancelled') return 'other'
  if (/Capacité toujours insuffisante|capacité insuffisante dans le budget web|Admission suspendue/.test(error))
    return 'resources'
  if (/La commande de construction a échoué/.test(error)) return 'build'
  if (/La nouvelle version ne passe pas le contrôle de santé/.test(error)) return 'health'
  if (/Worker interrompu pendant le déploiement|Worker injoignable|battement.*expiré/.test(error)) return 'interrupted'
  return 'other'
}

/**
 * Read-only explanations of an existing snapshot. A declared dependency is not proof of causality. No signal is removed
 * or marked recovered; alert confirmation/delivery uses the original report.
 */
export function platformDiagnostics(
  report: MonitoringReport,
  bindings: DiagnosticBinding[] = [],
  events: DiagnosticDeployment[] = [],
  options: { now?: number; dependencyInventoryAvailable?: boolean; eventInventoryAvailable?: boolean } = {}
): PlatformDiagnostics {
  const now = options.now ?? Date.now(),
    stale = monitoringStale(report, now),
    byId = new Map(report.checks.map((row) => [row.id, row])),
    consumed = new Set<string>(),
    items: PlatformDiagnostic[] = []
  const safeEvents = events
    .filter(
      (row) =>
        ID.test(row.id) &&
        ID.test(row.appId) &&
        ['failed', 'cancelled'].includes(row.status) &&
        ['resources', 'build', 'health', 'superseded', 'interrupted', 'other'].includes(row.category) &&
        fresh(row.finishedAt, now, 3600000)
    )
    .sort((a, b) => Date.parse(b.finishedAt) - Date.parse(a.finishedAt))
    .slice(0, 20)
    .map(({ id, appId, status, finishedAt, category }) => ({
      id,
      appId,
      appName: byId.get('web:' + appId)?.name || 'Application ' + appId,
      status,
      finishedAt,
      category,
    }))
  const base = {
    checkedAt: report.checkedAt,
    stale,
    dependencyInventoryAvailable: options.dependencyInventoryAvailable ?? true,
    eventInventoryAvailable: options.eventInventoryAvailable ?? true,
    events: safeEvents,
  }
  if (stale)
    return {
      ...base,
      items: [
        {
          id: 'report-stale',
          title: 'La supervision ne fournit plus de mesure actuelle',
          state: 'unknown',
          explanation: 'Ce relevé ne permet pas de déterminer l’état actuel des services ni la cause d’un incident.',
          evidence: [],
          checkIds: [],
          additionalEvidence: 0,
          hypotheses: [],
          nextAction: 'Vérifier le collecteur de supervision, puis actualiser le relevé avant de conclure à une panne.',
        },
      ],
      omittedCount: 0,
    }
  const add = (
    id: string,
    title: string,
    rows: MonitorCheck[],
    explanation: string,
    nextAction: string,
    hypotheses: string[] = [],
    extraEvidence: DiagnosticEvidence[] = []
  ) => {
    const checks = rows.filter((row) => issue(row) && !consumed.has(row.id))
    if (!checks.length && !extraEvidence.length) return
    for (const row of checks) consumed.add(row.id)
    const evidence = [
      ...checks.map(({ id, name, detail, state, href }) => ({
        id,
        name,
        detail,
        state,
        ...(href ? { href } : {}),
        checkedAt: report.checkedAt,
      })),
      ...extraEvidence,
    ]
    items.push({
      id,
      title,
      state: evidence.some((row) => row.state === 'error') ? 'error' : checks.length ? severity(checks) : 'warning',
      explanation,
      evidence: evidence.slice(0, 25),
      checkIds: checks.map((row) => row.id),
      additionalEvidence: Math.max(0, evidence.length - 25),
      hypotheses,
      nextAction,
    })
  }
  const selected = (ids: string[]) => ids.flatMap((id) => (byId.has(id) ? [byId.get(id)!] : []))
  const worker = byId.get('web-worker')
  if (issue(worker))
    add(
      'hosting-control',
      'Le pilotage des hébergements est à vérifier',
      selected(['web-worker', 'web-runtime-metrics', 'web-queue']).concat(
        report.checks.filter((row) => row.id.startsWith('web:') && row.state === 'unknown')
      ),
      'Le worker fournit les mesures des conteneurs et pilote les déploiements. Ces signaux partagent ce circuit de contrôle ; leur absence ne prouve pas l’arrêt des sites publiés.',
      'Lire l’état et les journaux du worker, puis contrôler une URL publiée indépendamment. Ne pas redémarrer les sites pour rétablir une mesure.'
    )
  const docker = byId.get('docker:databases')
  if (issue(docker))
    add(
      'database-inventory',
      'L’inventaire des bases est indisponible',
      selected(['docker:databases', 'database-inventory']),
      'Le collecteur n’a pas pu lire l’inventaire Docker. Il ne dispose donc pas d’une réponse HTTP indépendante pour chaque base dans ce relevé.',
      'Vérifier l’accès du collecteur au moteur Docker et consulter la santé locale d’une base, sans la redémarrer.'
    )
  const safeBindings = bindings.filter((row) => ID.test(row.id) && ID.test(row.appId) && ID.test(row.instanceId))
  for (const database of report.checks.filter((row) => row.id.startsWith('database:') && issue(row))) {
    const instanceId = database.id.slice('database:'.length),
      links = safeBindings.filter((row) => row.instanceId === instanceId),
      apps = [...new Set(links.map((row) => row.appId))].flatMap((id) =>
        byId.has('web:' + id) ? [byId.get('web:' + id)!] : []
      ),
      failedApps = apps.filter((row) => issue(row) && !consumed.has(row.id))
    add(
      'dependency:' + instanceId,
      `${database.name} · base à vérifier`,
      [database, ...failedApps],
      links.length
        ? `${links.length} liaison(s) privée(s) déclarée(s) vers cette base. La sonde de base teste son accès local, indépendamment du domaine public et de Cloudflare.`
        : 'La sonde de base teste son accès local, indépendamment du domaine public et de Cloudflare.',
      'Vérifier d’abord la réponse locale et les journaux de la base, puis la liaison privée et l’action en erreur dans l’application. Conserver les services en place pendant le diagnostic.',
      failedApps.length
        ? [
            'Le défaut observé sur la base peut contribuer aux erreurs des applications liées. Le lien déclaré et des échecs simultanés ne prouvent pas cette cause.',
          ]
        : [],
      apps
        .filter((row) => row.state === 'ok')
        .map(({ id, name, detail, state, href }) => ({ id, name, detail, state, href, checkedAt: report.checkedAt }))
    )
  }
  const linkErrors: Record<string, string> = {
    broker_unavailable: 'Le service de liaison privée est indisponible.',
    proxy_unavailable: 'Le relais local de l’application est indisponible.',
    base_stopped: 'La liaison signale une base arrêtée.',
    authorization_revoked: 'L’autorisation de la liaison a été révoquée.',
    configuration: 'La liaison signale une configuration invalide.',
  }
  for (const link of safeBindings) {
    if (!fresh(link.lastSeenAt, now, 45000) || link.status !== 'unavailable' || !linkErrors[link.errorCode]) continue
    const app = byId.get('web:' + link.appId),
      database = byId.get('database:' + link.instanceId)
    if (consumed.has('web:' + link.appId)) continue
    add(
      'private-link:' + link.id,
      `${app?.name || 'Application'} · liaison privée en défaut`,
      app ? [app] : [],
      'Le relais a signalé une difficulté sur cette liaison précise. Une réponse HTTP du site ou de la base ne valide pas toutes les opérations qui utilisent ce relais.',
      'Ouvrir la liaison privée de l’application et vérifier son autorisation et son état. Ne pas changer les URL publiques sans vérifier les variables serveur utilisées.',
      issue(app)
        ? [
            'Le défaut de liaison peut expliquer l’erreur du site ; le résultat de la sonde HTTP seul ne confirme pas cette causalité.',
          ]
        : [],
      [
        {
          id: 'private-link:' + link.id,
          name: 'Liaison vers ' + (database?.name || 'la base'),
          state: 'error',
          detail: linkErrors[link.errorCode]!,
          href: '/applications/' + link.appId,
          checkedAt: link.lastSeenAt,
        },
      ]
    )
  }
  for (const row of report.checks) {
    if (!issue(row) || consumed.has(row.id)) continue
    const origin = row.id === 'origin',
      web = row.id.startsWith('web:'),
      memory = row.id === 'ram' || row.id.startsWith('web-memory:'),
      disk = row.id.startsWith('disk:') || row.id.startsWith('storage:'),
      deployment = row.id === 'deployments-failed'
    add(
      row.id,
      row.name,
      [row],
      origin
        ? 'Cette sonde vérifie l’accès HTTPS à la plateforme et son certificat. Elle ne teste pas le moteur des bases et ne démontre pas une panne de celles-ci.'
        : web
          ? 'La sonde de la publication a signalé ce résultat. Elle n’identifie pas, à elle seule, la cause dans le code, la base, les ressources ou le routage.'
          : memory
            ? 'La mesure signale une pression ou un plafond de mémoire. Un pourcentage élevé ne prouve ni une fuite ni un arrêt du processus.'
            : disk
              ? 'La mesure de stockage nécessite une vérification. Elle ne prouve pas la perte de données.'
              : deployment
                ? 'Une tentative de déploiement et la version publiée sont contrôlées séparément. Les événements récents ci-dessous aident à retrouver la tentative concernée.'
                : 'Le contrôle ci-dessous est une observation. Sa cause doit être confirmée avant toute action sur un service.',
      origin
        ? 'Vérifier le pare-feu HTTPS, le certificat et la réponse directe à l’origine, puis comparer avec le chemin public.'
        : web
          ? 'Ouvrir la santé de l’application et le journal de la version concernée, puis vérifier ses dépendances déclarées.'
          : memory
            ? 'Comparer la mémoire réellement utilisée, le plafond et la mémoire disponible du serveur dans Ressources avant de modifier un budget.'
            : disk
              ? 'Examiner la répartition et les quotas dans Ressources. Identifier les fichiers avant tout nettoyage.'
              : deployment
                ? 'Ouvrir le déploiement concerné et consulter son journal masqué. Contrôler séparément la disponibilité de la version publiée.'
                : row.group === 'backups'
                  ? 'Consulter le dernier journal de sauvegarde et vérifier la disponibilité de l’archive. Une sauvegarde verte ne remplace pas un essai de restauration.'
                  : 'Consulter l’état et les journaux du composant concerné ; confirmer la cause avant de modifier ou redémarrer un service.'
    )
  }
  items.sort(
    (a, b) => ['error', 'warning', 'unknown'].indexOf(a.state) - ['error', 'warning', 'unknown'].indexOf(b.state)
  )
  return { ...base, items: items.slice(0, 100), omittedCount: Math.max(0, items.length - 100) }
}
