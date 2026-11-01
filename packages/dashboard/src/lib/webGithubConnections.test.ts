import { describe, expect, it } from 'vitest'
import type { WebGitHubConnectionSummary } from './webApps'
import {
  webAppGithubConnectionId,
  webGithubActiveConnections,
  webGithubAppCountLabel,
  webGithubCompletionMessage,
  webGithubConnectionAnchor,
  webGithubConnectionFromResponse,
  webGithubConnectionHref,
  webGithubConnectionIdValid,
  webGithubConnectionLabel,
  webGithubConnectionOption,
  webGithubConnectionPayload,
  webGithubConnectionQuery,
  webGithubConnectionsHeading,
  webGithubConnectionStatus,
  webGithubConnectStartBody,
  webGithubDefaultSelection,
  webGithubDeliveryLabel,
  webGithubLabelError,
} from './webGithubConnections'

const B = 'abcdefghij12345'
const connection = (patch: Partial<WebGitHubConnectionSummary> = {}): WebGitHubConnectionSummary => ({
  id: 'default',
  label: '',
  isPrincipal: true,
  disabled: false,
  configured: true,
  app: { id: '1', slug: 'pockethost-app1', owner: 'kolab' },
  installationId: '10',
  hasPrivateKey: true,
  hasWebhookSecret: true,
  webhookUrl: 'https://app.example/api/web-apps/github/webhook',
  appCount: 0,
  lastDelivery: null,
  ...patch,
})

describe('GitHub connection identifiers', () => {
  it('accepts only the principal id or 15 lowercase alphanumerics', () => {
    expect(webGithubConnectionIdValid('default')).toBe(true)
    expect(webGithubConnectionIdValid(B)).toBe(true)
    for (const id of ['', 'abcdefghij1234', 'abcdefghij123456', 'ABCDEFGHIJ12345', 'abc/../defghij1', 1, null])
      expect(webGithubConnectionIdValid(id)).toBe(false)
  })
  it('resolves the application connection: principal when absent, none for a Git source', () => {
    expect(webAppGithubConnectionId({})).toBe('default')
    expect(webAppGithubConnectionId({ source: 'github', githubConnectionId: B })).toBe(B)
    expect(webAppGithubConnectionId({ source: 'git', githubConnectionId: B })).toBe('')
    expect(webAppGithubConnectionId(null)).toBe('')
  })
  it('omits the query parameter for the principal and for unknown ids', () => {
    expect(webGithubConnectionQuery('default')).toEqual({})
    expect(webGithubConnectionQuery('')).toEqual({})
    expect(webGithubConnectionQuery('x/../y')).toEqual({})
    expect(webGithubConnectionQuery(B)).toEqual({ connectionId: B })
  })
  it('normalizes the config payload to the principal', () => {
    expect(webGithubConnectionPayload('')).toBe('default')
    expect(webGithubConnectionPayload('nope')).toBe('default')
    expect(webGithubConnectionPayload(B)).toBe(B)
  })
  it('builds card anchors only for valid ids', () => {
    expect(webGithubConnectionAnchor(B)).toBe(`connection-${B}`)
    expect(webGithubConnectionHref(B)).toBe(`/applications/settings#connection-${B}`)
    expect(webGithubConnectionHref('"><script>')).toBe('/applications/settings')
  })
})

describe('GitHub connection selection', () => {
  const items = [
    connection(),
    connection({ id: B, label: 'Org B', isPrincipal: false, app: { id: '2', slug: 'b', owner: 'acme' } }),
    connection({ id: 'zzzzzzzzzzzzzzz', isPrincipal: false, disabled: true }),
    connection({ id: 'yyyyyyyyyyyyyyy', isPrincipal: false, configured: false }),
  ]
  it('lists only configured and enabled connections', () => {
    expect(webGithubActiveConnections(items).map((c) => c.id)).toEqual(['default', B])
    expect(webGithubActiveConnections(undefined)).toEqual([])
  })
  it('keeps the current active choice, then prefers the principal, then the first active one', () => {
    expect(webGithubDefaultSelection(items, B)).toBe(B)
    expect(webGithubDefaultSelection(items, 'zzzzzzzzzzzzzzz')).toBe('default')
    expect(webGithubDefaultSelection(items)).toBe('default')
    expect(webGithubDefaultSelection([items[1]!, { ...items[0]!, disabled: true }])).toBe(B)
    expect(webGithubDefaultSelection([])).toBe('default')
  })
  it('labels connections without exposing anything but the label and the owner', () => {
    expect(webGithubConnectionLabel(items[0])).toBe('Connexion principale')
    expect(webGithubConnectionLabel(items[1])).toBe('Org B')
    expect(webGithubConnectionLabel(connection({ id: B, isPrincipal: false }))).toBe('kolab')
    expect(webGithubConnectionOption(items[0]!)).toBe('Connexion principale · kolab (principale)')
    expect(webGithubConnectionOption(items[1]!)).toBe('Org B · acme')
  })
})

