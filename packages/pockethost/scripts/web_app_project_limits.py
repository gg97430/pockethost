"""Live project policies: published atomically; runtime quotas are checked before starting a candidate."""
import json
import pathlib
import re
import threading

ID = re.compile(r'[a-z0-9]{15}')


def sync(root, api, atomic):
    value = api('project-limits', {})
    if value.get('version') != 1 or not isinstance(value.get('projects'), dict) or not isinstance(value.get('apps'), dict):
        raise RuntimeError('Registre des limites de projet invalide.')
    atomic(pathlib.Path(root) / 'project-limits.json', value)
    return value


def start_monitor(root, api, atomic):
    # The first read is synchronous: no admitted job can race the initial registry.
    sync(root, api, atomic)
    def monitor():
        while True:
            threading.Event().wait(5)
            try: sync(root, api, atomic)
            except Exception: pass  # Keep the last valid registry during outages.
    thread = threading.Thread(target=monitor, name='web-project-limits', daemon=True)
    thread.start()
    return thread


def policy(root, job):
    fallback = job.snapshot.get('projectPolicy') or {}
    try:
        path = pathlib.Path(root) / 'project-limits.json'
        if path.is_symlink() or path.stat().st_size > 2_000_000: raise ValueError()
        value = json.loads(path.read_text())
        project = value['apps'].get(job.app)
        if value['version'] != 1: raise ValueError()
        if not project: return None
        return {'projectId': project, 'limits': value['projects'][project], 'appIds': [app for app, p in value['apps'].items() if p == project]}
    except (OSError, ValueError, KeyError, TypeError):
        return fallback or None


def build_seconds(root, job):
    current = policy(root, job)
    resources = (current or {}).get('limits', {}).get('resources', {})
    if not resources.get('enabled'): return 1800
    minutes = resources.get('buildMinutes')
    if not isinstance(minutes, int) or not 1 <= minutes <= 30: raise RuntimeError('Durée de construction du projet invalide.')
    return minutes * 60


def admit(root, job, docker, memory_mib, cpu_millicores):
    current = policy(root, job)
    resources = (current or {}).get('limits', {}).get('resources', {})
    if not resources.get('enabled'): return
    members = set(current.get('appIds') or [job.app])
    members.add(job.app)
    memory, cpu = 0, 0
    ids = docker(['ps', '-q', '--filter', 'label=pockethost.web=true']).split()
    for ident in ids:
        info = json.loads(docker(['inspect', ident]))[0]
        labels = (info.get('Config') or {}).get('Labels') or {}
        if labels.get('pockethost.web.app') not in members or not info.get('State', {}).get('Running'): continue
        host = info.get('HostConfig') or {}
        from web_private_db_cpu import accounted_nano_cpus
        nano = accounted_nano_cpus(info)
        if not host.get('Memory') or not nano: raise RuntimeError('Quota de projet invérifiable pour un service actif.')
        memory += host['Memory']; cpu += nano
    if memory + memory_mib * 1024**2 > resources['memoryMiB'] * 1024**2 or cpu + cpu_millicores * 1_000_000 > resources['cpuMillicores'] * 1_000_000:
        raise RuntimeError('Quota CPU/RAM du projet atteint. La version publiée est conservée ; ajustez les limites du projet ou arrêtez un autre site.')
