/**
 * Connexions GitHub multiples : helpers purs, sérialisation sans secret, garde de suppression et routage du webhook
 * sous un faux JSVM (aucun PocketBase, aucun python3). Le chiffrement est simulé par JSON.
 */
import { createHash, createHmac } from 'node:crypto'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as gh from '../githubConnections'

const calls: { webGithub: any[]; previews: any[]; pushes: any[] } = { webGithub: [], previews: [], pushes: [] }

vi.mock('../access', () => ({
  requirePlatformAdmin: () => ({ id: 'admin0000000000' }),
}))
vi.mock('../previews', () => ({
  handleWebPreviewWebhook: (...args: any[]) => {
    calls.previews.push(args)
    return { accepted: true, queued: 0 }
  },
}))
vi.mock('../api', () => ({
  decryptWebValue: (value: string) => (value ? JSON.parse(value) : {}),
  encryptWebValue: (value: unknown) => JSON.stringify(value),
  webConfig: (row: any) => row.data.config || {},
  webEnabled: () => true,
  webInput: (e: any) => e.input || {},
  webJson: (_e: unknown, body: unknown, status = 200) => ({ status, body }),
  webSourceBuildSupported: () => true,
  webGithub: (action: string, extra: any, connectionId: string) => {
    calls.webGithub.push({ action, extra, connectionId })
    return { sha: (connectionId === 'default' ? 'a' : 'b').repeat(40) }
  },
  queueWebGitPush: (_tx: unknown, row: any, head: string) => {
    calls.pushes.push({ appId: row.id, head })
    return true
  },
}))

class FakeRecord {
  data: Record<string, any>
  id: string
  constructor(
    public collection: string,
    data: Record<string, any> = {}
  ) {
    this.data = { ...data }
    this.id = data.id || `r${Math.random().toString(36).slice(2, 16).padEnd(14, '0')}`
  }
  set(key: string, value: unknown) {
    this.data[key] = value
  }
  getString(key: string) {
    const value = this.data[key]
    return value === undefined || value === null ? '' : typeof value === 'string' ? value : JSON.stringify(value)
  }
  getInt(key: string) {
    return Number(this.data[key]) || 0
  }
}
class HttpError extends Error {
  constructor(
    message = '',
    public status = 400
  ) {
    super(message)
  }
}
class BadRequestError extends HttpError {}
class NotFoundError extends HttpError {}
class UnauthorizedError extends HttpError {}
class ForbiddenError extends HttpError {}

const B = 'bbbbbbbbbbbbbbb'
const SECRET_A = 'A'.repeat(40),
  SECRET_B = 'B'.repeat(40)
const PEM_A = '-----BEGIN RSA PRIVATE KEY-----\nAAAA-marker\n',
  PEM_B = '-----BEGIN RSA PRIVATE KEY-----\nBBBB-marker\n'
let db: {
  settings: FakeRecord[]
  apps: FakeRecord[]
  jobs: FakeRecord[]
  deliveries: FakeRecord[]
  sessions: FakeRecord[]
}

const all = (collection: string) =>
  collection === 'web_app_settings'
    ? db.settings
    : collection === 'web_apps'
      ? db.apps
      : collection === 'web_deployments'
        ? db.jobs
        : collection === 'web_webhook_deliveries'
          ? db.deliveries
          : collection === 'web_github_setup_sessions'
            ? db.sessions
            : []
const store = {
  findFirstRecordByFilter: (collection: string, _filter: string, params: any = {}) => {
    const row =
      collection === 'web_app_settings'
        ? db.settings.find((r) => r.data.key === params.key)
        : collection === 'web_webhook_deliveries'
          ? db.deliveries.find((r) => r.data.deliveryId === params.id)
          : undefined
    if (!row) throw new Error('not found')
    return row
  },
  findRecordsByFilter: (collection: string, filter: string, _sort = '', _limit = 0, _offset = 0, params: any = {}) => {
    const rows = all(collection)
    if (collection === 'web_deployments') return rows.filter((r) => ['queued', 'running'].includes(r.data.status))
    if (collection === 'web_webhook_deliveries' && filter.includes('connectionId'))
      return rows.filter((r) => r.data.connectionId === params.id)
    if (collection === 'web_github_setup_sessions')
      return rows.filter((r) => !['completed', 'cancelled'].includes(r.data.status))
    return rows
  },
  findCollectionByNameOrId: (name: string) => name,
  runInTransaction: (fn: (tx: any) => void) => fn(store),
  save: (row: FakeRecord) => {
    const rows = all(row.collection)
    if (!rows.includes(row)) rows.push(row)
  },
  delete: (row: FakeRecord) => {
    const rows = all(row.collection),
      index = rows.indexOf(row)
    if (index >= 0) rows.splice(index, 1)
  },
}
let random = 0
Object.assign(globalThis as any, {
  $app: store,
  $security: {
    hs256: (raw: string, secret: string) => createHmac('sha256', secret).update(raw).digest('hex'),
    sha256: (raw: string) => createHash('sha256').update(raw).digest('hex'),
    equal: (a: string, b: string) => a === b,
    randomString: (n: number) => 'r'.repeat(n),
    randomStringWithAlphabet: (n: number) => `${++random}`.padStart(n, 'c'),
  },
  $os: { getenv: (name: string) => (name === 'APP_HOST' ? 'app.example.com' : '') },
  readerToString: (value: unknown) => String(value),
  Record: FakeRecord,
  BadRequestError,
  NotFoundError,
  UnauthorizedError,
  ForbiddenError,
})

