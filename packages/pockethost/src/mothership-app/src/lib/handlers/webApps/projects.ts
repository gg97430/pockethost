import { webCapabilities, webRoleAllows } from '../../../../../common/webAccess'
import {
  formatWebDotenv,
  parseWebDotenv,
  planWebEnvironment,
  type WebEnvMap,
} from '../../../../../common/webEnvironment'
import { normalizeWebProjectLimits } from '../../../../../common/webProjectLimits'
import {
  WEB_MEMBERS,
  requireWebPermission,
  requireWebProjectPermission,
  requireWebUser,
  webEffectiveRole,
  webProjectAccessQuery,
  webProjectAllowedRepositories,
} from './access'
import { decryptWebValue, encryptWebValue, webConfig, webInput, webJson, webSourceBuildSupported } from './api'
import { webDeletionView } from './deletionState'
import { reconcileWebGitSourceProject } from './gitSource'
import { validateRuntimeProfile } from './runtimes'

export const WEB_ENVIRONMENTS = ['dev', 'staging', 'prod', 'preview'] as const
export const webProjectId = (value: string) => {
  if (!/^[a-z0-9]{15}$/.test(value || '')) throw new BadRequestError('Projet invalide.')
  return value
}
export const webEnvironmentName = (value: string) => {
  if (!(WEB_ENVIRONMENTS as readonly string[]).includes(value))
    throw new BadRequestError('Environnement attendu : dev, staging, prod ou preview.')
  return value
}
export const webMembership = (appId: string, store: any = $app): core.Record | null => {
  try {
    return store.findFirstRecordByFilter('web_app_environments', 'appId={:id}', { id: appId })
  } catch {
    return null
  }
}
export const webProject = (projectId: string, store: any = $app): core.Record =>
  store.findRecordById('web_projects', webProjectId(projectId))
export const webProjectVariable = (projectId: string, environment: string, store: any = $app): core.Record | null => {
  try {
    return store.findFirstRecordByFilter('web_project_variables', 'projectId={:id} && environment={:environment}', {
      id: projectId,
      environment,
    })
  } catch {
    return null
  }
}
export const auditWebProject = (
  projectId: string,
  actorId: string,
  action: string,
  details: Record<string, unknown>,
  store: any = $app
) => {
  const row = new Record(store.findCollectionByNameOrId('web_project_audit'))
  row.set('projectId', projectId)
  row.set('actorId', actorId)
  row.set('action', action)
  row.set('details', details)
  store.save(row)
}
export const webProjectLimits = (row: core.Record) =>
  normalizeWebProjectLimits(JSON.parse(row.getString('limits') || 'null') || {})
export const webProjectView = (row: core.Record) => ({
  id: row.id,
  name: row.getString('name'),
  repository: row.getString('repository'),
  multiRepository: row.getBool('multiRepository'),
  allowedRepositories: webProjectAllowedRepositories(row),
  previewEnabled: row.getBool('previewEnabled'),
  previewSourceAppId: row.getString('previewSourceAppId'),
  previewMax: row.getInt('previewMax') || 3,
  previewTtlHours: row.getInt('previewTtlHours') || 24,
  limits: webProjectLimits(row),
  deletion: webDeletionView(row),
})
const variableRevision = (row: core.Record | null) =>
  $security.sha256(row?.getString('valueEncrypted') || 'no-project-variables')
