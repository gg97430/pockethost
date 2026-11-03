#!/usr/bin/env python3
import io,json,pathlib,tempfile,time,unittest
from unittest.mock import patch
import platform_maintenance as m


class Maintenance(unittest.TestCase):
    def lease(self,**extra):return {'version':1,'owner':'release-test','startedAt':100,'expiresAt':200,**extra}

    def test_valid_pause_and_automatic_expiration(self):
        self.assertTrue(m.parse(self.lease(),150)['paused'])
        self.assertFalse(m.parse(self.lease(),200)['paused'])

    def test_bounded_lease_and_exact_schema(self):
        for value in [None,{},self.lease(version=True),self.lease(owner='../root'),self.lease(expiresAt=100),self.lease(expiresAt=1901),self.lease(startedAt=True),self.lease(unknown=1),self.lease(startedAt=170)]:
            with self.assertRaises(ValueError):m.parse(value,150)

    def test_missing_file_is_normal_and_symlink_fails_closed(self):
        with tempfile.TemporaryDirectory()as root:
            p=pathlib.Path(root)/'missing';self.assertFalse(m.status(p)['paused'])
            p.symlink_to('/etc/passwd');self.assertTrue(m.status(p)['paused'])

    def test_non_operator_file_is_not_a_pause_control(self):
        with tempfile.NamedTemporaryFile(mode='w')as file:
            json.dump(self.lease(),file);file.flush()
            class S:st_mode=0o100664;st_uid=0;st_size=99
            with patch.object(m.os,'fstat',return_value=S()):self.assertTrue(m.status(file.name,150)['paused'])

    def test_only_new_claims_are_blocked(self):
        with patch.object(m,'status',return_value={'paused':True,'owner':'release-test'}):
            for scope,path in [('worker','claim'),('worker','storage/claim'),('worker','deletions/claim'),('worker','backups/claim'),('tasks','claim'),('notifications','claim')]:
                self.assertIsNone(m.claim_response(path,scope)['job'])
            for path in ['report','inspect','private-db/policy','health/report','email']:
                self.assertIsNone(m.claim_response(path))

    def test_reports_and_relay_do_not_even_read_pause_file(self):
        with patch.object(m,'status',side_effect=AssertionError('Heartbeat must continue')):
            self.assertIsNone(m.claim_response('report','tasks'))
            self.assertIsNone(m.claim_response('report','notifications'))
            self.assertIsNone(m.claim_response('private-db/policy'))

    def test_clear_or_expired_pause_releases_admission(self):
        with patch.object(m,'status',return_value={'paused':False}):self.assertIsNone(m.claim_response('claim'))


if __name__=='__main__':unittest.main()