const setting = (key: string, value: Record<string, any>) =>
  new FakeRecord('web_app_settings', { key, valueEncrypted: JSON.stringify(value) })
const principal = (extra: Record<string, any> = {}) =>
  setting('github', {
    githubAppId: '101',
    githubAppSlug: 'pockethost-a',
    githubInstallationId: '1001',
    privateKey: PEM_A,
    webhookSecret: SECRET_A,
    ownerLogin: 'perso',
    ...extra,
  })
const second = (meta: Record<string, any> = {}) =>
  setting(`github:${B}`, {
    githubAppId: '202',
    githubAppSlug: 'pockethost-b',
    githubInstallationId: '2002',
    privateKey: PEM_B,
    webhookSecret: SECRET_B,
    ownerLogin: 'acme',
    meta: { label: 'Organisation Acme', disabled: false, ...meta },
  })
const app = (id: string, config: Record<string, any>) =>
  new FakeRecord('web_apps', { id, config: { branch: 'main', autoDeploy: true, ...config } })
const event = (raw: string, headers: Record<string, string>, extra: Record<string, any> = {}) =>
  ({
    request: {
      body: raw,
      pathValue: (name: string) => extra.path?.[name] || '',
      header: { get: (name: string) => headers[name] || '' },
      url: { query: () => ({ get: (name: string) => extra.query?.[name] || '' }) },
    },
    input: extra.input,
  }) as any
const sha = (raw: string) => createHash('sha256').update(raw).digest('hex')
const signed = (raw: string, secret: string, delivery: string, kind = 'push') => ({
  'X-GitHub-Event': kind,
  'X-GitHub-Delivery': delivery,
  'X-Hub-Signature-256': 'sha256=' + createHmac('sha256', secret).update(raw).digest('hex'),
})
const push = (installation: string) =>
  JSON.stringify({
    ref: 'refs/heads/main',
    after: 'f'.repeat(40),
    repository: { full_name: 'acme/web' },
    installation: { id: Number(installation) },
  })
const failure = (fn: () => unknown) => {
  try {
    fn()
  } catch (error) {
    return error as HttpError
  }
  throw new Error('expected a failure')
}

beforeEach(() => {
  db = {
    settings: [principal(), second()],
    apps: [
      app('appx00000000001', { repository: 'acme/web' }),
      app('appy00000000001', { repository: 'acme/web', githubConnectionId: B }),
      app('appg00000000001', { repository: 'git:host/acme/web', source: 'git', gitSourceId: 'src000000000001' }),
    ],
    jobs: [],
    deliveries: [],
    sessions: [],
  }
  calls.webGithub = []
  calls.previews = []
  calls.pushes = []
})