describe('GitHub connection labels and statuses', () => {
  it('validates labels like the server', () => {
    expect(webGithubLabelError('  ')).toBe('Indiquez un libellé.')
    expect(webGithubLabelError('a'.repeat(61))).toContain('60')
    expect(webGithubLabelError('a'.repeat(60))).toBe('')
    // Mesure du serveur (unités UTF-16) : 31 émojis = 62 unités, refusés comme côté PocketBase.
    expect(webGithubLabelError('😀'.repeat(31))).toContain('60')
    expect(webGithubLabelError('Acme\nx')).toContain('retour')
    expect(webGithubLabelError('Organisation Acme')).toBe('')
  })
  it('derives the card badge from the connection then its health', () => {
    expect(webGithubConnectionStatus({ disabled: true, configured: true }, { ok: true, configured: true })).toEqual({
      label: 'Désactivée',
      tone: 'neutral',
    })
    expect(webGithubConnectionStatus({ disabled: false, configured: true }, { ok: true, configured: true }).label).toBe(
      'Opérationnelle'
    )
    expect(
      webGithubConnectionStatus(
        { disabled: false, configured: true },
        { ok: true, configured: true, webhook: { urlMismatch: true } }
      ).label
    ).toBe('À vérifier')
    expect(webGithubConnectionStatus({ disabled: false, configured: true }, null, true).label).toBe('Vérification…')
    expect(webGithubConnectionStatus({ disabled: false, configured: true }, { ok: false, configured: true }).tone).toBe(
      'warning'
    )
  })
  it('formats usage, deliveries and the heading', () => {
    expect(webGithubAppCountLabel(0)).toBe('Aucune application rattachée')
    expect(webGithubAppCountLabel(1)).toBe('Utilisée par 1 application')
    expect(webGithubAppCountLabel(4)).toBe('Utilisée par 4 applications')
    const now = Date.parse('2026-10-29T12:00:00Z')
    expect(webGithubDeliveryLabel({ at: '2026-10-29T11:57:00Z', status: 'queued' }, now)).toBe(
      'il y a 3 min (déployée)'
    )
    expect(webGithubDeliveryLabel({ at: '2026-10-29T09:00:00Z', status: 'ignored' }, now)).toBe('il y a 3 h (ignorée)')
    expect(webGithubDeliveryLabel({ at: '2026-10-29T11:59:50Z' }, now)).toBe('à l’instant')
    expect(webGithubDeliveryLabel(null, now)).toBe('Aucune livraison reçue')
    expect(webGithubConnectionsHeading(2)).toBe('Connexions GitHub (2/10)')
  })
})

describe('GitHub wizard requests and results', () => {
  it('adds by default and replaces only an explicit, valid connection', () => {
    expect(webGithubConnectStartBody({})).toEqual({ mode: 'add' })
    expect(webGithubConnectStartBody({ organization: ' acme ', label: ' Org Acme ' })).toEqual({
      mode: 'add',
      organization: 'acme',
      label: 'Org Acme',
    })
    expect(webGithubConnectStartBody({ mode: 'add', connectionId: B })).toEqual({ mode: 'add' })
    expect(webGithubConnectStartBody({ mode: 'replace', connectionId: B })).toEqual({
      mode: 'replace',
      connectionId: B,
    })
    expect(() => webGithubConnectStartBody({ mode: 'replace', connectionId: '../x' })).toThrow()
  })
  it('reads a connection from the create/update response shapes', () => {
    expect(webGithubConnectionFromResponse({ connection: { id: B } })?.id).toBe(B)
    expect(webGithubConnectionFromResponse({ item: { id: 'default' } })?.id).toBe('default')
    expect(webGithubConnectionFromResponse({ id: B })?.id).toBe(B)
    expect(webGithubConnectionFromResponse({ connection: { id: 'bad id' } })).toBeNull()
    expect(webGithubConnectionFromResponse(null)).toBeNull()
  })
  it('announces the wizard outcome with the label or the account', () => {
    expect(webGithubCompletionMessage({ label: 'Acme', repositoryCount: 12 })).toBe(
      'Connexion « Acme » ajoutée. 12 dépôts autorisés.'
    )
    expect(webGithubCompletionMessage({ account: 'acme', mode: 'replace', repositoryCount: 1 })).toBe(
      'Connexion « acme » mise à jour. 1 dépôt autorisé.'
    )
    expect(webGithubCompletionMessage({ repositoryCount: 0 })).toBe('Connexion GitHub ajoutée. 0 dépôt autorisé.')
  })
})
