#!/usr/bin/env python3
"""Real-loss un-pause on a synthetic restored copy: dry-run first, exact reversal, audited."""
import contextlib
import io
import json
import pathlib
import sqlite3
import tempfile
import types
import unittest
from unittest import mock

import web_platform_recovery as engine
import web_platform_recovery_probe as probe
import web_platform_recovery_unpause as unpause

MASTER = 'M' * 32
GIT_A, GIT_B = 'gitlab.com/team/site', 'srv.example.com//srv/app'
EXTRA = 'acme0connection'  # A second GitHub App connection: key 'github:<15 [a-z0-9]>'.
EXTRA_META = {'label': 'Organisation Acme', 'disabled': False, 'createdAt': '2026-10-29T00:00:00.000Z', 'createdBy': 'u' * 15}


class UnpauseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.root = pathlib.Path(self.temporary.name).resolve()
        self.key = bytes(range(32)); self.keyfile = self.root / 'outer.key'; self.keyfile.write_text(self.key.hex()); self.keyfile.chmod(0o600)
        self.controller = self.root / 'controller.db'; self.instance_id = 'a' * 15; self.instance = self.root / 'instance.db'
        github = unpause.pb_encrypt({'githubAppId': '1', 'webhookSecret': 'original-github-webhook-secret'}, MASTER.encode())
        extra = unpause.pb_encrypt({'githubAppId': '2', 'githubAppSlug': 'kolab-acme', 'githubInstallationId': '22', 'privateKey': '-----BEGIN RSA PRIVATE KEY-----\nacme\n', 'webhookSecret': 'original-acme-webhook-secret', 'meta': EXTRA_META}, MASTER.encode())
        notify = unpause.pb_encrypt({'webhook': {'enabled': True, 'url': 'https://hooks.example.invalid/x', 'secret': 'original-notify-secret'}, 'email': {'enabled': False}}, MASTER.encode())
        with sqlite3.connect(self.controller) as db:
            db.executescript('''CREATE TABLE instances(id TEXT PRIMARY KEY);CREATE TABLE web_apps(id TEXT,status TEXT);CREATE TABLE web_app_health(id TEXT,enabled INTEGER);CREATE TABLE web_scheduled_tasks(id TEXT,enabled INTEGER);
              CREATE TABLE web_notification_settings(id TEXT,appId TEXT,enabled INTEGER,configEncrypted TEXT);CREATE TABLE web_ci_tokens(id TEXT,revokedAt TEXT,expiresAt TEXT);
              CREATE TABLE web_git_sources(id TEXT PRIMARY KEY,canonical TEXT,host TEXT,webhookId TEXT,privateKeyEncrypted TEXT,webhookSecretEncrypted TEXT,pendingHostKeysEncrypted TEXT);CREATE TABLE web_app_settings(id TEXT,key TEXT,valueEncrypted TEXT);
              CREATE TABLE _params(id TEXT,value TEXT);CREATE TABLE audit(n INTEGER);CREATE TRIGGER touch AFTER UPDATE ON web_app_health BEGIN INSERT INTO audit VALUES(1); END;''')
            db.execute('INSERT INTO instances VALUES(?)', (self.instance_id,))
            db.executemany('INSERT INTO web_apps VALUES(?,?)', [('app-running', 'running'), ('app-stopped', 'stopped')])
            db.executemany('INSERT INTO web_app_health VALUES(?,?)', [('h-on', 1), ('h-off', 0)]); db.executemany('INSERT INTO web_scheduled_tasks VALUES(?,?)', [('t-on', 1)])
            db.execute('INSERT INTO web_notification_settings VALUES(?,?,?,?)', ('n1', 'b' * 15, 1, notify))
            db.executemany('INSERT INTO web_ci_tokens VALUES(?,?,?)', [('ci-live', None, '2999-01-01 00:00:00.000Z'), ('ci-revoked', '2026-01-01 00:00:00.000Z', '2999-01-01 00:00:00.000Z'), ('ci-expired', '', '2020-01-01 00:00:00.000Z')])
            self.deploy_key = unpause.pb_encrypt({'privateKey': '-----BEGIN OPENSSH PRIVATE KEY-----\nx\n'}, MASTER.encode())
            db.executemany('INSERT INTO web_git_sources VALUES(?,?,?,?,?,?,?)', [('a' * 15, GIT_A, 'gitlab.com', 'A' * 24, self.deploy_key, unpause.pb_encrypt({'secret': 's' * 48}, MASTER.encode()), 'pending-scan'), ('b' * 15, GIT_B, 'srv.example.com', 'B' * 24, self.deploy_key, '', '')])
            db.execute('INSERT INTO web_app_settings VALUES(?,?,?)', ('gh', 'github', github))
            db.execute('INSERT INTO web_app_settings VALUES(?,?,?)', ('gh-acme', 'github:' + EXTRA, extra))
            db.execute('INSERT INTO _params VALUES(?,?)', ('settings', json.dumps({'smtp': {'enabled': True, 'host': 'smtp.invalid'}, 's3': {'enabled': False}, 'backups': {'cron': '0 4 * * *', 's3': {'enabled': True}}})))
        with sqlite3.connect(self.instance) as db:
            db.execute('CREATE TABLE _params(id TEXT,value TEXT)'); db.execute('INSERT INTO _params VALUES(?,?)', ('settings', json.dumps({'smtp': {'enabled': False}, 's3': {'enabled': False}, 'backups': {'cron': ''}})))
        plan = {'version': 1, 'platform': 'app1', 'backupId': 'unpause-fixture', 'controller': {'database': str(self.controller)}, 'instances': [{'id': self.instance_id, 'database': str(self.instance)}]}
        with mock.patch.object(engine.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=100 * 1024**3)):
            output = io.BytesIO(); engine.capture_to_sink(plan, self.key, self.keyfile, output)
            self.target = self.root / 'restored'; engine.restore_archive(lambda: io.BytesIO(output.getvalue()), self.key, self.target)
        self.database = self.target / 'controller/pb_data/data.db'
        self.master = self.root / 'master.key'; self.master.write_text(MASTER); self.master.chmod(0o600)

    def tearDown(self): self.temporary.cleanup()

    def cli(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err): code = unpause.main(['--root', str(self.target), *arguments])
        return code, json.loads(out.getvalue() or err.getvalue())

    def rows(self, sql):
        with sqlite3.connect(self.database) as db: return db.execute(sql).fetchall()

    def test_dry_run_is_default_and_changes_nothing(self):
        before = self.database.read_bytes()
        code, audit = self.cli('--all', '--revoke-ci-tokens')
        self.assertEqual(code, 0); self.assertEqual(audit['mode'], 'dry-run'); self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(audit['databases']['controller']['health'], 1); self.assertEqual(audit['databases']['controller']['ciTokensRevoked'], 2); self.assertEqual(audit['databases']['controller']['apps'], 1)
        self.assertEqual(sorted(audit['databases']['controller']['native']), ['backupsCron', 'backupsS3', 'smtp'])
        self.assertTrue(json.loads((self.target / 'RECOVERY_PAUSED.json').read_text())['paused'])
        self.assertEqual(self.cli()[0], 1)  # No category: refused.

    def test_pause_reports_external_credentials_without_repository_names(self):
        pauses = json.loads((self.target / 'RECOVERY_PAUSED.json').read_text())['databasePauses']['controller']
        # ci-expired is not revoked either: the pause only reports, the controller already refuses it.
        self.assertEqual(pauses['credentialsKept'], {'gitSources': {'count': 2, 'hosts': ['gitlab.com', 'srv.example.com'], 'webhooks': 1, 'deployKeys': 2}, 'ciTokensActive': 2, 'githubConnections': {'count': 2}})
        self.assertNotIn('kolab-acme', json.dumps(pauses))  # Counted by key, never decrypted.
        self.assertNotIn('team/site', json.dumps(pauses)); self.assertNotIn('/srv/app', json.dumps(pauses))
        self.assertEqual(self.rows("SELECT count(*) FROM web_git_sources WHERE webhookSecretEncrypted!=''"), [(1,)])  # Report only.

    def test_git_webhooks_rotate_or_disable_and_deploy_keys_stay(self):
        before = self.database.read_bytes(); secrets_out = self.root / 'git.json'
        code, audit = self.cli('--rotate-webhooks', '--master-key-file', str(self.master))
        self.assertEqual(code, 0); self.assertEqual(self.database.read_bytes(), before); self.assertEqual(audit['databases']['controller']['gitWebhookSecretsRotated'], 1)
        self.assertIn(unpause.GIT_KEY_WARNING, audit['warnings']); self.assertTrue(any('--revoke-ci-tokens' in warning for warning in audit['warnings']))
        self.assertNotIn(GIT_A, json.dumps(audit))
        code, audit = self.cli('--rotate-webhooks', '--master-key-file', str(self.master), '--secrets-out', str(secrets_out), '--apply'); self.assertEqual(code, 0, audit)
        rotated = json.loads(secrets_out.read_text()); self.assertEqual(list(rotated['gitWebhookSecrets']), [GIT_A]); self.assertEqual(rotated['gitWebhookIds'], {GIT_A: 'A' * 24})
        rows = {row[0]: row[1:] for row in self.rows('SELECT id,privateKeyEncrypted,webhookSecretEncrypted,pendingHostKeysEncrypted FROM web_git_sources')}
        secret = unpause.pb_decrypt(rows['a' * 15][1], MASTER.encode())['secret']
        self.assertEqual(secret, rotated['gitWebhookSecrets'][GIT_A]); self.assertGreaterEqual(len(secret), 32); self.assertNotEqual(secret, 's' * 48)
        self.assertEqual(rows['b' * 15][1], ''); self.assertEqual({row[0] for row in rows.values()}, {self.deploy_key}); self.assertEqual(rows['a' * 15][2], '')
        self.assertNotIn(secret, json.dumps(audit)); self.assertTrue(json.loads((self.target / 'RECOVERY_PAUSED.json').read_text())['paused'])
        code, audit = self.cli('--disable-git-sources', '--apply'); self.assertEqual(code, 0, audit)  # No master key needed to cut them.
        self.assertEqual(audit['databases']['controller']['gitWebhooksDisabled'], 1); self.assertEqual(self.rows("SELECT count(*) FROM web_git_sources WHERE webhookSecretEncrypted!=''"), [(0,)])
        self.assertEqual({row[0] for row in self.rows('SELECT privateKeyEncrypted FROM web_git_sources')}, {self.deploy_key})
        self.assertFalse(json.loads((self.target / 'RECOVERY_PAUSED.json').read_text())['unpause'][-1]['webhooksRotated'])
        self.assertTrue(json.loads((self.target / 'RECOVERY_PAUSED.json').read_text())['unpause'][-1]['gitWebhooksDisabled'])

    def test_older_backup_without_git_sources_table(self):
        with sqlite3.connect(self.database) as db: db.execute('DROP TABLE web_git_sources')
        secrets_out = self.root / 'old.json'
        code, audit = self.cli('--rotate-webhooks', '--disable-git-sources', '--revoke-ci-tokens', '--master-key-file', str(self.master), '--secrets-out', str(secrets_out), '--apply'); self.assertEqual(code, 0, audit)
        summary = audit['databases']['controller']; self.assertNotIn('gitWebhookSecretsRotated', summary); self.assertNotIn('gitWebhooksDisabled', summary)
        self.assertNotIn(unpause.GIT_KEY_WARNING, audit['warnings']); self.assertEqual(json.loads(secrets_out.read_text())['gitWebhookSecrets'], {})

    def test_apply_restores_exact_pre_pause_values_per_category_without_triggers(self):
        audit_file = self.root / 'audit.json'
        code, audit = self.cli('--health', '--native-smtp', '--databases', 'controller', '--apply', '--audit', str(audit_file))
        self.assertEqual(code, 0); self.assertEqual(audit['mode'], 'apply'); self.assertEqual(audit_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(dict(self.rows('SELECT id,enabled FROM web_app_health')), {'h-on': 1, 'h-off': 0})
        self.assertEqual(self.rows('SELECT enabled FROM web_scheduled_tasks'), [(0,)])  # Not requested: still paused.
        settings = json.loads(self.rows("SELECT value FROM _params WHERE id='settings'")[0][0])
        self.assertTrue(settings['smtp']['enabled']); self.assertEqual(settings['backups']['cron'], ''); self.assertFalse(settings['s3']['enabled'])
        self.assertEqual(self.rows('SELECT count(*) FROM audit'), [(0,)]); self.assertEqual(self.rows("SELECT count(*) FROM sqlite_master WHERE type='trigger'"), [(1,)])
        marker = json.loads((self.target / 'RECOVERY_PAUSED.json').read_text()); self.assertFalse(marker['paused']); self.assertEqual(marker['unpause'][0]['categories'], ['health', 'native-smtp'])
        with self.assertRaises(engine.RecoveryError): probe.verify_restore(self.target, 2)  # No longer an inert drill copy.
        self.cli('--native-backups', '--apps', '--apply')
        settings = json.loads(self.rows("SELECT value FROM _params WHERE id='settings'")[0][0]); self.assertEqual(settings['backups']['cron'], '0 4 * * *'); self.assertTrue(settings['backups']['s3']['enabled'])
        self.assertEqual(dict(self.rows('SELECT id,status FROM web_apps')), {'app-running': 'running', 'app-stopped': 'stopped'})
        self.assertNotIn('smtp.invalid', json.dumps(audit))

    def to_wal_after_native_stop(self):
        # What a clean native start/stop leaves: WAL header, no -wal/-shm sidecars.
        with sqlite3.connect(self.database) as db: db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        for suffix in ('-wal', '-shm'): pathlib.Path(str(self.database) + suffix).unlink(missing_ok=True)
        self.assertTrue(engine.wal_header(self.database))
        with self.assertRaises(sqlite3.OperationalError): sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True).execute('SELECT 1 FROM web_apps').fetchall()

    def test_wal_copy_after_native_proof_dry_runs_and_applies_into_main_file(self):
        self.to_wal_after_native_stop(); before = self.database.read_bytes()
        code, audit = self.cli('--health', '--revoke-ci-tokens'); self.assertEqual(code, 0, audit)
        self.assertEqual(audit['databases']['controller']['health'], 1); self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(self.cli('--health', '--apply')[0], 0)
        for suffix in ('-wal', '-shm'): pathlib.Path(str(self.database) + suffix).unlink(missing_ok=True)  # Moving data.db alone keeps the edit.
        self.assertEqual(dict(self.rows('SELECT id,enabled FROM web_app_health')), {'h-on': 1, 'h-off': 0})

    def test_missing_pause_state_requires_assume_enabled(self):
        (self.target / engine.PAUSE_STATE).unlink()
        code, audit = self.cli('--tasks', '--health', '--apply'); self.assertEqual(code, 0); self.assertTrue(audit['warnings'])
        self.assertEqual(self.rows('SELECT enabled FROM web_scheduled_tasks'), [(0,)])
        self.cli('--health', '--assume-enabled', '--apply'); self.assertEqual(dict(self.rows('SELECT id,enabled FROM web_app_health')), {'h-on': 1, 'h-off': 1})

    def test_restored_copy_hardening_revokes_ci_and_rotates_webhook_secrets_privately(self):
        secrets_out = self.root / 'rotated.json'
        self.assertEqual(self.cli('--rotate-webhooks', '--apply', '--master-key-file', str(self.master))[0], 1)  # --secrets-out required.
        code, audit = self.cli('--revoke-ci-tokens', '--rotate-webhooks', '--master-key-file', str(self.master), '--secrets-out', str(secrets_out), '--apply')
        self.assertEqual(code, 0); self.assertTrue(audit['databases']['controller']['githubWebhookSecretRotated']); self.assertEqual(audit['databases']['controller']['notificationWebhookSecretsRotated'], 1)
        self.assertEqual(dict(self.rows('SELECT id,revokedAt FROM web_ci_tokens'))['ci-revoked'], '2026-01-01 00:00:00.000Z'); self.assertRegex(dict(self.rows('SELECT id,revokedAt FROM web_ci_tokens'))['ci-live'], r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}Z$')
        rotated = json.loads(secrets_out.read_text()); self.assertEqual(secrets_out.stat().st_mode & 0o777, 0o600)
        github = unpause.pb_decrypt(self.rows("SELECT valueEncrypted FROM web_app_settings WHERE key='github'")[0][0], MASTER.encode())
        self.assertEqual(github['webhookSecret'], rotated['githubWebhookSecret']); self.assertNotEqual(github['webhookSecret'], 'original-github-webhook-secret'); self.assertEqual(github['githubAppId'], '1')
        notify = unpause.pb_decrypt(self.rows('SELECT configEncrypted FROM web_notification_settings')[0][0], MASTER.encode())
        self.assertEqual(notify['webhook']['secret'], rotated['notificationWebhookSecrets']['b' * 15]); self.assertEqual(notify['webhook']['url'], 'https://hooks.example.invalid/x')
        self.assertNotIn(rotated['githubWebhookSecret'], json.dumps(audit))
        self.assertTrue(json.loads((self.target / 'RECOVERY_PAUSED.json').read_text())['paused'])  # Hardening alone keeps the copy paused.
        wrong = self.root / 'wrong.key'; wrong.write_text('W' * 32); wrong.chmod(0o600)
        self.assertEqual(self.cli('--rotate-webhooks', '--master-key-file', str(wrong))[0], 1)

    def github_rows(self):
        return {key: unpause.pb_decrypt(value, MASTER.encode()) for key, value in self.rows("SELECT key,valueEncrypted FROM web_app_settings")}

    def test_rotate_webhooks_covers_every_github_connection_and_keeps_meta(self):
        before = self.database.read_bytes(); secrets_out = self.root / 'github.json'
        code, audit = self.cli('--rotate-webhooks', '--master-key-file', str(self.master)); self.assertEqual(code, 0, audit)
        self.assertEqual(self.database.read_bytes(), before)  # Dry-run.
        summary = audit['databases']['controller']; self.assertTrue(summary['githubWebhookSecretRotated']); self.assertEqual(summary['githubWebhookSecretsRotated'], 2)
        code, audit = self.cli('--rotate-webhooks', '--master-key-file', str(self.master), '--secrets-out', str(secrets_out), '--apply'); self.assertEqual(code, 0, audit)
        rotated = json.loads(secrets_out.read_text()); rows = self.github_rows()
        # Principal: unchanged output name, plus the per-connection map (slug = what GitHub shows).
        self.assertEqual(rotated['githubWebhookSecret'], rows['github']['webhookSecret'])
        self.assertEqual(set(rotated['githubWebhookSecrets']), {'default', EXTRA})
        self.assertEqual(rotated['githubWebhookSecrets']['default'], {'slug': '', 'secret': rows['github']['webhookSecret']})
        self.assertEqual(rotated['githubWebhookSecrets'][EXTRA], {'slug': 'kolab-acme', 'secret': rows['github:' + EXTRA]['webhookSecret']})
        self.assertNotIn(rows['github']['webhookSecret'], {'original-github-webhook-secret', rows['github:' + EXTRA]['webhookSecret']})
        self.assertNotEqual(rows['github:' + EXTRA]['webhookSecret'], 'original-acme-webhook-secret')
        # Everything else in the blob is kept: meta, App ids, private key (never rotated offline).
        acme = rows['github:' + EXTRA]
        self.assertEqual(acme['meta'], EXTRA_META); self.assertEqual((acme['githubAppId'], acme['githubAppSlug'], acme['githubInstallationId']), ('2', 'kolab-acme', '22'))
        self.assertEqual(acme['privateKey'], '-----BEGIN RSA PRIVATE KEY-----\nacme\n'); self.assertNotIn('meta', rows['github'])
        for value in (rotated['githubWebhookSecret'], acme['webhookSecret'], 'kolab-acme'): self.assertNotIn(value, json.dumps(audit))
        self.assertEqual(self.rows("SELECT id,key FROM web_app_settings ORDER BY id"), [('gh', 'github'), ('gh-acme', 'github:' + EXTRA)])

    def test_rotate_webhooks_without_extra_connection_behaves_as_before(self):
        with sqlite3.connect(self.database) as db: db.execute("DELETE FROM web_app_settings WHERE key!='github'")
        secrets_out = self.root / 'single.json'
        code, audit = self.cli('--rotate-webhooks', '--master-key-file', str(self.master), '--secrets-out', str(secrets_out), '--apply'); self.assertEqual(code, 0, audit)
        summary = audit['databases']['controller']; self.assertTrue(summary['githubWebhookSecretRotated']); self.assertEqual(summary['githubWebhookSecretsRotated'], 1)
        rotated = json.loads(secrets_out.read_text()); github = self.github_rows()['github']
        self.assertEqual(rotated['githubWebhookSecret'], github['webhookSecret']); self.assertEqual(list(rotated['githubWebhookSecrets']), ['default'])
        self.assertEqual(github, {'githubAppId': '1', 'webhookSecret': rotated['githubWebhookSecret']})  # No meta added.

    def test_rotate_webhooks_skips_foreign_keys_and_works_without_principal(self):
        foreign = unpause.pb_encrypt({'webhookSecret': 'foreign-secret'}, MASTER.encode())
        with sqlite3.connect(self.database) as db:
            db.execute("DELETE FROM web_app_settings WHERE key='github'")
            db.executemany('INSERT INTO web_app_settings VALUES(?,?,?)', [('bad1', 'github:SHORT', foreign), ('bad2', 'github:' + 'A' * 15, foreign), ('bad3', 'GITHUB:' + 'a' * 15, foreign), ('bad4', 'github:' + 'a' * 16, foreign)])
            db.execute("INSERT INTO web_app_settings VALUES('empty','github:" + 'e' * 15 + "','')")  # Not configured: skipped, never decrypted.
        secrets_out = self.root / 'extra-only.json'
        code, audit = self.cli('--rotate-webhooks', '--master-key-file', str(self.master), '--secrets-out', str(secrets_out), '--apply'); self.assertEqual(code, 0, audit)
        summary = audit['databases']['controller']; self.assertNotIn('githubWebhookSecretRotated', summary); self.assertEqual(summary['githubWebhookSecretsRotated'], 1)
        rotated = json.loads(secrets_out.read_text()); self.assertIsNone(rotated['githubWebhookSecret']); self.assertEqual(list(rotated['githubWebhookSecrets']), [EXTRA])
        self.assertEqual({row[0] for row in self.rows("SELECT valueEncrypted FROM web_app_settings WHERE id LIKE 'bad%'")}, {foreign})
        self.assertEqual(self.rows("SELECT valueEncrypted FROM web_app_settings WHERE id='empty'"), [('',)])

    def test_github_connection_under_another_master_key_aborts_without_writing(self):
        other = unpause.pb_encrypt({'webhookSecret': 'x'}, b'O' * 32)
        with sqlite3.connect(self.database) as db: db.execute("UPDATE web_app_settings SET valueEncrypted=? WHERE key!='github'", (other,))
        before = self.database.read_bytes(); secrets_out = self.root / 'never.json'
        code, error = self.cli('--rotate-webhooks', '--master-key-file', str(self.master), '--secrets-out', str(secrets_out), '--apply')
        self.assertEqual(code, 1); self.assertIn('clé maître', error['error']); self.assertEqual(self.database.read_bytes(), before); self.assertFalse(secrets_out.exists())


if __name__ == '__main__': unittest.main()
