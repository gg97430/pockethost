#!/usr/bin/env python3
"""Disposable archive/restore prerequisite probe. Never reads customer data.

Run as the controller user to verify the same PATH and command permissions.
"""
import hashlib, json, pathlib, sqlite3, subprocess, tempfile

with tempfile.TemporaryDirectory(prefix='kolab-archive-check-') as work:
    root = pathlib.Path(work)
    source, target = root / 'source', root / 'restored'
    (source / 'pb_data').mkdir(parents=True)
    target.mkdir()
    original = source / 'pb_data/data.db'
    with sqlite3.connect(original) as db:
        db.execute('CREATE TABLE validation(value TEXT)')
        db.execute('INSERT INTO validation VALUES (?)', ('kolab-check',))
    archive = root / 'backup.tar.gz'
    limits = ['cpulimit', '-q', '-m', '-f', '-l', '50', '--',
              'ionice', '-c', '3', 'nice', '-n', '19']
    commands = [
        limits + ['tar', '-I', 'gzip -1', '-cf', str(archive),
                  '-C', str(source), 'pb_data'],
        limits + ['tar', '-tzf', str(archive)],
        limits + ['tar', '-xzf', str(archive), '-C', str(target),
                  '--no-same-owner', '--no-same-permissions'],
    ]
    for command in commands:
        subprocess.run(command, check=True, capture_output=True, timeout=30)
    restored = target / 'pb_data/data.db'
    assert hashlib.sha256(original.read_bytes()).digest() == hashlib.sha256(restored.read_bytes()).digest()
    result = subprocess.run(
        ['sqlite3', '-readonly', str(restored),
         'PRAGMA quick_check; SELECT value FROM validation;'],
        check=True, capture_output=True, text=True, timeout=10,
    )
    assert result.stdout.splitlines() == ['ok', 'kolab-check']
    with sqlite3.connect(restored) as db:
        assert db.execute('SELECT value FROM validation').fetchone()[0] == 'kolab-check'
    print(json.dumps({'archive': True, 'extraction': True,
                      'sqliteCliVerified': True, 'cpuLimitPercent': 50}))
