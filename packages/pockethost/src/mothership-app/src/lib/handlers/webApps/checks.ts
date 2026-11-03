import { webRoleAllows } from '../../../../../common/webAccess'
import {
  emptyApplicationChecks,
  validateApplicationChecks,
  type ApplicationChecks,
  type JourneyCheck,
} from '../../../../../common/webChecks'
import {
  decryptWebValue,
  encryptWebValue,
  requireWebAppPermission,
  webActorCan,
  webAppSettingActive,
  webConfig,
  webDirectLoopback,
  webEnabled,
  webInput,
  webJson,
} from './api'

const SETTINGS = 'web_app_checks',
  RUNS = 'web_app_check_runs',
  ID = /^[a-z0-9]{15}$/
const stamp = (at = Date.now()) => new Date(at).toISOString().replace('T', ' ')
const settings = (appId: string, store: any = $app): core.Record | null =>
  store.findRecordsByFilter(SETTINGS, 'appId={:id}', '', 1, 0, { id: appId })[0] || null
// Unlike operational tasks, these optional credential-bearing scenarios are
// suspended when their configuration author loses access. Saving transfers the
// responsibility to the current application administrator.
const checksActive = (appId: string, actorId: string, store: any) =>
  webAppSettingActive(appId, actorId, store) && webActorCan(actorId, { appId }, 'app.configure', store)
const configOf = (row: core.Record | null): ApplicationChecks =>
  row ? validateApplicationChecks(JSON.parse(row.getString('config'))) : emptyApplicationChecks()
export const webChecksSnapshot = (appId: string, store: any = $app) => ({
  preflight: configOf(settings(appId, store)).preflight,
})
const runMetadata = (row: core.Record) => ({
  id: row.id,
  journeyId: row.getString('journeyId'),
  journeyName: row.getString('journeyName'),
  status: row.getString('status'),
  trigger: row.getString('trigger'),
  scheduledAt: row.getString('scheduledAt'),
  startedAt: row.getString('startedAt'),
  finishedAt: row.getString('finishedAt'),
  result: JSON.parse(row.getString('result') || 'null'),
})
const hostOf = (app: core.Record, journey: JourneyCheck, store: any) => {
  let host = ''
  if (journey.domain === 'generated') host = webConfig(app).generatedDomain || ''
  else {
    const domain = store.findRecordById('web_app_domains', journey.domain)
    if (domain.getString('appId') !== app.id || domain.getString('status') !== 'active')
      throw new BadRequestError('Le domaine du parcours doit être actif dans cette application.')
    host = domain.getString('host')
  }
  if (!/^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(host))
    throw new BadRequestError('Le parcours nécessite un domaine HTTPS public valide.')
  return host
}
const overlap = (appId: string, id: string, store: any) =>
  store.findRecordsByFilter(
    RUNS,
    'appId={:appId} && journeyId={:id} && (status="queued" || status="running")',
    '',
    1,
    0,
    { appId, id }
  ).length > 0
