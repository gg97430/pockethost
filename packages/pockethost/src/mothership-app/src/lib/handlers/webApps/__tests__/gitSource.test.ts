/**
 * Source « Dépôt Git (clé de déploiement) » : régressions du contrôleur sous un faux JSVM (aucun PocketBase, aucun
 * python3) — webhook (déduplication sur le corps signé, plafond avant ls-remote), scan d'un hôte publié, branches.
 */
import { createHash, createHmac } from 'node:crypto'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { webGitHostKeyPreset } from '../../../../../../common/webGit'
// gitSource.ts ne lit aucun global PocketBase au chargement : les faux globaux ci-dessous suffisent.
import * as git from '../gitSource'

vi.mock('../access', () => ({
  isWebPlatformAdmin: () => true,
  requirePlatformAdmin: () => ({ id: 'admin0000000000' }),
  requireWebPermission: () => ({}),
  requireWebUser: () => ({ id: 'admin0000000000' }),
}))
vi.mock('../api', () => ({
  decryptWebValue: (value: string) => JSON.parse(value || '{}'),
  encryptWebValue: (value: unknown) => JSON.stringify(value),
  webConfig: (row: any) => row.data.config || {},
  webEnabled: () => true,
  webInput: (e: any) => e.input || {},
  webJson: (_e: unknown, body: unknown, status = 200) => ({ status, body }),
  webSourceBuildSupported: () => true,
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
const hex = (value: string) => createHash('sha256').update(value).digest('hex')
const SECRET = 'S'.repeat(48)
const HOOK = 'H'.repeat(24)
let db: { sources: FakeRecord[]; apps: FakeRecord[]; deliveries: Map<string, FakeRecord>; store: Record<string, any> }
let reader: { calls: string[]; reply: (payload: any) => unknown }
let lastPayload = ''

const app = {
  dataDir: () => '/nonexistent',
  store: () => ({
    setFunc: (key: string, fn: (raw: unknown) => unknown) => {
      db.store[key] = fn(db.store[key])
    },
  }),
  findRecordById: (collection: string, id: string) => {
    const row = collection === 'web_git_sources' ? db.sources.find((r) => r.id === id) : undefined
    if (!row) throw new Error('not found')
    return row
  },
  findFirstRecordByFilter: (collection: string, _filter: string, params: any) => {
    const row =
      collection === 'web_git_sources'
        ? db.sources.find((r) => r.data.webhookId === params.id || r.data.canonical === params.canonical)
        : collection === 'web_webhook_deliveries'
          ? db.deliveries.get(params.id)
          : undefined
    if (!row) throw new Error('not found')
    return row
  },
  findRecordsByFilter: (collection: string) => (collection === 'web_apps' ? db.apps : []),
  findCollectionByNameOrId: (name: string) => name,
  runInTransaction: (fn: (tx: any) => void) => fn(app),
  save: (row: FakeRecord) => {
    if (row.collection === 'web_webhook_deliveries') db.deliveries.set(row.data.deliveryId, row)
  },
}
Object.assign(globalThis as any, {
  $app: app,
  $security: {
    hs256: (raw: string, secret: string) => createHmac('sha256', secret).update(raw).digest('hex'),
    equal: (a: string, b: string) => a === b,
    sha256: hex,
    randomString: (n: number) => 'n'.repeat(n),
  },
  $os: {
    getenv: () => '',
    writeFile: (_file: string, content: string) => {
      lastPayload = content
    },
    remove: () => {},
    cmd: () => ({
      output: () => {
        const payload = JSON.parse(lastPayload)
        reader.calls.push(payload.mode || payload.action)
        return JSON.stringify(reader.reply(payload))
      },
    }),
  },
  toString: (value: unknown) => String(value),
  readerToString: (value: unknown) => String(value),
  Record: FakeRecord,
  NotFoundError: class extends HttpError {},
  UnauthorizedError: class extends HttpError {},
  BadRequestError: class extends HttpError {},
  TooManyRequestsError: class extends HttpError {},
})

const source = (extra: Record<string, any> = {}) =>
  new FakeRecord('web_git_sources', {
    id: 'src000000000001',
    host: 'git.example.com',
    port: 22,
    sshUser: 'git',
    path: 'team/site.git',
    canonical: 'git.example.com/team/site',
    publicKey: 'ssh-ed25519 AAAA',
    privateKeyEncrypted: JSON.stringify({ privateKey: '-----BEGIN OPENSSH PRIVATE KEY-----\nx\n' }),
    hostKeys: [
      {
        type: 'ssh-ed25519',
        key: 'AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl',
        fingerprint: 'SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU',
      },
    ],
    hostKeysConfirmedAt: '2026-10-29T10:00:00Z',
    webhookId: HOOK,
    webhookSecretEncrypted: JSON.stringify({ secret: SECRET }),
    ...extra,
  })
const event = (raw: string, headers: Record<string, string>, extra: Record<string, any> = {}) =>
  ({
    request: {
      body: raw,
      pathValue: (name: string) => (name === 'webhookId' ? HOOK : name === 'sourceId' ? 'src000000000001' : ''),
      header: { get: (name: string) => headers[name] || '' },
      url: { query: () => ({ get: (name: string) => extra.query?.[name] || '' }) },
    },
    input: extra.input,
  }) as any
const push = (after: string) => JSON.stringify({ ref: 'refs/heads/main', after, deleted: false })
const github = (raw: string, delivery: string) => ({
  'X-GitHub-Event': 'push',
  'X-GitHub-Delivery': delivery,
  'X-Hub-Signature-256': 'sha256=' + createHmac('sha256', SECRET).update(raw).digest('hex'),
})

beforeEach(() => {
  db = {
    sources: [source()],
    apps: [
      new FakeRecord('web_apps', {
        id: 'app000000000001',
        config: { source: 'git', gitSourceId: 'src000000000001', branch: 'main', autoDeploy: true },
      }),
    ],
    deliveries: new Map(),
    store: {},
  }
  reader = { calls: [], reply: () => ({ commit: 'c'.repeat(40) }) }
})

describe('git source webhook', () => {
  it('dedupes on the signed body, not on the unsigned delivery header', () => {
    const raw = push('a'.repeat(40))
    const first = git.webGitWebhookDeliver(event(raw, github(raw, 'delivery-1')))
    expect(first.deploy).toEqual([{ appId: 'app000000000001', commit: 'c'.repeat(40) }])
    // Même corps signé rejoué avec un nouvel X-GitHub-Delivery : doublon, aucune nouvelle relecture ls-remote.
    for (const id of ['delivery-2', 'delivery-3', 'x'])
      expect(git.webGitWebhookDeliver(event(raw, github(raw, id))).body).toMatchObject({ duplicate: true })
    expect(reader.calls).toEqual(['resolve'])
    expect([...db.deliveries.keys()]).toEqual([`git-src000000000001-${hex(raw)}`])
    // L'identifiant de livraison (lisible par les lecteurs de l'application) ne révèle pas l'URL du webhook.
    expect([...db.deliveries.keys()][0]).not.toContain(HOOK)
  })
  it('caps ls-remote lookups per source when distinct deliveries flood the webhook', () => {
    let refused = 0
    for (let i = 1; i <= 40; i++) {
      const raw = push(i.toString(16).padStart(40, '0'))
      try {
        git.webGitWebhookDeliver(event(raw, { 'X-Gitlab-Event': 'Push Hook', 'X-Gitlab-Token': SECRET }))
      } catch (error) {
        expect((error as Error).message).toContain('Trop de contrôles Git')
        refused++
      }
    }
    expect(reader.calls.length).toBe(20)
    expect(refused).toBe(20)
  })
  it('rejects a bad signature before any lookup', () => {
    const raw = push('a'.repeat(40))
    expect(() =>
      git.webGitWebhookDeliver(event(raw, { ...github(raw, 'd'), 'X-Hub-Signature-256': 'sha256=00' }))
    ).toThrow('Signature Git invalide.')
    expect(reader.calls).toEqual([])
  })
})

describe('git source scan and branches', () => {
  it('reports a published host as confirmed and pins it when the row was left unpinned', () => {
    const published = webGitHostKeyPreset('github.com')
    db.sources = [
      source({
        host: 'github.com',
        path: 'o/r.git',
        canonical: 'github.com/o/r',
        hostKeys: [],
        hostKeysConfirmedAt: '',
      }),
    ]
    reader.reply = () => ({
      hostKeys: published.map((k: any) => ({ type: k.type, key: k.key, fingerprint: k.fingerprint })),
    })
    const result: any = git.HandleWebGitSourceScan(event('', {}))
    expect(result.body).toMatchObject({ preset: true, matches: true, confirmed: true })
    expect(result.body.source.hostKeysConfirmed).toBe(true)
    expect(result.body.source.hostKeys.every((k: any) => k.preset)).toBe(true)
  })
  it('returns every branch in one page when asked (no 50-branch cut)', () => {
    const branches = Array.from({ length: 120 }, (_, i) => ({
      name: `b${String(i).padStart(3, '0')}`,
      commit: 'a'.repeat(40),
    }))
    reader.reply = () => ({ branches, defaultBranch: 'b119', truncated: false })
    const result: any = git.HandleWebGitSourceBranches(event('', {}, { query: { perPage: '5000' } }))
    expect(result.body).toMatchObject({ totalItems: 120, hasMore: false, perPage: 5000 })
    expect(result.body.items).toHaveLength(120)
    expect(result.body.items[0].name).toBe('b119')
  })
})
