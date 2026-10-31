import { paginatePerformance } from '$common/performancePagination'
import { canManageInstance } from '../../../security/instanceAccess'
// Read only the newest log rows. Never open the business database or expose raw URLs/query parameters.
export const PERFORMANCE_READER = String.raw`
import sys,json,sqlite3,pathlib,datetime,math,re,urllib.parse
root,ident,hours,threshold=sys.argv[1:]
hours=int(hours);threshold=int(threshold);limit=50000
base=(pathlib.Path(root)/'instances').resolve()
instance=base/ident
folder=instance/'pb_data'
if instance.resolve()!=instance or folder.resolve()!=folder:raise ValueError('Invalid log location')
path=next((folder/name for name in ['auxiliary.db','logs.db'] if (folder/name).is_file()),None)
if path and path.resolve()!=path:raise ValueError('Invalid log file')
result={'available':False,'truncated':False,'sampleLimit':limit,'hours':hours,'thresholdMs':threshold,'total':0,'slow':0,'errors':0,'averageMs':0,'p95Ms':0,'maxMs':0,'groups':[],'slowest':[],'oldestAt':None,'newestAt':None}
def route(raw):
    try:path=urllib.parse.urlsplit(raw).path
    except ValueError:return '/api/autres',''
    parts=path.strip('/').split('/')
    if len(parts)>=3 and parts[:2]==['api','collections']:
        collection=parts[2] if re.fullmatch(r'[A-Za-z0-9_]{1,100}',parts[2]) else ':collection'
        action=parts[3] if len(parts)>3 and parts[3] in ['records','auth-with-password','auth-refresh','auth-with-oauth2','auth-with-otp','request-otp','request-password-reset','confirm-password-reset','impersonate'] else 'autres'
        suffix='/:id' if len(parts)>4 else ''
        return '/api/collections/'+collection+'/'+action+suffix,collection
    if path.startswith('/api/files/'):return '/api/files/:collection/:record/:fichier',''
    return (path if path in ['/api/health','/api/realtime','/api/batch','/api/collections','/api/settings'] else '/api/autres'),''
def percentile(values):return round(sorted(values)[max(0,math.ceil(.95*len(values))-1)],2) if values else 0
def timestamp(value):
    try:return datetime.datetime.fromisoformat(value.replace('Z','+00:00')).replace(tzinfo=datetime.timezone.utc).timestamp()
    except (ValueError,TypeError):return 0
if path:
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2) as db:
        db.execute('PRAGMA query_only=ON')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if '_logs' in tables:
            result['available']=True
            db.set_progress_handler(lambda: 1 if __import__('time').monotonic()>deadline else 0,10000)
            deadline=__import__('time').monotonic()+5
            rows=db.execute('SELECT created,json_extract(data,\'$.type\'),json_extract(data,\'$.method\'),json_extract(data,\'$.url\'),json_extract(data,\'$.execTime\'),json_extract(data,\'$.status\') FROM (SELECT created,data FROM _logs ORDER BY rowid DESC LIMIT ?)',(limit+1,)).fetchall()
            since=datetime.datetime.now(datetime.timezone.utc).timestamp()-hours*3600
            result['truncated']=len(rows)>limit and timestamp(rows[-1][0])>=since
            groups={};durations=[];slowest=[]
            for created,kind,method,url,duration,status in rows[:limit]:
                if kind!='request' or timestamp(created)<since or not isinstance(duration,(int,float)) or not math.isfinite(duration) or duration<0:continue
                endpoint,collection=route(str(url or ''))
                method=method if method in ['GET','POST','PATCH','PUT','DELETE','OPTIONS','HEAD'] else 'HTTP'
                status=status if isinstance(status,int) and 100<=status<=599 else 0
                key=method+' '+endpoint
                group=groups.setdefault(key,{'method':method,'endpoint':endpoint,'collection':collection,'count':0,'slowCount':0,'errors':0,'durations':[]})
                group['count']+=1;group['slowCount']+=duration>=threshold;group['errors']+=status>=400;group['durations'].append(duration)
                durations.append(duration);result['slow']+=duration>=threshold;result['errors']+=status>=400
                if not result['oldestAt'] or timestamp(created)<timestamp(result['oldestAt']):result['oldestAt']=created
                if not result['newestAt'] or timestamp(created)>timestamp(result['newestAt']):result['newestAt']=created
                if duration>=threshold:slowest.append({'created':created,'method':method,'endpoint':endpoint,'status':status,'durationMs':round(duration,2)})
            for group in groups.values():
                values=group.pop('durations');group.update(averageMs=round(sum(values)/len(values),2),p95Ms=percentile(values),maxMs=round(max(values),2))
            result.update(total=len(durations),averageMs=round(sum(durations)/len(durations),2) if durations else 0,p95Ms=percentile(durations),maxMs=round(max(durations),2) if durations else 0,groups=sorted(groups.values(),key=lambda g:g['maxMs'],reverse=True),slowest=sorted(slowest,key=lambda r:r['durationMs'],reverse=True))
print(json.dumps(result))
`