describe('keys and resolution', () => {
  it('maps connection ids to settings keys and rejects anything else', () => {
    expect(gh.githubSettingsKey('default')).toBe('github')
    expect(gh.githubSettingsKey(B)).toBe(`github:${B}`)
    for (const bad of ['', 'Default', 'abc', 'b'.repeat(14), 'b'.repeat(16), 'BBBBBBBBBBBBBBB', `${B}/`, '../x'])
      expect(() => gh.githubSettingsKey(bad)).toThrow('Connexion GitHub invalide.')
    expect(gh.githubConnectionIdOfKey('github')).toBe('default')
    expect(gh.githubConnectionIdOfKey(`github:${B}`)).toBe(B)
    expect(gh.githubConnectionIdOfKey('github:default')).toBe('')
    expect(gh.githubConnectionIdOfKey('other')).toBe('')
    expect(gh.GITHUB_EXTRA_CONNECTION_ID.test('default')).toBe(false)
  })
  it('resolves an absent key to the principal and a git source to none', () => {
    expect(gh.resolvedConnectionId({})).toBe('default')
    expect(gh.resolvedConnectionId(undefined)).toBe('default')
    expect(gh.resolvedConnectionId({ githubConnectionId: B })).toBe(B)
    expect(gh.resolvedConnectionId({ source: 'git', githubConnectionId: B })).toBe('')
  })
  it('counts implicit applications for the principal, never git sources or apps without repository', () => {
    expect(gh.githubConnectionUses({ repository: 'acme/web' }, 'default')).toBe(true)
    expect(gh.githubConnectionUses({ repository: 'acme/web', githubConnectionId: B }, 'default')).toBe(false)
    expect(gh.githubConnectionUses({ repository: 'acme/web', githubConnectionId: B }, B)).toBe(true)
    expect(gh.githubConnectionUses({ repository: '' }, 'default')).toBe(false)
    expect(gh.githubConnectionUses({ source: 'git', repository: 'git:x/y' }, '')).toBe(false)
    expect(gh.githubConnectionUses(undefined, 'default')).toBe(false)
  })
  it('treats sessions created before multi-connection as a legacy replacement (or creation) of the principal', () => {
    expect(gh.githubSessionTarget({ organization: 'acme' })).toEqual({
      connectionId: 'default',
      mode: 'replace',
      legacy: true,
    })
    expect(gh.githubSessionTarget({ connectionId: B, mode: 'add' })).toEqual({
      connectionId: B,
      mode: 'add',
      legacy: false,
    })
    expect(gh.githubSessionTarget({ connectionId: B })).toEqual({ connectionId: B, mode: 'add', legacy: false })
    expect(gh.githubSessionTarget({ connectionId: 'default', mode: 'replace' })).toEqual({
      connectionId: 'default',
      mode: 'replace',
      legacy: false,
    })
  })
  it('validates labels', () => {
    expect(gh.githubConnectionLabel('  Organisation Acme ')).toBe('Organisation Acme')
    for (const bad of ['', '   ', 'x'.repeat(61), 'a\nb', 'a\u0000', 'a\u2028', 42])
      expect(() => gh.githubConnectionLabel(bad)).toThrow('Libellé de connexion')
  })
  it('builds the legacy URL for the principal and a dedicated URL otherwise', () => {
    expect(gh.githubWebhookUrl('default')).toBe('https://app.example.com/api/web-apps/github/webhook')
    expect(gh.githubWebhookUrl(B)).toBe(`https://app.example.com/api/web-apps/github/webhook/${B}`)
  })
})

