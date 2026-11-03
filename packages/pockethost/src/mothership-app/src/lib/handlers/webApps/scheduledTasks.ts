import { type WebPermission, webRoleAllows } from '../../../../../common/webAccess'
import { nextWebCron, parseWebCron, webCronOffset, type WebCronTimezone } from '../../../../../common/webCron'
import {
  decryptWebValue,
  encryptWebValue,
  requireWebAppPermission,
  webAppSettingActive,
  webDirectLoopback,
  webEnabled,
  webInput,
  webJson,
  webRoot,
} from './api'
import { emitWebAlert } from './notifications'
const TASKS = 'web_scheduled_tasks',
  RUNS = 'web_task_runs'
const stamp = (time = Date.now()) => new Date(time).toISOString().replace('T', ' ')
const ident = (value: unknown) => {
  if (typeof value !== 'string' || !/^[a-z0-9]{15}$/.test(value)) throw new BadRequestError('Tâche invalide.')
  return value
}
const taskFor = (e: core.RequestEvent, permission: WebPermission) => {
  const { user: actor, app: found } = requireWebAppPermission(e, permission),
    app = found!,
    task = $app.findRecordById(TASKS, ident(e.request!.pathValue('taskId')))
  if (task.getString('appId') !== app.id) throw new NotFoundError()
  return { actor, app, task }
}
const command = (row: core.Record) => String(decryptWebValue(row.getString('commandEncrypted')).command || '')
const runMetadata = (row: core.Record, includeLog = true) => ({
  id: row.id,
  taskId: row.getString('taskId'),
  taskName: row.getString('taskName'),
  trigger: row.getString('trigger'),
  status: row.getString('status'),
  scheduledAt: row.getString('scheduledAt'),
  startedAt: row.getString('startedAt'),
  finishedAt: row.getString('finishedAt'),
  deadline: row.getString('deadline'),
  durationMs: row.getInt('durationMs'),
  exitCode:
    row.getString('status') === 'queued' || row.getString('status') === 'running' ? null : row.getInt('exitCode'),
  log: includeLog ? row.getString('log') : '',
  truncated: row.getBool('truncated'),
  error: row.getString('error'),
  created: row.getString('created'),
})
/** La commande déchiffrée peut contenir des identifiants écrits en clair : réservée à tasks.manage. */
const taskMetadata = (row: core.Record, canManage = true) => {
  const last = $app.findRecordsByFilter(RUNS, 'taskId={:id}', '-created', 1, 0, { id: row.id })[0]
  const success = $app.findRecordsByFilter(RUNS, 'taskId={:id} && status="succeeded"', '-finishedAt', 1, 0, {
    id: row.id,
  })[0]
  return {
    id: row.id,
    name: row.getString('name'),
    ...(canManage ? { command: command(row) } : {}),
    schedule: row.getString('schedule'),
    timezone: row.getString('timezone') || 'UTC',
    catchUp: row.getString('catchUp') || 'once',
    maxCatchUpDelayMinutes: row.getInt('maxCatchUpDelayMinutes') || 15,
    lastMissedAt: row.getString('lastMissedAt'),
    lastSuccessAt: success?.getString('finishedAt') || '',
    late: row.getBool('enabled') && Date.parse(row.getString('nextRunAt')) < Date.now() - 60000,
    enabled: row.getBool('enabled'),
    timeoutSeconds: row.getInt('timeoutSeconds'),
    nextRunAt: row.getString('nextRunAt'),
    lastRun: last ? runMetadata(last, false) : null,
    updated: row.getString('updated'),
  }
}
const next = (schedule: unknown, now = Date.now(), timezone: WebCronTimezone = 'UTC') => {
  try {
    const parsed = parseWebCron(schedule),
      due = nextWebCron(parsed, now, timezone)
    if (!due) throw new Error('Cette expression ne produit aucune occurrence dans les cinq prochaines années.')
    return { schedule: parsed.expression, due }
  } catch (error) {
    throw new BadRequestError(error instanceof Error ? error.message : 'Expression cron invalide.')
  }
}
const validate = (body: any, row?: core.Record) => {
  if (
    Object.keys(body).some(
      (key) =>
        ![
          'name',
          'command',
          'schedule',
          'enabled',
          'timeoutSeconds',
          'timezone',
          'catchUp',
          'maxCatchUpDelayMinutes',
        ].includes(key)
    )
  )
    throw new BadRequestError('Options de tâche invalides.')
  const name = body.name === undefined ? row?.getString('name') : body.name
  const cmd = body.command === undefined ? row && command(row) : body.command
  const schedule = body.schedule === undefined ? row?.getString('schedule') : body.schedule
  const enabled = body.enabled === undefined ? (row ? row.getBool('enabled') : true) : body.enabled
  const timeout = body.timeoutSeconds === undefined ? (row ? row.getInt('timeoutSeconds') : 60) : body.timeoutSeconds
  if (typeof name !== 'string' || !name.trim() || name.trim().length > 80 || name.includes('\0'))
    throw new BadRequestError('Nommez la tâche avec 1 à 80 caractères.')
  if (typeof cmd !== 'string' || !cmd.trim() || cmd.length > 4000 || cmd.includes('\0'))
    throw new BadRequestError('Commande requise, limitée à 4 000 caractères.')
  if (typeof enabled !== 'boolean' || !Number.isInteger(timeout) || timeout < 1 || timeout > 900)
    throw new BadRequestError('La durée maximale doit être comprise entre 1 et 900 secondes.')
  const timezone = body.timezone ?? (row ? row.getString('timezone') || 'UTC' : 'Indian/Reunion')
  try {
    webCronOffset(timezone)
  } catch {
    throw new BadRequestError('Choisissez UTC ou La Réunion (UTC+4).')
  }
  const catchUp = body.catchUp ?? (row?.getString('catchUp') || 'once')
  const maxCatchUpDelayMinutes = body.maxCatchUpDelayMinutes ?? (row?.getInt('maxCatchUpDelayMinutes') || 15)
  if (
    !['once', 'skip'].includes(catchUp) ||
    !Number.isInteger(maxCatchUpDelayMinutes) ||
    maxCatchUpDelayMinutes < 1 ||
    maxCatchUpDelayMinutes > 1440
  )
    throw new BadRequestError('Choisissez une reprise unique ou aucune, dans un délai de 1 à 1 440 minutes.')
  return {
    name: name.trim(),
    command: cmd,
    enabled,
    timeoutSeconds: timeout,
    timezone,
    catchUp,
    maxCatchUpDelayMinutes,
    ...next(schedule, Date.now(), timezone),
  }
}
/**
 * Les tâches appartiennent à l'application, pas à leur auteur : actives tant que l'application existe dans un projet
 * (legacy : tant que l'auteur reste superAdmin). Les droits sont vérifiés à l'écriture et au lancement manuel.
 */
