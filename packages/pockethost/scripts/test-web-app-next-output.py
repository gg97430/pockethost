#!/usr/bin/env python3
"""Execute the generated Next.js packaging script, then remove its build directory."""
import importlib.util
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

SOURCE = pathlib.Path(__file__).with_name('web-app-worker.py')
spec = importlib.util.spec_from_file_location('next_output_worker', SOURCE)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


@unittest.skipUnless(shutil.which('node'), 'Node.js is required to exercise the packaging script')
class OutputTests(unittest.TestCase):
    def exercise(self, standalone, nested, pnpm):
        with tempfile.TemporaryDirectory(prefix='next output ') as directory:
            root = pathlib.Path(directory).resolve()
            source = root / 'source'
            relative = 'apps/web' if nested else '.'
            app = source / relative
            app.mkdir(parents=True)
            package = {'dependencies': {'next': '16.4.2'}}
            (source / 'package.json').write_text(json.dumps(package))
            (source / 'package-lock.json').write_text('{}')
            (app / 'package.json').write_text(json.dumps(package))
            if nested:
                (app / 'package-lock.json').write_text('{}')
            (app / '.next/static').mkdir(parents=True)
            (app / '.next/static/site.css').write_text('body{}')
            (app / '.next/BUILD_ID').write_text('test')
            (app / '.next/cache').mkdir()
            (app / '.next/cache/build-only').write_text('cache')
            (app / 'public').mkdir()
            (app / 'public/site.txt').write_text('public')
            base = app / '.next/standalone' if standalone else source
            site = base / relative
            site.mkdir(parents=True, exist_ok=True)
            modules = base / 'node_modules'
            driver = modules / '.pnpm/pg@8.0.0/node_modules/pg' if pnpm else modules / 'pg'
            driver.mkdir(parents=True)
            (driver / 'package.json').write_text('{"name":"pg","main":"index.cjs"}')
            (driver / 'index.cjs').write_text('module.exports = "postgres-driver-fixture";')
            if pnpm:
                (modules / 'pg').symlink_to('.pnpm/pg@8.0.0/node_modules/pg')
            (modules / '.bin').mkdir()
            (modules / '.bin/pg-tool').symlink_to('../pg/index.cjs')
            alias = site / '.next/node_modules/pg-587764f78a6c7a9c'
            alias.parent.mkdir(parents=True)
            target = os.path.relpath(modules / 'pg', alias.parent)
            alias.symlink_to(target)
            runtime = site / '.next/server/chunks/ssr/fixture.mjs'
            runtime.parent.mkdir(parents=True)
            runtime.write_text('import pg from "pg-587764f78a6c7a9c"; export default pg;')
            if standalone:
                (site / 'server.js').write_text('/* standalone entry */')
            work = root / 'work'
            work.mkdir()
            value = {'id': 'a' * 15, 'appId': 'b' * 15, 'action': 'deploy', 'snapshot': {
                'config': {'rootDirectory': relative}, 'env': {},
            }}
            with mock.patch.object(worker, 'ROOT', root), mock.patch.object(worker, 'SECRET', 'test-secret' * 4), \
                    mock.patch.object(worker, 'resources'), mock.patch.object(worker, 'admit_resources'), \
                    mock.patch.object(worker.Job, 'run'):
                worker.Job(value).build(source, work)
            recipe = (work / 'Dockerfile').read_text()
            line = next(line for line in recipe.splitlines() if line.startswith('RUN node -e '))
            script = shlex.split(line)[3]
            output = root / 'output'
            script = re.sub(r"'(/src|/out)(/[^']*)?'", lambda m: json.dumps(str(source if m[1] == '/src' else output) + (m[2] or '')), script)
            result = subprocess.run(['node', '-e', script], cwd=app, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            # Reproduce the final Docker image: /src and /out are no longer present.
            shutil.rmtree(source)
            published = root / 'published'
            output.rename(published)
            copied_alias = published / relative / '.next/node_modules' / alias.name
            self.assertEqual(os.readlink(copied_alias), target)
            self.assertTrue(copied_alias.is_dir())
            self.assertEqual(os.readlink(published / 'node_modules/.bin/pg-tool'), '../pg/index.cjs')
            imported = subprocess.check_output([
                'node', '--input-type=module', '-e',
                'const m=await import(' + json.dumps((published / relative / '.next/server/chunks/ssr/fixture.mjs').as_uri()) + '); console.log(m.default);',
            ], text=True, timeout=15).strip()
            self.assertEqual(imported, 'postgres-driver-fixture')
            metadata = json.loads((published / 'ph-server.json').read_text())
            if standalone:
                self.assertEqual(metadata, (pathlib.PurePosixPath(relative) / 'server.js').as_posix())
                self.assertEqual((published / relative / '.next/static/site.css').read_text(), 'body{}')
                self.assertEqual((published / relative / 'public/site.txt').read_text(), 'public')
            else:
                self.assertEqual(metadata, {'mode': 'next-start', 'directory': relative})
                self.assertFalse((published / relative / '.next/cache').exists())

    def test_next_start_npm(self): self.exercise(False, False, False)
    def test_next_start_npm_monorepo(self): self.exercise(False, True, False)
    def test_next_start_pnpm(self): self.exercise(False, False, True)
    def test_next_start_pnpm_monorepo(self): self.exercise(False, True, True)
    def test_standalone_npm(self): self.exercise(True, False, False)
    def test_standalone_npm_monorepo(self): self.exercise(True, True, False)
    def test_standalone_pnpm(self): self.exercise(True, False, True)
    def test_standalone_pnpm_monorepo(self): self.exercise(True, True, True)


if __name__ == '__main__':
    unittest.main()
