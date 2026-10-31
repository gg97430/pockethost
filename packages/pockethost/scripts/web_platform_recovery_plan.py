#!/usr/bin/env python3
"""Private recovery plan built from the live inventory, and drift check.

Read-only on production paths: the controller SQLite is opened read-only (query_only)
and the only file ever written is the target plan (--output, mode 0600, atomic).

  generate --base <plan privé> [--output <nouveau plan>] [--data-root DIR] [--allow-without-database]
  check    --plan <plan privé> [--data-root DIR]        # exit 3 when the plan has drifted

hostPaths, nativeBackups, static s3Refs, metadata and options of the base plan are kept;
controller and instances are recomputed from DATA_ROOT/mothership/pb_data/data.db and
DATA_ROOT/instances/<id>/pb_data (PocketHost layout, constants.ts).
"""
import argparse
import json
import os
import pathlib
import secrets
import sqlite3
import stat
import sys

import web_platform_recovery as engine

KEPT_INSTANCE_FIELDS = ('storage', 'storageMode', 'initializeWalIndex')
NATIVE_BINARIES = 'platform/pocketbase'


def canonical_dir(value, message):
    path = pathlib.Path(value).absolute()
    if path.resolve() != path or not path.is_dir(): raise engine.RecoveryError(message)
    return path


def data_root_of(plan, override=None):
    if override: return canonical_dir(override, 'Racine des données PocketHost absente ou non canonique.')
    try: controller = pathlib.Path(plan['controller']['database']).absolute()
    except Exception: raise engine.RecoveryError('Indiquez --data-root : le plan ne décrit pas le contrôleur.') from None
    if controller.name != 'data.db' or controller.parent.name != 'pb_data' or controller.parent.parent.name != 'mothership':
        raise engine.RecoveryError('Indiquez --data-root : la base du contrôleur ne suit pas la disposition PocketHost.')
    return canonical_dir(controller.parents[2], 'Racine des données PocketHost absente ou non canonique.')


def read_plan(filename):
    try: plan = json.loads(engine.private_file(filename).read_bytes())
    except engine.RecoveryError: raise
    except Exception: raise engine.RecoveryError('Plan privé de reprise illisible.') from None
    if not isinstance(plan, dict) or plan.get('version') != 1 or not isinstance(plan.get('instances', []), list): raise engine.RecoveryError('Plan privé de reprise invalide.')
    return plan


def inventory(controller_db):
    path = pathlib.Path(controller_db).absolute()
    if path.resolve() != path or not path.is_file(): raise engine.RecoveryError('Base du contrôleur absente ou symbolique.')
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
    try:
        connection.execute('PRAGMA query_only=ON')
        columns = {row[1] for row in connection.execute('PRAGMA table_info(instances)')}
        if 'id' not in columns: raise engine.RecoveryError('Le contrôleur ne contient pas la table des instances.')
        rows = connection.execute('SELECT id,' + ('version' if 'version' in columns else "''") + ' FROM instances ORDER BY id').fetchall()
    except sqlite3.Error: raise engine.RecoveryError('Lecture de l’inventaire du contrôleur impossible.') from None
    finally: connection.close()
    if len(rows) > 1000 or any(not engine.ID.fullmatch(str(ident)) for ident, _ in rows): raise engine.RecoveryError('Inventaire des instances invalide.')
    return [{'id': ident, 'version': version if isinstance(version, str) else ''} for ident, version in rows]


def instance_paths(data_root, ident):
    folder = pathlib.Path(data_root) / 'instances' / ident / 'pb_data'
    return folder / 'data.db', folder / 'storage'


def controller_database(plan, data_root):
    configured = (plan.get('controller') or {}).get('database')
    return pathlib.Path(configured).absolute() if isinstance(configured, str) and configured else pathlib.Path(data_root) / 'mothership/pb_data/data.db'


