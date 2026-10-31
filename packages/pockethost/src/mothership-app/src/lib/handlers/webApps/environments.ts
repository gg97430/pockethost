import { requirePlatformAdmin, requireWebAppPermission, requireWebPermission } from './access'
import {
  assertWebSiteCapacity,
  bindWebGitSourceProject,
  decryptWebValue,
  encryptWebValue,
  findWebApp,
  queueWebDeployment,
  serializeWebApp,
  serializeWebDeployment,
  webConfig,
  webInput,
  webJson,
} from './api'
import { assertWebGithubRate, reconcileWebGitSourceProject } from './gitSource'
import { resetWebNotificationChannels } from './notifications'
import {
  assertWebDeploymentInScope,
  auditWebProject,
  currentWebEnvironmentRevision,
  resolveWebEnvironment,
  webAppDetachment,
  webDeploymentOrigin,
  webEnvironmentName,
  webEnvironmentResources,
  webHistoryInScope,
  webMembership,
  webProject,
  webProjectId,
  webProjectLineage,
  webProjectRepositoryMatches,
  webProjectView,
  webSameSiteFamily,
} from './projects'
import { validateRuntimeProfile } from './runtimes'

const slugOf = (value: unknown) => {
  if (typeof value !== 'string' || !/^[a-z0-9][a-z0-9-]{0,59}$/.test(value))
    throw new BadRequestError('Nom court attendu : lettres minuscules, chiffres et tirets.')
  return value
}
const permanent = (value: string) => {
  webEnvironmentName(value)
  if (value === 'preview')
    throw new BadRequestError('Les previews sont créées uniquement par le cycle des pull requests autorisées.')
  return value
}
const assertSlug = (projectId: string, slug: string, store: any, except = '') => {
  const found = store.findRecordsByFilter(
    'web_app_environments',
    'projectId={:id} && slug={:slug} && id!={:except}',
    '',
    1,
    0,
    { id: projectId, slug, except }
  )
  if (found.length) throw new BadRequestError('Ce nom d’environnement est déjà utilisé dans le projet.')
}
const PERMANENT_MAX = 10
/** Environnements permanents d'un projet (les previews ont leur propre quota) : création, rattachement, déplacement. */
const assertPermanentCapacity = (projectId: string, store: any) => {
  if (
    store.countRecords(
      'web_app_environments',
      $dbx.exp("environment!='preview' AND projectId={:id}", { id: projectId })
    ) >= PERMANENT_MAX
  )
    throw new BadRequestError('Dix environnements permanents maximum par projet.')
}
/** Validation partagée avec PUT /api/web-apps/{id}/project (projectGroups.ts). */
export {
  assertSlug as assertWebEnvironmentSlug,
  assertPermanentCapacity as assertWebPermanentCapacity,
  slugOf as webEnvironmentSlug,
  permanent as webPermanentEnvironment,
}
const membershipView = (row: core.Record, store: any) => {
  let app: core.Record | null = null
  try {
    if (row.getString('appId')) app = store.findRecordById('web_apps', row.getString('appId'))
  } catch {}
  return {
    id: row.id,
    appId: row.getString('appId'),
    projectId: row.getString('projectId'),
    environment: row.getString('environment'),
    slug: row.getString('slug'),
    state: row.getString('state'),
    sourceAppId: row.getString('sourceAppId'),
    previewNumber: row.getInt('previewNumber'),
    expiresAt: row.getString('expiresAt'),
    app: app ? serializeWebApp(app) : null,
  }
}
export const HandleWebAppEnvironments = (e: core.RequestEvent) => {
  // Application legacy (sans projet) : 404 pour un non-superAdmin, par construction de la garde.
  const app = requireWebAppPermission(e, 'app.read').app!,
    member = webMembership(app.id)
  if (!member) return webJson(e, { project: null, membership: null, items: [], legacy: true })
  const project = webProject(member.getString('projectId')),
    rows = $app.findRecordsByFilter('web_app_environments', 'projectId={:id}', 'environment,slug', 200, 0, {
      id: project.id,
    })
  const resolved = resolveWebEnvironment(app),
    localNames = new Set(Object.keys(decryptWebValue(app.getString('envEncrypted'))))
  return webJson(e, {
    project: webProjectView(project),
    membership: membershipView(member, $app),
    items: rows.map((row) => membershipView(row, $app)),
    legacy: false,
    effectiveVariables: Object.keys(resolved.env)
      .sort()
      .map((name) => ({
        name,
        build: resolved.env[name]!.build,
        runtime: resolved.env[name]!.runtime,
        overriddenLocally: localNames.has(name),
      })),
    resolutionRevision: resolved.context.revision,
  })
}
export const HandleWebAppProjectCreate = (e: core.RequestEvent) => {
  const user = requirePlatformAdmin(e),
    source = findWebApp(e.request!.pathValue('id')),
    input = webInput(e),
    environment = permanent(input.environment)
  if (typeof input.name !== 'string' || !input.name.trim() || input.name.trim().length > 80)
    throw new BadRequestError('Nom de projet requis (80 caractères maximum).')
  let project: any
  $app.runInTransaction((tx) => {
    if (webMembership(source.id, tx)) throw new BadRequestError('Cette application appartient déjà à un projet.')
    const app = tx.findRecordById('web_apps', source.id),
      row = new Record(tx.findCollectionByNameOrId('web_projects'))
    row.set('name', input.name.trim())
    row.set('repository', webConfig(app).repository)
    row.set('actorId', user.id)
    row.set('previewMax', 3)
    row.set('previewTtlHours', 24)
    tx.save(row)
    // Source « Dépôt Git » : elle appartient désormais à ce projet (refus si elle sert déjà un autre projet).
    bindWebGitSourceProject(webConfig(app), row.id, tx, app.id)
    const member = new Record(tx.findCollectionByNameOrId('web_app_environments'))
    member.set('projectId', row.id)
    member.set('appId', app.id)
    member.set('environment', environment)
    member.set('slug', slugOf(input.slug || environment))
    member.set('state', 'active')
    tx.save(member)
    auditWebProject(row.id, user.id, 'project-created', { appId: app.id, environment }, tx)
    project = webProjectView(row)
  })
  return webJson(e, { project }, 201)
}
/**
 * Rattache une application sans projet à `project` (relue dans `tx`) : même dépôt (sauf groupe de sites), source Git,
 * nom court libre, version publiée produite dans ce projet, variables aplaties d'une autre équipe retirées
 * (`keepVariables` : conservées), canaux d'alerte réinitialisés si l'équipe change. Partagé par la route historique et
 * PUT /api/web-apps/{id}/project.
 */