describe('connections', () => {
  it('reads the legacy principal without meta as an enabled « Connexion principale »', () => {
    const c = gh.webGithubConnection('default')
    expect(c.meta).toMatchObject({ label: 'Connexion principale', disabled: false })
    expect(gh.webGithubConnection(B).meta.label).toBe('Organisation Acme')
  })
  it('refuses unknown or disabled connections unless allowed', () => {
    expect(() => gh.webGithubConnection('ccccccccccccccc')).toThrow('Connexion GitHub introuvable ou désactivée.')
    db.settings = [principal(), second({ disabled: true })]
    expect(() => gh.webGithubConnection(B)).toThrow('Connexion GitHub introuvable ou désactivée.')
    expect(gh.webGithubConnection(B, store as any, { allowDisabled: true }).meta.disabled).toBe(true)
    db.settings = []
    expect(() => gh.webGithubConnection('default')).toThrow('Connexion GitHub introuvable ou désactivée.')
    expect(gh.webGithubConnection('default', store as any, { allowMissing: true }).value).toEqual({})
  })
  it('passes only reader fields to GITHUB_READER', () => {
    const config = gh.githubReaderConfig(gh.webGithubConnection(B).value)
    expect(Object.keys(config).sort()).toEqual(
      ['githubAppId', 'githubAppSlug', 'githubInstallationId', 'ownerLogin', 'privateKey'].sort()
    )
    expect(JSON.stringify(config)).not.toContain(SECRET_B)
  })
  it('serializes connections without any secret', () => {
    const result: any = gh.HandleWebGithubConnectionsList(event('', {}))
    const text = JSON.stringify(result.body)
    for (const secret of [SECRET_A, SECRET_B, 'AAAA-marker', 'BBBB-marker', 'valueEncrypted', 'privateKey"'])
      expect(text).not.toContain(secret)
    expect(result.body.max).toBe(10)
    expect(result.body.items).toHaveLength(2)
    expect(result.body.items[0]).toMatchObject({
      id: 'default',
      isPrincipal: true,
      configured: true,
      hasPrivateKey: true,
      hasWebhookSecret: true,
      webhookUrl: 'https://app.example.com/api/web-apps/github/webhook',
      appCount: 1,
      app: { id: '101', slug: 'pockethost-a', owner: 'perso' },
    })
    expect(result.body.items[1]).toMatchObject({ id: B, label: 'Organisation Acme', appCount: 1, isPrincipal: false })
  })
  it('enforces app and installation uniqueness across connections', () => {
    expect(() => gh.assertGithubConnectionUnique(store, '101', '9999', `github:${B}`)).toThrow(
      'Cette GitHub App est déjà connectée.'
    )
    expect(() => gh.assertGithubConnectionUnique(store, '9999', '2002', 'github')).toThrow(
      'Cette GitHub App est déjà connectée.'
    )
    expect(() => gh.assertGithubConnectionUnique(store, '202', '2002', `github:${B}`)).not.toThrow()
  })
  it('creates the principal when none exists, then additional connections', () => {
    db.settings = []
    const input = {
      githubAppId: '303',
      githubInstallationId: '3003',
      privateKey: PEM_A,
      webhookSecret: SECRET_A,
      label: 'Perso',
    }
    const first: any = gh.HandleWebGithubConnectionCreate(event('', {}, { input }))
    expect(first.status).toBe(201)
    expect(first.body.connection).toMatchObject({ id: 'default', label: 'Perso' })
    expect(db.settings[0]!.data.key).toBe('github')
    expect(failure(() => gh.HandleWebGithubConnectionCreate(event('', {}, { input }))).message).toBe(
      'Cette GitHub App est déjà connectée.'
    )
    const next: any = gh.HandleWebGithubConnectionCreate(
      event('', {}, { input: { ...input, githubAppId: '404', githubInstallationId: '4004' } })
    )
    expect(next.body.connection.id).toMatch(/^[a-z0-9]{15}$/)
    expect(next.body.connection.webhookUrl).toContain(`/webhook/${next.body.connection.id}`)
  })
  it('caps the number of connections', () => {
    db.settings = Array.from({ length: 10 }, (_, i) =>
      setting(i ? `github:${`${i}`.padStart(15, 'k')}` : 'github', {
        githubAppId: `${i}`,
        githubInstallationId: `${i}`,
      })
    )
    const input = { githubAppId: '999', githubInstallationId: '999', privateKey: PEM_A, webhookSecret: SECRET_A }
    expect(failure(() => gh.HandleWebGithubConnectionCreate(event('', {}, { input }))).message).toBe(
      '10 connexions GitHub maximum.'
    )
  })
  it('renames and disables without dropping secrets, writing meta only when needed', () => {
    gh.HandleWebGithubConnectionUpdate(
      event('', {}, { path: { connectionId: 'default' }, input: { githubAppSlug: 'pockethost-a2' } })
    )
    expect(JSON.parse(db.settings[0]!.data.valueEncrypted).meta).toBeUndefined()
    const result: any = gh.HandleWebGithubConnectionUpdate(
      event('', {}, { path: { connectionId: 'default' }, input: { label: 'Perso', disabled: true } })
    )
    expect(result.body.connection).toMatchObject({ label: 'Perso', disabled: true, hasPrivateKey: true })
    const stored = JSON.parse(db.settings[0]!.data.valueEncrypted)
    expect(stored).toMatchObject({ privateKey: PEM_A, webhookSecret: SECRET_A, githubAppSlug: 'pockethost-a2' })
    expect(stored.meta).toMatchObject({ label: 'Perso', disabled: true })
    expect(
      failure(() =>
        gh.HandleWebGithubConnectionUpdate(event('', {}, { path: { connectionId: 'ccccccccccccccc' }, input: {} }))
      )
    ).toBeInstanceOf(NotFoundError)
  })
})