export const webEnvironmentResources = (app: core.Record, store: any = $app) => {
  let runtime: core.Record | undefined, storage: core.Record | undefined
  try {
    runtime = store.findRecordsByFilter('web_runtime_profiles', 'appId={:id}', '', 1, 0, { id: app.id })[0]
  } catch {}
  try {
    storage = store.findRecordsByFilter('web_storage_settings', 'appId={:id}', '', 1, 0, { id: app.id })[0]
  } catch {}
  return {
    profile: validateRuntimeProfile(runtime ? JSON.parse(runtime.getString('profile') || '{}') : {}),
    volumes: storage ? JSON.parse(storage.getString('volumes') || '[]') : [],
    revision: $security.sha256(
      JSON.stringify([
        runtime?.getString('profile') || '',
        runtime?.getString('registryEncrypted') || '',
        runtime?.getString('revision') || '',
        storage?.getString('volumes') || '',
        storage?.getString('revision') || '',
      ])
    ),
  }
}
/** Égalité de dépôts : insensible à la casse pour GitHub, exacte pour 'git:' (sensible à la casse hors des forges). */
export const webSameRepository = (a: unknown, b: unknown) => {
  const left = `${a || ''}`,
    right = `${b || ''}`
  return left.startsWith('git:') || right.startsWith('git:')
    ? left === right
    : left.toLowerCase() === right.toLowerCase()
}
/**
 * Invariant dépôt application/projet. Même dépôt (insensible à la casse, exact pour 'git:'), ou dépôt vide côté
 * application pour un moteur non source (image, worker, CI) : un projet peut ainsi regrouper ces moteurs, avec ou sans
 * dépôt. Groupe de sites (multiRepository) : aucun invariant, le dépôt du projet ne sert qu'aux previews.
 */
export const webProjectRepositoryMatches = (app: core.Record, project: core.Record, store: any = $app) => {
  if (project.getBool('multiRepository')) return true
  const repository = `${webConfig(app).repository || ''}`
  if (webSameRepository(repository, project.getString('repository'))) return true
  return !repository && !webSourceBuildSupported(app, store)
}
/**
 * Groupe de sites : une promotion reste dans le même site (même dépôt, ou lignée de clonage `sourceAppId`). `a` et `b`
 * : lignes web_app_environments de la source et de la cible.
 */
export const webSameSiteFamily = (source: core.Record, target: core.Record, a: core.Record, b: core.Record) => {
  const left = `${webConfig(source).repository || ''}`,
    right = `${webConfig(target).repository || ''}`
  if (left && right && webSameRepository(left, right)) return true
  const fromA = a.getString('sourceAppId'),
    fromB = b.getString('sourceAppId')
  return fromA === target.id || fromB === source.id || (!!fromA && fromA === fromB)
}
/** Projet et ses projets d'origine (restaurations « · récupération » successives), du plus récent au plus ancien. */
export const webProjectLineage = (projectId: string, store: any = $app) => {
  const lineage: string[] = []
  for (let id = projectId; id && !lineage.includes(id) && lineage.length < 20; ) {
    lineage.push(id)
    let from = ''
    try {
      const row = store.findFirstRecordByFilter('web_project_audit', 'projectId={:id} && action="project-recovered"', {
        id,
      })
      from = `${JSON.parse(row.getString('details') || '{}').fromProjectId || ''}`
    } catch {}
    id = from
  }
  return lineage
}
/** L'application a déjà quitté un projet : ses variables locales ont pu recevoir les variables partagées aplaties. */
export const webAppDetachment = (appId: string, store: any = $app): core.Record | null =>
  store.findRecordsByFilter('web_project_audit', 'action="application-detached" && details ~ {:id}', '-created', 1, 0, {
    id: appId,
  })[0] || null
/**
 * Historique rejouable (déploiement, sauvegarde) : capturé dans le projet actuel ou l'un de ses projets d'origine, ou
 * hors projet si l'application n'a jamais quitté un projet. Une application déplacée d'une équipe à l'autre ne rejoue
 * jamais l'image, le dépôt ni les variables de l'équipe précédente. `projectId` = projet cible (rattachement en
 * cours).
 */
