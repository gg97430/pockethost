import { type WebPermission, webRoleAllows } from '../../../../../common/webAccess'
import {
  formatWebDotenv,
  parseWebDotenv,
  planWebEnvironment,
  validateWebEnvName,
  type WebEnvEntry,
  type WebEnvMap,
  type WebEnvPlan,
} from '../../../../../common/webEnvironment'
import { decryptWebValue, encryptWebValue, requireWebAppPermission, webConfig, webInput, webJson } from './api'
import { resolveWebEnvironment } from './projects'

const read = (row: core.Record): WebEnvMap => decryptWebValue(row.getString('envEncrypted'))
const revision = (row: core.Record) => $security.sha256(row.getString('envEncrypted') || 'no-environment')
/** Lecture des noms : variables.read ; valeurs : variables.reveal ; écriture : variables.write. */
const actorAndApp = (e: core.RequestEvent, permission: WebPermission) => {
  const access = requireWebAppPermission(e, permission)
  // Sans variables.reveal, le diff ne compare jamais les valeurs (aucun oracle sur une valeur devinée).
  return { user: access.user, app: access.app!, blind: !webRoleAllows(access.role, 'variables.reveal') }
}
const audit = (appId: string, actorId: string, action: string, details: Record<string, unknown>, store: any = $app) => {
  const row = new Record(store.findCollectionByNameOrId('web_environment_audit'))
  row.set('appId', appId)
  row.set('actorId', actorId)
  row.set('action', action)
  row.set('details', details)
  store.save(row)
}
/**
 * État de la publication. `blind` (rôle sans variables.reveal) : aucune comparaison de valeurs, sinon écrire une valeur
 * devinée et lire cet état la confirmerait. Seule compte la révision publiée : elle dérive du chiffré, à nonce
 * aléatoire, et change à chaque écriture, même à l'identique. Faute de savoir quelle valeur a changé, toute variable de
 * construction est présumée modifiée après une écriture.
 */
export const environmentDeploymentState = (app: core.Record, store: any = $app, blind = false) => {
  let snapshot: any = null,
    deployedCommit = ''
  try {
    const deployment = store.findRecordById('web_deployments', app.getString('activeDeployment'))
    snapshot = decryptWebValue(deployment.getString('snapshotEncrypted'))
    deployedCommit = deployment.getString('commit')
  } catch {}
  if (!snapshot?.env || !snapshot?.config)
    return { pendingChanges: true, requiresRebuild: true, runtimeOnlyChanges: false, canApplyRuntime: false }
  const resolved = resolveWebEnvironment(app, store),
    current = resolved.env,
    before = snapshot.env as WebEnvMap,
    config = webConfig(app),
    written = snapshot.projectEnvironment?.revision !== resolved.context.revision
  let changed = false,
    buildChanged = false
  for (const name of new Set([...Object.keys(current), ...Object.keys(before)])) {
    const a = current[name],
      b = before[name],
      valueChanged = blind ? written : a?.value !== b?.value
    if (!a || !b || valueChanged || a.build !== b.build || a.runtime !== b.runtime) changed = true
    if (!!a?.build !== !!b?.build || (a?.build && valueChanged)) buildChanged = true
  }
  const codeChanged =
    ['repository', 'branch', 'rootDirectory'].some((name) => config[name] !== snapshot.config[name]) ||
    !!(app.getString('desiredCommit') && app.getString('desiredCommit') !== deployedCommit)
  return {
    pendingChanges: changed || codeChanged,
    requiresRebuild: buildChanged || codeChanged,
    runtimeOnlyChanges: changed && !buildChanged && !codeChanged,
    canApplyRuntime: changed && !buildChanged && !codeChanged,
  }
}
const serialize = (app: core.Record, revealed = false, blind = false) => {
  const values = read(app),
    dotenv = formatWebDotenv(values, revealed),
    nextDotenv = formatWebDotenv(resolveWebEnvironment(app).env, revealed, false, 'nextjs')
  return {
    revision: revision(app),
    revealed,
    ...environmentDeploymentState(app, $app, blind),
    variables: Object.keys(values)
      .sort()
      .map((name) => ({
        name,
        build: values[name]!.build,
        runtime: values[name]!.runtime,
        hasValue: true,
        isEmpty: values[name]!.value === '',
        multiline: /[\r\n]/.test(values[name]!.value),
        public: name.startsWith('NEXT_PUBLIC_'),
        ...(revealed ? { value: values[name]!.value } : {}),
      })),
    dotenv: dotenv.text,
    nextDotenv: nextDotenv.text,
    nextExportWarnings: nextDotenv.warnings,
    example: formatWebDotenv(values, false, true).text,
    exportWarnings: dotenv.warnings,
  }
}
const structuredEntries = (input: any, current: WebEnvMap): WebEnvEntry[] => {
  if (!Array.isArray(input.rows) || input.rows.length > 100) throw new Error('100 variables maximum.')
  const entries: WebEnvEntry[] = [],
    seen = new Set<string>()
  for (const [index, row] of input.rows.entries()) {
    if (!row || typeof row.name !== 'string') throw new Error('Nom de variable invalide.')
    validateWebEnvName(row.name)
    if (seen.has(row.name)) throw new Error(`La variable ${row.name} est présente plusieurs fois.`)
    seen.add(row.name)
    if (row.remove === true) continue
    const old = Object.prototype.hasOwnProperty.call(current, row.name) ? current[row.name] : undefined
    if (row.value !== undefined && typeof row.value !== 'string') throw new Error('Valeur de variable invalide.')
    if (row.value?.length > 10000 || row.value?.includes('\u0000'))
      throw new Error('Valeur de variable invalide ou trop longue.')
    for (const name of ['build', 'runtime'])
      if (row[name] !== undefined && typeof row[name] !== 'boolean')
        throw new Error('Réglage de disponibilité invalide.')
    const preserve = row.setEmpty !== true && (row.value === undefined || row.value === '')
    if (preserve && !old)
      throw new Error(`La variable ${row.name} nécessite une valeur ou la case Valeur vide explicite.`)
    entries.push({
      name: row.name,
      value: row.setEmpty === true ? '' : row.value || '',
      preserve,
      build: row.build,
      runtime: row.runtime,
      line: index + 1,
    })
  }
  return entries
}
/**
 * Champ `env` de POST/PATCH /api/web-apps : sans aperçu ni révision (le dashboard passe par /environment ; il ne s'en
 * sert qu'à la création), mais mêmes règles que /environment. `remove` retire la variable, les autres sont conservées.
 */
