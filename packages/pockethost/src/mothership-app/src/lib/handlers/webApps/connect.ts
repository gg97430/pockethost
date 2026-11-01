import { decryptWebValue, encryptWebValue, requirePlatformAdmin, webInput, webJson } from './api'
import {
  GITHUB_CONNECTION_MAX,
  GITHUB_PRINCIPAL,
  assertGithubConnectionUnique,
  githubConnectionConfigured,
  githubConnectionLabel,
  githubConnectionMeta,
  githubSessionTarget,
  githubSettingsKey,
  listWebGithubConnections,
  newGithubConnectionId,
} from './githubConnections'

/** Fixed GitHub transport; sensitive conversion results never enter browser responses. */
export const GITHUB_CONNECT_READER = String.raw`
import sys,json,pathlib,tempfile,subprocess,time,base64,urllib.request,urllib.error,urllib.parse,os,signal,re
os.umask(0o077)
source=pathlib.Path(sys.argv[1]);data=json.loads(source.read_text());source.unlink()
action=data['action']
signal.signal(signal.SIGALRM,lambda *args:(_ for _ in ()).throw(TimeoutError()));signal.alarm(30)
def request(path,body=None,token=''):
 req=urllib.request.Request('https://api.github.com'+path,headers={'Accept':'application/vnd.github+json','User-Agent':'PocketHost-Web-Connect','X-GitHub-Api-Version':'2022-11-28',**({'Authorization':'Bearer '+token} if token else {})},data=None if body is None else json.dumps(body).encode())
 with urllib.request.urlopen(req,timeout=8) as response:return json.loads(response.read(2000000))
def jwt(config):
 b64=lambda value:base64.urlsafe_b64encode(value).rstrip(b'=').decode()
 now=int(time.time());part=b64(b'{"alg":"RS256","typ":"JWT"}')+'.'+b64(json.dumps({'iat':now-60,'exp':now+540,'iss':config['githubAppId']}).encode())
 with tempfile.TemporaryDirectory(prefix='ph-github-key-')as folder:
  key=pathlib.Path(folder)/'key.pem';key.write_text(config['privateKey']);key.chmod(0o600)
  signature=subprocess.check_output(['openssl','dgst','-sha256','-sign',str(key)],input=part.encode(),stderr=subprocess.DEVNULL,timeout=3)
 return part+'.'+b64(signature)
try:
 if action=='convert':
  result=request('/app-manifests/'+urllib.parse.quote(data['code'],safe='')+'/conversions',{})
  # The transport response is private. The controller validates and encrypts it before use.
  result={key:result.get(key) for key in ['id','slug','pem','webhook_secret','owner','permissions','events']}
 elif action in ['verify-installation','discover-installation']:
  config=data['config'];identity=jwt(config)
  if action=='discover-installation':
   installations=request('/app/installations?per_page=100',token=identity)
   matches=[row for row in installations if str(row.get('app_id'))==str(config['githubAppId']) and str(row.get('account',{}).get('id'))==str(config['ownerId']) and not row.get('suspended_at')]
   if len(matches)!=1:raise ValueError('Installez cette GitHub App sur son compte propriétaire et sélectionnez au moins un dépôt, puis vérifiez à nouveau.')
   ident=str(matches[0]['id'])
  else:ident=data['installationId']
  installation=request('/app/installations/'+urllib.parse.quote(ident,safe=''),token=identity)
  if str(installation.get('app_id'))!=str(config['githubAppId']) or str(installation.get('account',{}).get('id'))!=str(config['ownerId']) or installation.get('suspended_at'):
   raise ValueError('L’installation ne correspond pas au compte propriétaire de cette GitHub App privée.')
  if 'push' not in installation.get('events',[]):raise ValueError('Activez les notifications push dans cette GitHub App avant de continuer.')
  permissions=installation.get('permissions')or{}
  if permissions.get('contents')!='read' or permissions.get('metadata')!='read' or any(value=='write' for value in permissions.values()):
   raise ValueError('Cette installation doit autoriser uniquement la lecture du code et des métadonnées.')
  token=request('/app/installations/'+ident+'/access_tokens',{'permissions':{'contents':'read','metadata':'read'}},identity)['token']
  repos=request('/installation/repositories?per_page=1',token=token)
  count=repos.get('total_count')
  if not isinstance(count,int) or count<1:raise ValueError('Sélectionnez au moins un dépôt dans l’installation GitHub avant de continuer.')
  result={'installationId':ident,'account':installation['account'].get('login',''),'accountId':str(installation['account']['id']),'repositoryCount':count}
 else:raise ValueError('Opération GitHub inconnue.')
 print(json.dumps({'result':result}))
except Exception as error:
 status=getattr(error,'code',0)
 if isinstance(error,ValueError):message=str(error)
 elif isinstance(error,(TimeoutError,subprocess.TimeoutExpired)):message='GitHub ne répond pas dans le délai prévu. Réessayez avec une nouvelle étape de connexion.'
 elif status in [401,403,404]:message='GitHub refuse cette étape. Vérifiez le compte connecté et les dépôts sélectionnés.'
 elif status in [410,422]:message='Le code GitHub est expiré ou déjà utilisé. Recommencez la création de la connexion.'
 else:message='La connexion à GitHub a échoué. La connexion enregistrée auparavant reste conservée.'
 print(json.dumps({'error':message}))
finally:signal.alarm(0)
`