describe('delete guard', () => {
  it('refuses while an application (implicit for the principal) or a queued job uses it', () => {
    const del = (id: string) => () => gh.HandleWebGithubConnectionDelete(event('', {}, { path: { connectionId: id } }))
    expect(failure(del('default')).message).toBe(
      'Cette connexion GitHub est encore utilisée par 1 application(s). Rattachez-les à une autre connexion avant de la supprimer.'
    )
    db.apps = db.apps.filter((r) => r.id !== 'appy00000000001')
    db.jobs = [
      new FakeRecord('web_deployments', {
        appId: 'appz00000000001',
        status: 'queued',
        snapshotEncrypted: JSON.stringify({ config: { repository: 'acme/web', githubConnectionId: B } }),
      }),
    ]
    expect(failure(del(B)).message).toContain('1 application(s)')
    db.jobs[0]!.set('status', 'ready')
    db.sessions = [
      new FakeRecord('web_github_setup_sessions', {
        status: 'awaiting_installation',
        publicInfo: { connectionId: B, mode: 'replace' },
        pendingEncrypted: 'x',
      }),
      new FakeRecord('web_github_setup_sessions', {
        status: 'awaiting_installation',
        publicInfo: { connectionId: 'ddddddddddddddd', mode: 'add' },
      }),
    ]
    const result: any = del(B)()
    expect(result.body).toEqual({
      deleted: true,
      notice: 'Supprimez aussi la GitHub App sur GitHub.',
      manageUrl: 'https://github.com/apps/pockethost-b',
    })
    expect(db.settings.map((r) => r.data.key)).toEqual(['github'])
    expect(db.sessions.map((r) => r.data.status)).toEqual(['cancelled', 'awaiting_installation'])
    expect(db.sessions[0]!.data.pendingEncrypted).toBe('')
  })
})

describe('webhook routing', () => {
  it('routes the legacy path to the principal and only queues its applications', () => {
    const raw = push('1001'),
      result: any = gh.webGithubWebhookFor(event(raw, signed(raw, SECRET_A, 'd-a')), 'default')
    expect(result.status).toBe(202)
    expect(calls.pushes).toEqual([{ appId: 'appx00000000001', head: 'a'.repeat(40) }])
    expect(calls.webGithub).toEqual([
      { action: 'commit', extra: { repository: 'acme/web', branch: 'main' }, connectionId: 'default' },
    ])
    expect(db.deliveries[0]!.data).toMatchObject({
      deliveryId: `gh-default-${sha(raw)}`,
      connectionId: 'default',
      status: 'queued',
    })
  })
  it('routes /webhook/{id} to that connection only', () => {
    const raw = push('2002'),
      result: any = gh.HandleWebGithubConnectionWebhook(
        event(raw, signed(raw, SECRET_B, 'd-b'), { path: { connectionId: B } })
      )
    expect(result.status).toBe(202)
    expect(calls.pushes).toEqual([{ appId: 'appy00000000001', head: 'b'.repeat(40) }])
    expect(db.deliveries[0]!.data.connectionId).toBe(B)
    // Doublon sur B : dédoublonné, rien de plus en file.
    expect(
      (
        gh.HandleWebGithubConnectionWebhook(
          event(raw, signed(raw, SECRET_B, 'd-b'), { path: { connectionId: B } })
        ) as any
      ).body
    ).toMatchObject({ duplicate: true })
    expect(calls.pushes).toHaveLength(1)
    // L'identifiant de livraison n'est pas signé : le corps signé rejoué sous un autre identifiant reste un doublon.
    expect(
      (
        gh.HandleWebGithubConnectionWebhook(
          event(raw, signed(raw, SECRET_B, 'autre-identifiant'), { path: { connectionId: B } })
        ) as any
      ).body
    ).toMatchObject({ duplicate: true })
    expect(calls.pushes).toHaveLength(1)
    expect(calls.webGithub).toHaveLength(1)
  })
  it('returns the same 401 for a wrong secret, an unknown id, a malformed id or `default` on the new path', () => {
    const raw = push('2002')
    const errors = [
      failure(() =>
        gh.HandleWebGithubConnectionWebhook(event(raw, signed(raw, SECRET_A, 'x'), { path: { connectionId: B } }))
      ),
      failure(() => gh.webGithubWebhookFor(event(raw, signed(raw, SECRET_B, 'x')), 'default')),
      failure(() =>
        gh.HandleWebGithubConnectionWebhook(
          event(raw, signed(raw, SECRET_B, 'x'), { path: { connectionId: 'ccccccccccccccc' } })
        )
      ),
      failure(() =>
        gh.HandleWebGithubConnectionWebhook(
          event(raw, signed(raw, SECRET_A, 'x'), { path: { connectionId: 'default' } })
        )
      ),
      failure(() =>
        gh.HandleWebGithubConnectionWebhook(event(raw, signed(raw, SECRET_B, 'x'), { path: { connectionId: 'B..' } }))
      ),
    ]
    for (const error of errors) {
      expect(error).toBeInstanceOf(UnauthorizedError)
      expect(error.message).toBe('Signature GitHub invalide.')
    }
    expect(db.deliveries).toHaveLength(0)
    expect(calls.webGithub).toHaveLength(0)
  })
  it('refuses an installation from another connection', () => {
    const raw = push('1001')
    expect(
      failure(() =>
        gh.HandleWebGithubConnectionWebhook(event(raw, signed(raw, SECRET_B, 'x'), { path: { connectionId: B } }))
      )
    ).toBeInstanceOf(ForbiddenError)
    const pr = JSON.stringify({
      action: 'opened',
      number: 1,
      repository: { full_name: 'acme/web' },
      installation: { id: 1001 },
    })
    expect(
      failure(() =>
        gh.HandleWebGithubConnectionWebhook(
          event(pr, signed(pr, SECRET_B, 'p', 'pull_request'), { path: { connectionId: B } })
        )
      )
    ).toBeInstanceOf(ForbiddenError)
  })
  it('hands pull requests to previews with the receiving connection', () => {
    const pr = JSON.stringify({
      action: 'opened',
      number: 1,
      repository: { full_name: 'acme/web' },
      installation: { id: 2002 },
    })
    gh.HandleWebGithubConnectionWebhook(
      event(pr, signed(pr, SECRET_B, 'p', 'pull_request'), { path: { connectionId: B } })
    )
    expect(calls.previews).toHaveLength(1)
    expect(calls.previews[0]!.slice(1)).toEqual([`gh-${B}-${sha(pr)}`, '', B])
  })
  it('records a signed delivery for a disabled connection and queues nothing', () => {
    db.settings = [principal(), second({ disabled: true })]
    const raw = push('2002'),
      result: any = gh.HandleWebGithubConnectionWebhook(
        event(raw, signed(raw, SECRET_B, 'd-off'), { path: { connectionId: B } })
      )
    expect(result).toEqual({ status: 200, body: { accepted: true, ignored: true } })
    expect(db.deliveries[0]!.data).toMatchObject({
      connectionId: B,
      status: 'ignored',
      note: 'Connexion GitHub désactivée : aucun déploiement.',
    })
    expect(calls.pushes).toHaveLength(0)
    expect(calls.webGithub).toHaveLength(0)
  })
})

