#!/usr/bin/env python3
"""Real localhost FTP/explicit-FTPS control+PROT P fixture, synthetic credentials only."""
import argparse,ftplib,io,json,os,pathlib,secrets,socket,ssl,subprocess,tempfile,time,unittest
parser=argparse.ArgumentParser();parser.add_argument('--source',required=True,type=pathlib.Path);parser.add_argument('--node',default='node');args=parser.parse_args()
SOURCE=args.source.resolve();NODE=args.node
class Tests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name);self.data=self.root/'data';self.data.mkdir();(self.data/'fixture.txt').write_bytes(b'protected fixture data\n');self.process=None
  subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',str(self.root/'key.pem'),'-out',str(self.root/'cert.pem'),'-subj','/CN=localhost'],capture_output=True,check=True)
  self.stats=self.root/'stats.json'
 def tearDown(self):
  if self.process:
   self.process.terminate()
   try:self.process.wait(timeout=4)
   except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
  if hasattr(self,'log'):self.log.close()
  self.temp.cleanup()
 def free(self):
  with socket.socket()as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
 def start(self,required=True):
  port=self.free();data_port=self.free();script=self.root/'fixture.mjs'
  code=r'''import {createRequire} from 'node:module';import fs from 'node:fs';
import {authorizeFtpLogin} from HELPER;
const require=createRequire(PACKAGE);const {FtpSrv}=require('ftp-srv');
const root=ROOT;let attempts=0,observations=[];
const write=()=>fs.writeFileSync(root+'/stats.json',JSON.stringify({attempts,observations}));write();
const logger={trace(){},debug(){},info(){},warn(){},error(){},fatal(){},child(){return this}};
const server=new FtpSrv({url:'ftp://127.0.0.1:PORT',anonymous:false,pasv_url:'127.0.0.1',pasv_min:DATA,pasv_max:DATA,tls:{key:fs.readFileSync(root+'/key.pem'),cert:fs.readFileSync(root+'/cert.pem')},log:logger});
server.on('login',async({connection,username,password},resolve,reject)=>{
 try{const value=await authorizeFtpLogin(connection,REQUIRED,async()=>{attempts++;observations.push({secure:connection.secure});write();if(username!=='fixture'||password!=='synthetic-valid')throw Error('Invalid credentials');return{root:root+'/data'}});resolve(value)}catch{reject(Error('Authentication refused'))}
});
await server.listen();fs.writeFileSync(root+'/ready','yes');process.on('SIGTERM',()=>{server.close().finally(()=>process.exit(0))});'''
  helper=SOURCE/'packages/pockethost/src/cli/commands/EdgeCommand/FtpCommand/FtpService/ftpTls.ts'
  code=code.replace('HELPER',json.dumps(helper.as_uri())).replace('PACKAGE',json.dumps(str(SOURCE/'packages/pockethost/package.json'))).replace('ROOT',json.dumps(str(self.root))).replace('PORT',str(port)).replace('DATA',str(data_port)).replace('REQUIRED','true'if required else'false');script.write_text(code)
  self.log=(self.root/'node.log').open('w');self.process=subprocess.Popen([NODE,'--import','tsx',str(script)],cwd=SOURCE/'packages/pockethost',stdout=self.log,stderr=subprocess.STDOUT)
  for _ in range(100):
   if self.process.poll()is not None:raise AssertionError((self.root/'node.log').read_text())
   if(self.root/'ready').exists():break
   time.sleep(.05)
  else:raise AssertionError('Fixture startup timed out')
  self.port=port
 def read(self):return json.loads(self.stats.read_text())
 def ftps(self):
  context=ssl.create_default_context();context.check_hostname=False;context.verify_mode=ssl.CERT_NONE
  client=ftplib.FTP_TLS(context=context,timeout=10);client.connect('127.0.0.1',self.port);client.auth();return client
 def test_plain_password_never_calls_authenticator_when_tls_required(self):
  self.start(True)
  with ftplib.FTP(timeout=10)as client:
   client.connect('127.0.0.1',self.port)
   with self.assertRaises(ftplib.error_perm):client.login('fixture','synthetic-valid')
  self.assertEqual(self.read()['attempts'],0)
 def test_auth_tls_pipelined_plain_commands_never_call_authenticator(self):
  self.start(True)
  with socket.create_connection(('127.0.0.1',self.port),timeout=3)as client:
   client.recv(4096);client.sendall(b'AUTH TLS\r\nUSER fixture\r\nPASS synthetic-valid\r\n');time.sleep(.3)
  self.assertEqual(self.read()['attempts'],0);self.assertIsNone(self.process.poll())
 def test_explicit_tls_bad_credentials_call_authenticator_after_encryption(self):
  self.start(True)
  with self.ftps()as client:
   with self.assertRaises(ftplib.error_perm):client.login('fixture','synthetic-invalid')
  self.assertEqual(self.read(),{'attempts':1,'observations':[{'secure':True}]})
 def test_explicit_tls_valid_login_and_prot_p_data_transfer(self):
  self.start(True)
  with self.ftps()as client:
   client.login('fixture','synthetic-valid');client.prot_p();client.voidcmd('TYPE I');host,port=client.makepasv()
   # This pinned fork waits for the data TLS handshake before 150. Complete it explicitly instead of ftplib's 150-first ordering.
   data=client.context.wrap_socket(socket.create_connection((host,port),timeout=10),server_hostname='localhost')
   self.assertTrue(client.sendcmd('RETR fixture.txt').startswith('150'));received=io.BytesIO()
   while True:
    block=data.recv(65536)
    if not block:break
    received.write(block)
   data.close();self.assertTrue(client.voidresp().startswith('226'))
  self.assertEqual(received.getvalue(),b'protected fixture data\n');self.assertEqual(self.read()['observations'],[{'secure':True}])
 def test_default_opt_out_keeps_other_hosts_plain_login_behavior(self):
  self.start(False)
  with ftplib.FTP(timeout=10)as client:client.connect('127.0.0.1',self.port);client.login('fixture','synthetic-valid')
  self.assertEqual(self.read()['observations'],[{'secure':False}])
if __name__=='__main__':unittest.main(argv=[__file__])