def build_plan(base, data_root, allow_without_database=False, platform=None):
    """Return (plan, report). The plan keeps every non-inventory field of the base plan."""
    data_root = pathlib.Path(data_root)
    plan = {key: value for key, value in base.items() if key not in ('backupId', 'instances', 'capturedS3Refs')}
    plan['version'] = 1; plan['platform'] = platform or base.get('platform') or 'app1'
    controller = controller_database(base, data_root)
    plan['controller'] = {**(base.get('controller') or {}), 'database': str(controller)}
    plan['controller'].setdefault('storage', str(controller.parent / 'storage'))
    previous = {item.get('id'): item for item in base.get('instances', []) if isinstance(item, dict)}
    live = inventory(controller); instances, without = [], []
    for row in live:
        database, storage = instance_paths(data_root, row['id'])
        entry = {'storage': str(storage), **{key: previous[row['id']][key] for key in KEPT_INSTANCE_FIELDS if key in previous.get(row['id'], {})}, 'id': row['id'], 'database': str(database)}
        if not database.is_file(): entry['withoutDatabase'] = True; without.append(row['id'])
        instances.append(entry)
    if without and not allow_without_database: raise engine.RecoveryError('Des instances n’ont pas encore de base SQLite ; relancez avec --allow-without-database après vérification.')
    plan['instances'] = instances
    exclusions = engine.validate_file_reference_exclusions(plan.get('fileReferenceExclusions'), {row['id'] for row in live})
    if exclusions: plan['fileReferenceExclusions'] = exclusions
    plan['metadata'] = {**(base.get('metadata') or {}), 'generatedAt': engine.stamp(), 'generator': 'web_platform_recovery_plan', 'instanceVersions': {row['id']: row['version'] for row in live}}
    report = {'platform': plan['platform'], 'instances': len(instances), 'added': sorted(set(row['id'] for row in live) - set(previous)), 'removed': sorted(set(previous) - set(row['id'] for row in live)), 'withoutDatabase': without, 'nativeBinariesCaptured': any(item.get('target') == NATIVE_BINARIES for item in plan.get('hostPaths', []))}
    return plan, report


def drift(plan, data_root):
    live = {row['id'] for row in inventory(controller_database(plan, data_root))}
    planned = {item.get('id'): item for item in plan.get('instances', []) if isinstance(item, dict)}
    missing = sorted(ident for ident, item in planned.items() if ident in live and item.get('withoutDatabase') is not True and not pathlib.Path(item.get('database', '')).is_file())
    added, removed = sorted(live - set(planned)), sorted(set(planned) - live)
    mismatched = sorted(ident for ident, item in planned.items() if ident in live and pathlib.Path(item.get('database', '')).absolute() != instance_paths(data_root, ident)[0])
    warnings = [] if any(item.get('target') == NATIVE_BINARIES for item in plan.get('hostPaths', [])) else ['Les binaires PocketBase (hostPaths « platform/pocketbase ») ne sont pas capturés : la preuve native sera impossible.']
    return {'checked': True, 'drift': bool(added or removed or missing), 'added': added, 'removed': removed, 'databaseMissing': missing, 'pathMismatch': mismatched, 'liveInstances': len(live), 'plannedInstances': len(planned), 'warnings': warnings, 'checkedAt': engine.stamp()}


def write_private(filename, value, data_root=None):
    path = pathlib.Path(filename).absolute()
    if path.parent.resolve() != path.parent or not path.parent.is_dir(): raise engine.RecoveryError('Dossier du plan absent ou non canonique.')
    if data_root and (pathlib.Path(data_root) == path.parent or pathlib.Path(data_root) in path.parents): raise engine.RecoveryError('Le plan ne doit jamais être écrit dans les données de production.')
    if os.path.lexists(path):
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.geteuid()): raise engine.RecoveryError('Plan cible existant non régulier ou étranger.')
    raw = json.dumps(value, indent=1, sort_keys=True).encode() + b'\n'
    temporary = path.with_name('.' + path.name + '-' + secrets.token_hex(8))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as target:
            os.fchmod(target.fileno(), 0o600); target.write(raw); target.flush(); os.fsync(target.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if temporary.exists(): temporary.unlink()
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter); sub = parser.add_subparsers(dest='operation', required=True)
    generate = sub.add_parser('generate'); generate.add_argument('--base'); generate.add_argument('--output'); generate.add_argument('--data-root'); generate.add_argument('--platform'); generate.add_argument('--allow-without-database', action='store_true')
    check = sub.add_parser('check'); check.add_argument('--plan', required=True); check.add_argument('--data-root')
    args = parser.parse_args(argv)
    try:
        if args.operation == 'check':
            plan = read_plan(args.plan); result = drift(plan, data_root_of(plan, args.data_root))
            print(json.dumps(result)); return 3 if result['drift'] else 0
        if not args.base and not (args.data_root and args.platform): raise engine.RecoveryError('Sans plan de base, --data-root et --platform sont requis.')
        base = read_plan(args.base) if args.base else {'version': 1}
        data_root = data_root_of(base, args.data_root)
        plan, report = build_plan(base, data_root, args.allow_without_database, args.platform)
        if args.output:
            if args.base and pathlib.Path(args.output).absolute() == pathlib.Path(args.base).absolute(): report['replacedBase'] = True
            write_private(args.output, plan, data_root); report['written'] = True
        else: report['written'] = False
        print(json.dumps(report)); return 0
    except engine.RecoveryError as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}), file=sys.stderr); return 2


if __name__ == '__main__': sys.exit(main())