export const planWebAppEnv = (row: core.Record, input: unknown) => {
  if (!Array.isArray(input) || input.length > 100) throw new BadRequestError('Variables invalides (100 maximum).')
  try {
    const current = read(row),
      rows = input.map((item) => ({ ...item, build: !!item?.build, runtime: !!item?.runtime })),
      entries = structuredEntries({ rows }, current),
      named = new Set(rows.map((item) => item.name))
    for (const name of Object.keys(current))
      if (!named.has(name)) entries.push({ name, value: '', preserve: true, line: 0 })
    return planWebEnvironment(current, entries, 'replace')
  } catch (error) {
    throw new BadRequestError(error instanceof Error ? error.message : 'Variables invalides.')
  }
}
export const auditWebAppEnv = (appId: string, actorId: string, plan: WebEnvPlan, store: any) =>
  audit(
    appId,
    actorId,
    'updated',
    { mode: 'direct', changes: plan.changes, requiresRebuild: plan.requiresRebuild },
    store
  )
const previewPlan = (app: core.Record, input: any, blind = false) => {
  if (typeof input.revision !== 'string' || !$security.equal(input.revision, revision(app)))
    throw new BadRequestError('Les variables ont changé depuis leur ouverture. Rechargez-les avant de continuer.')
  if (
    !['merge', 'replace'].includes(input.mode) ||
    (typeof input.text !== 'string' && !Array.isArray(input.rows)) ||
    (typeof input.text === 'string' && input.rows !== undefined)
  )
    throw new BadRequestError('Texte .env ou tableau de variables et mode Fusionner/Remplacer requis.')
  try {
    const current = read(app)
    return planWebEnvironment(
      current,
      input.rows !== undefined ? structuredEntries(input, current) : parseWebDotenv(input.text),
      input.mode,
      { blind }
    )
  } catch (error) {
    throw new BadRequestError(error instanceof Error ? error.message : 'Fichier .env invalide.')
  }
}
const signature = (actor: string, appId: string, input: any) => {
  const key = $os.getenv('WEB_APPS_MASTER_KEY')
  const source =
    input.rows !== undefined && Array.isArray(input.rows)
      ? [
          'rows',
          input.rows.map((row: any) => [
            row?.name,
            row?.value,
            row?.setEmpty === true,
            row?.remove === true,
            row?.build,
            row?.runtime,
          ]),
        ]
      : ['text', input.text]
  return $security.hs256(JSON.stringify([actor, appId, input.revision, source, input.mode]), key)
}
export const HandleWebEnvironmentGet = (e: core.RequestEvent) => {
  const { app, blind } = actorAndApp(e, 'variables.read')
  return webJson(e, serialize(app, false, blind))
}
export const HandleWebEnvironmentReveal = (e: core.RequestEvent) => {
  const { app, user } = actorAndApp(e, 'variables.reveal')
  audit(app.id, user.id, 'revealed', { names: Object.keys(resolveWebEnvironment(app).env).sort() })
  e.response.header().set('Pragma', 'no-cache')
  return webJson(e, serialize(app, true))
}
export const HandleWebEnvironmentPreview = (e: core.RequestEvent) => {
  const { app, user, blind } = actorAndApp(e, 'variables.write'),
    input = webInput(e),
    plan = previewPlan(app, input, blind)
  return webJson(e, {
    revision: revision(app),
    previewToken: signature(user.id, app.id, input),
    changes: plan.changes,
    requiresRebuild: plan.requiresRebuild,
    total: Object.keys(plan.next).length,
  })
}
export const HandleWebEnvironmentPut = (e: core.RequestEvent) => {
  const { app, user, blind } = actorAndApp(e, 'variables.write'),
    input = webInput(e)
  if (typeof input.previewToken !== 'string' || !$security.equal(input.previewToken, signature(user.id, app.id, input)))
    throw new BadRequestError('Prévisualisez les changements avant de les appliquer.')
  let saved: core.Record | undefined
  $app.runInTransaction((tx) => {
    const row = tx.findRecordById('web_apps', app.id),
      plan = previewPlan(row, input)
    row.set('envEncrypted', encryptWebValue(plan.next))
    resolveWebEnvironment(row, tx)
    tx.save(row)
    audit(
      app.id,
      user.id,
      'updated',
      { mode: input.mode, changes: plan.changes, requiresRebuild: plan.requiresRebuild },
      tx
    )
    saved = row
  })
  return webJson(e, serialize(saved!, false, blind))
}