export const webHistoryInScope = (
  app: core.Record,
  originProjectId: string,
  store: any = $app,
  projectId = webMembership(app.id, store)?.getString('projectId') || ''
) => {
  if (!projectId) return true
  if (originProjectId) return webProjectLineage(projectId, store).includes(originProjectId)
  return !webAppDetachment(app.id, store)
}
/** Projet dans lequel le contenu d'un déploiement (image, variables résolues) a été produit. */
export const webDeploymentOrigin = (row: core.Record) =>
  `${decryptWebValue(row.getString('snapshotEncrypted'))?.projectEnvironment?.projectId || ''}`
export const assertWebDeploymentInScope = (app: core.Record, row: core.Record, store: any = $app) => {
  if (!webHistoryInScope(app, webDeploymentOrigin(row), store))
    throw new ForbiddenError(
      'Cette version a été produite dans un autre projet. Déployez une nouvelle version depuis ce projet ; seul un administrateur de la plateforme peut la rejouer.'
    )
}
export const resolveWebEnvironment = (app: core.Record, store: any = $app) => {
  const membership = webMembership(app.id, store),
    local = decryptWebValue(app.getString('envEncrypted')) as WebEnvMap
  if (!membership)
    return {
      env: local,
      context: {
        projectId: '',
        environment: 'legacy',
        revision: $security.sha256(app.getString('envEncrypted') || 'no-environment'),
      },
    }
  const project = webProject(membership.getString('projectId'), store),
    environment = webEnvironmentName(membership.getString('environment'))
  if (!webProjectRepositoryMatches(app, project, store))
    throw new BadRequestError(
      'Le dépôt de cette application diffère de celui de son projet. Détachez-la du projet avant de changer de dépôt.'
    )
  const shared = webProjectVariable(project.id, environment, store),
    values = decryptWebValue(shared?.getString('valueEncrypted') || '') as WebEnvMap
  // There is intentionally no fallback from preview/dev/staging to prod.
  const env: WebEnvMap = Object.create(null)
  for (const name of Object.keys(values)) env[name] = { ...values[name]! }
  for (const name of Object.keys(local)) env[name] = { ...local[name]! }
  try {
    planWebEnvironment(env, [], 'merge')
  } catch (error) {
    throw new BadRequestError(error instanceof Error ? error.message : 'Variables résolues invalides.')
  }
  return {
    env,
    context: {
      projectId: project.id,
      environment,
      revision: $security.sha256(
        JSON.stringify([
          membership.id,
          membership.getString('state'),
          environment,
          variableRevision(shared),
          app.getString('envEncrypted'),
        ])
      ),
    },
  }
}
export const currentWebEnvironmentRevision = (app: core.Record, store: any = $app) =>
  $security.sha256(
    JSON.stringify([
      app.getString('name'),
      app.getString('config'),
      app.getString('envEncrypted'),
      app.getString('desiredCommit'),
      app.getString('activeDeployment'),
      resolveWebEnvironment(app, store).context.revision,
      webEnvironmentResources(app, store).revision,
    ])
  )
