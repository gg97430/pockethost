"""Operator-owned CPU burst exceptions for private database relays only."""
import json
import os
import pathlib
import re
import stat

POLICY_FILE = pathlib.Path('/etc/pockethost-web/private-db-cpu.json')
ID = re.compile(r'[a-z0-9]{15}\Z')
RESERVED_NANO_CPUS = 50_000_000


def policy():
    try:
        fd = os.open(POLICY_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {'applications': [], 'instances': []}
    try:
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > 16384:
                raise ValueError('Untrusted CPU policy')
            value = json.loads(source.read(16385))
        if value.get('version') != 1:
            raise ValueError('Invalid CPU policy version')
        for key in ('applications', 'instances'):
            ids = value.get(key)
            if not isinstance(ids, list) or len(ids) > 500 or any(not isinstance(i, str) or not ID.fullmatch(i) for i in ids):
                raise ValueError('Invalid CPU policy IDs')
        return value
    except (ValueError, TypeError, AttributeError) as error:
        raise RuntimeError('Politique CPU des relais privés invalide.') from error


def unlimited(kind, ident):
    return ident in policy()[kind]


def cpu_unlimited(host):
    # Docker update --cpus=0 is a no-op on existing containers. CpuQuota=-1
    # clears the effective cgroup quota while NanoCpus can retain its old value.
    return host.get('CpuQuota') == -1 or (host.get('NanoCpus') == 0 and host.get('CpuQuota', 0) == 0)


def accounted_nano_cpus(info):
    """Reserve the relay's existing CPU budget when an operator allows bursts.

    Ordinary application containers must still have an explicit CPU quota.
    """
    host = info.get('HostConfig') or {}
    nano = host.get('NanoCpus', 0)
    if nano != 0:
        return nano
    labels = (info.get('Config') or {}).get('Labels') or {}
    app = labels.get('pockethost.web.app', '')
    parent = labels.get('pockethost.web.private-db.parent', '')
    if (labels.get('pockethost.web.private-db') == 'true'
            and ID.fullmatch(app) and re.fullmatch(r'[a-f0-9]{64}', parent)
            and host.get('NetworkMode') == 'container:' + parent
            and host.get('Memory') == 96 * 1024**2 and host.get('PidsLimit') == 64
            and unlimited('applications', app)):
        return RESERVED_NANO_CPUS
    return nano
