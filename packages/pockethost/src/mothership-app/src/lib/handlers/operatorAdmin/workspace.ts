import { parsePlatformResources } from '../../../../../common/platformResources'
import type { WorkspaceOverview } from '../../../../../common/workspaceOverview'
import { instanceAccessQuery } from '../../security/instanceAccess'
import { webAppAccessQuery } from '../../security/webProjectAccess'

/** Small authenticated landing summary. No Docker sampling, external calls, log reads or secret serialization. */
export const HandleWorkspaceOverview = (e: core.RequestEvent) => {
  const user = e.auth
  if (!user || user.collection().name !== 'users') throw new UnauthorizedError('Authentification requise.')
  if (user.getString('suspension')) throw new ForbiddenError('Compte suspendu.')
  e.response.header().set('Cache-Control', 'no-store')
  const base = instanceAccessQuery(user),
    app = webAppAccessQuery(user)
  const web = $os.getenv('WEB_APPS_ENABLED') === 'true'
  // COUNT operates on SQL, whereas the list helpers use PocketBase filter syntax.
  // Same policy: owner/attached bases; explicit viewer+ project membership; superadmin sees every resource.
  // Only fixed collection/condition strings reach these expressions; identities are always bound.
  const platform = user.getBool('superAdmin')
  const memberSql = `wm.user={:uid} AND wm.role IN ('viewer','developer','admin','owner')`
  const visibleAppSql = platform
    ? '1=1'
    : `EXISTS (SELECT 1 FROM web_app_environments we JOIN web_project_members wm ON wm.project=we.projectId WHERE we.appId=web_apps.id AND ${memberSql})`
  const scopes: Record<string, string> = {
    instances: platform ? '1=1' : 'uid={:uid} OR id IN (SELECT value FROM json_each({:bases}))',
    web_projects: platform
      ? '1=1'
      : `EXISTS (SELECT 1 FROM web_project_members wm WHERE wm.project=web_projects.id AND ${memberSql})`,
    web_apps: visibleAppSql,
    web_deployments: `appId IN (SELECT id FROM web_apps WHERE ${visibleAppSql}) AND action IN ('deploy','ci-deploy')`,
  }
  const count = (collection: string, extra = '') =>
    $app.countRecords(
      collection,
      $dbx.exp(
        `(${scopes[collection]})${extra ? ` AND (${extra.replace(/&&/g, ' AND ').replace(/\|\|/g, ' OR ')})` : ''}`,
        { uid: user.id, bases: JSON.stringify(user.getStringSlice('managedInstances') || []) }
      )
    )
  const appCount = (extra = '') => (web ? count('web_apps', extra) : 0)
  const jobAccess = webAppAccessQuery(user, 'appId')
  const jobs = {
    ...jobAccess,
    filter: `(${jobAccess.filter}) && @collection.web_apps:present.id ?= appId && (action="deploy" || action="ci-deploy")`,
  }
  const jobCount = (extra: string) => (web ? count('web_deployments', extra) : 0)
  const failedApps = 'status="failed" || lastError!=""'
  const failedBases = 'power=true && status="failed"'
  const cutoff = new Date(Date.now() - 86400000).toISOString().replace('T', ' ')
  const completed = (status: string) => jobCount(`finishedAt>="${cutoff}" && status="${status}"`)
  const recent = web ? $app.findRecordsByFilter('web_deployments', jobs.filter, '-created,-id', 8, 0, jobs.params) : []
  const names = new Map<string, string>()
  for (const row of recent) {
    const id = row!.getString('appId')
    if (!names.has(id))
      try {
        names.set(id, $app.findRecordById('web_apps', id).getString('name'))
      } catch {}
  }
  const attention: WorkspaceOverview['attention'] = []
  for (const row of $app.findRecordsByFilter(
    'instances',
    `(${base.filter}) && (${failedBases})`,
    '-updated',
    6,
    0,
    base.params
  ))
    attention.push({
      id: row!.id,
      name: row!.getString('subdomain'),
      kind: 'database',
      status: 'failed',
      servingPreviousVersion: false,
    })
  if (web)
    for (const row of $app.findRecordsByFilter(
      'web_apps',
      `(${app.filter}) && (${failedApps})`,
      '-updated',
      6,
      0,
      app.params
    ))
      attention.push({
        id: row!.id,
        name: row!.getString('name'),
        kind: 'application',
        status: row!.getString('status'),
        servingPreviousVersion: row!.getString('status') === 'running' && !!row!.getString('activeDeployment'),
      })
  let resources: WorkspaceOverview['resources'] = null
  if (user.getBool('superAdmin'))
    try {
      const root = $os.getenv('PLATFORM_RESOURCES_ROOT') || '/var/lib/kolab-resource-monitoring'
      const content = toString($os.readFile(`${root}/resources-15m.json`))
      if (content.length > 4 * 1024 * 1024) throw Error('Resource cache too large')
      const response = parsePlatformResources(JSON.parse(content), '15m')
      if (response.sample)
        resources = { host: response.sample.host, collectedAt: response.sample.collectedAt, stale: response.stale }
    } catch {}
  const result: WorkspaceOverview = {
    collectedAt: new Date().toISOString(),
    webEnabled: web,
    canCreateBase: user.getBool('superAdmin') || Number(user.get('subscription_quantity')) > 0,
    canCreateApp: web && user.getBool('superAdmin'),
    counts: {
      databases: count('instances'),
      databasesRunning: count('instances', 'power=true && status="running"'),
      databasesOff: count('instances', 'power=false'),
      databasesFailed: count('instances', failedBases),
      applications: appCount(),
      applicationsRunning: appCount('status="running"'),
      applicationsStopped: appCount('status="stopped"'),
      applicationsFailed: appCount(failedApps),
      projects: web ? count('web_projects') : 0,
      projectsWithErrors: web
        ? count(
            'web_projects',
            `EXISTS (SELECT 1 FROM web_app_environments wi JOIN web_apps wa ON wa.id=wi.appId WHERE wi.projectId=web_projects.id AND (wa.status='failed' OR wa.lastError!=''))`
          )
        : 0,
      deploymentsInProgress: jobCount('status="running"'),
      deploymentsQueued: jobCount('status="queued"'),
      autoDeploy: appCount("json_extract(config, '$.autoDeploy')=1"),
    },
    deployments24h: { ready: completed('ready'), failed: completed('failed'), cancelled: completed('cancelled') },
    recentDeployments: recent
      .filter((row) => names.has(row!.getString('appId')))
      .map((row) => ({
        id: row!.id,
        appId: row!.getString('appId'),
        appName: names.get(row!.getString('appId')) || 'Application',
        action: row!.getString('action'),
        status: row!.getString('status'),
        commit: row!.getString('commit').slice(0, 12),
        created: row!.getString('created'),
        finishedAt: row!.getString('finishedAt'),
      })),
    attention,
    resources,
  }
  return e.json(200, result)
}
