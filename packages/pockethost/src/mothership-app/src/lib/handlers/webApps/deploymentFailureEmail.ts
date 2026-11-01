import { decryptWebValue, webRoot } from './api'
import { resolveWebEnvironment } from './projects'

const ID = /^[a-z0-9]{15}$/
export const DEPLOYMENT_EMAIL_MAX_BYTES = 1024 * 1024
const UNAVAILABLE = 'Journal indisponible. Consultez le déploiement dans le dashboard.'
/** No shell, symlink, special file or unbounded read; every directory is opened without following links. */
export const DEPLOYMENT_EMAIL_LOG_READER = String.raw`
import sys,os,stat,json,re,signal
signal.signal(signal.SIGALRM,lambda *_:sys.exit(1));signal.alarm(5)
limit=1048576;fds=[]
try:
 root=sys.argv[1];ident=sys.argv[2]
 if not root.startswith('/')or not re.fullmatch(r'[a-z0-9]{15}',ident):raise ValueError()
 parts=root.split('/')[1:]
 if any(part in ('.','..')for part in parts):raise ValueError()
 fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY);fds.append(fd)
 for part in [p for p in parts if p]+['logs']:
  fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);fds.append(fd)
 fd=os.open(ident+'.log',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd);fds.append(fd)
 info=os.fstat(fd)
 if not stat.S_ISREG(info.st_mode):raise ValueError()
 offset=max(0,info.st_size-limit);os.lseek(fd,offset,os.SEEK_SET);data=b''
 while len(data)<limit:
  chunk=os.read(fd,limit-len(data))
  if not chunk:break
  data+=chunk
 if offset:
  newline=data.find(b'\n');data=data[newline+1:]if newline>=0 else b''
 print(json.dumps({'text':data.decode('utf-8','replace'),'truncated':bool(offset)},ensure_ascii=True))
except Exception:print(json.dumps({'unavailable':True}))
finally:
 for fd in reversed(fds):os.close(fd)
`

/** Same literal public settings as the worker. A secret name always wins, even for a short literal. */
export const deploymentPublicConfiguration = (name: string, value: string, auto = false) => {
  if (/(?:^|_)(?:SECRET|PASSWORD|TOKEN|KEY|AUTH|CREDENTIALS?)(?:_|$)/.test(name)) return false
  if (auto && name.startsWith('RAILPACK_')) return true
  if (name.startsWith('NEXT_PUBLIC_')) return true
  if (['PGSSLMODE', 'DATABASE_SSL'].includes(name))
    return ['disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full', 'true', 'false'].includes(value)
  if (name === 'NODE_ENV' || name.endsWith('_ENVIRONMENT'))
    return ['production', 'development', 'test', 'staging', 'preview'].includes(value)
  if (name.endsWith('_ENABLED')) return ['true', 'false', '0', '1'].includes(value)
  return (name === 'PORT' || /_(?:CONCURRENCY|TIMEOUT_MS)$/.test(name)) && /^[0-9]{1,7}$/.test(value)
}
const cleanControls = (text: string) =>
  text
    .replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, '')
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '')
    .replace(/\r\n/g, '\n')
    .replace(/[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]/g, '')
