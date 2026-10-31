"""Cloudflare DNS preview/apply. Fixed API origin; no credentials in public output."""
import hashlib,ipaddress,json,pathlib,re,signal,sys,urllib.request,urllib.parse,urllib.error
API='https://api.cloudflare.com/client/v4'
HOST=re.compile(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?\Z')
def request(token,path,body=None):
    req=urllib.request.Request(API+path,data=None if body is None else json.dumps(body).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','User-Agent':'Kolab-Hosting-DNS/1'})
    try:
        with urllib.request.urlopen(req,timeout=10) as r:value=json.loads(r.read(4000000))
    except urllib.error.HTTPError as e:
        raise ValueError('Cloudflare refuse cet accès. Vérifiez le jeton et ses droits Zone:Read et DNS:Edit sur cette zone.') from None
    except Exception:
        raise ValueError('Cloudflare ne répond pas. Relisez les DNS avant de réessayer ; une demande envoyée peut avoir été appliquée.') from None
    if not value.get('success'):raise ValueError('Cloudflare a refusé cette opération DNS. Vérifiez les droits et les conflits d’enregistrements.')
    return value

def records(token,zone,name):
    result=[]
    for page in range(1,12):
        value=request(token,'/zones/'+zone+'/dns_records?'+urllib.parse.urlencode({'name':name,'per_page':100,'page':page}))
        result.extend(value['result'])
        if page>=value.get('result_info',{}).get('total_pages',1):return result
    raise ValueError('Trop d’enregistrements pour ce nom. Gérez ce cas dans Cloudflare.')
def clean(row):
    return {key:row.get(key) for key in ['id','type','name','content','proxied','ttl','modified_on'] if key in row}
def preview(token,hostname,public_ip,ownership):
    if not HOST.fullmatch(hostname):raise ValueError('Nom de domaine invalide.')
    address=ipaddress.ip_address(public_ip)
    if not address.is_global or address.version!=4:raise ValueError('Adresse IPv4 publique du serveur non configurée.')
    if not re.fullmatch('pockethost-verification=[A-Za-z0-9_-]{24,100}',ownership):raise ValueError('Preuve DNS invalide.')
    labels=hostname.split('.');zone=None
    # Query exact ancestors, nearest zone first: no suffix guessing and no access to unrelated zones.
    for i in range(len(labels)-1):
        candidate='.'.join(labels[i:]);value=request(token,'/zones?'+urllib.parse.urlencode({'name':candidate,'status':'active','per_page':50}))
        matches=[r for r in value['result'] if r['name']==candidate and r.get('status')=='active']
        if matches:zone=matches[0];break
    if not zone:raise ValueError('Ce domaine ne fait partie d’aucune zone Cloudflare active accessible avec ce jeton.')
    zid=zone['id'];txtname='_pockethost.'+hostname
    current=sorted([clean(r) for n in [hostname,txtname] for r in records(token,zid,n)],key=lambda r:(r['name'],r['type'],r['id']))
    if any(r['type']=='NS' for r in current):raise ValueError('Ce nom contient une délégation NS. Gérez cette délégation dans Cloudflare avant de continuer.')
    batch={'deletes':[],'patches':[],'posts':[]};changes=[]
    def delete(r):
        batch['deletes'].append({'id':r['id']});changes.append({'action':'delete','before':r,'after':None})
    def desired(name,kind,content,matching):
        after={'type':kind,'name':name,'content':content,'ttl':1}
        if kind=='A':after['proxied']=False
        if matching:
            first=matching[0]
            if any(first.get(k)!=v for k,v in after.items()):
                batch['patches'].append({'id':first['id'],**after});changes.append({'action':'update','before':first,'after':after})
            for other in matching[1:]:delete(other)
        else:
            batch['posts'].append(after);changes.append({'action':'create','before':None,'after':after})
    hostrows=[r for r in current if r['name']==hostname];txtrows=[r for r in current if r['name']==txtname]
    for row in hostrows:
        if row['type'] in ['AAAA','CNAME']:delete(row)
    for row in txtrows:
        if row['type']=='CNAME':delete(row)
    desired(hostname,'A',str(address),[r for r in hostrows if r['type']=='A'])
    desired(txtname,'TXT',ownership,[r for r in txtrows if r['type']=='TXT' and r['content'].startswith('pockethost-verification=')])
    revision=hashlib.sha256(json.dumps({'zone':zid,'current':current,'ip':str(address),'ownership':ownership},sort_keys=True).encode()).hexdigest()
    return {'zone':{'id':zid,'name':zone['name']},'hostname':hostname,'current':current,'changes':changes,'revision':revision,'batch':batch}
def run(data):
    plan=preview(data['token'],data['hostname'],data['publicIp'],data['ownershipValue'])
    if data['action']=='preview':return plan
    if data['action']!='apply':raise ValueError('Opération Cloudflare invalide.')
    if data.get('revision')!=plan['revision']:raise ValueError('Les DNS ont changé depuis l’aperçu. Aucun changement effectué : préparez un nouvel aperçu.')
    if plan['changes']:request(data['token'],'/zones/'+plan['zone']['id']+'/dns_records/batch',plan['batch'])
    # Batch is atomic in Cloudflare's database, but public DNS propagation is asynchronous.
    return {'applied':True,'changes':len(plan['changes']),'hostname':plan['hostname'],'zone':plan['zone']['name']}
if __name__=='__main__':
    signal.signal(signal.SIGALRM,lambda *args:(_ for _ in ()).throw(TimeoutError()));signal.alarm(45)
    try:
        source=pathlib.Path(sys.argv[1]);data=json.loads(source.read_text());source.unlink()
        print(json.dumps({'result':run(data)}))
    except Exception as e:
        print(json.dumps({'error':str(e) if isinstance(e,ValueError) else 'Opération Cloudflare interrompue. Relisez les DNS avant de réessayer.'}))
