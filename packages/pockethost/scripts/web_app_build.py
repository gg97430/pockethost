"""BuildKit dependency layering for Next.js, including monorepo manifests."""
import json
import pathlib
import re
import shlex
import hashlib
import hmac


def cache_namespace(app_id, secret):
    if not isinstance(secret, str) or len(secret) < 32:
        raise RuntimeError('Secret du worker requis pour isoler les caches de construction.')
    return 'ph-web-' + app_id + '-' + hmac.new(secret.encode(), ('nextjs-cache:' + app_id).encode(), hashlib.sha256).hexdigest()


def dependency_plan(context, relative, manager, lockfile, package_root, cache_scope):
    context = pathlib.Path(context).resolve()
    root = context / package_root
    root_package = json.loads((root / 'package.json').read_text())
    manifests, lifecycle, local = [], False, []
    local_directory = False
    for path in sorted(root.rglob('package.json')):
        rel = path.relative_to(context)
        if any(part in ('node_modules', '.git', '.next', '.yarn') for part in rel.parts):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(context) or len(manifests) >= 200:
            raise RuntimeError('Les manifests du workspace ne peuvent pas être mis en cache de façon sûre.')
        value = json.loads(path.read_text())
        lifecycle = lifecycle or any(value.get('scripts', {}).get(key) for key in ('preinstall', 'install', 'postinstall', 'prepublish', 'preprepare', 'prepare', 'postprepare'))
        manifests.append(rel.as_posix())
        # Dépendances locales (« file:vendor/x.tgz ») : l'installation en a besoin avant « COPY . . ».
        for section in ('dependencies', 'devDependencies', 'optionalDependencies'):
            for spec in (value.get(section) or {}).values():
                if isinstance(spec, str) and spec.startswith(('file:', 'link:')):
                    local.append(path.parent / spec.split(':', 1)[1])
    files = set(manifests + [(pathlib.PurePosixPath(package_root) / lockfile).as_posix()])
    for target in local:
        if target.is_symlink() or not target.resolve().is_relative_to(context):
            raise RuntimeError('Dépendance locale hors du dépôt ou symbolique refusée.')
        if target.is_dir():
            local_directory = True  # Les dépendances locales ont besoin de leurs sources pour être installées.
        elif target.is_file():
            files.add(target.resolve().relative_to(context).as_posix())
    for name in ('pnpm-workspace.yaml', '.yarnrc.yml', '.npmrc'):
        file = root / name
        if file.is_file():
            if file.is_symlink(): raise RuntimeError('Fichier de gestionnaire symbolique refusé.')
            files.add(file.relative_to(context).as_posix())
    for folder in ('patches', '.yarn/patches', '.yarn/releases', '.yarn/plugins', '.yarn/cache'):
        for file in (root / folder).rglob('*'):
            if file.is_file():
                if file.is_symlink(): raise RuntimeError('Patch de workspace symbolique refusé.')
                files.add(file.relative_to(context).as_posix())
    version = root_package.get('packageManager', '')
    if version and (not re.fullmatch(r'(npm|pnpm|yarn)@[0-9][A-Za-z0-9.+-]{0,80}', version) or not version.startswith(manager + '@')):
        raise RuntimeError('Le gestionnaire du workspace ne correspond pas à son lockfile.')
    # Yarn et les dépendances de type dossier conservent leur sémantique d'installation.
    # npm/pnpm : différer les scripts du projet évite d'invalider l'installation à chaque commit.
    deferred = lifecycle and not local_directory and (manager == 'pnpm' or (manager == 'npm' and len(manifests) == 1))
    if local_directory or (lifecycle and not deferred):
        prelude, after = 'COPY . .\n', ''
    else:
        prelude = ''.join('COPY ' + json.dumps([name, '/src/' + name]) + '\n' for name in sorted(files))
        after = 'COPY . .\n'
    cache = {'npm': '/root/.npm', 'pnpm': '/pnpm/store', 'yarn': '/root/.yarn/cache'}[manager]
    install = {'npm': 'npm ci', 'pnpm': 'pnpm install --frozen-lockfile --store-dir /pnpm/store',
        'yarn': 'YARN_CACHE_FOLDER=/root/.yarn/cache yarn install ' + ('--frozen-lockfile' if not version or version.startswith('yarn@1.') else '--immutable')}[manager]
    mounts = ('--mount=type=cache,id=' + cache_scope + '-' + manager + ',target=' + cache + ',sharing=locked '
              '--mount=type=secret,id=env ')
    shell = 'set -e; set -a; . /run/secrets/env; set +a; '
    run = 'RUN ' + mounts + shell + install + (' --ignore-scripts' if deferred else '') + '\n'
    if deferred:
        rebuild = ('npm rebuild' + ''.join(' && npm run ' + hook + ' --if-present' for hook in ('prepublish', 'preprepare', 'prepare', 'postprepare'))
                   if manager == 'npm' else 'pnpm rebuild -r --pending && pnpm install --offline --frozen-lockfile --store-dir /pnpm/store')
        after += 'WORKDIR /src/' + package_root + '\nRUN ' + mounts + shell + rebuild + '\n'
    corepack = '' if manager == 'npm' else 'RUN corepack enable\n' + ('RUN corepack prepare ' + shlex.quote(version) + ' --activate\n' if version else '')
    return prelude, corepack + run, after
