#!/usr/bin/env python3
import json,pathlib,tempfile,unittest
import web_app_preflight as m

DB,APP='a'*15,'b'*15
class Preflight(unittest.TestCase):
    def config(self):return {'enabled':True,'requiredEnvironment':[],'privateDatabases':False,'authentication':[],'publicUrls':[]}
    def test_requires_the_declared_scope_without_echoing_values(self):
        values={'DATABASE_URL':{'value':'postgres://private-password@database','build':True,'runtime':False}}
        rows=m.environment_checks([{'name':'DATABASE_URL','scope':'both'}],values)
        self.assertEqual(rows[0]['status'],'failed');self.assertNotIn('private-password',json.dumps(rows))
        values['DATABASE_URL']['runtime']=True;self.assertEqual(m.environment_checks([{'name':'DATABASE_URL','scope':'both'}],values)[0]['status'],'passed')
    def test_checks_private_health_and_authentication_without_printing_secrets(self):
        config=self.config();config.update(privateDatabases=True,authentication=[{'bindingId':DB,'collection':'_superusers','identityEnv':'ADMIN_EMAIL','passwordEnv':'ADMIN_PASSWORD'}])
        binding={'id':DB,'port':4825,'available':True};calls=[]
        def request(app,binding,method,path,body=None):calls.append((method,path,body));return 200
        rows=m.evaluate(APP,config,{'ADMIN_EMAIL':{'value':'reader@example.invalid','runtime':True},'ADMIN_PASSWORD':{'value':'private-password','runtime':True}},set(),[binding],request)
        self.assertTrue(all(row['status']=='passed'for row in rows));self.assertEqual([row[0]for row in calls],['GET','POST'])
        self.assertNotIn('private-password',json.dumps(rows));self.assertNotIn('reader@example.invalid',json.dumps(rows))
    def test_rejects_a_private_url_in_a_public_browser_variable(self):
        config=self.config();config['publicUrls']=[{'name':'NEXT_PUBLIC_DB_URL','target':'privateDatabase','bindingId':DB}]
        rows=m.evaluate(APP,config,{'NEXT_PUBLIC_DB_URL':{'value':'http://127.0.0.1:4825','runtime':True}},set(),[{'id':DB,'port':4825}])
        self.assertEqual(rows[0]['status'],'failed')
        config['publicUrls'][0]['name']='DATABASE_URL'
        rows=m.evaluate(APP,config,{'DATABASE_URL':{'value':'http://127.0.0.1:4825','runtime':True}},set(),[{'id':DB,'port':4825}])
        self.assertEqual(rows[0]['status'],'passed')
    def test_required_database_checks_never_succeed_without_a_verifiable_binding(self):
        config=self.config();config['privateDatabases']=True
        self.assertEqual(m.evaluate(APP,config,{},set(),[]),[{'name':'Liaisons BDD privées','status':'failed','code':'database_unavailable'}])
    def test_build_only_credentials_do_not_validate_runtime_authentication(self):
        config=self.config();config['authentication']=[{'bindingId':DB,'collection':'users','identityEnv':'EMAIL','passwordEnv':'PASSWORD'}]
        def request(*args):
            self.assertNotEqual(args[2],'POST');return 200
        rows=m.evaluate(APP,config,{'EMAIL':{'value':'reader','runtime':True},'PASSWORD':{'value':'secret','build':True}},set(),[{'id':DB,'available':True}],request)
        self.assertEqual(rows[-1]['status'],'failed')
    def test_does_not_authenticate_a_revoked_binding_or_send_a_public_password(self):
        config=self.config();config['authentication']=[{'bindingId':DB,'collection':'users','identityEnv':'EMAIL','passwordEnv':'NEXT_PUBLIC_PASSWORD'}]
        def never(*args):
            if args[2]=='POST':raise AssertionError('Credential must not be sent')
            return 200
        rows=m.evaluate(APP,config,{'EMAIL':{'value':'test'},'NEXT_PUBLIC_PASSWORD':{'value':'password'}},set(),[{'id':DB,'available':True}],never)
        self.assertEqual(rows[-1]['status'],'failed')
        config['authentication'][0]['passwordEnv']='PASSWORD';rows=m.evaluate(APP,config,{},set(),[],never)
        self.assertTrue(all(row['status']=='failed'for row in rows))
    def test_source_contract_stops_before_build_and_preserves_only_variable_names(self):
        class Job:
            snapshot={'config':{'rootDirectory':'.'},'env':{}};logs=[]
            def log(self,value):self.logs.append(value)
        with tempfile.TemporaryDirectory()as root:
            folder=pathlib.Path(root)/'.kolab';folder.mkdir();(folder/'checks.json').write_text(json.dumps({'version':1,'requiredEnvironment':[{'name':'REQUIRED_TOKEN','scope':'runtime'}]}))
            job=Job()
            with self.assertRaises(RuntimeError):m.source_contract(job,root)
            self.assertIn('REQUIRED_TOKEN',''.join(job.logs))
            (folder/'checks.json').unlink();(folder/'checks.json').symlink_to('/etc/passwd')
            with self.assertRaises(RuntimeError):m.source_contract(job,root)
    def test_inactive_checks_do_not_probe_or_change_existing_deployments(self):
        self.assertEqual(m.evaluate(APP,{'enabled':False},{},set(),[]),[])


if __name__=='__main__':unittest.main()