export const attachWebAppToProject = (
  app: core.Record,
  project: core.Record,
  input: { environment: string; slug: string; keepVariables?: boolean },
  user: core.Record,
  tx: any
) => {
  const { environment, slug } = input
  if (webMembership(app.id, tx)) throw new BadRequestError('Détachez d’abord cette application de son projet actuel.')
  const current = tx.findRecordById('web_apps', app.id)
  if (!webProjectRepositoryMatches(current, project, tx))
    throw new BadRequestError('Le projet et l’application doivent utiliser le même dépôt.')
  bindWebGitSourceProject(webConfig(current), project.id, tx, app.id)
  assertSlug(project.id, slug, tx)
  assertPermanentCapacity(project.id, tx)
  // La version publiée d'un autre projet ne doit pas tourner (terminal, tâches) sous les membres du nouveau projet.
  let active: core.Record | null = null
  try {
    active = tx.findRecordById('web_deployments', current.getString('activeDeployment'))
  } catch {}
  if (
    active &&
    !webHistoryInScope(current, webDeploymentOrigin(active), tx, project.id) &&
    (current.getString('status') !== 'stopped' ||
      tx.findRecordsByFilter('web_deployments', 'appId={:id} && (status="queued" || status="running")', '', 1, 0, {
        id: app.id,
      }).length)
  )
    throw new BadRequestError(
      'Arrêtez l’application avant de la rattacher : sa version publiée vient d’un autre projet. Redéployez-la ensuite depuis ce projet.'
    )
  // Variables partagées de l'ancien projet aplaties au détachement : retirées si l'application change d'équipe.
  const detached = webAppDetachment(app.id, tx),
    sameTeam = !!detached && webProjectLineage(project.id, tx).includes(detached.getString('projectId')),
    shared: string[] =
      detached && !sameTeam ? JSON.parse(detached.getString('details') || '{}').sharedVariables || [] : []
  // Canaux d'alerte de l'équipe précédente (ou posés hors projet) : jamais réutilisés par la nouvelle équipe.
  const notificationsReset = !sameTeam && resetWebNotificationChannels(app.id, user.id, tx)
  const local = decryptWebValue(current.getString('envEncrypted')),
    stripped =
      input.keepVariables === true ? [] : shared.filter((name) => Object.prototype.hasOwnProperty.call(local, name))
  if (stripped.length) {
    for (const name of stripped) delete local[name]
    current.set('envEncrypted', encryptWebValue(local))
    tx.save(current)
  }
  const row = new Record(tx.findCollectionByNameOrId('web_app_environments'))
  row.set('projectId', project.id)
  row.set('appId', app.id)
  row.set('environment', environment)
  row.set('slug', slug)
  row.set('state', 'active')
  tx.save(row)
  auditWebProject(
    project.id,
    user.id,
    'application-attached',
    {
      appId: app.id,
      environment,
      ...(shared.length
        ? { strippedVariables: stripped, keptVariables: shared.filter((name) => !stripped.includes(name)) }
        : {}),
      ...(notificationsReset ? { notificationsReset: true } : {}),
    },
    tx
  )
  return { stripped, notificationsReset }
}
export const HandleWebAppProjectAttach = (e: core.RequestEvent) => {
  const user = requirePlatformAdmin(e),
    app = findWebApp(e.request!.pathValue('id')),
    input = webInput(e),
    environment = permanent(input.environment),
    slug = slugOf(input.slug || environment)
  if (input.keepVariables !== undefined && typeof input.keepVariables !== 'boolean')
    throw new BadRequestError('Le choix de conservation des variables doit être explicite.')
  $app.runInTransaction((tx) => {
    if (webMembership(app.id, tx)) throw new BadRequestError('Détachez d’abord cette application de son projet actuel.')
    attachWebAppToProject(
      app,
      webProject(input.projectId, tx),
      { environment, slug, keepVariables: input.keepVariables },
      user,
      tx
    )
  })
  return HandleWebAppEnvironments(e)
}
/**
 * Sort l'application de son projet : variables partagées aplaties dans ses variables locales (noms renvoyés, jamais les
 * valeurs), audit `application-detached`, source Git réconciliée. Partagé par la route historique et PUT
 * /api/web-apps/{id}/project.
 */