export const assertWebEnvironmentRevision = (app: core.Record, expected: string, store: any = $app) => {
  if (typeof expected !== 'string' || !$security.equal(expected, currentWebEnvironmentRevision(app, store)))
    throw new BadRequestError(
      'L’environnement cible a changé. Préparez une nouvelle promotion ; ses réglages actuels sont conservés.'
    )
}
export const assertWebEnvironmentDeployAllowed = (app: core.Record, action: string, store: any = $app) => {
  const member = webMembership(app.id, store)
  if (
    member?.getString('environment') === 'preview' &&
    !['stop', 'preview-remove'].includes(action) &&
    (member.getString('state') !== 'active' ||
      !Number.isFinite(Date.parse(member.getString('expiresAt'))) ||
      Date.parse(member.getString('expiresAt')) <= Date.now())
  )
    throw new BadRequestError('Cette preview est fermée ou expirée. Son nettoyage est en cours.')
}
export const assertWebEnvironmentBackupAllowed = (app: core.Record, store: any = $app) => {
  if (webMembership(app.id, store)?.getString('environment') === 'preview')
    throw new BadRequestError(
      'Les previews de pull requests sont temporaires et ne sont pas sauvegardées. Promouvez leur commit vers dev, staging ou prod pour le conserver.'
    )
}
/** Projets accessibles (superAdmin : tous), paginés en base via une jointure sur web_project_members. */
export const HandleWebProjectsList = (e: core.RequestEvent) => {
  const user = requireWebUser(e),
    q = webProjectAccessQuery(user, 'id'),
    page = Math.max(1, Math.floor(Number(e.request!.url!.query().get('page')) || 1)),
    rows = $app.findRecordsByFilter('web_projects', q.filter, '-created', 26, (page - 1) * 25, q.params)
  return webJson(e, {
    items: rows.slice(0, 25).map((row: core.Record) => {
      const role = webEffectiveRole(user, row.id)
      return { ...webProjectView(row), role: role || null, capabilities: webCapabilities(role) }
    }),
    page,
    hasMore: rows.length > 25,
  })
}
export const HandleWebProjectUpdate = (e: core.RequestEvent) => {
  const { user, project: current } = requireWebProjectPermission(e, 'project.update'),
    input = webInput(e),
    projectId = current!.id
  // Nom omis (admin sans droit de renommer) : le nom actuel est conservé.
  if (input.name === undefined) input.name = current!.getString('name')
  // Renommer reste réservé aux propriétaires ; les réglages de preview suffisent à un admin.
  if (typeof input.name === 'string' && input.name.trim() !== current!.getString('name'))
    requireWebPermission(e, { projectId }, 'project.rename')
  // Dépôts qu'un admin peut choisir : la GitHub App est partagée, seul un superAdmin modifie cette liste (renvoyer la
  // liste actuelle telle quelle reste permis).
  let allowedRepositories: string[] | undefined
  if (input.allowedRepositories !== undefined) {
    const list = input.allowedRepositories
    if (
      !Array.isArray(list) ||
      list.length > 50 ||
      list.some(
        (r: unknown) => typeof r !== 'string' || r.length > 250 || !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(r)
      )
    )
      throw new BadRequestError('Dépôts autorisés attendus au format propriétaire/dépôt (50 au maximum).')
    allowedRepositories = list.filter(
      (r: string, i: number) => list.findIndex((o: string) => o.toLowerCase() === r.toLowerCase()) === i
    )
    const key = (items: string[]) => JSON.stringify(items.map((r) => r.toLowerCase()).sort())
    if (key(allowedRepositories!) !== key(webProjectAllowedRepositories(current!)))
      requireWebPermission(e, { projectId }, 'app.repository')
  }
  // Groupe de sites : conversion explicite par un superAdmin, sans retour (des sites de dépôts différents s'y côtoient).
  let multiRepository = false
  if (input.multiRepository !== undefined) {
    if (typeof input.multiRepository !== 'boolean')
      throw new BadRequestError('Le choix multi-dépôt doit être explicite.')
    if (!input.multiRepository && current!.getBool('multiRepository'))
      throw new BadRequestError('Un groupe de sites ne peut pas redevenir un projet mono-dépôt.')
    if (input.multiRepository && !current!.getBool('multiRepository')) {
      requireWebPermission(e, { projectId }, 'app.repository')
      multiRepository = true
    }
  }
  let project: any
  $app.runInTransaction((tx) => {
    const row = webProject(projectId, tx)
    // Réglages de preview omis (renommage, conversion) : valeurs relues ici, jamais celles d'un écran resté ouvert.
    if (input.previewEnabled === undefined) input.previewEnabled = row.getBool('previewEnabled')
    if (input.previewSourceAppId === undefined) input.previewSourceAppId = row.getString('previewSourceAppId')
    if (input.previewMax === undefined) input.previewMax = row.getInt('previewMax')
    if (input.previewTtlHours === undefined) input.previewTtlHours = row.getInt('previewTtlHours')
    if (
      typeof input.name !== 'string' ||
      !input.name.trim() ||
      input.name.trim().length > 80 ||
      typeof input.previewEnabled !== 'boolean' ||
      !Number.isInteger(input.previewMax) ||
      input.previewMax < 1 ||
      input.previewMax > 5 ||
      !Number.isInteger(input.previewTtlHours) ||
      input.previewTtlHours < 1 ||
      input.previewTtlHours > 72
    )
      throw new BadRequestError('Nom, quota 1 à 5 et durée 1 à 72 heures requis.')
    if (input.previewEnabled) {
      const source = tx.findRecordById('web_apps', webProjectId(input.previewSourceAppId)),
        membership = webMembership(source.id, tx)
      if (membership?.getString('projectId') !== row.id || membership.getString('environment') === 'preview')
        throw new BadRequestError('Choisissez une application dev, staging ou prod de ce projet comme modèle.')
      if (webConfig(source).source === 'git')
        throw new BadRequestError('Les aperçus de pull request nécessitent la source GitHub App.')
      // Groupe de sites : le dépôt du projet est celui du modèle (filtre du webhook GitHub et des rafraîchissements).
      if (multiRepository || row.getBool('multiRepository')) {
        const repository = `${webConfig(source).repository || ''}`
        if (!repository) throw new BadRequestError('Le modèle de preview doit avoir un dépôt GitHub.')
        // Les previews ouvertes sont retrouvées par numéro de PR dans le projet : un autre dépôt les réutiliserait.
        if (
          !webSameRepository(repository, row.getString('repository')) &&
          tx.findRecordsByFilter(
            'web_app_environments',
            "projectId={:id} && environment='preview' && state!='closed'",
            '',
            1,
            0,
            { id: row.id }
          ).length
        )
          throw new BadRequestError('Fermez d’abord les previews ouvertes avant de changer de modèle de preview.')
        row.set('repository', repository)
      }
    }
    if (multiRepository) row.set('multiRepository', true)
    row.set('name', input.name.trim())
    row.set('previewEnabled', input.previewEnabled)
    row.set('previewSourceAppId', input.previewEnabled ? input.previewSourceAppId : '')
    row.set('previewMax', input.previewMax)
    row.set('previewTtlHours', input.previewTtlHours)
    if (allowedRepositories) row.set('allowedRepositories', allowedRepositories)
    if (input.limits !== undefined) {
      try {
        row.set('limits', normalizeWebProjectLimits(input.limits))
      } catch (error) {
        throw new BadRequestError(error instanceof Error ? error.message : 'Limites invalides.')
      }
    }
    tx.save(row)
    auditWebProject(
      row.id,
      user.id,
      'settings-updated',
      {
        name: input.name.trim(),
        previewEnabled: input.previewEnabled,
        previewMax: input.previewMax,
        previewTtlHours: input.previewTtlHours,
        ...(allowedRepositories ? { allowedRepositories } : {}),
        ...(multiRepository ? { multiRepository: true } : {}),
        ...(input.limits !== undefined ? { limits: webProjectLimits(row) } : {}),
      },
      tx
    )
    project = webProjectView(row)
  })
  return webJson(e, { project })
}
const variableScope = (e: core.RequestEvent, permission: 'variables.read' | 'variables.reveal' | 'variables.write') => {
  const { user, project, role } = requireWebProjectPermission(e, permission)
  return {
    user,
    project: project!,
    environment: webEnvironmentName(e.request!.pathValue('environment')),
    // Sans variables.reveal, le diff ne compare jamais les valeurs (aucun oracle sur une valeur devinée).
    blind: !webRoleAllows(role, 'variables.reveal'),
  }
}
const variablesView = (row: core.Record | null, revealed = false) => {
  const values = decryptWebValue(row?.getString('valueEncrypted') || '') as WebEnvMap,
    dotenv = formatWebDotenv(values, revealed)
  return {
    revision: variableRevision(row),
    revealed,
    dotenv: dotenv.text,
    variables: Object.keys(values)
      .sort()
      .map((name) => ({
        name,
        build: values[name]!.build,
        runtime: values[name]!.runtime,
        ...(revealed ? { value: values[name]!.value } : {}),
      })),
    exportWarnings: dotenv.warnings,
  }
}
export const HandleWebProjectVariablesGet = (e: core.RequestEvent) => {
  const { project, environment } = variableScope(e, 'variables.read')
  return webJson(e, variablesView(webProjectVariable(project.id, environment)))
}
export const HandleWebProjectVariablesReveal = (e: core.RequestEvent) => {
  const { user, project, environment } = variableScope(e, 'variables.reveal')
  auditWebProject(project.id, user.id, 'variables-revealed', { environment })
  return webJson(e, variablesView(webProjectVariable(project.id, environment), true))
}
const sharedToken = (actor: string, projectId: string, environment: string, input: any) =>
  $security.hs256(
    JSON.stringify([actor, projectId, environment, input.revision, input.text, input.mode]),
    $os.getenv('WEB_APPS_MASTER_KEY')
  )
