"""Cloudflare DNS preview/apply. Fixed API origin; no credentials in public output."""
import hashlib,ipaddress,json,pathlib,re,signal,socket,ssl,sys,urllib.request,urllib.parse,urllib.error
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
    identifier(zone)
    result=[]
    for page in range(1,12):
        value=request(token,'/zones/'+zone+'/dns_records?'+urllib.parse.urlencode({'name':name,'per_page':100,'page':page}))
        for row in value['result']:
            if row.get('name')!=name:raise ValueError('La réponse Cloudflare ne correspond pas au nom demandé. Aucun changement supplémentaire effectué.')
            identifier(row.get('id'));result.append(row)
        if page>=value.get('result_info',{}).get('total_pages',1):return result
    raise ValueError('Trop d’enregistrements pour ce nom. Gérez ce cas dans Cloudflare.')
def clean(row):
    return {key:row.get(key) for key in ['id','type','name','content','proxied','ttl','modified_on'] if key in row}
def identifier(value):
    if not isinstance(value,str)or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',value):raise ValueError('Identifiant Cloudflare invalide. Relisez les DNS avant de réessayer.')
    return value
def origin_tls(hostname):
    # Fixed loopback destination, public CA validation and the requested SNI hostname.
    try:
        context=ssl.create_default_context()
        with socket.create_connection(('127.0.0.1',443),timeout=4)as raw:
            with context.wrap_socket(raw,server_hostname=hostname):pass
    except (OSError,ValueError):
        raise ValueError('Le certificat HTTPS de l’origine n’est pas encore valide pour ce domaine. Activez et vérifiez HTTPS avant le proxy Cloudflare.')from None
def current_records(token,zone,hostname):
    return sorted([clean(row)for name in(hostname,'_pockethost.'+hostname)for row in records(token,zone,name)],key=lambda row:(row['name'],row['type'],row['id']))
