import { webRoleAllows } from '../../../../../common/webAccess'
import {
  assertWebQueueCapacity,
  encryptWebValue,
  requireWebAppPermission,
  serializeWebDeployment,
  webConfig,
  webJson,
} from './api'
import { environmentDeploymentState } from './environment'
import { assertWebDeploymentInScope, resolveWebEnvironment } from './projects'
import { runtimeWorkerSnapshot } from './runtimes'
import { storageWorkerSnapshot } from './storage'

export const HandleWebApplyRuntime = (e: core.RequestEvent) => {
  const { user, app: found, role } = requireWebAppPermission(e, 'app.deploy'),
    app = found!,
    // Le refus ci-dessous confirmerait une valeur devinée au même titre que pendingChanges.
    blind = !webRoleAllows(role, 'variables.reveal')
  let result: any
  $app.runInTransaction((tx) => {
    const current = tx.findRecordById('web_apps', app.id)
    if (
      tx.findRecordsByFilter('web_deployments', 'appId={:id} && (status="queued" || status="running")', '', 1, 0, {
        id: app.id,
      }).length
    )
      throw new BadRequestError('Attendez la fin du traitement de cette application avant d’appliquer les variables.')
    if (!environmentDeploymentState(current, tx, blind).canApplyRuntime)
      throw new BadRequestError(
        'Aucune modification uniquement à l’exécution à appliquer. Les changements de construction nécessitent un déploiement.'
      )
    const active = tx.findRecordById('web_deployments', current.getString('activeDeployment'))
    const previous = JSON.parse(active.getString('result') || '{}')
    if (
      active.getString('status') !== 'ready' ||
      previous.available === false ||
      !(
        new RegExp(`^ph-web/${app.id}:[a-z0-9]{15}$`).test(previous.image || '') ||
        /^sha256:[a-f0-9]{64}$/.test(previous.image || '')
      )
    )
      throw new BadRequestError('L’image publiée n’est plus disponible. Effectuez un déploiement complet.')
    // Le nouvel instantané porterait le projet actuel : l'image d'un autre projet ne doit jamais y être réétiquetée.
    assertWebDeploymentInScope(current, active, tx)
    assertWebQueueCapacity(current, 'apply-runtime', tx)
    const resolved = resolveWebEnvironment(current, tx)
    const snapshot = {
      config: webConfig(current),
      env: resolved.env,
      projectEnvironment: resolved.context,
      runtime: runtimeWorkerSnapshot(current, tx),
      storage: storageWorkerSnapshot(current, tx),
      domains: [],
      rollbackResult: previous,
    }
    try {
      snapshot.domains = tx
        .findRecordsByFilter(
          'web_app_domains',
          'appId={:id} && (status="active" || status="pending_certificate" || status="verified")',
          '',
          100,
          0,
          { id: app.id }
        )
        .map((r) => ({ id: r.id, host: r.getString('host'), certificate: r.getString('status') === 'active' })) as any
    } catch {}
    const row = new Record(tx.findCollectionByNameOrId('web_deployments'))
    row.set('appId', app.id)
    row.set('actorId', user.id)
    row.set('action', 'apply-runtime')
    row.set('commit', active.getString('commit'))
    row.set('status', 'queued')
    row.set('stage', 'Application des variables à l’exécution')
    row.set('snapshotEncrypted', encryptWebValue(snapshot))
    tx.save(row)
    result = serializeWebDeployment(row)
  })
  return webJson(e, { deployment: result }, 202)
}
