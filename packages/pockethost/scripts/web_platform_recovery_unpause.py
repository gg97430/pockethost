#!/usr/bin/env python3
"""Reverse the restore pause for a REAL server loss, one explicit category at a time.

Dry-run by default: nothing is written without --apply. Works on a restored directory
(RECOVERY_PAUSED.json + recovery-pause-state.json), before its databases are moved into
DATA_ROOT and while no PocketBase uses them. The pause state recorded by the engine gives
the exact pre-pause values; restores made before that state existed need --assume-enabled.

Categories: --health --tasks --backups --notifications --previews --apps
            --native-smtp --native-s3 --native-backups   (or --all)
Restored-copy hardening: --revoke-ci-tokens, --rotate-webhooks --master-key-file F --secrets-out F
            (every GitHub App connection: key 'github' and 'github:<id>'),
            --disable-git-sources (Git deploy-key webhooks answer 401; no master key needed)
Git deploy keys are never rotated offline: « Régénérer la clé » per repository after an incident.
Audit: JSON on stdout, and --audit FILE (new file, mode 0600). No secret value is printed.
"""
import argparse
import base64
import datetime
import json
import os
import pathlib
import secrets
import sqlite3
import sys

import web_platform_recovery as engine

TABLE_CATEGORIES = {'health': ('web_app_health', 'enabled'), 'tasks': ('web_scheduled_tasks', 'enabled'), 'backups': ('web_app_backup_settings', 'enabled'), 'notifications': ('web_notification_settings', 'enabled'), 'previews': ('web_projects', 'previewEnabled')}
NATIVE_CATEGORIES = ('native-smtp', 'native-s3', 'native-backups')
CATEGORIES = (*TABLE_CATEGORIES, 'apps', *NATIVE_CATEGORIES)
# GitHub App connections in web_app_settings: 'github' = principal ('default'), 'github:<15 [a-z0-9]>' = extra ones.
GITHUB_SETTINGS_KEY = engine.GITHUB_SETTINGS_KEY
GIT_KEY_WARNING = 'Les clés de déploiement Git restent valides chez les hébergeurs tant qu’elles ne sont pas remplacées : utilisez « Régénérer la clé » (POST /api/web-git-sources/{id}/rotate-key) puis retirez l’ancienne clé chez l’hébergeur.'