const actorValid = (id: string, appId: string, store: any = $app) => webAppSettingActive(appId, id, store)
const publication = (appId: string, store: any = $app) => {
  const app = store.findRecordById('web_apps', appId),
    id = app.getString('activeDeployment')
  if (app.getString('status') !== 'running' || !/^[a-z0-9]{15}$/.test(id))
    throw new BadRequestError('Démarrez une version publiée avant de lancer une tâche.')
  const deployment = store.findRecordById('web_deployments', id),
    result = JSON.parse(deployment.getString('result') || '{}')
  if (
    deployment.getString('appId') !== appId ||
    deployment.getString('status') !== 'ready' ||
    result.container !== `ph-web-${appId}-${id}`
  )
    throw new BadRequestError('Le conteneur publié est indisponible.')
  return { app, deployment, containerName: result.container }
}
const overlap = (taskId: string, store: any = $app) =>
  store.findRecordsByFilter(RUNS, 'taskId={:id} && (status="queued" || status="running")', '', 1, 0, { id: taskId })
    .length > 0
const enqueue = (
  task: core.Record,
  actorId: string,
  trigger: string,
  occurrence: string,
  scheduledAt: string,
  store: any
) => {
  if (overlap(task.id, store))
    throw new BadRequestError('Cette tâche possède déjà une exécution en attente ou en cours.')
  const row = new Record(store.findCollectionByNameOrId(RUNS))
  row.set('appId', task.getString('appId'))
  row.set('taskId', task.id)
  row.set('taskName', task.getString('name'))
  row.set('actorId', actorId)
  row.set('trigger', trigger)
  row.set('occurrence', occurrence)
  row.set('scheduledAt', scheduledAt)
  row.set('status', 'queued')
  row.set('timeoutSeconds', task.getInt('timeoutSeconds'))
  row.set('exitCode', -1)
  row.set(
    'snapshotEncrypted',
    encryptWebValue({ command: command(task), timeoutSeconds: task.getInt('timeoutSeconds') })
  )
  store.save(row)
  return row
}
export const HandleWebTasksList = (e: core.RequestEvent) => {
  const { app: found, role } = requireWebAppPermission(e, 'app.read'),
    app = found!,
    canManage = webRoleAllows(role, 'tasks.manage')
  let worker: any = { ready: false, checkedAt: '' }
  try {
    const value = JSON.parse(toString($os.readFile(`${webRoot()}/tasks-status.json`)))
    worker = {
      ready: value.ready === true && Date.parse(value.checkedAt) > Date.now() - 25000,
      checkedAt: value.checkedAt || '',
    }
  } catch {}
  return webJson(e, {
    items: $app
      .findRecordsByFilter(TASKS, 'appId={:id}', 'created', 100, 0, { id: app.id })
      .map((row) => taskMetadata(row, canManage)),
    timezone: 'Indian/Reunion',
    worker,
  })
}
export const HandleWebTasksCreate = (e: core.RequestEvent) => {
  const { user: actor, app: found } = requireWebAppPermission(e, 'tasks.manage'),
    app = found!,
    value = validate(webInput(e))
  let result: any
  $app.runInTransaction((tx) => {
    if (tx.findRecordsByFilter(TASKS, 'appId={:id}', '', 20, 0, { id: app.id }).length >= 20)
      throw new BadRequestError('Vingt tâches maximum par application.')
    const row = new Record(tx.findCollectionByNameOrId(TASKS))
    row.set('appId', app.id)
    row.set('actorId', actor.id)
    row.set('name', value.name)
    row.set('commandEncrypted', encryptWebValue({ command: value.command }))
    row.set('schedule', value.schedule)
    row.set('enabled', value.enabled)
    row.set('timeoutSeconds', value.timeoutSeconds)
    row.set('timezone', value.timezone)
    row.set('catchUp', value.catchUp)
    row.set('maxCatchUpDelayMinutes', value.maxCatchUpDelayMinutes)
    row.set('nextRunAt', value.due)
    tx.save(row)
    result = row
  })
  return webJson(e, { task: taskMetadata(result) }, 201)
}
export const HandleWebTaskUpdate = (e: core.RequestEvent) => {
  const { actor, task } = taskFor(e, 'tasks.manage'),
    value = validate(webInput(e), task)
  task.set('actorId', actor.id)
  task.set('name', value.name)
  task.set('commandEncrypted', encryptWebValue({ command: value.command }))
  task.set('timeoutSeconds', value.timeoutSeconds)
  if (
    task.getString('schedule') !== value.schedule ||
    (task.getString('timezone') || 'UTC') !== value.timezone ||
    (!task.getBool('enabled') && value.enabled)
  )
    task.set('nextRunAt', value.due)
  task.set('timezone', value.timezone)
  task.set('catchUp', value.catchUp)
  task.set('maxCatchUpDelayMinutes', value.maxCatchUpDelayMinutes)
  task.set('schedule', value.schedule)
  task.set('enabled', value.enabled)
  $app.save(task)
  return webJson(e, { task: taskMetadata(task) })
}
export const HandleWebTaskDelete = (e: core.RequestEvent) => {
  const { task } = taskFor(e, 'tasks.manage')
  $app.runInTransaction((tx) => {
    const rows = tx.findRecordsByFilter(RUNS, 'taskId={:id} && (status="queued" || status="running")', '', 100, 0, {
      id: task.id,
    })
    if (rows.some((row) => row.getString('status') === 'running'))
      throw new BadRequestError('Attendez la fin de l’exécution avant de supprimer cette tâche.')
    for (const row of rows) {
      row.set('status', 'cancelled')
      row.set('finishedAt', stamp())
      row.set('snapshotEncrypted', '')
      row.set('error', 'Tâche supprimée avant son exécution.')
      tx.save(row)
    }
    tx.delete(task)
  })
  return webJson(e, { deleted: true })
}
export const HandleWebTaskRun = (e: core.RequestEvent) => {
  const { actor, app, task } = taskFor(e, 'tasks.run')
  let result: any
  if (Object.keys(webInput(e)).length) throw new BadRequestError('Aucune option d’exécution n’est attendue.')
  $app.runInTransaction((tx) => {
    publication(app.id, tx)
    result = enqueue(
      tx.findRecordById(TASKS, task.id),
      actor.id,
      'manual',
      `manual:${$security.randomString(32)}`,
      stamp(),
      tx
    )
  })
  return webJson(e, { run: runMetadata(result) }, 202)
}
export const HandleWebTaskRuns = (e: core.RequestEvent) => {
  const app = requireWebAppPermission(e, 'logs.read').app!,
    query = e.request!.url!.query(),
    page = Math.max(1, Math.min(100000, Math.floor(Number(query.get('page')) || 1))),
    taskId = query.get('taskId') || ''
  if (taskId) ident(taskId)
  const filter = 'appId={:id}' + (taskId ? ' && taskId={:taskId}' : ''),
    rows = $app.findRecordsByFilter(RUNS, filter, '-created,-id', 11, (page - 1) * 10, { id: app.id, taskId })
  const logRunId = query.get('logRunId') || ''
  if (logRunId) ident(logRunId)
  return webJson(e, {
    items: rows.slice(0, 10).map((row) => runMetadata(row, row.id === logRunId)),
    page,
    hasMore: rows.length > 10,
  })
}
const requireWorker = (e: core.RequestEvent) => {
  if (!webEnabled()) throw new NotFoundError()
  const expected = $os.getenv('WEB_APPS_WORKER_SECRET'),
    received = e.request!.header.get('X-Web-Worker') || ''
  if (expected.length < 32 || !$security.equal(expected, received)) throw new UnauthorizedError()
  if (!webDirectLoopback(e)) throw new ForbiddenError('Accès local requis.')
}
const finish = (row: core.Record, status: string, error: string, store: any) => {
  row.set('status', status)
  row.set('error', error)
  row.set('finishedAt', stamp())
  row.set('durationMs', Math.max(0, Date.now() - Date.parse(row.getString('startedAt') || stamp())))
  row.set('leaseHash', '')
  row.set('snapshotEncrypted', '')
  store.save(row)
}
const lease = (input: any, store: any = $app) => {
  const row = store.findRecordById(RUNS, ident(input.id))
  if (
    row.getString('status') !== 'running' ||
    typeof input.proof !== 'string' ||
    !/^[A-Za-z0-9]{80}$/.test(input.proof) ||
    !$security.equal(row.getString('leaseHash'), $security.sha256(input.proof))
  )
    throw new UnauthorizedError('Exécution invalide.')
  return row
}
export const HandleWebTaskClaim = (e: core.RequestEvent) => {
  requireWorker(e)
  let result: any = null
  $app.runInTransaction((tx) => {
    const now = Date.now(),
      nowText = stamp(now)
    for (const row of tx.findRecordsByFilter(RUNS, 'status="running"', 'startedAt', 100, 0)) {
      if (Date.parse(row.getString('deadline')) + 15000 < now) {
        row.set('exitCode', 124)
        finish(
          row,
          'timed_out',
          'La durée maximale a été dépassée ; le superviseur du conteneur a interrompu la commande.',
          tx
        )
      } else return
    }
    for (const task of tx.findRecordsByFilter(
      TASKS,
      'enabled=true && nextRunAt!="" && nextRunAt<={:now}',
      'nextRunAt',
      100,
      0,
      { now: nowText }
    )) {
      const scheduledAt = task.getString('nextRunAt'),
        occurrence = `cron:${scheduledAt.slice(0, 16)}`
      task.set('lastScheduledAt', scheduledAt)
      task.set(
        'nextRunAt',
        next(task.getString('schedule'), now, (task.getString('timezone') || 'UTC') as WebCronTimezone).due
      )
      if (!actorValid(task.getString('actorId'), task.getString('appId'), tx)) {
        // Application legacy dont l'auteur a perdu ses droits : la planification est suspendue, et c'est signalé.
        task.set('enabled', false)
        emitWebAlert(
          tx,
          'app',
          task.getString('appId'),
          'task_failed',
          'task-disabled:' + task.id + ':' + scheduledAt,
          {
            taskName: task.getString('name').slice(0, 80),
            status: 'disabled',
            reason: 'rights',
          }
        )
      } else if (
        !tx.findRecordsByFilter(RUNS, 'taskId={:id} && occurrence={:occurrence}', '', 1, 0, { id: task.id, occurrence })
          .length
      ) {
        const delay = Math.max(0, now - Date.parse(scheduledAt))
        const missed =
          overlap(task.id, tx) ||
          (delay > 60000 &&
            (task.getString('catchUp') === 'skip' || delay > (task.getInt('maxCatchUpDelayMinutes') || 15) * 60000))
        if (missed) {
          const skipped = new Record(tx.findCollectionByNameOrId(RUNS))
          for (const [key, value] of Object.entries({
            appId: task.getString('appId'),
            taskId: task.id,
            taskName: task.getString('name'),
            actorId: task.getString('actorId'),
            trigger: 'cron',
            occurrence,
            scheduledAt,
            status: 'missed',
            timeoutSeconds: task.getInt('timeoutSeconds'),
            exitCode: -1,
            finishedAt: nowText,
            error: 'Occurrence manquée : tâche déjà en attente ou délai de reprise dépassé.',
          }))
            skipped.set(key, value)
          tx.save(skipped)
          task.set('lastMissedAt', scheduledAt)
        } else enqueue(task, task.getString('actorId'), 'cron', occurrence, scheduledAt, tx)
      }
      tx.save(task)
    }
    for (const row of tx.findRecordsByFilter(RUNS, 'status="queued"', 'created,id', 100, 0)) {
      try {
        if (!actorValid(row.getString('actorId'), row.getString('appId'), tx)) throw new Error('rights')
        const current = publication(row.getString('appId'), tx),
          snapshot = decryptWebValue(row.getString('snapshotEncrypted')),
          published = decryptWebValue(current.deployment.getString('snapshotEncrypted'))
        const values = [
          ...Object.values(decryptWebValue(current.app.getString('envEncrypted')) || {}),
          ...Object.values(published.env || {}),
        ] as { value?: string }[]
        const secrets = [
          ...new Set(
            values
              .map((entry) => entry?.value)
              .filter((value): value is string => typeof value === 'string' && value.length > 0)
          ),
        ]
        const proof = $security.randomString(80),
          timeout = row.getInt('timeoutSeconds')
        row.set('deploymentId', current.deployment.id)
        row.set('status', 'running')
        row.set('startedAt', stamp(now))
        row.set('deadline', stamp(now + (timeout + 5) * 1000))
        row.set('leaseHash', $security.sha256(proof))
        row.set('snapshotEncrypted', encryptWebValue({ ...snapshot, secrets }))
        tx.save(row)
        result = {
          id: row.id,
          taskId: row.getString('taskId'),
          appId: current.app.id,
          deploymentId: current.deployment.id,
          containerName: current.containerName,
          command: snapshot.command,
          timeoutSeconds: timeout,
          deadline: row.getString('deadline'),
          proof,
          secrets,
        }
        break
      } catch {
        finish(row, 'failed', 'Le conteneur publié ou les droits sur le projet ne permettent plus cette exécution.', tx)
      }
    }
  })
  return webJson(e, { job: result })
}
export const HandleWebTaskValidate = (e: core.RequestEvent) => {
  requireWorker(e)
  const row = lease(webInput(e))
  let valid = false
  try {
    const current = publication(row.getString('appId'))
    valid =
      actorValid(row.getString('actorId'), row.getString('appId')) &&
      current.deployment.id === row.getString('deploymentId') &&
      Date.parse(row.getString('deadline')) > Date.now()
  } catch {}
  return webJson(e, { valid })
}
export const HandleWebTaskReport = (e: core.RequestEvent) => {
  requireWorker(e)
  const input = webInput(e)
  if (
    !['succeeded', 'failed', 'timed_out'].includes(input.status) ||
    !Number.isInteger(input.exitCode) ||
    input.exitCode < -1 ||
    input.exitCode > 255 ||
    typeof input.log !== 'string' ||
    input.log.length > 200000 ||
    typeof input.truncated !== 'boolean'
  )
    throw new BadRequestError('Compte rendu de tâche invalide.')
  if (input.status === 'succeeded' && input.exitCode !== 0) throw new BadRequestError('Code de sortie incohérent.')
  let result: any
  $app.runInTransaction((tx) => {
    const row = lease(input, tx),
      snapshot = decryptWebValue(row.getString('snapshotEncrypted'))
    let log = input.log
    for (const secret of (snapshot.secrets || []).sort((a: string, b: string) => b.length - a.length))
      if (secret) log = log.split(secret).join('[masqué]')
    row.set('log', log.slice(-65536))
    row.set('truncated', input.truncated || log.length > 65536)
    row.set('exitCode', input.exitCode)
    const error =
      input.status === 'timed_out'
        ? 'La durée maximale a été dépassée.'
        : input.reason === 'container_changed'
          ? 'Le conteneur publié ou les droits ont changé pendant l’exécution.'
          : input.status === 'failed'
            ? 'La commande a échoué. Consultez sa sortie et son code de retour.'
            : ''
    finish(row, input.status, error, tx)
    result = runMetadata(row)
  })
  return webJson(e, { run: result })
}