describe('application config', () => {
  it('normalizes the principal to an absent key and checks a changed connection', () => {
    expect(gh.applyWebGithubConnectionConfig({ githubConnectionId: 'x' }, { githubConnectionId: 'default' })).toEqual(
      {}
    )
    expect(gh.applyWebGithubConnectionConfig({}, { githubConnectionId: '' }, { githubConnectionId: B })).toEqual({})
    expect(gh.applyWebGithubConnectionConfig({}, { githubConnectionId: B })).toEqual({ githubConnectionId: B })
    expect(gh.applyWebGithubConnectionConfig({ githubConnectionId: B }, { githubConnectionId: B }, {}, true)).toEqual(
      {}
    )
    expect(() => gh.applyWebGithubConnectionConfig({}, { githubConnectionId: 'BAD' })).toThrow(
      'Connexion GitHub invalide.'
    )
    expect(() => gh.applyWebGithubConnectionConfig({}, { githubConnectionId: 'ccccccccccccccc' })).toThrow(
      'Connexion GitHub introuvable ou désactivée.'
    )
    db.settings = [principal(), second({ disabled: true })]
    expect(() => gh.applyWebGithubConnectionConfig({}, { githubConnectionId: B })).toThrow(
      'Connexion GitHub introuvable ou désactivée.'
    )
    // Valeur inchangée (connexion désactivée depuis) : la configuration enregistrée reste lisible.
    expect(gh.applyWebGithubConnectionConfig({}, {}, { githubConnectionId: B })).toEqual({ githubConnectionId: B })
  })
  it('reports a count-only capability', () => {
    expect(gh.webGithubCapability()).toEqual({ githubConfigured: true, githubConnectionCount: 2 })
    db.settings = [second({ disabled: true })]
    expect(gh.webGithubCapability()).toEqual({ githubConfigured: false, githubConnectionCount: 0 })
  })
})
