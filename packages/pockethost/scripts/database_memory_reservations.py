"""Operator-owned native database RAM commitments shared by admission and allocation."""
import datetime
import hashlib
import json
import os
import pathlib
import re
import stat
import time

POLICY = pathlib.Path('/etc/kolab/database-memory-reservations.json')
SUMMARY = pathlib.Path('/run/kolab-memory-allocation/reservations.json')
MIB = 1024 ** 2


def trusted_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as f:
        s = os.fstat(f.fileno())
        if not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022 or s.st_size > 65536:
            raise ValueError('Invalid database memory policy')
        raw = f.read(65537)
        if len(raw) > 65536:
            raise ValueError('Invalid database memory policy')
        return json.loads(raw)


def parse_policy(value):
    if not isinstance(value, dict) or set(value) != {'version', 'databases'} or type(value['version']) is not int or value['version'] != 1:
        raise ValueError('Invalid database memory policy')
    rows = value['databases']
    if not isinstance(rows, dict) or len(rows) > 1000:
        raise ValueError('Invalid database memory policy')
    if any(not re.fullmatch(r'[a-z0-9]{15}', key) or type(mib) is not int or not 512 <= mib <= 524288
           for key, mib in rows.items()):
        raise ValueError('Invalid database memory policy')
    return {key: mib * MIB for key, mib in rows.items()}


def load_policy():
    try:
        return parse_policy(trusted_json(POLICY))
    except FileNotFoundError:
        return {}


def policy_hash(policy):
    return hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def unconsumed(policy, usage):
    return sum(max(0, amount - max(0, usage.get(owner, 0))) for owner, amount in policy.items())


def reserved_headroom(now=None):
    """Missing/stale measurements retain the whole commitment, never release it."""
    policy = load_policy()
    total = sum(policy.values())
    if not total:
        return 0
    now = time.time() if now is None else now
    try:
        value = trusted_json(SUMMARY)
        at = datetime.datetime.fromisoformat(value['checkedAt']).timestamp()
        remaining = value['unconsumedBytes']
        if (value['version'] != 1 or value['policyHash'] != policy_hash(policy) or
                not -5 <= now - at <= 30 or type(remaining) is not int or not 0 <= remaining <= total):
            raise ValueError('Invalid database memory summary')
        return remaining
    except (OSError, ValueError, KeyError, TypeError):
        return total