export const HandleInstancePerformance = (e: core.RequestEvent) => {
  const id = e.request!.pathValue('id')
  if (!/^[a-z0-9]{15}$/.test(id)) throw new BadRequestError('Instance introuvable.')
  const instance = $app.findRecordById('instances', id)
  if (!e.auth || e.auth.collection().name !== 'users' || !canManageInstance(instance, e.auth)) {
    throw new ForbiddenError('Non autorisé.')
  }
  const query = e.request!.url!.query()
  const hours = Number(query.get('hours') || 24)
  const threshold = Number(query.get('thresholdMs') || 500)
  if (![1, 24, 168].includes(hours) || ![100, 500, 1000, 3000].includes(threshold))
    throw new BadRequestError('Filtres invalides.')
  const slowPage = Number(query.get('slowPage') || 1)
  const groupPage = Number(query.get('groupPage') || 1)
  const sort = query.get('sort') || 'maxMs'
  if (
    ![slowPage, groupPage].every((page) => Number.isSafeInteger(page) && page >= 1 && page <= 100000) ||
    !['maxMs', 'p95Ms', 'count'].includes(sort)
  )
    throw new BadRequestError('Pagination invalide.')
  const snapshot = query.get('snapshot') || ''
  const cacheKey = `ph_performance_v2_${id}`
  const raw = $app.store().get(cacheKey)
  const cached = raw ? JSON.parse(`${raw}`) : null
  const matches = cached && cached.hours === hours && cached.threshold === threshold
  e.response.header().set('Cache-Control', 'no-store')
  if (snapshot) {
    if (!matches || cached.value.snapshot !== snapshot || cached.expires <= Date.now())
      throw new ApiError(409, 'L’analyse a expiré ou a été actualisée. Relancez-la pour parcourir ses résultats.')
    return e.json(200, paginatePerformance(cached.value, slowPage, groupPage, sort))
  }
  if (matches && cached.until > Date.now())
    return e.json(200, paginatePerformance(cached.value, slowPage, groupPage, sort))
  const root = $os.getenv('DATA_ROOT') || `${$app.dataDir()}`.replace(/\/mothership\/pb_data\/?$/, '')
  let value
  try {
    value = JSON.parse(
      toString($os.cmd('python3', '-c', PERFORMANCE_READER, root, id, `${hours}`, `${threshold}`).combinedOutput())
    )
  } catch {
    throw new BadRequestError('Analyse indisponible pour le moment. Réessayez dans quelques instants.')
  }
  value.analyzedAt = new Date().toISOString()
  value.snapshot = $security.randomString(20)
  $app
    .store()
    .set(cacheKey, JSON.stringify({ hours, threshold, until: Date.now() + 15000, expires: Date.now() + 600000, value }))
  return e.json(200, paginatePerformance(value, slowPage, groupPage, sort))
}