const COLLECTION = 'web_github_setup_sessions'
const lifetime = 30 * 60 * 1000
const token = () => $security.randomString(64)
const hash = (value: string) => $security.sha256(value)
// PocketBase dates are stored with a space separator; filter parameters are not normalized.
const now = () => new Date().toISOString().replace('T', ' ')
const publicInfo = (row: core.Record) => JSON.parse(row.getString('publicInfo') || '{}')
const pendingConfig = (row: core.Record) => decryptWebValue(row.getString('pendingEncrypted'))
const stateValue = (value: unknown) => {
  if (typeof value !== 'string' || !/^[A-Za-z0-9]{64}$/.test(value))
    throw new BadRequestError(
      'Cette étape de connexion est invalide ou expirée. Recommencez depuis les paramètres GitHub.'
    )
  return value
}
/** Ligne d'une connexion par sa clé (`github` ou `github:<id>`), filtre paramétré. */
const settingsRow = (store: any = $app, key = 'github') =>
  store.findRecordsByFilter('web_app_settings', 'key = {:key}', '', 1, 0, { key })[0] as core.Record | undefined
/** Empreinte de la seule connexion ciblée : modifier une autre connexion n'invalide pas la session. */
const revision = (store: any = $app, key = 'github') => {
  const row = settingsRow(store, key)
  return hash(row ? row.getString('valueEncrypted') : 'no-connection')
}
const origin = () => {
  const host = $os.getenv('APP_HOST') || `app.${$os.getenv('APEX_DOMAIN') || 'monappli.re'}`
  if (!/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(host))
    throw new BadRequestError('Le domaine HTTPS du dashboard doit être configuré avant la connexion GitHub.')
  return `https://${host}`
}
const appMeta = (config: any) => ({ id: `${config.githubAppId}`, slug: config.githubAppSlug, owner: config.ownerLogin })
const installUrl = (slug: string, state: string) =>
  `https://github.com/apps/${encodeURIComponent(slug)}/installations/new?state=${encodeURIComponent(state)}`
