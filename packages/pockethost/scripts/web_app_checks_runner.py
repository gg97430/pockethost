#!/usr/bin/env python3
"""Bounded same-origin HTTP reads. Credentials arrive on stdin, never argv/logs."""
import datetime,http.client,http.cookies,json,pathlib,re,socket,ssl,sys,time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))

MAX_BODY=256*1024
AUTH=re.compile(r'^/(?:api/auth/login|api/client-space/auth/login|api/login|login|api/collections/[_a-zA-Z][_a-zA-Z0-9]{0,99}/auth-with-password)$')
FIELD=re.compile(r'[a-zA-Z_][a-zA-Z_0-9]*(?:\.[a-zA-Z_][a-zA-Z_0-9]*){0,3}\Z')
MISSING=object()


def valid_field(value):
    return isinstance(value,str)and bool(FIELD.fullmatch(value))and not any(x in ('__proto__','prototype','constructor')for x in value.split('.'))


def safe_path(value):
    return isinstance(value,str)and 1<=len(value)<=1000 and value.startswith('/')and not value.startswith('//')and not re.search(r'[\x00-\x1f\x7f\\#]',value)


def resolve(host):
    from web_app_health import valid_public_host
    from web_app_notifications import public_addresses,NotificationError
    if not valid_public_host(host):raise ValueError('Origin refused')
    try:rows=public_addresses(host)
    except NotificationError:raise ValueError('Public origin unavailable')from None
    addresses={row[3][0]for row in rows}
    if not addresses:raise ValueError('Public origin unavailable')
    return sorted(addresses,key=lambda value:(':'in value,value))[0]


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self,host,address):
        super().__init__(host,443,timeout=3,context=ssl.create_default_context());self.address=address
    def connect(self):
        self.sock=self._context.wrap_socket(socket.create_connection((self.address,443),3),server_hostname=self.host)


def select(value,path):
    if not valid_field(path):return MISSING
    for name in path.split('.'):
        if not isinstance(value,dict)or name not in value:return MISSING
        value=value[name]
    return value


def validate(value):
    if not isinstance(value,dict)or set(value)!={'host','journey','password'}:raise ValueError('Invalid check')
    journey=value['journey']
    if not isinstance(journey,dict)or set(journey)!={'id','name','enabled','intervalMinutes','domain','auth','steps'}:
        raise ValueError('Invalid check')
    auth=journey['auth']
    if not isinstance(auth,dict)or set(auth)!={'kind','path','identityField','passwordField','identity','tokenField'}or auth['kind']not in ('none','json'):raise ValueError('Invalid authentication')
    if not isinstance(value['password'],str)or len(value['password'])>4096:raise ValueError('Invalid credentials')
    if auth['kind']=='json':
        if (not isinstance(auth['path'],str)or not AUTH.fullmatch(auth['path'])or not valid_field(auth['identityField'])or'.'in auth['identityField']or
            not valid_field(auth['passwordField'])or'.'in auth['passwordField']or auth['identityField']==auth['passwordField']or
            not isinstance(auth['identity'],str)or not 1<=len(auth['identity'])<=254 or not value['password']or
            auth['tokenField']!=''and not valid_field(auth['tokenField'])):raise ValueError('Invalid authentication')
    steps=journey['steps']
    if not isinstance(steps,list)or not 1<=len(steps)<=10:raise ValueError('Invalid steps')
    for step in steps:
        if (not isinstance(step,dict)or set(step)!={'name','path','status','jsonField','expected'}or not isinstance(step['name'],str)or not 1<=len(step['name'])<=80 or
            not safe_path(step['path'])or type(step['status'])is not int or not 200<=step['status']<=499 or
            step['jsonField']!=''and not valid_field(step['jsonField'])or step['expected']is not None and(not isinstance(step['expected'],str)or len(step['expected'])>256)):
            raise ValueError('Invalid step')
    return journey


def run(value,connection=PinnedHTTPS,resolver=resolve):
    journey=validate(value)
    started=time.monotonic();results=[];headers={'Accept':'application/json,text/html','User-Agent':'Kolab-ReadOnly-Check/1'}
    try:address=resolver(value['host'])
    except (OSError,ValueError):return {'status':'failed','errorCode':'origin_unavailable','steps':[],'durationMs':round((time.monotonic()-started)*1000)}

    def request(method,path,body=None):
        peer=connection(value['host'],address)
        try:
            extra={'Content-Type':'application/json'}if body is not None else{}
            peer.request(method,path,body=None if body is None else json.dumps(body).encode(),headers={**headers,**extra})
            response=peer.getresponse();raw=response.read(MAX_BODY+1)
            if len(raw)>MAX_BODY:raise ValueError('Response too large')
            return response.status,response.getheaders(),raw
        finally:peer.close()

    auth=journey['auth']
    if auth['kind']=='json':
        began=time.monotonic();status=0;good=False
        try:
            status,response_headers,raw=request('POST',auth['path'],{auth['identityField']:auth['identity'],auth['passwordField']:value['password']})
            if 200<=status<300:
                if auth['tokenField']:
                    token=select(json.loads(raw),auth['tokenField'])
                    if isinstance(token,str)and 1<=len(token)<=8192 and not re.search(r'[\x00-\x1f\x7f]',token):headers['Authorization']='Bearer '+token;good=True
                else:
                    cookies=http.cookies.SimpleCookie()
                    for key,content in response_headers:
                        if key.lower()=='set-cookie'and len(content)<=16384:cookies.load(content)
                    if cookies and len(cookies)<=20:
                        headers['Cookie']='; '.join(item.key+'='+item.coded_value for item in cookies.values());good=True
        except (OSError,ValueError,http.client.HTTPException):pass
        results.append({'name':'Connexion du compte de test','status':'passed'if good else'failed','httpStatus':status,'code':''if good else'authentication_failed','durationMs':round((time.monotonic()-began)*1000)})
        if not good:return {'status':'failed','errorCode':'authentication_failed','steps':results,'durationMs':round((time.monotonic()-started)*1000)}
    for step in journey['steps']:
        began=time.monotonic();status=0;good=False;code='network'
        try:
            status,_,raw=request('GET',step['path'])
            good=status==step['status'];code='http_status'
            if good and step['jsonField']:
                actual=select(json.loads(raw),step['jsonField']);good=actual is not MISSING;code='json_assertion'
                if good and step['expected']is not None:
                    actual=actual if isinstance(actual,str)else json.dumps(actual,separators=(',',':'),ensure_ascii=False)
                    good=actual==step['expected']
        except (OSError,ValueError,http.client.HTTPException):good=False
        results.append({'name':step['name'],'status':'passed'if good else'failed','httpStatus':status,'code':''if good else code,'durationMs':round((time.monotonic()-began)*1000)})
        if not good:break
    return {'status':'passed'if all(row['status']=='passed'for row in results)else'failed','errorCode':''if all(row['status']=='passed'for row in results)else'journey_failed','steps':results,'durationMs':round((time.monotonic()-started)*1000)}


def main():
    if len(sys.argv)!=1:raise ValueError('stdin only')
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(512*1024**2,512*1024**2));resource.setrlimit(resource.RLIMIT_CPU,(10,10))
        resource.setrlimit(resource.RLIMIT_FSIZE,(65536,65536))
    except ImportError:pass
    raw=sys.stdin.buffer.read(65537)
    if len(raw)>65536:raise ValueError('Invalid input')
    result=run(json.loads(raw));result['checkedAt']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except Exception:print(json.dumps({'status':'failed','errorCode':'configuration','steps':[]}))