const enqueue = (row: core.Record, app: core.Record, journey: JourneyCheck, trigger: string, store: any) => {
  if (overlap(app.id, journey.id, store)) return null
  if (app.getString('status') !== 'running' || !ID.test(app.getString('activeDeployment')))
    throw new BadRequestError('Publiez et démarrez le site avant de lancer un parcours.')
  const secrets = decryptWebValue(row.getString('secretsEncrypted') || '') || {},
    password = secrets[journey.id]?.password || ''
  if (journey.auth.kind === 'json' && !password)
    throw new BadRequestError('Enregistrez le mot de passe du compte de test avant de lancer ce parcours.')
  const run = new Record(store.findCollectionByNameOrId(RUNS))
  for (const [key, value] of Object.entries({
    appId: app.id,
    settingsId: row.id,
    journeyId: journey.id,
    journeyName: journey.name,
    actorId: row.getString('actorId'),
    trigger,
    status: 'queued',
    scheduledAt: stamp(),
    snapshotEncrypted: encryptWebValue({
      host: hostOf(app, journey, store),
      journey,
      password,
      revision: row.getString('revision'),
    }),
  }))
    run.set(key, value)
  store.save(run)
  return run
}
export const HandleWebChecksGet = (e: core.RequestEvent) => {
  const { app: found, role } = requireWebAppPermission(e, 'app.read'),
    app = found!,
    row = settings(app.id),
    canManage = webRoleAllows(role, 'app.configure')
  const secrets = canManage && row ? decryptWebValue(row.getString('secretsEncrypted') || '') || {} : {}
  const history = webRoleAllows(role, 'logs.read')
    ? $app.findRecordsByFilter(RUNS, 'appId={:id}', '-created,-id', 30, 0, { id: app.id }).map(runMetadata)
    : []
  return webJson(e, {
    journeysActive: !!row && checksActive(app.id, row.getString('actorId'), $app),
    settings: canManage
      ? {
          revision: row?.getString('revision') || '',
          config: configOf(row),
          hasSecret: Object.fromEntries(Object.keys(secrets).map((id) => [id, !!secrets[id]?.password])),
        }
      : null,
    history,
    summary: row ? JSON.parse(row.getString('summary') || '{}') : {},
  })
}
export const HandleWebChecksPut = (e: core.RequestEvent) => {
  const { user: actor, app: found } = requireWebAppPermission(e, 'app.configure'),
    app = found!,
    body = webInput(e)
  if (
    Object.keys(body).sort().join(',') !== 'config,revision,secrets' ||
    typeof body.revision !== 'string' ||
    body.revision.length > 32 ||
    JSON.stringify(body).length > 150000 ||
    !body.secrets ||
    typeof body.secrets !== 'object' ||
    Array.isArray(body.secrets)
  )
    throw new BadRequestError('Configuration et révision attendues.')
  let config: ApplicationChecks
  try {
    config = validateApplicationChecks(body.config)
  } catch {
    throw new BadRequestError(
      'Configuration de contrôles invalide : six parcours et dix lectures au maximum par parcours.'
    )
  }
  const ids = new Set(config.journeys.map((row) => row.id))
  for (const [id, secret] of Object.entries(body.secrets) as [string, any][]) {
    if (
      !ids.has(id) ||
      !secret ||
      Object.keys(secret).join(',') !== 'password' ||
      (secret.password !== null && (typeof secret.password !== 'string' || secret.password.length > 4096))
    )
      throw new BadRequestError('Mot de passe de test invalide.')
  }
  let result: core.Record | null = null
  $app.runInTransaction((tx) => {
    const old = settings(app.id, tx)
    if ((old?.getString('revision') || '') !== body.revision)
      throw new ApiError(409, 'Ces contrôles ont changé. Rechargez avant d’enregistrer.')
    for (const item of [
      ...config.preflight.authentication,
      ...config.preflight.publicUrls.filter((row) => row.target === 'privateDatabase'),
    ]) {
      const binding = tx.findRecordById('web_private_db_bindings', item.bindingId!)
      if (binding.getString('appId') !== app.id || !binding.getBool('enabled'))
        throw new BadRequestError('Sélectionnez une liaison privée active de cette application.')
    }
    const previous = old ? decryptWebValue(old.getString('secretsEncrypted') || '') || {} : {},
      secrets: any = {}
    for (const journey of config.journeys) {
      hostOf(app, journey, tx)
      if (journey.auth.kind === 'none') continue
      const supplied = body.secrets[journey.id]
      const password = supplied === undefined ? previous[journey.id]?.password || '' : supplied.password || ''
      if (journey.enabled && !password)
        throw new BadRequestError('Un mot de passe dédié est requis pour activer ce parcours.')
      if (password) secrets[journey.id] = { password }
    }
    const row = old || new Record(tx.findCollectionByNameOrId(SETTINGS))
    row.set('appId', app.id)
    row.set('actorId', actor.id)
    row.set('config', config)
    row.set('summary', {})
    row.set('secretsEncrypted', encryptWebValue(secrets))
    row.set('revision', $security.randomString(24))
    row.set('nextRunAt', config.journeys.some((row) => row.enabled) ? stamp(Date.now() + 60000) : '')
    tx.save(row)
    result = row
  })
  $app.logger().info('Application read-only checks updated', 'appId', app.id, 'actor', actor.id)
  return webJson(e, { revision: result!.getString('revision'), saved: true })
}
export const HandleWebChecksRun = (e: core.RequestEvent) => {
  const { app: found } = requireWebAppPermission(e, 'health.manage'),
    app = found!,
    body = webInput(e)
  if (
    Object.keys(body).some((key) => key !== 'journeyId') ||
    (body.journeyId !== undefined && (typeof body.journeyId !== 'string' || !ID.test(body.journeyId)))
  )
    throw new BadRequestError('Parcours invalide.')
  const runs: any[] = []
  $app.runInTransaction((tx) => {
    const row = settings(app.id, tx)
    if (!row) throw new BadRequestError('Enregistrez un parcours avant de le lancer.')
    const selected = configOf(row).journeys.filter((item) =>
      body.journeyId ? item.id === body.journeyId : item.enabled
    )
    if (!selected.length) throw new BadRequestError('Aucun parcours sélectionné.')
    for (const journey of selected) {
      const run = enqueue(row, app, journey, 'manual', tx)
      if (run) runs.push(runMetadata(run))
    }
  })
  return webJson(e, { runs }, 202)
}
const requireWorker = (e: core.RequestEvent) => {
  if (!webEnabled()) throw new NotFoundError()
  const expected = $os.getenv('WEB_APPS_WORKER_SECRET'),
    received = e.request!.header.get('X-Web-Worker') || ''
  if (expected.length < 32 || !$security.equal(expected, received) || !webDirectLoopback(e))
    throw new ForbiddenError('Worker local requis.')
}
const finish = (run: core.Record, result: any, store: any) => {
  let snapshot: any = {}
  try {
    snapshot = decryptWebValue(run.getString('snapshotEncrypted'))
  } catch {}
  run.set('status', result.status)
  run.set('result', result)
  run.set('finishedAt', stamp())
  run.set('leaseHash', '')
  run.set('snapshotEncrypted', '')
  store.save(run)
  try {
    const row = store.findRecordById(SETTINGS, run.getString('settingsId')),
      summary = JSON.parse(row.getString('summary') || '{}')
    if (
      row.getString('revision') === snapshot.revision &&
      configOf(row).journeys.some((item) => item.id === run.getString('journeyId'))
    ) {
      summary[run.getString('journeyId')] = { ...runMetadata(run), scheduledAt: run.getString('scheduledAt') }
      row.set('summary', summary)
      store.save(row)
    }
  } catch {}
  for (const old of store.findRecordsByFilter(
    RUNS,
    'appId={:id} && status!="queued" && status!="running"',
    '-created,-id',
    100,
    1000,
    { id: run.getString('appId') }
  ))
    store.delete(old)
}
export const HandleWebChecksClaim = (e: core.RequestEvent) => {
  requireWorker(e)
  const body = webInput(e)
  let job: any = null
  if (
    Object.keys(body).some((key) => key !== 'cleanupOnly') ||
    (body.cleanupOnly !== undefined && typeof body.cleanupOnly !== 'boolean')
  )
    throw new BadRequestError('Réclamation invalide.')
  $app.runInTransaction((tx) => {
    const now = Date.now()
    for (const run of tx.findRecordsByFilter(RUNS, 'status="running"', 'startedAt', 100, 0)) {
      if (Date.parse(run.getString('deadline')) > now) return
      finish(run, { status: 'failed', errorCode: 'worker_lost', steps: [] }, tx)
    }
    if (body.cleanupOnly === true) return
    for (const row of tx.findRecordsByFilter(SETTINGS, 'nextRunAt!="" && nextRunAt<={:now}', 'nextRunAt', 100, 0, {
      now: stamp(now),
    })) {
      const config = configOf(row),
        summary = JSON.parse(row.getString('summary') || '{}'),
        app = tx.findRecordById('web_apps', row.getString('appId'))
      const allowed = checksActive(app.id, row.getString('actorId'), tx)
      for (const journey of config.journeys) {
        if (!journey.enabled || !allowed) continue
        const previous = Date.parse(summary[journey.id]?.scheduledAt || row.getString('created'))
        if (Number.isFinite(previous) && previous + journey.intervalMinutes * 60000 > now) continue
        try {
          enqueue(row, app, journey, 'scheduled', tx)
        } catch {}
      }
      row.set('nextRunAt', allowed && config.journeys.some((row) => row.enabled) ? stamp(now + 60000) : '')
      tx.save(row)
    }
    for (const run of tx.findRecordsByFilter(RUNS, 'status="queued"', 'created,id', 100, 0)) {
      try {
        const row = tx.findRecordById(SETTINGS, run.getString('settingsId')),
          snapshot = decryptWebValue(run.getString('snapshotEncrypted')),
          app = tx.findRecordById('web_apps', run.getString('appId'))
        if (
          row.getString('revision') !== snapshot.revision ||
          !checksActive(app.id, run.getString('actorId'), tx) ||
          app.getString('status') !== 'running' ||
          hostOf(app, snapshot.journey, tx) !== snapshot.host
        )
          throw Error('Changed')
        const proof = $security.randomString(80)
        run.set('status', 'running')
        run.set('startedAt', stamp(now))
        run.set('deadline', stamp(now + 75000))
        run.set('leaseHash', $security.sha256(proof))
        tx.save(run)
        job = {
          id: run.id,
          proof,
          appId: app.id,
          deadline: run.getString('deadline'),
          input: { host: snapshot.host, journey: snapshot.journey, password: snapshot.password },
        }
        break
      } catch {
        finish(run, { status: 'cancelled', errorCode: 'configuration_changed', steps: [] }, tx)
      }
    }
  })
  return webJson(e, { job })
}
export const HandleWebChecksReport = (e: core.RequestEvent) => {
  requireWorker(e)
  const body = webInput(e)
  if (
    typeof body.id !== 'string' ||
    !ID.test(body.id) ||
    typeof body.proof !== 'string' ||
    !/^[A-Za-z0-9]{80}$/.test(body.proof) ||
    !body.result ||
    !['passed', 'failed'].includes(body.result.status) ||
    JSON.stringify(body.result).length > 30000
  )
    throw new BadRequestError('Compte rendu invalide.')
  $app.runInTransaction((tx) => {
    const run = tx.findRecordById(RUNS, body.id)
    if (
      run.getString('status') !== 'running' ||
      !(Date.parse(run.getString('deadline')) > Date.now()) ||
      !$security.equal(run.getString('leaseHash'), $security.sha256(body.proof))
    )
      throw new UnauthorizedError()
    const snapshot = decryptWebValue(run.getString('snapshotEncrypted')),
      allowed = new Set([
        '',
        'origin_unavailable',
        'authentication_failed',
        'journey_failed',
        'configuration',
        'timeout',
        'network',
        'worker_lost',
      ])
    if (!allowed.has(body.result.errorCode) || !Array.isArray(body.result.steps) || body.result.steps.length > 11)
      throw new BadRequestError('Résultat invalide.')
    const expected = [
      ...(snapshot.journey.auth.kind === 'json' ? [{ name: 'Connexion du compte de test', status: 0 }] : []),
      ...snapshot.journey.steps,
    ]
    if (
      body.result.steps.length > expected.length ||
      (body.result.status === 'passed' &&
        (body.result.errorCode !== '' ||
          body.result.steps.length !== expected.length ||
          body.result.steps.some((row: any) => row.status !== 'passed'))) ||
      (body.result.status === 'failed' && body.result.errorCode === '')
    )
      throw new BadRequestError('Un parcours incomplet ne peut pas être déclaré réussi.')
    const steps = body.result.steps.map((step: any, index: number) => {
      if (
        expected[index]?.name !== step.name ||
        !['passed', 'failed'].includes(step.status) ||
        !Number.isInteger(step.httpStatus) ||
        step.httpStatus < 0 ||
        step.httpStatus > 599 ||
        !['', 'authentication_failed', 'http_status', 'json_assertion', 'network'].includes(step.code) ||
        (step.status === 'passed' &&
          (step.code !== '' ||
            (expected[index].status
              ? step.httpStatus !== expected[index].status
              : step.httpStatus < 200 || step.httpStatus >= 300)))
      )
        throw new BadRequestError('Étape invalide.')
      return {
        name: step.name,
        status: step.status,
        httpStatus: step.httpStatus,
        code: step.code,
        durationMs: Math.max(0, Math.min(60000, Number(step.durationMs) || 0)),
      }
    })
    finish(
      run,
      {
        status: body.result.status,
        errorCode: body.result.errorCode,
        steps,
        durationMs: Math.max(0, Math.min(60000, Number(body.result.durationMs) || 0)),
      },
      tx
    )
  })
  return webJson(e, { saved: true })
}