def public_ownership(hostname,ownership):
    # Reuse the normal web-domain public resolver; no public-A comparison for a proxy.
    from web_app_domains import _dns
    try:
        texts=_dns('_pockethost.'+hostname,'TXT')
        values=[''.join(re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"',value))for value in texts]
    except Exception:raise ValueError('Le TXT public de vérification est indisponible. Attendez sa propagation DNS.')from None
    if ownership not in values:raise ValueError('Le TXT public de vérification est absent ou incorrect. Attendez sa propagation DNS.')
def provider_identity(current,hostname,public_ip,ownership,record_id=None,proxied=None):
    hostrows=[row for row in current if row['name']==hostname]
    proofrows=[row for row in current if row['name']=='_pockethost.'+hostname]
    addresses=[row for row in hostrows if row['type']=='A']
    if any(row['type']in('AAAA','CNAME','NS')for row in hostrows):raise ValueError('Le nom doit contenir une seule adresse A du serveur, sans AAAA, CNAME ni délégation NS.')
    if len(addresses)!=1 or addresses[0].get('content')!=public_ip:raise ValueError('L’enregistrement A Cloudflare ne pointe pas uniquement vers ce serveur. Relisez les DNS.')
    row=addresses[0]
    if type(row.get('proxied'))is not bool or(proxied is not None and row['proxied']is not proxied):raise ValueError('Le mode proxy Cloudflare ne correspond pas au changement demandé. Relisez les DNS.')
    if record_id is not None and row['id']!=record_id:raise ValueError('L’enregistrement A Cloudflare a changé depuis sa configuration. Aucun proxy forcé.')
    proofs=[row for row in proofrows if row['type']=='TXT'and row.get('content','').startswith('pockethost-verification=')]
    if any(row['type']in('CNAME','NS')for row in proofrows)or len(proofs)!=1 or proofs[0].get('content')!=ownership:raise ValueError('Le TXT Cloudflare de vérification est absent ou incorrect. Relisez les DNS.')
    return row
def preview(token,hostname,public_ip,ownership,proxied=False):
    if not HOST.fullmatch(hostname):raise ValueError('Nom de domaine invalide.')
    address=ipaddress.ip_address(public_ip)
    if not address.is_global or address.version!=4:raise ValueError('Adresse IPv4 publique du serveur non configurée.')
    if not re.fullmatch('pockethost-verification=[A-Za-z0-9_-]{24,100}',ownership):raise ValueError('Preuve DNS invalide.')
    if type(proxied)is not bool:raise ValueError('Le choix du proxy Cloudflare doit être un booléen.')
    if proxied:origin_tls(hostname)
    labels=hostname.split('.');zone=None
    # Query exact ancestors, nearest zone first: no suffix guessing and no access to unrelated zones.
    for i in range(len(labels)-1):
        candidate='.'.join(labels[i:]);value=request(token,'/zones?'+urllib.parse.urlencode({'name':candidate,'status':'active','per_page':50}))
        matches=[r for r in value['result'] if r['name']==candidate and r.get('status')=='active']
        if matches:zone=matches[0];break
    if not zone:raise ValueError('Ce domaine ne fait partie d’aucune zone Cloudflare active accessible avec ce jeton.')
    zid=identifier(zone['id']);txtname='_pockethost.'+hostname
    current=current_records(token,zid,hostname)
    if any(r['type']=='NS' for r in current):raise ValueError('Ce nom contient une délégation NS. Gérez cette délégation dans Cloudflare avant de continuer.')
    batch={'deletes':[],'patches':[],'posts':[]};changes=[]
    def delete(r):
        batch['deletes'].append({'id':r['id']});changes.append({'action':'delete','before':r,'after':None})
    def desired(name,kind,content,matching):
        after={'type':kind,'name':name,'content':content,'ttl':1}
        if kind=='A':after['proxied']=proxied
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
    revision=hashlib.sha256(json.dumps({'zone':zid,'current':current,'ip':str(address),'ownership':ownership,'proxied':proxied},sort_keys=True).encode()).hexdigest()
    return {'zone':{'id':zid,'name':zone['name']},'hostname':hostname,'proxied':proxied,'current':current,'changes':changes,'revision':revision,'batch':batch}
def verify(token,hostname,public_ip,ownership,proxied=False,zone_id=None,record_id=None):
    if zone_id is not None:identifier(zone_id)
    if record_id is not None:identifier(record_id)
    plan=preview(token,hostname,public_ip,ownership,proxied)
    if zone_id is not None and zone_id!=plan['zone']['id']:raise ValueError('La zone Cloudflare a changé depuis sa configuration. Aucun proxy forcé.')
    row=provider_identity(plan['current'],hostname,public_ip,ownership,record_id)
    public_ownership(hostname,ownership)
    return {'verified':True,'zoneId':plan['zone']['id'],'recordId':row['id'],'proxied':row['proxied']}
def activate(token,hostname,public_ip,ownership,zone_id,record_id):
    identifier(zone_id);identifier(record_id)
    plan=preview(token,hostname,public_ip,ownership,True)
    if zone_id!=plan['zone']['id']:raise ValueError('La zone Cloudflare a changé depuis sa configuration. Aucun proxy forcé.')
    before=provider_identity(plan['current'],hostname,public_ip,ownership,record_id)
    public_ownership(hostname,ownership)
    fresh=provider_identity(current_records(token,zone_id,hostname),hostname,public_ip,ownership,record_id)
    # Re-read the exact pinned A immediately before the only permitted write.
    latest=request(token,'/zones/'+zone_id+'/dns_records/'+record_id)['result']
    if before!=fresh or clean(latest)!=before:raise ValueError('L’enregistrement A Cloudflare a changé pendant la vérification. Aucun proxy forcé.')
    if not before['proxied']:request(token,'/zones/'+zone_id+'/dns_records/batch',{'patches':[{'id':record_id,'proxied':True}]})
    # Never compensate by rewriting on error: preserve concurrent external changes.
    provider_identity(current_records(token,zone_id,hostname),hostname,public_ip,ownership,record_id,True)
    public_ownership(hostname,ownership);origin_tls(hostname)
    return {'verified':True,'activated':True,'zoneId':zone_id,'recordId':record_id,'proxied':True}
def run(data):
    action=data.get('action')
    if action not in('preview','apply','verify','activate'):raise ValueError('Opération Cloudflare invalide.')
    arguments=(data['token'],data['hostname'],data['publicIp'],data['ownershipValue'])
    if action=='activate':return activate(*arguments,data.get('zoneId'),data.get('recordId'))
    proxied=data.get('proxied',False)
    if action=='verify':return verify(*arguments,proxied,zone_id=data.get('zoneId'),record_id=data.get('recordId'))
    plan=preview(*arguments,proxied)
    if action=='preview':return plan
    if data.get('revision')!=plan['revision']:raise ValueError('Les DNS ont changé depuis l’aperçu. Aucun changement effectué : préparez un nouvel aperçu.')
    if plan['changes']:request(data['token'],'/zones/'+plan['zone']['id']+'/dns_records/batch',plan['batch'])
    # Batch is atomic in Cloudflare's database, but public DNS propagation is asynchronous.
    previous=next((row['id']for row in plan['current']if row['name']==plan['hostname']and row['type']=='A'),None)
    row=provider_identity(current_records(data['token'],plan['zone']['id'],plan['hostname']),plan['hostname'],data['publicIp'],data['ownershipValue'],previous,proxied)
    return {'applied':True,'changes':len(plan['changes']),'hostname':plan['hostname'],'zone':plan['zone']['name'],'zoneId':plan['zone']['id'],'recordId':row['id'],'proxied':row['proxied']}
if __name__=='__main__':
    signal.signal(signal.SIGALRM,lambda *args:(_ for _ in ()).throw(TimeoutError()));signal.alarm(45)
    try:
        source=pathlib.Path(sys.argv[1]);data=json.loads(source.read_text());source.unlink()
        print(json.dumps({'result':run(data)}))
    except Exception as e:
        print(json.dumps({'error':str(e) if isinstance(e,ValueError) else 'Opération Cloudflare interrompue. Relisez les DNS avant de réessayer.'}))
