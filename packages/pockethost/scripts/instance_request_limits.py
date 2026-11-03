#!/usr/bin/env python3
"""Native settings via a one-use dashboard grant and one pinned TCP connection.

No passwords, native bearer tokens, unrelated settings or raw errors leave this
process. Reconnecting after a Docker port reassignment is deliberately forbidden.
"""
import contextlib
import fcntl
import hashlib
import http.client
import json
import os
import pathlib
import re
import stat
import subprocess
import sys

ID = re.compile(r'[a-z0-9]{15}\Z')
HEX = re.compile(r'[a-f0-9]{64}\Z')


class SettingsError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def revision(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate_input(value):
    if not isinstance(value, dict) or set(value) != {'enabled', 'rules', 'revision'}:
        raise SettingsError('Configuration PocketBase invalide.')
    if type(value['enabled']) is not bool or not isinstance(value['revision'], str) or not HEX.fullmatch(value['revision']):
        raise SettingsError('État ou révision invalide.')
    rules = value['rules']
    if not isinstance(rules, list) or len(rules) > 50:
        raise SettingsError('Maximum 50 règles par base.')
    for rule in rules:
        if (not isinstance(rule, dict) or set(rule) != {'label', 'audience', 'duration', 'maxRequests'}
                or not isinstance(rule['label'], str) or not 1 <= len(rule['label']) <= 100
                or re.search(r'[\x00-\x1f\x7f]', rule['label'])
                or rule['audience'] not in ('', '@guest', '@auth')
                or type(rule['duration']) is not int or not 1 <= rule['duration'] <= 86400
                or type(rule['maxRequests']) is not int or not 1 <= rule['maxRequests'] <= 1000000):
            raise SettingsError('Règle invalide : cible, audience, durée et nombre de requêtes attendus.')
    return value


def inspect_database(config, runner=subprocess.run):
    ident = config.get('instance')
    if not isinstance(ident, str) or not ID.fullmatch(ident):
        raise SettingsError('Identifiant de base invalide.')
    r = runner(['docker', '--host', 'unix:///var/run/docker.sock', 'inspect', '--type', 'container', ident],
               capture_output=True, text=True, check=True, timeout=3)
    rows = json.loads(r.stdout)
    if len(rows) != 1:
        raise SettingsError('Base introuvable.')
    row = rows[0]
    cfg, state, host = row.get('Config') or {}, row.get('State') or {}, row.get('HostConfig') or {}
    cid, started = row.get('Id'), state.get('StartedAt')
    expected = pathlib.Path(config['dataRoot']) / 'instances' / ident
    image = (config.get('databaseImages') or {}).get(ident)
    reference = cfg.get('Image', '')
    allowed_image = reference == image and row.get('Image') == image if image else (
        reference == config['databaseImage'] or reference.startswith(config['databaseImage'] + ':')
        or reference.startswith(config['databaseImage'] + '@'))
    env = dict(v.split('=', 1) for v in cfg.get('Env') or [] if '=' in v)
    bindings = (row.get('NetworkSettings') or {}).get('Ports', {}).get('8090/tcp') or []
    if (row.get('Name') != '/' + ident or not isinstance(cid, str) or not HEX.fullmatch(cid)
            or not state.get('Running') or not started or not allowed_image or host.get('Privileged')
            or env.get('PH_INSTANCE_ID') != ident or expected.resolve() != expected
            or not any(m.get('Destination') == '/home/pockethost' and m.get('Type') == 'bind'
                       and m.get('Source') == str(expected) for m in row.get('Mounts') or [])
            or len(bindings) != 1 or bindings[0].get('HostIp') != '127.0.0.1'
            or not re.fullmatch(r'[0-9]{1,5}', str(bindings[0].get('HostPort', '')))):
        raise SettingsError('Identité ou publication locale de la base non vérifiable.')
    port = int(bindings[0]['HostPort'])
    if not 1024 <= port <= 65535:
        raise SettingsError('Port de base invalide.')
    return cid, started, port


def call(connection, method, path, body=None, token=''):
    # auto_open=0 prevents http.client from reconnecting to a reused HostPort.
    if connection.sock is None:
        raise SettingsError('Connexion interrompue. Rechargez les paramètres.')
    connection.request(method, path, None if body is None else json.dumps(body),
                       {'Content-Type': 'application/json', **({'Authorization': token} if token else {})})
    r = connection.getresponse()
    raw = r.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024 or r.status != 200:
        raise SettingsError('Paramètres inaccessibles. Vérifiez les hooks et la disponibilité de cette base.')
    return json.loads(raw)


def execute(config, inspect=inspect_database, connect=http.client.HTTPConnection):
    action = config.get('action')
    if action not in ('read', 'update') or not re.fullmatch(r'[A-Za-z0-9]{64}', config.get('ticket', '')):
        raise SettingsError('Autorisation invalide.')
    proposed = validate_input(config.get('input')) if action == 'update' else None
    identity = inspect(config)
    with contextlib.closing(connect('127.0.0.1', identity[2], timeout=15)) as connection:
        connection.connect()
        connection.auto_open = 0
        if inspect(config) != identity:
            raise SettingsError('La base a redémarré. Rechargez les paramètres.')
        auth = call(connection, 'POST', '/api/ph-console/login', {'ticket': config['ticket']})
        token = auth.get('token')
        if not isinstance(token, str) or not token:
            raise SettingsError('Autorisation native indisponible.')
        settings = call(connection, 'GET', '/api/settings', token=token)
        current = settings.get('rateLimits')
        if not isinstance(current, dict) or type(current.get('enabled')) is not bool or not isinstance(current.get('rules'), list):
            raise SettingsError('Cette version de PocketBase ne fournit pas ce limiteur.')
        if proposed:
            if revision(current) != proposed['revision']:
                raise SettingsError('Ces règles ont été modifiées. Rechargez avant d’enregistrer.', 409)
            updated = {**current, 'enabled': proposed['enabled'], 'rules': proposed['rules']}
            call(connection, 'PATCH', '/api/settings', {'rateLimits': updated}, token)
            verified = call(connection, 'GET', '/api/settings', token=token)
            if verified.get('rateLimits') != updated:
                raise SettingsError('Enregistrement non confirmé. Rechargez les paramètres.')
            current = verified['rateLimits']
        return {'enabled': current['enabled'], 'rules': current['rules'],
                'excludedIPCount': len(current.get('excludedIPs') or []), 'revision': revision(current)}


def main():
    fd = os.open(sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600 or st.st_size > 65536:
            raise SettingsError('Requête locale invalide.')
        config = json.load(f)
    if not ID.fullmatch(config.get('instance', '')):
        raise SettingsError('Base invalide.')
    # Serialize admin edits without opening or changing any business database.
    lockpath = pathlib.Path(sys.argv[1]).parent / ('.request-limits-' + config['instance'] + '.lock')
    lock = os.open(lockpath, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(lock, 'w') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SettingsError('Une autre lecture ou modification est en cours. Réessayez.', 409)
        return execute(config)


if __name__ == '__main__':
    try:
        print(json.dumps({'ok': True, 'value': main()}))
    except SettingsError as error:
        print(json.dumps({'ok': False, 'status': error.status, 'message': str(error)}))
    except Exception:
        print(json.dumps({'ok': False, 'status': 400, 'message': 'Connexion native indisponible. Rechargez les paramètres.'}))