const transport = (action: string, values: Record<string, unknown>) => {
  const file = `${$app.dataDir()}/.web-github-connect-${$security.randomString(24)}.json`
  try {
    $os.writeFile(file, JSON.stringify({ action, ...values }), 0o600 as any)
    const result = JSON.parse(toString($os.cmd('python3', '-c', GITHUB_CONNECT_READER, file).combinedOutput()))
    if (result.error) throw new BadRequestError(result.error)
    if (!result.result) throw new Error('Invalid GitHub response')
    return result.result
  } catch (error) {
    if (error instanceof BadRequestError) throw error
    throw new BadRequestError('GitHub est indisponible. Votre connexion actuelle reste conservée.')
  } finally {
    try {
      $os.remove(file)
    } catch {}
  }
}
const valid = (row: core.Record, actorId: string) => {
  if (row.getString('actorId') !== actorId)
    throw new ForbiddenError('Cette connexion appartient à une autre session administrateur.')
  if (!Number.isFinite(Date.parse(row.getString('expiresAt'))) || Date.parse(row.getString('expiresAt')) <= Date.now())
    throw new BadRequestError('Cette connexion GitHub a expiré. Recommencez depuis les paramètres.')
}
/** Atomically consume an actor-bound state before making any external request. */
const consume = (actorId: string, state: string, kind: 'manifest' | 'installation') => {
  let id = ''
  $app.runInTransaction((tx) => {
    const field = kind === 'manifest' ? 'manifestStateHash' : 'installStateHash'
    const rows = tx.findRecordsByFilter(COLLECTION, `${field} = {:hash}`, '', 1, 0, { hash: hash(state) })
    const row = rows[0]
    if (!row) throw new BadRequestError('Cette étape GitHub est invalide ou a déjà été utilisée.')
    valid(row, actorId)
    if (row.getString('status') !== (kind === 'manifest' ? 'created' : 'awaiting_installation'))
      throw new BadRequestError('Cette étape GitHub a déjà été utilisée. Reprenez la connexion depuis les paramètres.')
    row.set('status', kind === 'manifest' ? 'manifest_exchanging' : 'installation_verifying')
    row.set('lastError', '')
    tx.save(row)
    id = row.id
  })
  return $app.findRecordById(COLLECTION, id)
}
const markFailed = (id: string, status: string, error: unknown) => {
  const row = $app.findRecordById(COLLECTION, id)
  if (!['manifest_exchanging', 'installation_verifying'].includes(row.getString('status'))) return
  row.set('status', status)
  row.set(
    'lastError',
    error instanceof BadRequestError
      ? `${error.message}`.slice(0, 500)
      : 'La connexion GitHub n’a pas pu être finalisée.'
  )
  $app.save(row)
}
const validatedConversion = (result: any) => {
  const id = `${result.id || ''}`,
    ownerId = `${result.owner?.id || ''}`,
    ownerLogin = `${result.owner?.login || ''}`
  if (
    !/^[1-9][0-9]{0,19}$/.test(id) ||
    !/^[1-9][0-9]{0,19}$/.test(ownerId) ||
    !/^[A-Za-z0-9-]{1,100}$/.test(ownerLogin) ||
    !/^[a-z0-9-]{1,100}$/.test(result.slug || '') ||
    !/^-----BEGIN (RSA )?PRIVATE KEY-----/.test(result.pem || '') ||
    typeof result.webhook_secret !== 'string' ||
    result.webhook_secret.length < 20 ||
    result.webhook_secret.length > 256
  )
    throw new BadRequestError('GitHub a renvoyé une configuration incomplète. Recommencez la connexion.')
  const permissions = result.permissions || {}
  if (
    permissions.contents !== 'read' ||
    permissions.metadata !== 'read' ||
    Object.values(permissions).some((v) => v === 'write') ||
    !Array.isArray(result.events) ||
    !result.events.includes('push')
  )
    throw new BadRequestError('La GitHub App doit autoriser la lecture du code et recevoir les événements push.')
  return {
    githubAppId: id,
    githubAppSlug: result.slug,
    privateKey: result.pem,
    webhookSecret: result.webhook_secret,
    ownerId,
    ownerLogin,
  }
}
/**
 * Mode par défaut `add` (non destructif : un ancien dashboard qui n'envoie que {organization} ajoute une connexion).
 * `replace` + `connectionId` : recrée la GitHub App de cette connexion existante.
 */
