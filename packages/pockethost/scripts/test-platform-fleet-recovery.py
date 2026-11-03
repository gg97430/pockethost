#!/usr/bin/env python3
import json,pathlib,tempfile,unittest
from unittest.mock import patch
import platform_fleet_recovery as m


class SealedTrust(unittest.TestCase):
    def test_authenticated_roundtrip_never_contains_private_key_bytes(self):
        files={name:b'PRIVATE-KEY-CONTENT-'+name.encode()for name in m.paths('app4')}
        raw=m.encode(files,'app4',b'k'*32)
        self.assertNotIn(b'PRIVATE-KEY-CONTENT',raw);self.assertEqual(m.decode(raw,'app4',b'k'*32),files)
        with self.assertRaises(Exception):m.decode(raw,'app5',b'k'*32)
        changed=json.loads(raw);changed['ciphertext']=changed['ciphertext'][:-5]+'AAAAA'
        with self.assertRaises(Exception):m.decode(json.dumps(changed).encode(),'app4',b'k'*32)
    def test_rejects_missing_keys_or_additional_output_paths(self):
        files={name:b'fixture'for name in m.paths('app4')};files['../../etc/sudoers']=b'no'
        with self.assertRaises(ValueError):m.encode(files,'app4',b'k'*32)
        files.pop('../../etc/sudoers');files.pop('fleet/keys/app3')
        with self.assertRaises(ValueError):m.encode(files,'app4',b'k'*32)
    def test_authentication_failure_creates_no_quarantine(self):
        with tempfile.TemporaryDirectory()as directory:
            dest=pathlib.Path(directory).resolve()/'quarantine'
            with patch.object(m,'read',return_value=b'{"version":1}'),patch.object(m,'key',return_value=b'k'*32):
                with self.assertRaises(Exception):m.unseal('source','key','app4',dest)
            self.assertFalse(dest.exists())
    def test_valid_unseal_writes_only_new_private_quarantine(self):
        files={name:b'fixture-private-key'for name in m.paths('app4')};raw=m.encode(files,'app4',b'k'*32)
        with tempfile.TemporaryDirectory()as directory:
            dest=pathlib.Path(directory).resolve()/'quarantine'
            with patch.object(m,'read',return_value=raw),patch.object(m,'key',return_value=b'k'*32):
                self.assertFalse(m.unseal('source','key','app4',dest)['liveTrustChanged'])
            for name,content in files.items():
                self.assertEqual((dest/name).read_bytes(),content);self.assertEqual((dest/name).stat().st_mode&0o777,0o600)


if __name__=='__main__':unittest.main()
