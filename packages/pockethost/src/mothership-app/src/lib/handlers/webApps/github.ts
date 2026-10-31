/** Fixed GitHub HTTPS transport. No installation tokens or private keys enter browser responses. */
export const GITHUB_READER = String.raw`
import sys,json,pathlib,tempfile,subprocess,time,base64,urllib.request,urllib.error,urllib.parse,os,signal
os.umask(0o077)
p=pathlib.Path(sys.argv[1]);data=json.loads(p.read_text());p.unlink();config=data['config'];action=data['action'];token='';identity=''
signal.signal(signal.SIGALRM,lambda *args:(_ for _ in ()).throw(TimeoutError()));signal.alarm(30)
def request(path,body=None,token=''):
 req=urllib.request.Request('https://api.github.com'+path,headers={'Accept':'application/vnd.github+json','User-Agent':'PocketHost-Web','X-GitHub-Api-Version':'2022-11-28',**({'Authorization':'Bearer '+token}if token else{})},data=None if body is None else json.dumps(body).encode())
 with urllib.request.urlopen(req,timeout=8)as response:return json.loads(response.read(4000000))
page=max(1,min(1000,int(data.get('page')or 1)));per_page=max(1,min(100,int(data.get('perPage')or 50)))
configured=bool(config.get('privateKey')and config.get('githubAppId')and config.get('githubInstallationId'))
def installation_token(repository_id=None):
 body={'permissions':{'contents':'read','metadata':'read'}}
 if action=='pull-request':body['permissions']['pull_requests']='read'
 if repository_id is not None:body['repository_ids']=[int(repository_id)]
 return request('/app/installations/'+str(config['githubInstallationId'])+'/access_tokens',body,identity)['token']
try:
 if configured:
  b64=lambda b:base64.urlsafe_b64encode(b).rstrip(b'=').decode();now=int(time.time())
  part=b64(b'{"alg":"RS256","typ":"JWT"}')+'.'+b64(json.dumps({'iat':now-60,'exp':now+540,'iss':config['githubAppId']}).encode())
  with tempfile.TemporaryDirectory()as folder:
   key=pathlib.Path(folder)/'key.pem';key.write_text(config['privateKey']);key.chmod(0o600)
   signature=subprocess.check_output(['openssl','dgst','-sha256','-sign',str(key)],input=part.encode(),stderr=subprocess.DEVNULL,timeout=3)
  identity=part+'.'+b64(signature)
  if action not in ['health','hook-config']:token=installation_token(data.get('repositoryId')if action=='branches'else None)
 if action=='token':result={'token':token}
 elif action=='health':
  if not configured:result={'configured':False,'ok':False,'error':'Connectez GitHub pour accéder aux dépôts privés et aux déploiements automatiques.'}
  else:
   app=request('/app',token=identity);install=request('/app/installations/'+str(config['githubInstallationId']),token=identity)
   if str(install.get('app_id'))!=str(config['githubAppId']):raise ValueError()
   suspended=bool(install.get('suspended_at'));permissions=install.get('permissions')or{}
   if suspended:repos={'total_count':0}
   else:
    token=installation_token();repos=request('/installation/repositories?per_page=1',token=token)
   count=int(repos.get('total_count',0))
   login=(install.get('account')or{}).get('login','');kind=(install.get('account')or{}).get('type','')
   manage='https://github.com/'+('organizations/'+urllib.parse.quote(login,safe='')+'/settings/installations/'if kind=='Organization'else'settings/installations/')+str(config['githubInstallationId'])
   result={'configured':True,'ok':not suspended and count>0,'app':{'id':str(app['id']),'slug':app.get('slug',''),'owner':(app.get('owner')or{}).get('login','')},'installation':{'id':str(install['id']),'account':login,'repositorySelection':install.get('repository_selection','selected')},'permissions':{k:permissions.get(k,'none')for k in ['contents','metadata','pull_requests']},'repositoryCount':count,'manageUrl':manage,'previewSupported':permissions.get('pull_requests')=='read' and 'pull_request'in install.get('events',[])}
   if suspended:result['error']='L’installation GitHub est suspendue. Réactivez-la sur GitHub.'
   elif count==0:result['error']='Aucun dépôt autorisé. Choisissez les dépôts dans les paramètres de l’installation GitHub.'
 elif action=='repositories':
  search=str(data.get('search')or'').strip().lower()[:100];truncated=False;total=0
  if not configured:rows=[];total=0;more=False
  elif search:
   rows=[]
   for current in range(1,11):
    response=request('/installation/repositories?per_page=100&page='+str(current),token=token);total=int(response.get('total_count',0));batch=response.get('repositories',[]);rows.extend(batch)
    if len(rows)>=total or len(batch)<100:break
   truncated=len(rows)<total;rows=[r for r in rows if search in r['full_name'].lower()];total=len(rows);more=page*per_page<total;rows=rows[(page-1)*per_page:page*per_page]
  else:
   response=request('/installation/repositories?per_page='+str(per_page)+'&page='+str(page),token=token);rows=response.get('repositories',[]);total=int(response.get('total_count',0));more=page*per_page<total
  result={'items':[{'id':r['id'],'fullName':r['full_name'],'defaultBranch':r['default_branch'],'private':r['private']}for r in rows],'configured':configured,'page':page,'perPage':per_page,'totalItems':total,'hasMore':more,'truncated':truncated,'searchScope':'authorized-repositories'}
 elif action=='branches':
  if not configured or not data.get('repositoryId'):raise ValueError()
  # Creating a repository-scoped token above proves membership in this installation.
  repository=request('/repositories/'+str(int(data['repositoryId'])),token=token)['full_name']
  rows=request('/repos/'+repository+'/branches?per_page='+str(per_page)+'&page='+str(page),token=token)
  result={'items':[{'name':r['name']}for r in rows],'page':page,'perPage':per_page,'hasMore':len(rows)==per_page,'repository':repository}
 elif action=='pull-request':
  if not configured:raise ValueError()
  number=int(data.get('number',0))
  if number<1 or number>100000000:raise ValueError()
  r=request('/repos/'+data['repository']+'/pulls/'+str(number),token=token)
  result={k:r.get(k)for k in ['number','state','draft','author_association','head','base','merged']}
 elif action=='hook-config':
  # JWT de l'App seulement ; seule l'URL est renvoyée (jamais le secret, même masqué).
  if not configured:raise ValueError()
  hook=request('/app/hook/config',token=identity);result={'url':str(hook.get('url')or'')[:500]}
 elif action=='commit':
  r=request('/repos/'+data['repository']+'/commits/'+urllib.parse.quote(data['branch'],safe=''),token=token);result={'sha':r['sha']}
 else:raise ValueError()
 print(json.dumps(result))
except Exception as error:
 status=getattr(error,'code',0)
 message='GitHub refuse l’accès au dépôt ou à la branche. Vérifiez l’installation et ses autorisations.'if status in [401,403,404]else'GitHub est indisponible ou sa configuration est invalide.'
 if status==429:message='La limite de requêtes GitHub est atteinte. Réessayez plus tard.'
 if isinstance(error,(TimeoutError,subprocess.TimeoutExpired)):message='Le contrôle GitHub a dépassé le délai prévu. Réessayez.'
 print(json.dumps({'error':message}))
finally:signal.alarm(0)
`
