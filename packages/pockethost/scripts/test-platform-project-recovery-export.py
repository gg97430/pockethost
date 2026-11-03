import json, os, pathlib, sys, tempfile, unittest
from unittest.mock import patch
import platform_project_recovery_export as export


class Exports(unittest.TestCase):
    def setUp(self):
        self.key = b'x' * 32
        self.files = {name: ('private-' + name).encode() for name in export.FILES}

    def test_authenticated_server_inventory(self):
        self.files['journals/' + 'a' * 15 + '.json'] = b'{"phase":"paused"}'
        raw = export.encode(self.files, 'app4', self.key)
        self.assertEqual(export.decode(raw, 'app4', self.key), self.files)
        self.assertNotIn(b'private-', raw)
        for server, key in [('app5', self.key), ('app4', b'y' * 32)]:
            with self.assertRaises(Exception): export.decode(raw, server, key)

    def test_closed_restore_destinations(self):
        for name in ('../../etc/shadow', 'journals/a.json', 'journals/' + 'b' * 15 + '.env'):
            with self.assertRaises(ValueError): export.encode({**self.files, name: b'x'}, 'app4', self.key)
        with self.assertRaises(ValueError): export.encode({}, 'app4', self.key)

    def test_quarantine_only_and_auth_before_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory).resolve(); root.chmod(0o700)
            key = root / 'key'; key.write_text('11' * 32); key.chmod(0o600)
            source = root / 'sealed'; source.write_bytes(export.encode(self.files, 'app4', export.wrapping_key(key, (os.geteuid(),)))); source.chmod(0o600)
            destination = root / 'quarantine'
            with self.assertRaises(Exception): export.unseal(source, key, 'app5', destination)
            self.assertFalse(destination.exists())
            value = export.unseal(source, key, 'app4', destination)
            self.assertFalse(value['liveConfigurationChanged'])
            self.assertEqual((destination / 'project-recovery.key').read_bytes(), self.files['project-recovery.key'])
            self.assertEqual((destination / 'project-recovery.key').stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError): export.unseal(source, key, 'app4', destination)


if __name__ == '__main__': unittest.main()