export const detachWebAppFromProject = (app: core.Record, user: core.Record, tx: any): string[] => {
  const current = tx.findRecordById('web_apps', app.id),
    member = webMembership(app.id, tx)
  if (!member) throw new BadRequestError('Cette application n’appartient pas à un projet.')
  if (member.getString('environment') === 'preview')
    throw new BadRequestError('Une preview temporaire doit être fermée par son cycle de pull request.')
  const local = decryptWebValue(current.getString('envEncrypted')),
    resolved = resolveWebEnvironment(current, tx).env,
    sharedVariables = Object.keys(resolved).filter((name) => !Object.prototype.hasOwnProperty.call(local, name))
  current.set('envEncrypted', encryptWebValue(resolved))
  tx.save(current)
  // Noms (jamais les valeurs) des variables partagées aplaties : retirées si l'application rejoint un autre projet.
  auditWebProject(
    member.getString('projectId'),
    user.id,
    'application-detached',
    { appId: app.id, sharedVariables },
    tx
  )
  tx.delete(member)
  // Source « Dépôt Git » : l'ancien projet n'en garde la gestion que si l'une de ses applications l'utilise encore.
  reconcileWebGitSourceProject(current, tx)
  return sharedVariables
}
export const HandleWebAppProjectDetach = (e: core.RequestEvent) => {
  const user = requirePlatformAdmin(e),
    app = findWebApp(e.request!.pathValue('id'))
  $app.runInTransaction((tx) => {
    detachWebAppFromProject(app, user, tx)
  })
  return webJson(e, { detached: true })
}
export const createWebEnvironment = (
  project: core.Record,
  source: core.Record,
  input: any,
  actorId: string,
  store: any,
  preview?: { number: number; expiresAt: string; existing?: core.Record }
) => {
  const sourceMember = webMembership(source.id, store),
    environment = preview ? 'preview' : permanent(input.environment),
    slug = slugOf(preview ? `pr-${preview.number}` : input.slug || environment)
  if (sourceMember?.getString('projectId') !== project.id || sourceMember.getString('environment') === 'preview')
    throw new BadRequestError('Choisissez une application permanente de ce projet comme modèle.')
  if (input.copyVariables !== undefined && typeof input.copyVariables !== 'boolean')
    throw new BadRequestError('Le choix de copie des variables doit être explicite.')
  if (preview && input.copyVariables)
    throw new BadRequestError('Les variables du modèle ne sont jamais copiées vers une preview.')
  const branch = `${input.branch || webConfig(source).branch}`
  if (!/^[A-Za-z0-9_./-]{1,200}$/.test(branch) || branch.includes('..')) throw new BadRequestError('Branche invalide.')
  const name = `${input.name || `${source.getString('name')} · ${slug}`}`.trim()
  if (!name || name.length > 80) throw new BadRequestError('Nom d’application requis (80 caractères maximum).')
  assertSlug(project.id, slug, store, preview?.existing?.id)
  // Preview : refus sans erreur dans le webhook (quota noté sur la livraison, previews.ts).
  if (!preview) {
    assertPermanentCapacity(project.id, store)
    assertWebSiteCapacity(store)
  }
  const app = new Record(store.findCollectionByNameOrId('web_apps')),
    config = { ...webConfig(source), branch, autoDeploy: preview ? false : false }
  delete config.generatedDomain
  app.set('name', name)
  app.set('uid', source.getString('uid') || actorId)
  app.set('status', 'stopped')
  app.set('config', config)
  app.set('envEncrypted', encryptWebValue(input.copyVariables ? decryptWebValue(source.getString('envEncrypted')) : {}))
  store.save(app)
  config.generatedDomain = `web-${app.id}.${$os.getenv('APEX_DOMAIN') || 'monappli.re'}`
  app.set('config', config)
  store.save(app)
  const resources = webEnvironmentResources(source, store)
  const profile = new Record(store.findCollectionByNameOrId('web_runtime_profiles'))
  profile.set('appId', app.id)
  profile.set('profile', resources.profile)
  profile.set('registryEncrypted', '')
  profile.set('revision', $security.randomString(24))
  store.save(profile)
  if (resources.volumes.length) {
    const storage = new Record(store.findCollectionByNameOrId('web_storage_settings'))
    storage.set('appId', app.id)
    storage.set('volumes', resources.volumes)
    storage.set('retention', 7)
    storage.set('revision', $security.randomString(24))
    store.save(storage)
  }
  const row = preview?.existing || new Record(store.findCollectionByNameOrId('web_app_environments'))
  row.set('projectId', project.id)
  row.set('appId', app.id)
  row.set('environment', environment)
  row.set('slug', slug)
  row.set('state', 'active')
  row.set('sourceAppId', source.id)
  row.set('previewNumber', preview?.number || 0)
  row.set('expiresAt', preview?.expiresAt || '')
  store.save(row)
  auditWebProject(
    project.id,
    actorId,
    preview ? 'preview-created' : 'environment-cloned',
    { appId: app.id, sourceAppId: source.id, environment, variablesCopied: input.copyVariables === true },
    store
  )
  return app
}
export const HandleWebEnvironmentClone = (e: core.RequestEvent) => {
  const { user, app: source } = requireWebAppPermission(e, 'environments.manage'),
    input = webInput(e)
  let app: any
  $app.runInTransaction((tx) => {
    const member = webMembership(source!.id, tx)
    if (!member) throw new BadRequestError('Créez ou choisissez d’abord un projet.')
    app = serializeWebApp(
      createWebEnvironment(
        webProject(member.getString('projectId'), tx),
        tx.findRecordById('web_apps', source!.id),
        input,
        user.id,
        tx
      )
    )
  })
  return webJson(e, { app }, 201)
}
const normalized = (value: any): any =>
  Array.isArray(value)
    ? value.map(normalized)
    : value && typeof value === 'object'
      ? Object.fromEntries(
          Object.keys(value)
            .sort()
            .map((key) => [key, normalized(value[key])])
        )
      : value === undefined
        ? null
        : value