export const HandleWebGithubConnectStart = (e: core.RequestEvent) => {
  const user = requirePlatformAdmin(e),
    input = webInput(e),
    organization = `${input.organization || ''}`.trim()
  if (organization && !/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/.test(organization))
    throw new BadRequestError('Le nom de l’organisation GitHub est invalide.')
  if (input.mode !== undefined && input.mode !== 'add' && input.mode !== 'replace')
    throw new BadRequestError('Mode attendu : add ou replace.')
  const mode: 'add' | 'replace' = input.mode === 'replace' ? 'replace' : 'add',
    label =
      input.label === undefined || input.label === null || input.label === '' ? '' : githubConnectionLabel(input.label)
  const base = origin(),
    state = token(),
    expiresAt = new Date(Date.now() + lifetime).toISOString()
  let id = '',
    target = ''
  $app.runInTransaction((tx) => {
    const connections = listWebGithubConnections(tx)
    if (mode === 'replace') {
      const wanted = `${input.connectionId || ''}`
      if (!connections.some((c) => c.id === wanted)) throw new BadRequestError('Connexion GitHub introuvable.')
      target = wanted
    } else if (!settingsRow(tx, 'github')) target = GITHUB_PRINCIPAL
    else {
      if (connections.length >= GITHUB_CONNECTION_MAX)
        throw new BadRequestError(`${GITHUB_CONNECTION_MAX} connexions GitHub maximum.`)
      target = newGithubConnectionId(connections.map((c) => c.id))
    }
    const pending = tx.findRecordsByFilter(
      COLLECTION,
      'actorId = {:actor} && expiresAt > {:now} && status != "completed" && status != "cancelled"',
      '',
      20,
      0,
      { actor: user.id, now: now() }
    )
    for (const old of pending) {
      old.set('status', 'cancelled')
      old.set('pendingEncrypted', '')
      tx.save(old)
    }
    const row = new Record(tx.findCollectionByNameOrId(COLLECTION))
    row.set('actorId', user.id)
    row.set('manifestStateHash', hash(state))
    row.set('baselineDigest', revision(tx, githubSettingsKey(target)))
    row.set('status', 'created')
    row.set('expiresAt', expiresAt)
    row.set('publicInfo', { organization, label, connectionId: target, mode })
    tx.save(row)
    id = row.id
  })
  const manifest = {
    name: `PocketHost-${$security.randomString(10).toLowerCase()}`,
    url: `${base}/applications`,
    // URL legacy pour la principale, URL dédiée sinon : GitHub la reçoit par le manifeste, rien à copier-coller.
    hook_attributes: {
      url: `${base}/api/web-apps/github/webhook${target === GITHUB_PRINCIPAL ? '' : `/${target}`}`,
      active: true,
    },
    redirect_url: `${base}/applications/settings/github/manifest`,
    setup_url: `${base}/applications/settings/github/install`,
    public: false,
    default_permissions: { contents: 'read', metadata: 'read', pull_requests: 'read' },
    default_events: ['push', 'pull_request'],
    request_oauth_on_install: false,
  }
  const endpoint = organization
    ? `https://github.com/organizations/${encodeURIComponent(organization)}/settings/apps/new`
    : 'https://github.com/settings/apps/new'
  return webJson(
    e,
    { sessionId: id, state, createUrl: `${endpoint}?state=${state}`, manifest, expiresAt, connectionId: target, mode },
    201
  )
}
export const HandleWebGithubConnectManifest = (e: core.RequestEvent) => {
  const user = requirePlatformAdmin(e),
    input = webInput(e),
    state = stateValue(input.state)
  if (typeof input.code !== 'string' || !/^[A-Za-z0-9_-]{20,200}$/.test(input.code))
    throw new BadRequestError('Code GitHub invalide.')
  const row = consume(user.id, state, 'manifest')
  try {
    const config = validatedConversion(transport('convert', { code: input.code }))
    const info = publicInfo(row)
    if (info.organization && info.organization.toLowerCase() !== config.ownerLogin.toLowerCase())
      throw new BadRequestError('La GitHub App n’appartient pas à l’organisation choisie.')
    const nextState = token()
    $app.runInTransaction((tx) => {
      const current = tx.findRecordById(COLLECTION, row.id)
      valid(current, user.id)
      if (current.getString('status') !== 'manifest_exchanging')
        throw new BadRequestError('Cette connexion n’est plus active.')
      current.set('pendingEncrypted', encryptWebValue(config))
      current.set('publicInfo', { ...info, app: appMeta(config) })
      current.set('installStateHash', hash(nextState))
      current.set('status', 'awaiting_installation')
      tx.save(current)
    })
    return webJson(e, {
      sessionId: row.id,
      status: 'awaiting_installation',
      app: appMeta(config),
      installUrl: installUrl(config.githubAppSlug, nextState),
      state: nextState,
      expiresAt: row.getString('expiresAt'),
    })
  } catch (error) {
    markFailed(row.id, 'manifest_failed', error)
    throw error
  }
}
export const HandleWebGithubConnectInstallStart = (e: core.RequestEvent) => {
  const user = requirePlatformAdmin(e),
    input = webInput(e),
    id = `${input.sessionId || ''}`
  if (!/^[a-z0-9]{15}$/.test(id)) throw new BadRequestError('Connexion GitHub invalide.')
  const state = token()
  let config: any,
    expiresAt = ''
  $app.runInTransaction((tx) => {
    const row = tx.findRecordById(COLLECTION, id)
    valid(row, user.id)
    if (!['awaiting_installation', 'installation_failed'].includes(row.getString('status')))
      throw new BadRequestError('Recommencez la création de la GitHub App depuis les paramètres.')
    config = pendingConfig(row)
    if (!config.githubAppSlug) throw new BadRequestError('La création de la GitHub App n’est pas terminée.')
    row.set('installStateHash', hash(state))
    row.set('status', 'awaiting_installation')
    row.set('lastError', '')
    tx.save(row)
    expiresAt = row.getString('expiresAt')
  })
  return webJson(e, { sessionId: id, installUrl: installUrl(config.githubAppSlug, state), state, expiresAt })
}
const activateConnection = (
  row: core.Record,
  actorId: string,
  config: any,
  result: any,
  expectedInstallation?: string
) => {
  const installationId = `${result.installationId || ''}`
  if (
    !/^[1-9][0-9]{0,19}$/.test(installationId) ||
    (expectedInstallation && installationId !== expectedInstallation) ||
    `${result.accountId}` !== `${config.ownerId}` ||
    !Number.isInteger(result.repositoryCount) ||
    result.repositoryCount < 1
  )
    throw new BadRequestError('L’installation GitHub n’a pas pu être vérifiée.')
  let target = GITHUB_PRINCIPAL,
    mode = 'replace',
    label = ''
  $app.runInTransaction((tx) => {
    const current = tx.findRecordById(COLLECTION, row.id)
    valid(current, actorId)
    if (current.getString('status') !== 'installation_verifying')
      throw new BadRequestError('Cette connexion n’est plus active.')
    const info = publicInfo(current),
      session = githubSessionTarget(info)
    target = session.connectionId
    mode = session.mode
    const key = githubSettingsKey(target)
    // Seule la connexion ciblée compte : modifier une autre connexion n'invalide pas la session.
    if (current.getString('baselineDigest') !== revision(tx, key))
      throw new BadRequestError(
        'La connexion GitHub a été modifiée pendant cette étape. Recommencez pour conserver ce changement.'
      )
    assertGithubConnectionUnique(tx, config.githubAppId, installationId, key)
    const existing = settingsRow(tx, key)
    if (mode === 'add') {
      if (existing) throw new BadRequestError('Cette connexion GitHub existe déjà. Recommencez l’ajout.')
      if (listWebGithubConnections(tx).length >= GITHUB_CONNECTION_MAX)
        throw new BadRequestError(`${GITHUB_CONNECTION_MAX} connexions GitHub maximum.`)
    } else if (!existing && !session.legacy) throw new BadRequestError('Connexion GitHub introuvable.')
    // Session antérieure à la mise à jour sans principale : créée sans `meta`, comme par l'ancien code.
    // Remplacement : `meta` existant conservé (la principale historique n'en a pas). Ajout : libellé ou compte.
    const previous = existing ? decryptWebValue(existing.getString('valueEncrypted')) || {} : {},
      meta =
        mode === 'add'
          ? {
              label: `${info.label || config.ownerLogin || ''}`.slice(0, 60) || 'Connexion GitHub',
              disabled: false,
              createdAt: new Date().toISOString(),
              createdBy: actorId,
            }
          : previous.meta
    label = githubConnectionMeta(target, meta ? { ...config, meta } : config).label
    const active = existing || new Record(tx.findCollectionByNameOrId('web_app_settings'))
    active.set('key', key)
    active.set(
      'valueEncrypted',
      encryptWebValue({ ...config, githubInstallationId: installationId, ...(meta ? { meta } : {}) })
    )
    tx.save(active)
    current.set('status', 'completed')
    current.set('pendingEncrypted', '')
    current.set('installStateHash', '')
    current.set('lastError', '')
    tx.save(current)
  })
  return {
    connected: true,
    connectionId: target,
    label,
    mode,
    app: appMeta(config),
    installation: { id: installationId, account: result.account },
    repositoryCount: result.repositoryCount,
  }
}
export const HandleWebGithubConnectInstall = (e: core.RequestEvent) => {
  const user = requirePlatformAdmin(e),
    input = webInput(e),
    state = stateValue(input.state)
  const installationId = `${input.installationId || ''}`
  if (!/^[1-9][0-9]{0,19}$/.test(installationId))
    throw new BadRequestError('Identifiant d’installation GitHub invalide.')
  const row = consume(user.id, state, 'installation')
  try {
    const config = pendingConfig(row),
      result = transport('verify-installation', { config, installationId })
    return webJson(e, activateConnection(row, user.id, config, result, installationId))
  } catch (error) {
    markFailed(row.id, 'installation_failed', error)
    throw error
  }
}
/** Recovery for already installed Apps: still authenticated, actor-bound and tied to the new private App owner. */
export const HandleWebGithubConnectInstallCheck = (e: core.RequestEvent) => {
  const user = requirePlatformAdmin(e),
    input = webInput(e),
    id = `${input.sessionId || ''}`
  if (!/^[a-z0-9]{15}$/.test(id)) throw new BadRequestError('Connexion GitHub invalide.')
  $app.runInTransaction((tx) => {
    const row = tx.findRecordById(COLLECTION, id)
    valid(row, user.id)
    if (!['awaiting_installation', 'installation_failed'].includes(row.getString('status')))
      throw new BadRequestError('Cette connexion GitHub ne peut pas être vérifiée maintenant.')
    // Invalidate any in-flight browser callback and claim the one installation verification slot.
    row.set('installStateHash', hash(token()))
    row.set('status', 'installation_verifying')
    row.set('lastError', '')
    tx.save(row)
  })
  const row = $app.findRecordById(COLLECTION, id)
  try {
    const config = pendingConfig(row)
    if (!config.githubAppId) throw new BadRequestError('La création de la GitHub App n’est pas terminée.')
    const result = transport('discover-installation', { config })
    return webJson(e, activateConnection(row, user.id, config, result))
  } catch (error) {
    markFailed(row.id, 'installation_failed', error)
    throw error
  }
}
export const HandleWebGithubConnectStatus = (e: core.RequestEvent) => {
  const user = requirePlatformAdmin(e),
    rows = $app.findRecordsByFilter(
      COLLECTION,
      'actorId = {:actor} && expiresAt > {:now} && status != "completed" && status != "cancelled"',
      '-created,-id',
      1,
      0,
      { actor: user.id, now: now() }
    )
  const connections = listWebGithubConnections(),
    row = rows[0],
    info = row ? publicInfo(row) : {},
    target = githubSessionTarget(info)
  return webJson(e, {
    configured: connections.some((c) => !c.meta.disabled && githubConnectionConfigured(c.value)),
    connections: connections.length,
    pending: row
      ? {
          sessionId: row.id,
          status: row.getString('status'),
          expiresAt: row.getString('expiresAt'),
          app: info.app || null,
          error: row.getString('lastError'),
          connectionId: target.connectionId,
          mode: target.mode,
          label: typeof info.label === 'string' ? info.label : '',
        }
      : null,
  })
}
export const HandleWebGithubConnectMaintenance = () => {
  try {
    $app.findCollectionByNameOrId(COLLECTION)
  } catch {
    return
  }
  const expired = $app.findRecordsByFilter(COLLECTION, 'expiresAt < {:now}', 'expiresAt', 500, 0, { now: now() })
  for (const row of expired) $app.delete(row)
}
