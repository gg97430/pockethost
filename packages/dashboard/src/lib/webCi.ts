/** Workflow proposal only. No credential is interpolated into generated files. */
export type CiProposal = {
  imageRepository: string
  deployUrl: string
  branch: string
  dockerfile: string
  appId: string
  /** GitHub runner labels, comma or space separated. Defaults to ubuntu-latest. */
  runnerLabels?: string
}
export const WEB_CI_DEFAULT_RUNNER = 'ubuntu-latest'
export const WEB_CI_USER_AGENT = 'kolab-hosting-ci/1'
/** Parses and validates runner labels: 1 to 5 labels made of [A-Za-z0-9._-]. */
export const webCiRunnerLabels = (raw = '') => {
  const labels = raw.split(/[\s,]+/).filter(Boolean)
  if (!labels.length) return [WEB_CI_DEFAULT_RUNNER]
  if (labels.length > 5 || labels.some((label) => !/^[A-Za-z0-9._-]{1,100}$/.test(label)))
    throw new Error('Étiquettes de runner invalides : 5 au maximum, caractères A-Z a-z 0-9 . _ - uniquement.')
  return [...new Set(labels)]
}
const expression = (value: string) => '${{ ' + value + ' }}'
export const webCiWorkflow = (value: CiProposal) => {
  if (
    !/^ghcr\.io\/[a-z0-9][a-z0-9._/-]*$/.test(value.imageRepository) ||
    value.imageRepository.includes('..') ||
    value.imageRepository.includes('//')
  )
    throw new Error('Le modèle nécessite un dépôt ghcr.io en minuscules.')
  const endpoint = new URL(value.deployUrl)
  if (
    endpoint.protocol !== 'https:' ||
    endpoint.username ||
    endpoint.password ||
    endpoint.pathname !== '/api/web-apps/ci/deploy' ||
    endpoint.search ||
    endpoint.hash ||
    (endpoint.port && endpoint.port !== '443')
  )
    throw new Error('Adresse de déploiement CI invalide.')
  if (
    !/^[A-Za-z0-9_./-]{1,200}$/.test(value.branch) ||
    value.branch.includes('..') ||
    !/^[a-z0-9]{15}$/.test(value.appId) ||
    !/^[A-Za-z0-9_./-]{1,200}$/.test(value.dockerfile) ||
    value.dockerfile.split('/').includes('..') ||
    value.dockerfile.startsWith('/')
  )
    throw new Error('Branche ou chemin Dockerfile invalide.')
  const runner = webCiRunnerLabels(value.runnerLabels)
  const hosted = runner.length === 1 && runner[0] === WEB_CI_DEFAULT_RUNNER
  const lines = [
    '# Proposition PocketHost : relisez puis ajoutez ce fichier à votre dépôt.',
    '# Secret requis : POCKETHOST_DEPLOY_TOKEN ; optionnel : POCKETHOST_BUILD_ENV (.env.local Next.js).',
    'name: PocketHost image deployment',
    'on:',
    '  push:',
    '    branches: [' + JSON.stringify(value.branch) + ']',
    '  workflow_dispatch:',
    'permissions:',
    '  contents: read',
    '  packages: write',
    'concurrency:',
    '  group: pockethost-' + value.appId + '-' + expression('github.ref'),
    '  cancel-in-progress: true',
    'env:',
    '  IMAGE_REPOSITORY: ' + JSON.stringify(value.imageRepository),
    'jobs:',
    '  build-and-deploy:',
    ...(hosted
      ? []
      : ['    # Runner auto-hébergé : il ne doit jamais tourner sur les serveurs d’hébergement PocketHost.']),
    '    runs-on: ' + (runner.length === 1 ? JSON.stringify(runner[0]) : JSON.stringify(runner)),
    '    timeout-minutes: 45',
    '    steps:',
    '      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7',
    '        with:',
    '          persist-credentials: false',
    '      - uses: docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069 # v4',
    '      - uses: docker/login-action@81c6f93d4bc991e510b342a00b419b3bb4efc5c6 # v4',
    '        with:',
    '          registry: ghcr.io',
    '          username: ' + expression('github.actor'),
    '          password: ' + expression('github.token'),
    '      - name: Prepare optional build environment',
    '        env:',
    '          BUILD_ENV: ' + expression('secrets.POCKETHOST_BUILD_ENV'),
    "        run: node -e \"require('fs').writeFileSync(process.env.RUNNER_TEMP+'/pockethost-build.env',process.env.BUILD_ENV||'',{mode:0o600})\"",
    '      - name: Build and push immutable image',
    '        id: build',
    '        uses: docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc # v7',
    '        with:',
    '          context: .',
    '          file: ' + JSON.stringify(value.dockerfile),
    '          push: true',
    '          tags: ' + expression('env.IMAGE_REPOSITORY') + ':' + expression('github.sha'),
    '          cache-from: type=gha',
    '          cache-to: type=gha,mode=max',
    '          build-args: PH_BUILD_REVISION=' + expression('github.run_id') + '-' + expression('github.run_attempt'),
    '          secret-files: pockethost_env=' + expression('runner.temp') + '/pockethost-build.env',
    '      - name: Deploy digest and wait for publication',
    '        env:',
    '          PHCI_TOKEN: ' + expression('secrets.POCKETHOST_DEPLOY_TOKEN'),
    '          GHCR_TOKEN: ' + expression('github.token'),
    '          GHCR_LOGIN: ' + expression('github.actor'),
    '          IMAGE_DIGEST: ' + expression('steps.build.outputs.digest'),
    '          SOURCE_COMMIT: ' + expression('github.sha'),
    '          SOURCE_BRANCH: ' + expression('github.ref_name'),
    '          RUN_ID: ' + expression('github.run_id'),
    '          RUN_ATTEMPT: ' + expression('github.run_attempt'),
    '        run: |',
    "          python3 - <<'PY'",
    '          import json, os, sys, time, urllib.request, urllib.error',
    '          class NoRedirect(urllib.request.HTTPRedirectHandler):',
    '              def redirect_request(self, *args, **kwargs): return None',
    '          token = os.environ["PHCI_TOKEN"]',
    '          if not token.startswith("phci."): sys.exit("Configure POCKETHOST_DEPLOY_TOKEN in GitHub Actions secrets.")',
    '          payload = {"image": os.environ["IMAGE_REPOSITORY"] + "@" + os.environ["IMAGE_DIGEST"], "commit": os.environ["SOURCE_COMMIT"], "branch": os.environ["SOURCE_BRANCH"], "requestId": os.environ["RUN_ID"] + ":" + os.environ["RUN_ATTEMPT"], "registry": {"server": "ghcr.io", "username": os.environ["GHCR_LOGIN"], "password": os.environ["GHCR_TOKEN"]}}',
    '          data = json.dumps(payload).encode()',
    '          opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)',
    '          deadline = time.monotonic() + 2400',
    '          while time.monotonic() < deadline:',
    '              request = urllib.request.Request(' +
      JSON.stringify(endpoint.toString()) +
      ', data=data, headers={"Content-Type": "application/json", "User-Agent": ' +
      JSON.stringify(WEB_CI_USER_AGENT) +
      ', "Authorization": "Bearer " + token})',
    '              try:',
    '                  with opener.open(request, timeout=20) as response: result = json.load(response)',
    '              except urllib.error.HTTPError as error:',
    '                  if error.code < 500: sys.exit("PocketHost refused the deployment (HTTP " + str(error.code) + "). Check the dashboard.")',
    '                  time.sleep(5); continue',
    '              except OSError:',
    '                  time.sleep(5); continue',
    '              state = result["deployment"]["status"]',
    '              print("PocketHost: " + state, flush=True)',
    '              if state == "ready": sys.exit(0)',
    '              if state in ("failed", "cancelled"): sys.exit("Deployment ended without publication. Check PocketHost logs.")',
    '              time.sleep(5)',
    '          sys.exit("PocketHost did not confirm publication within 40 minutes.")',
    '          PY',
    '      - name: Remove build environment',
    '        if: always()',
    '        run: rm -f "$RUNNER_TEMP/pockethost-build.env"',
  ]
  return lines.join('\n') + '\n'
}
export const webCiDockerignore = '.git\nnode_modules\n.next\n.env\n.env.*\n*.log\n'
export const webCiDockerfile = `# Example for a Next.js npm project with package-lock.json.
# Adapt the install/build commands for pnpm, Yarn or a monorepo.
FROM node:24-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG PH_BUILD_REVISION
RUN --mount=type=secret,id=pockethost_env node -e "const fs=require('fs'),cp=require('child_process');const name='.env.production.local';try{if(fs.existsSync('/run/secrets/pockethost_env'))fs.copyFileSync('/run/secrets/pockethost_env',name);cp.execFileSync('npm',['run','build'],{stdio:'inherit'})}finally{fs.rmSync(name,{force:true})}"
FROM node:24-bookworm-slim
WORKDIR /app
LABEL pockethost.framework=nextjs pockethost.next.directory=/app/.next
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000
COPY --from=build --chown=10001:10001 /app/ ./
USER 10001:10001
EXPOSE 3000
CMD ["./node_modules/.bin/next","start","-H","0.0.0.0","-p","3000"]
`