const canonical = (value: any): string => JSON.stringify(normalized(value))
const buildFingerprint = (config: any, env: any) => {
  const c = { ...config }
  // githubConnectionId : un même commit donne un même build, quelle que soit l'App qui le lit.
  for (const key of ['branch', 'generatedDomain', 'healthPath', 'autoDeploy', 'repositoryId', 'githubConnectionId'])
    delete c[key]
  return $security.sha256(
    canonical([
      c,
      Object.fromEntries(
        Object.keys(env)
          .filter((name) => env[name].build)
          .sort()
          .map((name) => [name, env[name].value])
      ),
    ])
  )
}
const promotionPlan = (source: core.Record, target: core.Record, store: any) => {
  const a = webMembership(source.id, store),
    b = webMembership(target.id, store)
  if (
    !a ||
    !b ||
    source.id === target.id ||
    a.getString('projectId') !== b.getString('projectId') ||
    b.getString('environment') === 'preview' ||
    b.getString('state') !== 'active'
  )
    throw new BadRequestError('Choisissez un autre environnement permanent du même projet comme cible.')
  // Groupe de sites : les environnements d'un même site seulement (dépôt commun ou lignée de clonage).
  if (
    webProject(a.getString('projectId'), store).getBool('multiRepository') &&
    !webSameSiteFamily(source, target, a, b)
  )
    throw new BadRequestError(
      'Choisissez comme cible un environnement du même site (même dépôt ou cloné depuis la source).'
    )
  let published: core.Record
  try {
    published = store.findRecordById('web_deployments', source.getString('activeDeployment'))
  } catch {
    throw new BadRequestError('Publiez une version de la source avant de la promouvoir.')
  }
  const snapshot = decryptWebValue(published.getString('snapshotEncrypted')),
    result = JSON.parse(published.getString('result') || '{}'),
    resolved = resolveWebEnvironment(target, store)
  if (
    published.getString('status') !== 'ready' ||
    published.getString('appId') !== source.id ||
    !/^[a-f0-9]{40}$/.test(published.getString('commit')) ||
    !snapshot?.env ||
    !snapshot?.config
  )
    throw new BadRequestError('Publiez une version valide de l’environnement source avant de la promouvoir.')
  // La cible étiquetterait cette image avec son projet : jamais une version produite dans un autre projet.
  assertWebDeploymentInScope(source, published, store)
  const image = /^sha256:[a-f0-9]{64}$/.test(result.imageId || '')
    ? result.imageId
    : /^sha256:[a-f0-9]{64}$/.test(result.image || '')
      ? result.image
      : ''
  const { registry: _registry, ...publicSourceRuntime } = snapshot.runtime || {},
    sourceRuntime = validateRuntimeProfile(publicSourceRuntime),
    targetResources = webEnvironmentResources(target, store)
  const reusableRuntime =
    sourceRuntime.kind !== 'compose' &&
    targetResources.profile.kind !== 'compose' &&
    canonical(sourceRuntime) === canonical(targetResources.profile) &&
    canonical(snapshot.storage || []) === canonical(targetResources.volumes)
  return {
    sourceDeploymentId: published.id,
    commit: published.getString('commit'),
    // Branche d'où vient le commit : repli du worker pour une source Git qui refuse le fetch par commit.
    sourceBranch: `${snapshot.config.branch || ''}`,
    image,
    mode:
      image &&
      reusableRuntime &&
      result.available !== false &&
      buildFingerprint(snapshot.config, snapshot.env) === buildFingerprint(webConfig(target), resolved.env)
        ? 'image'
        : 'build',
    revision: currentWebEnvironmentRevision(target, store),
    resolved,
  }
}
const promotionToken = (actorId: string, sourceId: string, targetId: string, plan: any, expiresAt: string) =>
  $security.hs256(
    JSON.stringify([
      actorId,
      sourceId,
      targetId,
      plan.sourceDeploymentId,
      plan.commit,
      plan.mode,
      plan.revision,
      expiresAt,
    ]),
    $os.getenv('WEB_APPS_MASTER_KEY')
  )
