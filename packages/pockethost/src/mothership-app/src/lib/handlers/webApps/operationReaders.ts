/** Bounded incremental log reads; cursors describe only this authorized deployment file. */
export const WEB_LOG_READER = String.raw`
import sys,pathlib,json,base64,hashlib,codecs,os
root=pathlib.Path(sys.argv[1]).absolute();ident=sys.argv[2];cursor=sys.argv[3];limit=65536
path=root/'logs'/(ident+'.log')
if root.resolve()!=root or path.parent.resolve()!=path.parent or path.is_symlink():raise ValueError('Invalid log path')
def result(text='',cursor='',reset=False,truncated=False,hasMore=False):print(json.dumps(dict(text=text,cursor=cursor,reset=reset,truncated=truncated,hasMore=hasMore)))
try:
 with open(path,'rb')as f:
  stat=os.fstat(f.fileno());prefix=hashlib.sha256(f.read(128)).hexdigest();token={}
  if cursor:
   try:token=json.loads(base64.urlsafe_b64decode(cursor+'='*((-len(cursor))%4)))
   except Exception:token={}
  valid=token.get('i')==stat.st_ino and token.get('d')==stat.st_dev and token.get('h')==prefix and token.get('j')==ident and isinstance(token.get('o'),int) and 0<=token['o']<=stat.st_size
  reset=not valid;offset=token['o']if valid else max(0,stat.st_size-limit)
  f.seek(offset);data=f.read(limit)
  if offset and reset:
   while data and data[0]&0xc0==0x80:data=data[1:];offset+=1
  decoder=codecs.getincrementaldecoder('utf-8')('replace');text=decoder.decode(data,final=offset+len(data)>=stat.st_size);consumed=len(data)-len(decoder.getstate()[0]);offset+=consumed
  token={'i':stat.st_ino,'d':stat.st_dev,'h':prefix,'j':ident,'o':offset}
  encoded=base64.urlsafe_b64encode(json.dumps(token,separators=(',',':')).encode()).rstrip(b'=').decode()
  result(text,encoded,reset,reset and stat.st_size>limit,offset<stat.st_size)
except FileNotFoundError:result(reset=bool(cursor))
`