const sharedPlan = (old: core.Record | null, input: any, blind = false) => {
  if (typeof input.revision !== 'string' || !$security.equal(input.revision, variableRevision(old)))
    throw new BadRequestError('Ces variables partagées ont changé. Rechargez-les avant de les enregistrer.')
  try {
    return planWebEnvironment(
      decryptWebValue(old?.getString('valueEncrypted') || ''),
      parseWebDotenv(input.text),
      input.mode,
      { blind }
    )
  } catch (error) {
    throw new BadRequestError(error instanceof Error ? error.message : 'Fichier .env invalide.')
  }
}
const validateCombinedVariables = (projectId: string, environment: string, values: WebEnvMap, store: any) => {
  for (const member of store.findRecordsByFilter(
    'web_app_environments',
    'projectId={:id} && environment={:environment} && state="active"',
    '',
    1000,
    0,
    { id: projectId, environment }
  )) {
    const app = store.findRecordById('web_apps', member.getString('appId')),
      combined = { ...values, ...decryptWebValue(app.getString('envEncrypted')) }
    try {
      planWebEnvironment(combined, [], 'merge')
    } catch (error) {
      throw new BadRequestError(
        `Variables combinées de ${app.getString('name')} : ${error instanceof Error ? error.message : 'limite dépassée'}`
      )
    }
  }
}
export const HandleWebProjectVariablesPreview = (e: core.RequestEvent) => {
  const { user, project, environment, blind } = variableScope(e, 'variables.write'),
    input = webInput(e),
    plan = sharedPlan(webProjectVariable(project.id, environment), input, blind)
  validateCombinedVariables(project.id, environment, plan.next, $app)
  return webJson(e, { changes: plan.changes, previewToken: sharedToken(user.id, project.id, environment, input) })
}
export const HandleWebProjectVariablesPut = (e: core.RequestEvent) => {
  const { user, project, environment } = variableScope(e, 'variables.write'),
    input = webInput(e)
  if (
    typeof input.previewToken !== 'string' ||
    !$security.equal(input.previewToken, sharedToken(user.id, project.id, environment, input))
  )
    throw new BadRequestError('Prévisualisez les changements avant de modifier les variables partagées.')
  let result: any
  $app.runInTransaction((tx) => {
    const old = webProjectVariable(project.id, environment, tx)
    const plan = sharedPlan(old, input)
    validateCombinedVariables(project.id, environment, plan.next, tx)
    const row = old || new Record(tx.findCollectionByNameOrId('web_project_variables'))
    row.set('projectId', project.id)
    row.set('environment', environment)
    row.set('valueEncrypted', encryptWebValue(plan.next))
    tx.save(row)
    auditWebProject(project.id, user.id, 'variables-updated', { environment, changes: plan.changes }, tx)
    result = variablesView(row)
  })
  return webJson(e, result)
}
export const exportWebEnvironmentBackup = (app: core.Record, store: any = $app) => {
  const member = webMembership(app.id, store)
  if (!member) return null
  const project = webProject(member.getString('projectId'), store),
    environment = member.getString('environment')
  return {
    version: 1,
    project: {
      id: project.id,
      name: project.getString('name'),
      repository: project.getString('repository'),
      multiRepository: project.getBool('multiRepository'),
    },
    membership: { environment, slug: member.getString('slug') },
    variablesEncrypted:
      webProjectVariable(project.id, environment, store)?.getString('valueEncrypted') || encryptWebValue({}),
  }
}
export const restoreWebEnvironmentBackup = (app: core.Record, state: any, store: any = $app) => {
  const old = webMembership(app.id, store)
  if (!state) {
    if (old) store.delete(old)
    reconcileWebGitSourceProject(app, store)
    return
  }
  // Groupe de sites : le dépôt du projet est celui des previews, pas celui du site (sauvegardes antérieures : inchangé).
  const multiRepository = state?.project?.multiRepository === true
  if (
    state.version !== 1 ||
    typeof state.project?.name !== 'string' ||
    (!multiRepository && !webSameRepository(state.project.repository, webConfig(app).repository))
  )
    throw new BadRequestError('Rattachement de projet de sauvegarde invalide.')
  const environment = webEnvironmentName(state.membership?.environment)
  if (environment === 'preview')
    throw new BadRequestError('Une preview temporaire ne peut pas être restaurée comme environnement permanent.')
  planWebEnvironment(decryptWebValue(state.variablesEncrypted), [], 'merge')
  const project = new Record(store.findCollectionByNameOrId('web_projects'))
  project.set('name', (state.project.name + ' · récupération').slice(0, 80))
  project.set('repository', `${state.project.repository || ''}`)
  project.set('multiRepository', multiRepository)
  project.set('previewMax', 3)
  project.set('previewTtlHours', 24)
  project.set('limits', normalizeWebProjectLimits(state.project.limits || {}))
  // Les membres (et les dépôts autorisés) du projet actuel suivent l'application dans le projet de récupération.
  const fromProjectId = old?.getString('projectId') || ''
  try {
    if (fromProjectId)
      project.set(
        'allowedRepositories',
        webProjectAllowedRepositories(store.findRecordById('web_projects', fromProjectId))
      )
  } catch {}
  store.save(project)
  const members = fromProjectId
    ? store.findRecordsByFilter(WEB_MEMBERS, 'project={:id}', 'created', 1000, 0, { id: fromProjectId })
    : []
  for (const row of members) {
    const copy = new Record(store.findCollectionByNameOrId(WEB_MEMBERS))
    copy.set('project', project.id)
    copy.set('user', row.getString('user'))
    copy.set('role', row.getString('role'))
    store.save(copy)
  }
  auditWebProject(project.id, '', 'project-recovered', { appId: app.id, fromProjectId, members: members.length }, store)
  const variables = new Record(store.findCollectionByNameOrId('web_project_variables'))
  variables.set('projectId', project.id)
  variables.set('environment', environment)
  variables.set('valueEncrypted', state.variablesEncrypted)
  store.save(variables)
  const member = old || new Record(store.findCollectionByNameOrId('web_app_environments'))
  member.set('appId', app.id)
  member.set('projectId', project.id)
  member.set('environment', environment)
  member.set('slug', environment)
  member.set('state', 'active')
  member.set('previewNumber', 0)
  member.set('expiresAt', '')
  member.set('sourceAppId', '')
  store.save(member)
  // Source « Dépôt Git » : elle suit l'application dans le projet de récupération si elle n'y sert plus qu'elle.
  reconcileWebGitSourceProject(app, store)
}
