import { canManageInstance } from '../../../security/instanceAccess'
/** Bounded metadata/log inspection; EXPLAIN only on the real DB, candidate indexes only in memory. */
export const DIAGNOSTIC_READER = String.raw`
import sys,json,sqlite3,pathlib,datetime,math,re,urllib.parse,time,hashlib,collections
root,ident,hours,threshold,selected=sys.argv[1:]
hours=int(hours);threshold=int(threshold);limit=50000;deadline=time.monotonic()+10
base=(pathlib.Path(root)/'instances').resolve();home=base/ident;folder=home/'pb_data'
if home.resolve()!=home or folder.resolve()!=folder:raise ValueError('Invalid location')
def safe_file(path):
    if path.exists() and path.resolve()!=path:raise ValueError('Invalid file')
    return path if path.is_file() else None
def connect(path):
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)
    db.execute('PRAGMA query_only=ON');db.set_progress_handler(lambda: int(time.monotonic()>deadline),10000)
    return db
def quote(name):return '"'+name.replace('"','""')+'"'
def stamp(value):
    try:
        parsed=datetime.datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.timestamp()
    except (ValueError,TypeError):return 0
atom=re.compile(r'''([A-Za-z_][A-Za-z0-9_]*)\s*(>=|<=|=|>|<)\s*(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true\b|false\b)''')
def terms(text):
    if len(text)>4096:return None
    if not text.strip():return []
    pos=0;found=[]
    def spaces():
        nonlocal pos
        while pos<len(text) and text[pos].isspace():pos+=1
    def group(depth=0):
        nonlocal pos
        if depth>4:return False
        spaces()
        if pos<len(text) and text[pos]=='(':
            pos+=1
            if not group(depth+1):return False
            spaces()
            if pos>=len(text) or text[pos]!=')':return False
            pos+=1
        else:
            match=atom.match(text,pos)
            if not match:return False
            found.append((match[1],match[2]));pos=match.end()
            if len(found)>12:return False
        spaces()
        while text[pos:pos+2]=='&&':
            pos+=2
            if not group(depth+1):return False
            spaces()
        return True
    return found if group() and pos==len(text) else None
result={'available':False,'logsAvailable':False,'truncated':False,'budgetLimited':False,'sampleLimit':limit,'sampledRows':0,'hours':hours,'thresholdMs':threshold,'collections':[],'patterns':[],'patternCount':0,'planLimit':20,'warnings':[],'sqliteVersion':sqlite3.sqlite_version}
path=safe_file(folder/'data.db')
if not path:
    print(json.dumps(result));sys.exit()
db=connect(path)
try:
    schema_cols={r[1] for r in db.execute('PRAGMA table_info(_collections)')}
    field_col='fields' if 'fields' in schema_cols else 'schema'
    metadata=db.execute('SELECT id,name,type,'+quote(field_col)+' FROM _collections'+(' WHERE name=? OR id=?' if selected else '')+' ORDER BY name LIMIT 301', (selected,selected) if selected else ()).fetchall()
except sqlite3.Error:
    print(json.dumps(result));sys.exit()
result['available']=True
if len(metadata)>300:result['warnings'].append('Inventaire limité aux 300 premières collections.')
schemas={};aliases={};inventory={}
for cid,name,kind,raw_fields in metadata[:300]:
    if time.monotonic()>deadline:result['budgetLimited']=True;break
    if name.startswith('_') or name=='ph_two_factor':continue
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',name):continue
    aliases[cid]=name;aliases[name]=name
    try:fields=json.loads(raw_fields or '[]')
    except (ValueError,TypeError):fields=[]
    try:columns=list(db.execute('PRAGMA table_info('+quote(name)+')'))
    except sqlite3.Error:continue
    if not columns:continue
    field_types={f.get('name'):f.get('type') for f in fields if isinstance(f,dict)}
    indexes=[]
    for row in db.execute('PRAGMA index_list('+quote(name)+')'):
        info=list(db.execute('PRAGMA index_xinfo('+quote(row[1])+')'))
        parts=[{'name':x[2] or '(expression)','descending':bool(x[3]),'collation':x[4]} for x in info if len(x)>5 and x[5]]
        indexes.append({'name':row[1],'unique':bool(row[2]),'partial':bool(row[4]),'columns':parts})
    data={'name':name,'type':kind,'fields':[{'name':c[1],'type':field_types.get(c[1],c[2])} for c in columns],'indexes':indexes,'calls':0,'slow':0,'maxMs':0,'usage':{'withoutFields':0,'withTotal':0,'largePages':0,'expanded':0,'writes':0,'peakWritesPerSecond':0,'repeatedWithinSecond':0},'hookFiles':[]}
    inventory[name]=data;schemas[name]={'columns':columns,'types':field_types,'multi':{f.get('name') for f in fields if isinstance(f,dict) and f.get('type') in ['relation','select'] and int(f.get('maxSelect',(f.get('options') or {}).get('maxSelect',1)) or 0)!=1}}
selected_name=aliases.get(selected) if selected else None
if selected and not selected_name:
    result['warnings'].append('Collection absente de cet inventaire.');db.close();print(json.dumps(result));sys.exit()
patterns={};repeats=collections.Counter();writes=collections.Counter()
logpath=safe_file(folder/'auxiliary.db') or safe_file(folder/'logs.db')
if logpath:
    try:
        with connect(logpath) as logs:
            rows=logs.execute("SELECT created,json_extract(data,'$.type'),json_extract(data,'$.method'),json_extract(data,'$.url'),json_extract(data,'$.execTime'),json_extract(data,'$.status') FROM (SELECT created,data FROM _logs ORDER BY rowid DESC LIMIT ?)",(limit+1,)).fetchall()
        result['logsAvailable']=True;since=time.time()-hours*3600
        result['truncated']=len(rows)>limit and stamp(rows[-1][0])>=since
        for number,(created,category,method,url,duration,status) in enumerate(rows[:limit]):
            if number%256==0 and time.monotonic()>deadline:result['budgetLimited']=True;break
            result['sampledRows']+=1
            if category!='request' or stamp(created)<since or not isinstance(duration,(int,float)) or not math.isfinite(duration) or duration<0:continue
            try:
                parsed=urllib.parse.urlsplit(str(url or ''));match=re.fullmatch(r'/api/collections/([A-Za-z0-9_]+)/records(?:/[^/]+)?/?',parsed.path)
                if not match:continue
                name=aliases.get(match[1])
                if not name or name not in inventory or (selected_name and name!=selected_name):continue
                item=inventory[name];item['calls']+=1;item['slow']+=duration>=threshold;item['maxMs']=round(max(item['maxMs'],duration),2)
                second=int(stamp(created))
                if method in ['POST','PATCH','PUT','DELETE']:writes[(name,second)]+=1;item['usage']['writes']+=1
                if method!='GET':continue
                fingerprint=hashlib.sha256(str(url).encode()).hexdigest()
                repeats[(name,second,fingerprint)]+=1
                if not re.fullmatch(r'/api/collections/[A-Za-z0-9_]+/records/?',parsed.path):continue
                if len(str(url))>8192:continue
                query=urllib.parse.parse_qs(parsed.query,keep_blank_values=True,max_num_fields=30)
                item['usage']['withoutFields']+=not bool(query.get('fields',[''])[0])
                item['usage']['withTotal']+=query.get('skipTotal',['false'])[0].lower() not in ['true','1']
                item['usage']['expanded']+=bool(query.get('expand',[''])[0])
                per_page=query.get('perPage',['30'])[0]
                item['usage']['largePages']+=per_page.isdigit() and int(per_page)>200
                if duration<threshold or not isinstance(status,int) or not 200<=status<400:continue
                where=terms(query.get('filter',[''])[0]);sort_text=query.get('sort',[''])[0]
                orders=[];valid_sort=True
                for part in sort_text.split(',') if sort_text else []:
                    part=part.strip();field=part.lstrip('+-')
                    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',field) or len(orders)>=3:valid_sort=False;break
                    orders.append((field,'DESC' if part.startswith('-') else 'ASC'))
                supported=where is not None and valid_sort
                key=(name,tuple(where or []),tuple(orders) if valid_sort else (),supported)
                if key not in patterns and len(patterns)>=500:
                    if 'Profils distincts limités à 500.' not in result['warnings']:result['warnings'].append('Profils distincts limités à 500.')
                    continue
                pattern=patterns.setdefault(key,{'collection':name,'where':where or [],'orders':orders if valid_sort else [],'supported':supported,'count':0,'sumMs':0,'maxMs':0})
                pattern['count']+=1;pattern['sumMs']+=duration;pattern['maxMs']=max(pattern['maxMs'],duration)
            except (ValueError,TypeError):continue
    except sqlite3.Error:result['warnings'].append('Journaux indisponibles ou délai de lecture dépassé.')
for (name,second),count in writes.items():inventory[name]['usage']['peakWritesPerSecond']=max(inventory[name]['usage']['peakWritesPerSecond'],count)
for (name,second,fingerprint),count in repeats.items():inventory[name]['usage']['repeatedWithinSecond']+=max(0,count-1)
# Source file names only. Literal references are hints, not proof of execution. Platform files are excluded.
hooks=home/'pb_hooks'
if hooks.is_dir() and hooks.resolve()==hooks:
    for file in list(hooks.glob('*.js'))[:100]:
        if file.name.startswith('_ph_') or file.resolve()!=file or file.stat().st_size>262144:continue
        try:source=file.read_text(errors='replace')
        except OSError:continue
        for name,item in inventory.items():
            if selected_name and selected_name!=name:continue
            if ('"'+name+'"') in source or ("'"+name+"'") in source:item['hookFiles'].append(file.name)
def score(plan):return sum(2 for x in plan if x.startswith('SCAN ') and 'USING' not in x)+sum(1 for x in plan if 'TEMP B-TREE' in x)
def explain(connection,sql,bindings):return [str(row[3]) for row in connection.execute('EXPLAIN QUERY PLAN '+sql,bindings)]
ordered=sorted(patterns.values(),key=lambda p:(-p['count'],-p['maxMs'],p['collection']))
result['patternCount']=len(ordered)
for p in ordered[:20]:
    if time.monotonic()>deadline:result['budgetLimited']=True;break
    name=p['collection'];definition=schemas[name];columns={c[1]:c for c in definition['columns']}
    entry={'collection':name,'count':p['count'],'averageMs':round(p['sumMs']/p['count'],2),'maxMs':round(p['maxMs'],2),'filterFields':[{'name':field,'operator':op} for field,op in p['where']],'sortFields':[{'name':field,'direction':direction} for field,direction in p['orders']],'supported':p['supported'],'beforePlan':[],'afterPlan':[],'sql':'','proposal':None,'note':'','codeSearch':'rg -n '+json.dumps(name)+' app lib src'}
    used=[f for f,_ in p['where']]+[f for f,_ in p['orders']]
    unsupported=not p['supported'] or any(f not in columns or f in definition['multi'] or definition['types'].get(f) in ['json','file','geoPoint'] or f in ['password','tokenKey'] for f in used)
    if inventory[name]['type']=='view':unsupported=True
    if unsupported:
        entry['supported']=False;entry['note']='Filtre complexe, relation, expression, vue ou type non pris en charge : examen manuel nécessaire.';result['patterns'].append(entry);continue
    if not used:
        entry['note']='Aucun filtre ou tri simple observé. Aucun index proposé sur cette seule base.';result['patterns'].append(entry);continue
    sql='SELECT '+quote('id' if 'id' in columns else next(iter(columns)))+' FROM '+quote(name)
    bindings=[]
    if p['where']:
        sql+=' WHERE '+' AND '.join(quote(field)+' '+op+' ?' for field,op in p['where'])
        bindings=[0 if columns[field][2].upper() in ['NUMERIC','INTEGER','REAL','NUMBER','INT','FLOAT'] else '' for field,op in p['where']]
    if p['orders']:sql+=' ORDER BY '+', '.join(quote(field)+' '+direction for field,direction in p['orders'])
    sql+=' LIMIT 50';entry['sql']=sql
    try:entry['beforePlan']=explain(db,sql,bindings)
    except sqlite3.Error:
        entry['note']='Plan indisponible avec le moteur SQLite de diagnostic. Aucun index proposé.';result['patterns'].append(entry);continue
    candidates=[]
    for field,op in p['where']:
        if op=='=' and field not in [f for f,_ in candidates]:candidates.append((field,'ASC'))
    for field,op in p['where']:
        if op!='=' and field not in [f for f,_ in candidates]:candidates.append((field,'ASC'))
    for field,direction in p['orders']:
        if field not in [f for f,_ in candidates]:candidates.append((field,direction))
    if len(candidates)>4:
        entry['note']='Plus de quatre colonnes impliquées : choix et ordre des index à examiner manuellement.';result['patterns'].append(entry);continue
    names=[f for f,_ in candidates]
    covered=any(not idx['partial'] and [c['name'] for c in idx['columns']][:len(names)]==names for idx in inventory[name]['indexes'])
    if covered:
        entry['note']='Un index existant commence déjà par ces colonnes. Examiner le plan, les règles API, les volumes et les hooks avant tout ajout.';result['patterns'].append(entry);continue
    memory=sqlite3.connect(':memory:')
    try:
        specs=[]
        for c in definition['columns']:
            affinity='REAL' if c[2].upper() in ['NUMERIC','INTEGER','REAL','NUMBER','INT','FLOAT'] else 'TEXT'
            specs.append(quote(c[1])+' '+affinity+(' PRIMARY KEY' if c[1]=='id' else ''))
        memory.execute('CREATE TABLE '+quote(name)+' ('+', '.join(specs)+')')
        for index in inventory[name]['indexes']:
            try:
                original=db.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?",(index['name'],)).fetchone()
                if original and original[0] and re.match(r'^CREATE\s+(?:UNIQUE\s+)?INDEX\b',original[0],re.I):memory.execute(original[0])
                elif all(c['name'] in columns for c in index['columns']):
                    memory.execute('CREATE '+('UNIQUE ' if index['unique'] else '')+'INDEX '+quote('copy_'+hashlib.sha256(index['name'].encode()).hexdigest()[:12])+' ON '+quote(name)+' ('+', '.join(quote(c['name']) for c in index['columns'])+')')
            except sqlite3.Error:pass
        idx_name='ph_diag_'+hashlib.sha256((name+repr(candidates)).encode()).hexdigest()[:12]
        ddl='CREATE INDEX '+quote(idx_name)+' ON '+quote(name)+' ('+', '.join(quote(f)+' '+d for f,d in candidates)+');'
        memory.execute(ddl);entry['afterPlan']=explain(memory,sql,bindings)
        if score(entry['afterPlan'])<score(entry['beforePlan']):
            entry['proposal']={'name':idx_name,'columns':[{'name':f,'direction':d} for f,d in candidates],'sql':ddl}
            entry['note']='Piste à tester : le plan simplifié pourrait réduire un parcours complet ou un tri temporaire. Aucun gain de temps mesuré.'
        else:entry['note']='Aucune amélioration de structure du plan démontrée par cette projection. Aucun index proposé.'
    except sqlite3.Error:entry['note']='Projection indisponible. Aucun index proposé.'
    finally:memory.close()
    result['patterns'].append(entry)
result['collections']=sorted([v for k,v in inventory.items() if not selected_name or k==selected_name],key=lambda x:(-x['slow'],-x['calls'],x['name']))
result['selectedCollection']=selected_name or ''
db.close();print(json.dumps(result))
`

