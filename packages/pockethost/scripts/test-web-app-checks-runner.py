#!/usr/bin/env python3
import json,socket,unittest
from unittest.mock import patch
import web_app_checks_runner as m


class Journeys(unittest.TestCase):
    def value(self):return {'host':'app.example.com','password':'dedicated-password','journey':{'id':'a'*15,'name':'Tableau de bord','enabled':True,'intervalMinutes':5,'domain':'generated','auth':{'kind':'none','path':'','identityField':'','passwordField':'','identity':'','tokenField':''},'steps':[{'name':'Accueil','path':'/','status':200,'jsonField':'','expected':None}]}}
    def transport(self,replies):
        calls=[]
        class Connection:
            def __init__(self,host,address):calls.append(('connect',host,address))
            def request(self,method,path,body=None,headers=None):calls.append((method,path,body,dict(headers)))
            def getresponse(self):
                status,headers,body=replies.pop(0)
                class Response:
                    def read(self,*args):return body
                    def getheaders(self):return headers
                row=Response();row.status=status;return row
            def close(self):pass
        return Connection,calls
    def test_only_explicit_auth_post_then_get_and_no_secret_in_report(self):
        value=self.value();value['journey']['auth']={'kind':'json','path':'/api/auth/login','identityField':'email','passwordField':'password','identity':'reader@example.invalid','tokenField':'token'}
        value['journey']['steps'][0].update(path='/api/me',jsonField='active',expected='true')
        transport,calls=self.transport([(200,[],b'{"token":"private-session-token"}'),(200,[],b'{"active":true,"private":"response-secret"}')])
        result=m.run(value,transport,lambda host:'93.184.216.34')
        self.assertEqual(result['status'],'passed')
        requests=[row for row in calls if row[0]in ('POST','GET')]
        self.assertEqual([row[0]for row in requests],['POST','GET'])
        self.assertEqual(requests[1][3]['Authorization'],'Bearer private-session-token')
        for secret in ['dedicated-password','private-session-token','response-secret','reader@example.invalid']:self.assertNotIn(secret,json.dumps(result))
    def test_blocks_private_dns_and_pins_only_resolved_public_address(self):
        for address in ['127.0.0.1','169.254.169.254','100.114.81.36','10.0.0.1','::1','::ffff:127.0.0.1','2002:7f00:0001::1','64:ff9b::7f00:1','ff02::1','224.0.0.1']:
            with patch.object(m.socket,'getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,443))]):
                with self.assertRaises(ValueError):m.resolve('app.example.com')
        with patch.object(m.socket,'getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]):self.assertEqual(m.resolve('app.example.com'),'93.184.216.34')
        transport,calls=self.transport([(200,[],b'healthy')]);m.run(self.value(),transport,lambda host:'93.184.216.34')
        self.assertEqual(calls[0],('connect','app.example.com','93.184.216.34'))
    def test_does_not_follow_redirects_or_send_credentials_to_another_host(self):
        transport,calls=self.transport([(302,[('Location','http://169.254.169.254/latest/meta-data')],b'')])
        result=m.run(self.value(),transport,lambda host:'93.184.216.34')
        self.assertEqual(result['status'],'failed');self.assertEqual(len([row for row in calls if row[0]=='connect']),1)
    def test_rejects_mutating_business_methods_and_auth_paths(self):
        for change in [{'method':'DELETE'},{'path':'//evil.example'},{'path':'/\\evil.example'}]:
            value=self.value();value['journey']['steps'][0].update(change)
            with self.assertRaises(ValueError):m.validate(value)
        value=self.value();value['journey']['auth'].update(kind='json',path='/api/invoices/send',identityField='email',passwordField='password',identity='reader@example.invalid',tokenField='token')
        with self.assertRaises(ValueError):m.validate(value)
    def test_cookie_auth_remains_on_same_origin_and_failures_are_bounded(self):
        value=self.value();value['journey']['auth'].update(kind='json',path='/api/auth/login',identityField='email',passwordField='password',identity='reader@example.invalid')
        transport,calls=self.transport([(200,[('Set-Cookie','session=private-cookie; Secure; HttpOnly')],b'{}'),(429,[],b'sensitive-server-response')])
        result=m.run(value,transport,lambda host:'93.184.216.34')
        self.assertEqual(result['steps'][-1]['httpStatus'],429);self.assertNotIn('private-cookie',json.dumps(result));self.assertNotIn('sensitive-server-response',json.dumps(result))
    def test_oversized_response_never_reaches_report(self):
        transport,calls=self.transport([(200,[],b'x'*(m.MAX_BODY+1))])
        result=m.run(self.value(),transport,lambda host:'93.184.216.34')
        self.assertEqual(result['status'],'failed');self.assertLess(len(json.dumps(result)),1000)


if __name__=='__main__':unittest.main()