const secretValues = (snapshot: any, current: any) => {
  const values: string[] = []
  const add = (value: unknown) => {
    if (typeof value !== 'string' || !value) return
    values.push(value, cleanControls(value))
    for (const line of value.split(/\r?\n/)) if (line.trim().length >= 12) values.push(line.trim())
  }
  for (const state of [snapshot, current]) {
    if (!state || typeof state !== 'object' || Array.isArray(state)) throw Error('Invalid environment')
    const env = state.env || {}
    if (typeof env !== 'object' || Array.isArray(env)) throw Error('Invalid environment')
    for (const [name, entry] of Object.entries(env) as [string, any][]) {
      if (!entry || typeof entry.value !== 'string') throw Error('Invalid environment')
      if (!deploymentPublicConfiguration(name, entry.value, state.runtime?.kind === 'auto')) add(entry.value)
    }
    add(state.runtime?.registry?.password)
    add(state.ci?.registry?.password)
  }
  return [...new Set(values.filter(Boolean))].sort((a, b) => b.length - a.length)
}
/** One substitution pass: a short password cannot repeatedly expand the replacement marker. */
export const sanitizeDeploymentEmailLog = (text: string, snapshot: any, current: any) => {
  const secrets = secretValues(snapshot, current),
    pattern = secrets.length
      ? new RegExp(
          ['\\[masqué\\]', ...secrets.map((value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))].join('|'),
          'g'
        )
      : null
  // Remove URL credentials before short secrets can alter a scheme or its separators.
  const cleaned = cleanControls(text)
    .replace(/\b([a-z][a-z0-9+.-]*:\/\/)[^\s/@]+@/gi, '$1[masqué]@')
    .replace(
      /\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|x-access-token:[^\s@]+)/g,
      '[masqué]'
    )
  return pattern ? cleaned.replace(pattern, '[masqué]') : cleaned
}
const oneLine = (value: unknown, limit = 180) =>
  cleanControls(typeof value === 'string' ? value : '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, limit)
/** Existing application only; deletion/suspension must also stop queued copies of its deployment logs. */
export const deploymentEmailApplication = (appId: string, store: any = $app): core.Record | null => {
  if (!ID.test(appId || '')) return null
  try {
    const app = store.findRecordById('web_apps', appId)
    if (app.getString('deletionId') || ['deleting', 'deleted', 'suspended'].includes(app.getString('status')))
      return null
    const owner = store.findRecordById('users', app.getString('uid'))
    if (owner.getString('suspension')) return null
    return app
  } catch {
    return null
  }
}
const failureJob = (appId: string, jobId: string, store: any) => {
  if (!ID.test(appId || '') || !ID.test(jobId || '')) return null
  try {
    const row = store.findRecordById('web_deployments', jobId)
    return row.getString('appId') === appId &&
      row.getString('status') === 'failed' &&
      ['deploy', 'ci-deploy'].includes(row.getString('action'))
      ? row
      : null
  } catch {
    return null
  }
}
/** Job metadata comes only from its frozen snapshot, never pending app configuration or an event payload. */
export const deploymentFailureDetails = (job: core.Record) => {
  let snapshot: any = null
  try {
    snapshot = decryptWebValue(job.getString('snapshotEncrypted'))
  } catch {}
  const repository = oneLine(snapshot?.config?.repository),
    branch = oneLine(snapshot?.config?.branch),
    commit = job.getString('commit')
  return {
    deploymentId: job.id,
    action: job.getString('action'),
    stage: oneLine(job.getString('stage'), 100),
    finishedAt: job.getString('finishedAt'),
    repository: /^(?:git:)?[A-Za-z0-9][A-Za-z0-9_./:@+-]{0,249}$/.test(repository) ? repository : '',
    branch: branch && !/[\s~^:?*\[\\]/.test(branch) ? branch : '',
    commit: /^[a-f0-9]{40,64}$/.test(commit) ? commit : '',
  }
}
/** UTF-8 tail bounded after masking as well (short secret replacements can make the text grow). */
export const boundedDeploymentAttachment = (text: string, truncated: boolean) => {
  const marker = '[Journal tronqué : seules les dernières lignes sont jointes.]\n',
    budget = DEPLOYMENT_EMAIL_MAX_BYTES - marker.length * 4
  let bytes = 0,
    start = text.length
  while (start > 0) {
    const code = text.charCodeAt(start - 1)
    const width = code < 0x80 ? 1 : code < 0x800 ? 2 : code >= 0xdc00 && code <= 0xdfff && start > 1 ? 4 : 3
    if (bytes + width > budget) break
    bytes += width
    start -= width === 4 ? 2 : 1
  }
  if (start > 0) {
    const newline = text.indexOf('\n', start)
    text = newline < 0 ? '' : text.slice(newline + 1)
    truncated = true
  }
  return (truncated ? marker : '') + text
}
export const deploymentFailureAttachment = (row: core.Record, payload: any, test: boolean, store: any = $app) => {
  const deliveryId = ID.test(row.id) ? row.id : 'invalid00000000',
    fallback = { filename: `deployment-${deliveryId}.log`, text: UNAVAILABLE + '\n', details: null as any }
  if (test) return { ...fallback, text: 'Journal fictif de test : la commande de construction a échoué (code 1).\n' }
  const appId = row.getString('scope') === 'platform' ? payload.details?.appId : row.getString('appId'),
    app = deploymentEmailApplication(appId, store),
    job = app && failureJob(app.id, payload.details?.deploymentId, store)
  if (!app || !job || payload.application?.id !== app.id) return fallback
  const metadata = deploymentFailureDetails(job)
  const result = {
    ...fallback,
    filename: `deployment-${app.id}-${job.id}.log`,
    details: { ...metadata, stage: '', summary: 'Détails indisponibles. Consultez le dashboard.' },
  }
  try {
    // Failure to decrypt either state prevents log release: masking must never become best effort.
    const encrypted = job.getString('snapshotEncrypted')
    if (!encrypted) return result
    const snapshot = decryptWebValue(encrypted),
      current = { env: resolveWebEnvironment(app, store).env, runtime: snapshot?.runtime }
    result.details.stage = oneLine(sanitizeDeploymentEmailLog(metadata.stage, snapshot, current), 100)
    result.details.summary = oneLine(sanitizeDeploymentEmailLog(job.getString('error'), snapshot, current), 300)
    const log = JSON.parse(
      toString($os.cmd('python3', '-c', DEPLOYMENT_EMAIL_LOG_READER, webRoot(), job.id).combinedOutput())
    )
    if (log.unavailable || typeof log.text !== 'string') return result
    result.text = boundedDeploymentAttachment(sanitizeDeploymentEmailLog(log.text, snapshot, current), !!log.truncated)
    return result
  } catch {
    return result
  }
}