def pb_date(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.strftime('%Y-%m-%d %H:%M:%S.') + '%03dZ' % (now.microsecond // 1000)


def master_key(filename):
    value = engine.private_file(filename).read_text().strip()
    if len(value) != 32 or any(not 0x21 <= ord(char) <= 0x7e for char in value): raise engine.RecoveryError('Clé maître Applications web attendue : 32 caractères imprimables dans un fichier privé.')
    return value.encode()


def pb_decrypt(value, key):
    # PocketBase $security.encrypt: base64(nonce12 || AES-256-GCM(ciphertext||tag)).
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        raw = base64.b64decode(value, validate=True)
        return json.loads(AESGCM(key).decrypt(raw[:12], raw[12:], None))
    except Exception: raise engine.RecoveryError('Une valeur chiffrée ne peut pas être ouverte avec cette clé maître.') from None


def pb_encrypt(value, key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    return base64.b64encode(nonce + AESGCM(key).encrypt(nonce, json.dumps(value, separators=(',', ':')).encode(), None)).decode()


def github_connection_id(key):
    """'github' -> 'default', 'github:<id>' -> '<id>', anything else -> None (never rotated)."""
    if not isinstance(key, str) or not GITHUB_SETTINGS_KEY.fullmatch(key): return None
    return 'default' if key == 'github' else key[len('github:'):]


def columns_of(connection, table):
    return {row[1] for row in connection.execute('PRAGMA table_info("' + table.replace('"', '""') + '")')}


def native_changes(connection, wanted, saved_rows, assume, warnings, ident):
    result = []
    saved = {(item.get('column'), item.get('name')): item for item in saved_rows or [] if isinstance(item, dict)}
    for column, name, raw in engine.settings_records(connection):
        try: value = json.loads(raw)
        except Exception: warnings.append(ident + ' : paramètres natifs illisibles, non modifiés.'); continue
        original = saved.get((column, name))
        if original is None and not assume: warnings.append(ident + ' : état natif d’origine absent ; utilisez --assume-enabled.'); continue
        guess = lambda section, field: bool(isinstance(value.get(section), dict) and value[section].get(field))
        flags = original or {'smtp': guess('smtp', 'host'), 's3': guess('s3', 'bucket'), 'filesystemS3': None, 'backupsCron': None, 'backupsS3': bool(isinstance(value.get('backups'), dict) and isinstance(value['backups'].get('s3'), dict) and value['backups']['s3'].get('bucket'))}
        updated, applied = json.loads(json.dumps(value)), []
        def setflag(path, flag, label):
            node = updated
            for part in path[:-1]: node = node.get(part) if isinstance(node, dict) else None
            if isinstance(node, dict) and flag is not None and node.get(path[-1]) != flag: node[path[-1]] = flag; applied.append(label)
        if 'native-smtp' in wanted: setflag(['smtp', 'enabled'], flags.get('smtp'), 'smtp')
        if 'native-s3' in wanted: setflag(['s3', 'enabled'], flags.get('s3'), 's3'); setflag(['filesystem', 's3', 'enabled'], flags.get('filesystemS3'), 'filesystemS3')
        if 'native-backups' in wanted:
            if flags.get('backupsCron') is None: warnings.append(ident + ' : planification native des sauvegardes inconnue, à saisir manuellement.')
            setflag(['backups', 'cron'], flags.get('backupsCron'), 'backupsCron'); setflag(['backups', 's3', 'enabled'], flags.get('backupsS3'), 'backupsS3')
        if applied: result.append({'column': column, 'name': name, 'value': json.dumps(updated) if isinstance(raw, str) else json.dumps(updated).encode(), 'fields': applied})
    return result


def plan_database(connection, ident, wanted, state, assume, warnings):
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    changes = {}
    for category in wanted:
        if category not in TABLE_CATEGORIES: continue
        table, column = TABLE_CATEGORIES[category]
        if table not in tables or not {'id', column} <= columns_of(connection, table): continue
        saved = (state.get('tables') or {}).get(table) or {}
        existing = {row[0] for row in connection.execute(f'SELECT id FROM "{table}"')}
        if isinstance(saved.get('enabledIds'), list): ids = sorted(existing & set(saved['enabledIds']))
        elif assume: ids = sorted(existing)
        else: warnings.append(f'{ident} : état d’origine absent pour {category} ; utilisez --assume-enabled.'); continue
        changes[category] = {'table': table, 'column': column, 'ids': ids}
    if 'apps' in wanted and 'web_apps' in tables:
        saved = state.get('webAppsStatus')
        if not isinstance(saved, dict): warnings.append(ident + ' : statuts d’origine des applications absents ; aucune application relancée.')
        else:
            current = dict(connection.execute('SELECT id,status FROM web_apps').fetchall())
            changes['apps'] = {'statuses': {app: status for app, status in saved.items() if app in current and isinstance(status, str) and status != current[app]}}
    native = [category for category in wanted if category in NATIVE_CATEGORIES]
    if native: changes['native'] = native_changes(connection, native, state.get('nativeSettings'), assume, warnings, ident)
    return changes


def hardening(connection, revoke, rotate, key, disable_git=False, warnings=None, now=None):
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    result = {'ciTokens': [], 'github': [], 'notifications': {}, 'gitSources': [], 'disableGit': bool(disable_git)}
    warnings = [] if warnings is None else warnings
    if 'web_ci_tokens' in tables and {'id', 'revokedAt'} <= columns_of(connection, 'web_ci_tokens'):
        active = [row[0] for row in connection.execute("SELECT id FROM web_ci_tokens WHERE coalesce(revokedAt,'')=''")]
        if revoke: result['ciTokens'] = active
        elif active:
            # Expired tokens are already refused by the controller; only usable ones deserve the warning.
            usable = connection.execute("SELECT count(*) FROM web_ci_tokens WHERE coalesce(revokedAt,'')='' AND (coalesce(expiresAt,'')='' OR expiresAt>?)", (pb_date(now),)).fetchone()[0] if 'expiresAt' in columns_of(connection, 'web_ci_tokens') else len(active)
            if usable: warnings.append(f'{usable} jeton(s) CI encore valide(s) : une copie clonée accepterait leurs déploiements ; utilisez --revoke-ci-tokens.')
    git = 'web_git_sources' in tables and {'id', 'canonical', 'webhookId', 'webhookSecretEncrypted'} <= columns_of(connection, 'web_git_sources')
    if git:
        # Only the new secret matters: the old one is never decrypted, so a foreign master key is not needed to disable.
        if rotate or disable_git:
            result['gitSources'] = connection.execute("SELECT id,canonical,webhookId FROM web_git_sources WHERE coalesce(webhookSecretEncrypted,'')!='' ORDER BY canonical,id").fetchall()
        if 'privateKeyEncrypted' in columns_of(connection, 'web_git_sources') and connection.execute("SELECT count(*) FROM web_git_sources WHERE coalesce(privateKeyEncrypted,'')!=''").fetchone()[0]:
            warnings.append(GIT_KEY_WARNING)
    if rotate:
        if 'web_app_settings' in tables:
            # Every connection, principal first. The whole decrypted dict is re-encrypted, so 'meta' is kept.
            rows = connection.execute("SELECT id,key,valueEncrypted FROM web_app_settings WHERE key='github' OR key LIKE 'github:%' ORDER BY key").fetchall()
            for ident, name, raw in rows:
                connection_id = github_connection_id(name)
                if connection_id is None or not raw: continue
                value = pb_decrypt(raw, key)
                if value.get('webhookSecret'): result['github'].append((ident, connection_id, value))
        if 'web_notification_settings' in tables and 'configEncrypted' in columns_of(connection, 'web_notification_settings'):
            for ident, app, raw in connection.execute('SELECT id,appId,configEncrypted FROM web_notification_settings'):
                if not raw: continue
                value = pb_decrypt(raw, key)
                if isinstance(value.get('webhook'), dict) and value['webhook'].get('url'): result['notifications'][ident] = (app, value)
    return result


def apply(connection, changes, hard, key, now):
    secrets_out = {'notificationWebhookSecrets': {}, 'githubWebhookSecrets': {}}
    connection.isolation_level = None
    connection.execute('BEGIN IMMEDIATE')
    try:
        # Same rule as the pause: application SQL triggers never fire on offline edits.
        triggers = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND sql IS NOT NULL").fetchall()
        for name, _ in triggers: connection.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
        for category, change in changes.items():
            if category in TABLE_CATEGORIES:
                connection.executemany(f'UPDATE "{change["table"]}" SET "{change["column"]}"=1 WHERE id=?', [(ident,) for ident in change['ids']])
            elif category == 'apps':
                connection.executemany('UPDATE web_apps SET status=? WHERE id=?', [(status, app) for app, status in change['statuses'].items()])
            elif category == 'native':
                for item in change: connection.execute('UPDATE _params SET value=? WHERE "' + item['column'] + '"=?', (item['value'], item['name']))
        if hard['ciTokens']: connection.executemany('UPDATE web_ci_tokens SET revokedAt=? WHERE id=?', [(pb_date(now), ident) for ident in hard['ciTokens']])
        for ident, connection_id, value in hard['github']:
            value['webhookSecret'] = secrets.token_hex(32)
            # Keyed by connection id, with the App slug: what the operator recognizes on GitHub.
            secrets_out['githubWebhookSecrets'][connection_id] = {'slug': str(value.get('githubAppSlug') or ''), 'secret': value['webhookSecret']}
            if connection_id == 'default': secrets_out['githubWebhookSecret'] = value['webhookSecret']  # Principal: unchanged output name.
            connection.execute('UPDATE web_app_settings SET valueEncrypted=? WHERE id=?', (pb_encrypt(value, key), ident))
        for ident, (app, value) in hard['notifications'].items():
            value['webhook']['secret'] = secrets.token_hex(32); secrets_out['notificationWebhookSecrets'][app] = value['webhook']['secret']
            connection.execute('UPDATE web_notification_settings SET configEncrypted=? WHERE id=?', (pb_encrypt(value, key), ident))
        for ident, canonical, webhook in hard['gitSources']:
            # Keyed by repository: that is what the operator recognizes at the Git host.
            if hard['disableGit']:
                connection.execute("UPDATE web_git_sources SET webhookSecretEncrypted='' WHERE id=?", (ident,))
                secrets_out.setdefault('gitWebhooksDisabled', []).append(canonical)
            else:
                secret = secrets.token_urlsafe(36)[:48]  # The controller refuses secrets shorter than 32 characters.
                connection.execute('UPDATE web_git_sources SET webhookSecretEncrypted=? WHERE id=?', (pb_encrypt({'secret': secret}, key), ident))
                secrets_out.setdefault('gitWebhookSecrets', {})[canonical] = secret
                secrets_out.setdefault('gitWebhookIds', {})[canonical] = webhook
        if hard['gitSources'] and 'pendingHostKeysEncrypted' in columns_of(connection, 'web_git_sources'):
            connection.execute("UPDATE web_git_sources SET pendingHostKeysEncrypted='' WHERE coalesce(pendingHostKeysEncrypted,'')!=''")  # In-flight host-key scans.
        for _, sql in triggers: connection.execute(sql)
        connection.execute('COMMIT')
    except BaseException:
        connection.execute('ROLLBACK'); raise
    # A WAL copy must not keep the edit only in -wal: the copy is moved as data.db alone.
    if connection.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal' and connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] != 0: raise engine.RecoveryError('Point de contrôle WAL impossible : arrêtez tout service qui utilise la copie.')
    return secrets_out


def write_new_private(filename, value):
    path = pathlib.Path(filename).absolute()
    if path.parent.resolve() != path.parent: raise engine.RecoveryError('Dossier de sortie non canonique.')
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as target:
        os.fchmod(target.fileno(), 0o600); json.dump(value, target, sort_keys=True); target.flush(); os.fsync(target.fileno())


def summarize(changes, hard):
    output = {}
    for category, change in changes.items():
        if category in TABLE_CATEGORIES: output[category] = len(change['ids'])
        elif category == 'apps': output['apps'] = len(change['statuses'])
        elif category == 'native': output['native'] = sorted({field for item in change for field in item['fields']})
    if hard['ciTokens']: output['ciTokensRevoked'] = len(hard['ciTokens'])
    if hard['github']:
        if any(connection_id == 'default' for _, connection_id, _ in hard['github']): output['githubWebhookSecretRotated'] = True  # Principal.
        output['githubWebhookSecretsRotated'] = len(hard['github'])  # Every connection, principal included.
    if hard['notifications']: output['notificationWebhookSecretsRotated'] = len(hard['notifications'])
    if hard['gitSources']: output['gitWebhooksDisabled' if hard['disableGit'] else 'gitWebhookSecretsRotated'] = len(hard['gitSources'])
    return output


def run(args):
    root = pathlib.Path(args.root).absolute()
    if root.resolve() != root or not root.is_dir() or root.stat().st_mode & 0o077: raise engine.RecoveryError('Restauration privée canonique requise.')
    marker = json.loads(engine.private_file(root / 'RECOVERY_PAUSED.json').read_bytes())
    manifest = json.loads(engine.private_file(root / 'recovery-manifest.json').read_bytes())
    if marker.get('backupId') != manifest.get('backupId'): raise engine.RecoveryError('Marqueur et manifeste de restauration incohérents.')
    try: pause_state = json.loads(engine.private_file(root / engine.PAUSE_STATE).read_bytes())
    except FileNotFoundError: pause_state = {}
    restored = set(marker.get('databasesRestored', []))
    selected = restored if args.databases in (None, 'all') else set(args.databases.split(','))
    if not selected or selected - restored: raise engine.RecoveryError('Sélection de bases absente de cette restauration.')
    wanted = [category for category in CATEGORIES if args.all or getattr(args, category.replace('-', '_'))]
    rotate, revoke, disable_git = args.rotate_webhooks, args.revoke_ci_tokens, args.disable_git_sources
    if not wanted and not rotate and not revoke and not disable_git: raise engine.RecoveryError('Aucune catégorie demandée : rien à réactiver.')
    key = master_key(args.master_key_file) if rotate else None
    if rotate and args.apply and not args.secrets_out: raise engine.RecoveryError('La rotation exige --secrets-out pour transmettre les nouveaux secrets.')
    if (args.apply and rotate) and os.path.lexists(args.secrets_out): raise engine.RecoveryError('Le fichier --secrets-out doit être nouveau.')
    now = datetime.datetime.now(datetime.timezone.utc); warnings = []; audit = {'mode': 'apply' if args.apply else 'dry-run', 'backupId': marker.get('backupId'), 'at': engine.stamp(), 'categories': wanted, 'databases': {}, 'warnings': warnings}
    definitions = {item['id']: item for item in manifest.get('databases', []) if item.get('id') in selected}
    collected = {}
    for ident in sorted(definitions):
        filename = root / engine.safe_relative(definitions[ident]['target']) / 'data.db'
        if filename.resolve() != filename or not filename.is_file(): raise engine.RecoveryError('Base restaurée absente ou symbolique.')
        # The native proof leaves copies in WAL mode: a dry-run must still open them (query_only).
        connection = engine.connect_copy(filename, write=args.apply, timeout=1)
        try:
            state = (pause_state.get('databases') or {}).get(ident) or {}
            if not state and not args.assume_enabled: warnings.append(ident + ' : aucun état de pause enregistré (restauration antérieure).')
            changes = plan_database(connection, ident, wanted, state, args.assume_enabled, warnings)
            if ident == 'controller': hard = hardening(connection, revoke, rotate, key, disable_git, warnings, now)
            else: hard = {'ciTokens': [], 'github': [], 'notifications': {}, 'gitSources': [], 'disableGit': disable_git}
            pending = any(change['ids'] if category in TABLE_CATEGORIES else change['statuses'] if category == 'apps' else change for category, change in changes.items()) or hard['ciTokens'] or hard['github'] or hard['notifications'] or hard['gitSources']
            if args.apply and pending: collected[ident] = apply(connection, changes, hard, key, now)
            audit['databases'][ident] = summarize(changes, hard)
        finally: connection.close()
    if args.apply:
        controller = collected.get('controller', {})
        rotated = {'githubWebhookSecret': controller.get('githubWebhookSecret'), 'githubWebhookSecrets': controller.get('githubWebhookSecrets', {}), 'notificationWebhookSecrets': controller.get('notificationWebhookSecrets', {}), 'gitWebhookSecrets': controller.get('gitWebhookSecrets', {}), 'gitWebhookIds': controller.get('gitWebhookIds', {})}
        if disable_git: rotated['gitWebhooksDisabled'] = controller.get('gitWebhooksDisabled', [])
        if rotate: write_new_private(args.secrets_out, rotated); audit['secretsWritten'] = True
        if wanted: marker['paused'] = False  # The copy is no longer an inert drill: the probe refuses it.
        marker.setdefault('unpause', []).append({'at': audit['at'], 'categories': wanted, 'databases': sorted(definitions), 'ciTokensRevoked': revoke, 'webhooksRotated': rotate, 'gitWebhooksDisabled': disable_git})
        temporary = root / ('.RECOVERY_PAUSED.json-' + secrets.token_hex(6)); temporary.write_bytes(engine.encoded(marker)); temporary.chmod(0o600); os.replace(temporary, root / 'RECOVERY_PAUSED.json')
    if args.audit: write_new_private(args.audit, audit)
    return audit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', required=True); parser.add_argument('--databases', help='controller,<id>,... (défaut : toutes les bases restaurées)')
    for category in CATEGORIES: parser.add_argument('--' + category, action='store_true')
    parser.add_argument('--all', action='store_true', help='Toutes les catégories (pas la révocation ni la rotation)')
    parser.add_argument('--assume-enabled', action='store_true', help='Sans état de pause : tout réactiver (sauf planification native inconnue)')
    parser.add_argument('--revoke-ci-tokens', action='store_true'); parser.add_argument('--rotate-webhooks', action='store_true'); parser.add_argument('--master-key-file'); parser.add_argument('--secrets-out')
    parser.add_argument('--disable-git-sources', action='store_true', help='Couper les webhooks des dépôts Git (clé de déploiement) au lieu de les renouveler')
    parser.add_argument('--apply', action='store_true', help='Écrire réellement (sinon simulation)'); parser.add_argument('--audit')
    args = parser.parse_args(argv)
    try:
        if args.rotate_webhooks and not args.master_key_file: raise engine.RecoveryError('La rotation exige --master-key-file.')
        print(json.dumps(run(args))); return 0
    except engine.RecoveryError as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}), file=sys.stderr); return 1
    except sqlite3.Error:
        print(json.dumps({'status': 'failed', 'error': 'Base restaurée verrouillée ou illisible : arrêtez tout service qui l’utilise.'}), file=sys.stderr); return 1


if __name__ == '__main__': sys.exit(main())