export const HandleInstanceDiagnostics = (e: core.RequestEvent) => {
  const id = e.request!.pathValue('id')
  if (!/^[a-z0-9]{15}$/.test(id)) throw new BadRequestError('Instance introuvable.')
  const instance = $app.findRecordById('instances', id)
  if (!e.auth || e.auth.collection().name !== 'users' || !canManageInstance(instance, e.auth))
    throw new ForbiddenError('Non autorisé.')
  const query = e.request!.url!.query()
  const hours = Number(query.get('hours') || 24)
  const threshold = Number(query.get('thresholdMs') || 500)
  const collection = query.get('collection') || ''
  if (
    ![1, 24, 168].includes(hours) ||
    ![100, 500, 1000, 3000].includes(threshold) ||
    (collection && !/^[A-Za-z0-9_]{1,100}$/.test(collection))
  )
    throw new BadRequestError('Filtres invalides.')
  const key = `ph_diagnostics_${id}`
  const raw = $app.store().get(key)
  const cached = raw ? JSON.parse(`${raw}`) : null
  e.response.header().set('Cache-Control', 'no-store')
  if (
    cached &&
    cached.hours === hours &&
    cached.threshold === threshold &&
    cached.collection === collection &&
    cached.until > Date.now()
  )
    return e.json(200, cached.value)
  const root = $os.getenv('DATA_ROOT') || `${$app.dataDir()}`.replace(/\/mothership\/pb_data\/?$/, '')
  let value
  try {
    value = JSON.parse(
      toString(
        $os.cmd('python3', '-c', DIAGNOSTIC_READER, root, id, `${hours}`, `${threshold}`, collection).combinedOutput()
      )
    )
  } catch {
    throw new BadRequestError('Diagnostic indisponible pour le moment. Réessayez dans quelques instants.')
  }
  value.analyzedAt = new Date().toISOString()
  $app.store().set(key, JSON.stringify({ hours, threshold, collection, until: Date.now() + 60000, value }))
  return e.json(200, value)
}