/** Promouvoir publie sur la cible : app.deploy est exigé sur la source ET sur la cible (même projet, déjà imposé). */
const promotionTarget = (e: core.RequestEvent, targetAppId: string) =>
  requireWebPermission(e, { appId: webProjectId(targetAppId) }, 'app.deploy').app!
export const HandleWebEnvironmentPromotionPreview = (e: core.RequestEvent) => {
  const { user, app: source } = requireWebAppPermission(e, 'app.deploy'),
    input = webInput(e),
    target = promotionTarget(e, input.targetAppId),
    plan = promotionPlan(source!, target, $app),
    expiresAt = new Date(Date.now() + 10 * 60000).toISOString()
  return webJson(e, {
    targetAppId: target.id,
    sourceDeploymentId: plan.sourceDeploymentId,
    commit: plan.commit,
    mode: plan.mode,
    revision: plan.revision,
    expiresAt,
    previewToken: promotionToken(user.id, source!.id, target.id, plan, expiresAt),
    variableNames: Object.keys(plan.resolved.env).sort(),
    warning:
      plan.mode === 'image'
        ? 'L’image vérifiée sera adaptée à la cible et démarrée avec les variables de la cible.'
        : 'Le commit sera reconstruit avec les variables de construction et d’exécution de la cible. Les valeurs de la source ne sont pas copiées.',
  })
}
export const HandleWebEnvironmentPromote = (e: core.RequestEvent) => {
  const { user, app: source } = requireWebAppPermission(e, 'app.deploy'),
    input = webInput(e)
  promotionTarget(e, input.targetAppId)
  // Aucun appel GitHub ici (le commit vient du plan) : même plafond que les autres mises en file manuelles.
  assertWebGithubRate(source!.id, user.id)
  let deployment: any
  $app.runInTransaction((tx) => {
    const from = tx.findRecordById('web_apps', source!.id),
      target = tx.findRecordById('web_apps', webProjectId(input.targetAppId)),
      plan = promotionPlan(from, target, tx)
    if (
      input.revision !== plan.revision ||
      typeof input.expiresAt !== 'string' ||
      Date.parse(input.expiresAt) <= Date.now() ||
      Date.parse(input.expiresAt) > Date.now() + 10 * 60000 ||
      typeof input.previewToken !== 'string' ||
      !$security.equal(input.previewToken, promotionToken(user.id, from.id, target.id, plan, input.expiresAt))
    )
      throw new BadRequestError('La source ou la cible a changé. Préparez un nouvel aperçu de promotion.')
    if (
      tx.findRecordsByFilter('web_deployments', 'appId={:id} && (status="queued" || status="running")', '', 1, 0, {
        id: target.id,
      }).length
    )
      throw new BadRequestError('La cible a déjà un traitement en cours.')
    target.set('desiredCommit', plan.commit)
    tx.save(target)
    const resolved = resolveWebEnvironment(target, tx),
      promotion = {
        sourceAppId: from.id,
        sourceDeploymentId: plan.sourceDeploymentId,
        sourceBranch: plan.sourceBranch,
        expectedTargetRevision: currentWebEnvironmentRevision(target, tx),
      }
    const row = queueWebDeployment(
      target,
      plan.mode === 'image' ? 'promote' : 'deploy',
      user.id,
      {
        commit: plan.commit,
        env: resolved.env,
        projectEnvironment: resolved.context,
        ...(plan.mode === 'image' ? { rollbackResult: { imageSource: plan.image, sourceAppId: from.id } } : {}),
        promotion,
      },
      tx
    )
    auditWebProject(
      webMembership(target.id, tx)!.getString('projectId'),
      user.id,
      'environment-promoted',
      { sourceAppId: from.id, targetAppId: target.id, commit: plan.commit, mode: plan.mode },
      tx
    )
    deployment = serializeWebDeployment(row)
  })
  return webJson(e, { deployment }, 202)
}
