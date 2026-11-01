#!/usr/bin/env python3
"""Plan generator and drift check on a synthetic DATA_ROOT; production paths are never written."""
import contextlib
import io
import json
import os
import pathlib
import sqlite3
import tempfile
import unittest

import web_platform_recovery as engine
import web_platform_recovery_plan as recovery_plan


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.root = pathlib.Path(self.temporary.name).resolve()
        self.data = self.root / 'data'; self.controller = self.data / 'mothership/pb_data/data.db'; self.controller.parent.mkdir(parents=True)
        self.ids = ['a' * 15, 'b' * 15]
        with sqlite3.connect(self.controller) as db:
            db.execute('CREATE TABLE instances(id TEXT PRIMARY KEY,version TEXT)'); db.executemany('INSERT INTO instances VALUES(?,?)', [(ident, '0.39.*') for ident in self.ids])
        for ident in self.ids:
            folder = self.data / 'instances' / ident / 'pb_data'; folder.mkdir(parents=True); (folder / 'data.db').write_bytes(b'')
        self.private = self.root / 'private'; self.private.mkdir(mode=0o700)
        self.base = self.private / 'plan.json'
        self.base.write_text(json.dumps({'version': 1, 'platform': 'app1', 'controller': {'database': str(self.controller), 'storage': str(self.controller.parent / 'storage')}, 'instances': [{'id': self.ids[0], 'database': '/old/path/data.db', 'initializeWalIndex': True}, {'id': 'z' * 15, 'database': '/gone/data.db'}], 'hostPaths': [{'source': '/home/ubuntu/.local/share/pockethost/pocketbase', 'target': 'platform/pocketbase'}], 'maxWalGrowthBytes': 1024, 'backupId': 'stale'}))
        self.base.chmod(0o600)
        self.before = self.controller.read_bytes()

    def tearDown(self): self.temporary.cleanup()

    def cli(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()): code = recovery_plan.main(list(arguments))
        return code, (json.loads(output.getvalue()) if output.getvalue() else None)

    def test_generate_keeps_host_paths_and_options_and_writes_only_a_private_plan(self):
        target = self.private / 'generated.json'
        code, report = self.cli('generate', '--base', str(self.base), '--output', str(target))
        self.assertEqual(code, 0); self.assertEqual(report['added'], [self.ids[1]]); self.assertEqual(report['removed'], ['z' * 15]); self.assertTrue(report['nativeBinariesCaptured'])
        plan = json.loads(target.read_text()); self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual([item['id'] for item in plan['instances']], self.ids); self.assertNotIn('backupId', plan); self.assertEqual(plan['maxWalGrowthBytes'], 1024)
        first = plan['instances'][0]; self.assertEqual(first['database'], str(self.data / 'instances' / self.ids[0] / 'pb_data/data.db')); self.assertTrue(first['initializeWalIndex'])
        self.assertEqual(plan['metadata']['instanceVersions'], {ident: '0.39.*' for ident in self.ids}); self.assertEqual(plan['hostPaths'][0]['target'], 'platform/pocketbase')
        self.assertEqual(self.controller.read_bytes(), self.before); self.assertEqual(sorted(path.name for path in self.private.iterdir()), ['generated.json', 'plan.json'])
        engine.load_plan(target)  # The engine accepts the generated plan as is.
        code, _ = self.cli('generate', '--base', str(self.base), '--output', str(self.data / 'plan.json'))
        self.assertEqual(code, 2); self.assertFalse((self.data / 'plan.json').exists())

    def test_instance_without_database_requires_explicit_flag(self):
        with sqlite3.connect(self.controller) as db: db.execute('INSERT INTO instances VALUES(?,?)', ('c' * 15, '0.39.*'))
        self.assertEqual(self.cli('generate', '--base', str(self.base))[0], 2)
        code, report = self.cli('generate', '--base', str(self.base), '--allow-without-database'); self.assertEqual(code, 0); self.assertEqual(report['withoutDatabase'], ['c' * 15]); self.assertFalse(report['written'])

    def test_check_detects_added_removed_and_missing_databases(self):
        code, result = self.cli('check', '--plan', str(self.base))
        self.assertEqual(code, 3); self.assertTrue(result['drift']); self.assertEqual(result['added'], [self.ids[1]]); self.assertEqual(result['removed'], ['z' * 15]); self.assertEqual(result['databaseMissing'], [self.ids[0]])
        target = self.private / 'current.json'; self.cli('generate', '--base', str(self.base), '--output', str(target))
        code, result = self.cli('check', '--plan', str(target)); self.assertEqual(code, 0); self.assertFalse(result['drift']); self.assertEqual(result['warnings'], [])
        with sqlite3.connect(self.controller) as db: db.execute('DELETE FROM instances WHERE id=?', (self.ids[1],))
        code, result = self.cli('check', '--plan', str(target)); self.assertEqual(code, 3); self.assertEqual(result['removed'], [self.ids[1]])
        self.base.chmod(0o644); self.assertEqual(self.cli('check', '--plan', str(self.base))[0], 2)

    def test_data_root_is_derived_only_from_the_pockethost_layout(self):
        self.assertEqual(recovery_plan.data_root_of({'controller': {'database': str(self.controller)}}), self.data)
        with self.assertRaises(engine.RecoveryError): recovery_plan.data_root_of({'controller': {'database': str(self.root / 'controller.db')}})
        self.assertEqual(recovery_plan.data_root_of({}, str(self.data)), self.data)
        link = self.root / 'link'; link.symlink_to(self.data)
        with self.assertRaises(engine.RecoveryError): recovery_plan.data_root_of({}, str(link))


if __name__ == '__main__': unittest.main()
