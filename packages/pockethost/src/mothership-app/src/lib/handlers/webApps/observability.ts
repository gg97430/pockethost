import {
  decryptWebValue,
  requireWebAppPermission,
  webDirectLoopback,
  webEnabled,
  webInput,
  webJson,
  webRoot,
} from './api'
export const RUNTIME_LOG_READER = String.raw`
import sys,json,pathlib,os,stat,datetime,re
root=pathlib.Path(sys.argv[1]).absolute();app=sys.argv[2];deployment=sys.argv[3];q=json.loads(sys.argv[4]);empty={'items':[],'generation':'','deploymentId':deployment,'checkedAt':'','state':'waiting','olderCursor':'','newerCursor':'','hasOlder':False,'hasNewer':False,'reset':False,'truncated':False,'retention':{'hours':24,'maxBytes':2097152}}
try:
 path=root/'runtime-logs'/(app+'.json')
 if root.resolve()!=root or path.resolve()!=path:raise ValueError()
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  info=os.fstat(fd)
  if not stat.S_ISREG(info.st_mode)or info.st_size>3145728:raise ValueError()
  with os.fdopen(os.dup(fd),'rb')as f:value=json.load(f)
 finally:os.close(fd)
 if value.get('appId')!=app or value.get('deploymentId')!=deployment or value.get('version')!=1:raise ValueError()
 rows=value.get('records',[])
 if not isinstance(rows,list)or len(rows)>10000:raise ValueError()
 rows=[r for r in rows if isinstance(r,dict)and isinstance(r.get('message'),str)and isinstance(r.get('id'),str)and isinstance(r.get('timestamp'),str)]
 def date(v):return datetime.datetime.fromisoformat(re.sub(r'(\.\d{6})\d+',r'\1',v).replace('Z','+00:00')).timestamp()
 if q.get('from'):rows=[r for r in rows if date(r['timestamp'])>=date(q['from'])]
 if q.get('to'):rows=[r for r in rows if date(r['timestamp'])<=date(q['to'])]
 if q.get('q'):rows=[r for r in rows if q['q'].lower()in r['message'].lower()]
 for name in ['level','stream']:
  if q.get(name)and q[name]!='all':rows=[r for r in rows if r.get(name)==q[name]]
 cursor=q.get('cursor','');generation=value.get('generation','');position=None;reset=False
 if cursor:
  gen,_,ident=cursor.partition(':')
  if gen==generation:position=next((i for i,r in enumerate(rows)if r['id']==ident),None)
  if position is None:reset=True
 direction=q.get('direction','older')
 if reset:direction='older'
 end=position if position is not None and direction=='older'else len(rows);start=position+1 if position is not None and direction=='newer'else 0
 candidates=rows[start:end] if direction=='older'else rows[start:]
 selected=[];size=0
 for row in (reversed(candidates)if direction=='older'else candidates):
  amount=len(json.dumps(row,ensure_ascii=False).encode())
  if len(selected)>=q['limit']or size+amount>65536:break
  selected.append(row);size+=amount
 if direction=='older':selected.reverse()
 first=rows.index(selected[0])if selected else -1;last=rows.index(selected[-1])if selected else -1
 print(json.dumps({**empty,'items':selected,'generation':generation,'checkedAt':value.get('checkedAt',''),'state':'stale'if value.get('checkedAt')and date(value['checkedAt'])<datetime.datetime.now(datetime.timezone.utc).timestamp()-30 else value.get('state','waiting'),'olderCursor':generation+':'+selected[0]['id']if selected else cursor,'newerCursor':generation+':'+selected[-1]['id']if selected else cursor,'hasOlder':first>0,'hasNewer':last>=0 and last<len(rows)-1,'reset':reset,'truncated':bool(value.get('truncated'))},ensure_ascii=False))
except Exception:print(json.dumps(empty))
`
const worker = (e: core.RequestEvent) => {
  if (!webEnabled()) throw new NotFoundError()
  const expected = $os.getenv('WEB_APPS_WORKER_SECRET')
  if (expected.length < 32 || !$security.equal(expected, e.request!.header.get('X-Web-Worker') || ''))
    throw new UnauthorizedError()
  if (!webDirectLoopback(e)) throw new ForbiddenError('Accès local requis.')
}
const secretsFor = (app: core.Record, deployment: core.Record) => {
  const values = [
    decryptWebValue(app.getString('envEncrypted')),
    decryptWebValue(deployment.getString('snapshotEncrypted'))?.env || {},
  ]
  return [
    ...new Set(
      values.flatMap((env) =>
        Object.values(env || {})
          .map((entry: any) => entry?.value)
          .filter((value): value is string => typeof value === 'string' && !!value)
      )
    ),
  ].sort((a, b) => b.length - a.length)
}
export const HandleWebRuntimeLogConfig = (e: core.RequestEvent) => {
  worker(e)
  const input = webInput(e),
    page = Math.max(1, Math.min(10000, Math.floor(Number(input.page) || 1))),
    rows = $app.findRecordsByFilter('web_apps', 'activeDeployment!=""', 'id', 26, (page - 1) * 25)
  const items: any[] = []
  for (const app of rows.slice(0, 25))
    try {
      const deployment = $app.findRecordById('web_deployments', app.getString('activeDeployment')),
        result = JSON.parse(deployment.getString('result') || '{}')
      if (
        deployment.getString('appId') !== app.id ||
        deployment.getString('status') !== 'ready' ||
        result.container !== `ph-web-${app.id}-${deployment.id}`
      )
        continue
      items.push({
        appId: app.id,
        deploymentId: deployment.id,
        containerName: result.container,
        secrets: secretsFor(app, deployment),
      })
    } catch {}
  return webJson(e, { items, page, hasMore: rows.length > 25 })
}
export const HandleWebRuntimeLogs = (e: core.RequestEvent) => {
  const app = requireWebAppPermission(e, 'logs.read').app!,
    depId = app.getString('activeDeployment'),
    query = e.request!.url!.query()
  if (!/^[a-z0-9]{15}$/.test(depId))
    return webJson(e, {
      items: [],
      state: 'unpublished',
      hasOlder: false,
      hasNewer: false,
      olderCursor: '',
      newerCursor: '',
      reset: false,
      truncated: false,
      retention: { hours: 24, maxBytes: 2097152 },
    })
  const deployment = $app.findRecordById('web_deployments', depId)
  if (deployment.getString('appId') !== app.id) throw new NotFoundError()
  const options = {
    q: (query.get('q') || '').trim(),
    level: query.get('level') || 'all',
    stream: query.get('stream') || 'all',
    from: query.get('from') || '',
    to: query.get('to') || '',
    cursor: query.get('cursor') || '',
    direction: query.get('direction') || 'older',
    limit: Math.max(1, Math.min(200, Math.floor(Number(query.get('limit')) || 100))),
  }
  if (
    options.q.length > 100 ||
    !['all', 'debug', 'info', 'warn', 'error', 'unknown'].includes(options.level) ||
    !['all', 'stdout', 'stderr'].includes(options.stream) ||
    !['older', 'newer'].includes(options.direction) ||
    (options.cursor && !/^[a-f0-9]{16}:[a-f0-9]{24}$/.test(options.cursor))
  )
    throw new BadRequestError('Filtres du journal invalides.')
  for (const value of [options.from, options.to])
    if (value && (!Number.isFinite(Date.parse(value)) || value.length > 40))
      throw new BadRequestError('Horodatage invalide.')
  if (options.from && options.to && Date.parse(options.from) > Date.parse(options.to))
    throw new BadRequestError('La fin doit suivre le début.')
  let value: any
  try {
    value = JSON.parse(
      toString(
        $os.cmd('python3', '-c', RUNTIME_LOG_READER, webRoot(), app.id, depId, JSON.stringify(options)).combinedOutput()
      )
    )
  } catch {
    throw new BadRequestError('Le journal d’exécution est momentanément indisponible.')
  }
  const masks = [
    ...new Set(secretsFor(app, deployment).flatMap((secret) => [secret, ...secret.split(/\r?\n/).filter(Boolean)])),
  ].sort((a, b) => b.length - a.length)
  // Match once: short values must not repeatedly expand the masking marker.
  const pattern = masks.length
    ? new RegExp(masks.map((secret) => secret.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|'), 'g')
    : null
  for (const row of value.items || []) if (pattern) row.message = String(row.message || '').replace(pattern, '[masqué]')
  return webJson(e, value)
}
