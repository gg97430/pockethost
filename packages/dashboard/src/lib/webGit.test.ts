import { describe, expect, it } from 'vitest'
import {
  canonicalGitRemote,
  isWebGitPresetHost,
  isWebGitRepository,
  parseGitRemote,
  WEB_GIT_DEPLOY_KEY_STEPS,
  webGitApi,
  webGitBranchValid,
  webGitDeployKeysUrl,
  webGitFingerprint,
  webGitKeyType,
  webGitProvider,
  webGitPublicKeyValid,
  webGitRepository,
  webGitRepositoryLabel,
  webGitSameFingerprints,
  webGitSource,
  webGitSourceRemote,
  webGitWebhookGuide,
  webGitWebhookSettingsUrl,
  webGitWebhookUrl,
  type WebGitSendOptions,
} from './webGit'

const fp = 'SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU'
const fp2 = 'SHA256:eUXGGm1YGsMAS7vkcx6JOJdOGHPem5gQp4taiCfCLB8'
const id = 'abcdefghijklmno'
const publicKey = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl kolab-web-' + id

describe('git remote parsing', () => {
  it('accepts scp-like and ssh:// remotes and builds a lowercased canonical', () => {
    expect(parseGitRemote('  git@GitHub.com:Owner/Site.git ')).toEqual({
      user: 'git',
      host: 'github.com',
      port: 22,
      path: 'Owner/Site.git',
      canonical: 'github.com/owner/site',
      sshUrl: 'git@github.com:Owner/Site.git',
      provider: 'github',
    })
    const gitlab = parseGitRemote('ssh://git@gitlab.com/group/sub/app.git')
    expect(gitlab.canonical).toBe('gitlab.com/group/sub/app')
    expect(gitlab.provider).toBe('gitlab')
    const custom = parseGitRemote('ssh://forge@git.example.org:2222/team/app')
    expect(custom).toMatchObject({ user: 'forge', port: 2222, provider: 'other' })
    expect(custom.canonical).toBe('forge@git.example.org:2222//team/app')
    expect(custom.sshUrl).toBe('ssh://forge@git.example.org:2222/team/app')
    expect(parseGitRemote('ssh://code.example.org/srv/repo.git').user).toBe('git')
    expect(parseGitRemote('git@code.example.org:/srv/repo.git').canonical).toBe('code.example.org//srv/repo')
    expect(canonicalGitRemote({ host: 'Git.Example.org', port: 22, path: '/A/B.git/' })).toBe('git.example.org//A/B')
    expect(canonicalGitRemote({ host: 'GitHub.com', port: 22, path: '/A/B.git/' })).toBe('github.com/a/b')
  })
  it('keeps absolute paths absolute, as sent to plain sshd servers', () => {
    // Régression : le « / » initial était retiré, transformant /srv/git/site.git en chemin relatif au HOME.
    const scp = parseGitRemote('git@git.example.com:/srv/git/site.git')
    expect(scp).toMatchObject({ path: '/srv/git/site.git', sshUrl: 'git@git.example.com:/srv/git/site.git' })
    const ssh = parseGitRemote('ssh://deploy@git.example.com/srv/git/site.git')
    expect(ssh).toMatchObject({ path: '/srv/git/site.git', sshUrl: 'deploy@git.example.com:/srv/git/site.git' })
    expect(parseGitRemote('git@git.example.com:srv/git/site.git').canonical).not.toBe(scp.canonical)
    expect(parseGitRemote('alice@git.example.com:site.git').canonical).not.toBe(
      parseGitRemote('bob@git.example.com:site.git').canonical
    )
    expect(() => parseGitRemote('git@127.0.0.1:o/r')).toThrow()
  })
  it('rejects other transports, traversal, options and control characters', () => {
    for (const value of [
      '',
      'https://github.com/owner/site.git',
      'http://github.com/owner/site',
      'file:///srv/repo.git',
      'ext::sh -c touch% /tmp/x',
      'git::ssh://host/repo',
      'github.com:owner/site',
      '-oProxyCommand=touch@host:repo',
      'git@-oProxyCommand=x:repo',
      'git@host:../etc/passwd',
      'git@host:owner/../site',
      'git@host:owner/a..b',
      'git@host:owner/-site',
      'git@host:owner/si\nte',
      'git@host:owner/si te',
      'git@host:',
      'git@host:.git',
      'ssh://git@host:0/repo',
      'ssh://git@host:65536/repo',
      'ssh://git@host:22',
      'ssh://git@[::1]/repo',
      'ssh://user:pass@host/repo',
      'git@host_name:repo',
      'git@host:repo;rm -rf',
    ])
      expect(() => parseGitRemote(value), value).toThrow()
  })
  it('detects providers, preset hosts and repository markers', () => {
    expect(webGitProvider('gitlab.example.org')).toBe('gitlab')
    expect(webGitProvider('codeberg.org')).toBe('gitea')
    expect(webGitProvider('forgejo.example.org')).toBe('gitea')
    expect(webGitProvider('bitbucket.org')).toBe('bitbucket')
    expect(webGitProvider('git.example.org')).toBe('other')
    expect(isWebGitPresetHost('GitHub.com')).toBe(true)
    expect(isWebGitPresetHost('github.com', 443)).toBe(false)
    expect(isWebGitPresetHost('gitlab.example.org')).toBe(false)
    expect(webGitRepository('gitlab.com/a/b')).toBe('git:gitlab.com/a/b')
    expect(isWebGitRepository('git:gitlab.com/a/b')).toBe(true)
    expect(isWebGitRepository('owner/repo')).toBe(false)
    expect(webGitRepositoryLabel('git:gitlab.com/a/b')).toBe('gitlab.com/a/b')
    expect(webGitRepositoryLabel('owner/repo')).toBe('owner/repo')
  })
  it('links the host pages for deploy keys and webhooks, with read-only instructions', () => {
    const remote = { host: 'github.com', path: 'Owner/Site.git' }
    expect(webGitDeployKeysUrl(remote)).toBe('https://github.com/Owner/Site/settings/keys')
    expect(webGitWebhookSettingsUrl(remote)).toBe('https://github.com/Owner/Site/settings/hooks/new')
    expect(webGitDeployKeysUrl({ host: 'gitlab.com', path: 'g/s/app' })).toBe(
      'https://gitlab.com/g/s/app/-/settings/repository#js-deploy-keys-settings'
    )
    expect(webGitDeployKeysUrl({ host: 'codeberg.org', path: 'me/app.git' })).toBe(
      'https://codeberg.org/me/app/settings/keys'
    )
    expect(webGitDeployKeysUrl({ host: 'bitbucket.org', path: 'ws/app.git' })).toBe(
      'https://bitbucket.org/ws/app/admin/access-keys/'
    )
    expect(webGitDeployKeysUrl({ host: 'git.example.org', path: 'a/b' })).toBeNull()
    expect(webGitDeployKeysUrl({ host: 'git.example.org', path: 'a/b' }, 'gitea')).toBe(
      'https://git.example.org/a/b/settings/keys'
    )
    for (const steps of Object.values(WEB_GIT_DEPLOY_KEY_STEPS)) expect(steps).toContain('lecture seule')
    expect(WEB_GIT_DEPLOY_KEY_STEPS.github).toContain('Settings → Deploy keys')
    expect(webGitWebhookGuide('gitlab').steps.join(' ')).toContain('Secret token')
    expect(webGitWebhookGuide('gitea').steps.join(' ')).toContain('application/json')
    // Le contrôleur vérifie X-Hub-Signature (sha256=…) de Bitbucket Cloud : le webhook doit être proposé.
    expect(webGitWebhookGuide('bitbucket')).toMatchObject({ supported: true })
    expect(webGitWebhookGuide('bitbucket').steps.join(' ')).toContain('Repository push')
    expect(webGitWebhookSettingsUrl({ host: 'bitbucket.org', path: 'ws/app.git' })).toBe(
      'https://bitbucket.org/ws/app/admin/webhooks'
    )
    expect(webGitWebhookGuide('other').steps.join(' ')).toContain('X-Gitlab-Token')
  })
})

describe('fingerprints, keys and branches', () => {
  it('normalizes and compares SHA-256 fingerprints as sets', () => {
    expect(webGitFingerprint(fp.slice(7))).toBe(fp)
    expect(webGitFingerprint('MD5:aa:bb')).toBe('')
    expect(webGitSameFingerprints([fp, fp2], [fp2, fp])).toBe(true)
    expect(webGitSameFingerprints([fp], [fp, fp2])).toBe(false)
    expect(webGitSameFingerprints([], [])).toBe(false)
    expect(webGitSameFingerprints(['bad'], ['bad'])).toBe(false)
    expect(webGitKeyType('ssh-ed25519')).toBe('Ed25519')
    expect(webGitKeyType('ecdsa-sha2-nistp256')).toBe('ECDSA')
    expect(webGitKeyType('ssh-rsa')).toBe('RSA')
  })
  it('validates the generated public line and branch names', () => {
    expect(webGitPublicKeyValid(publicKey)).toBe(true)
    expect(webGitPublicKeyValid('ssh-rsa AAAAB3NzaC1yc2E')).toBe(false)
    expect(webGitPublicKeyValid(publicKey + '\nssh-ed25519 AAAA')).toBe(false)
    expect(webGitPublicKeyValid('-----BEGIN OPENSSH PRIVATE KEY-----')).toBe(false)
    for (const name of ['main', 'release/1.2', 'feature_x']) expect(webGitBranchValid(name)).toBe(true)
    for (const name of ['', '-x', 'a..b', 'a b', '/main', 'main/', 'x.lock', 'a;b'])
      expect(webGitBranchValid(name), name).toBe(false)
  })
})

describe('source normalization', () => {
  it('keeps only public fields and derives flags', () => {
    const source = webGitSource({
      source: {
        id,
        host: 'Git.Example.org',
        port: 2222,
        path: 'team/app.git',
        publicKey,
        fingerprint: fp,
        privateKeyEncrypted: 'never',
        webhookSecretEncrypted: 'never',
        hostKeys: [
          { type: 'ssh-ed25519', key: 'AAAA', fingerprint: fp },
          { type: 'x', fingerprint: 'bad' },
        ],
        hostKeysConfirmedAt: '2026-10-29 10:00:00.000Z',
        webhook: { url: 'https://app.monappli.re/api/web-git-sources/webhook/' + 'a'.repeat(24), configured: true },
        appCount: 3,
      },
    })!
    expect(source).toMatchObject({
      host: 'git.example.org',
      sshUser: 'git',
      canonical: 'git.example.org:2222/team/app',
      hostKeysConfirmed: true,
      webhookConfigured: true,
      webhookUrl: 'https://app.monappli.re/api/web-git-sources/webhook/' + 'a'.repeat(24),
      appCount: 3,
    })
    expect(source.hostKeys).toEqual([{ type: 'ssh-ed25519', fingerprint: fp, preset: false }])
    expect(JSON.stringify(source)).not.toContain('never')
    expect(webGitSourceRemote(source).sshUrl).toBe('ssh://git@git.example.org:2222/team/app.git')
    expect(webGitSource({ id: 'bad' })).toBeNull()
    expect(webGitSource({ id, hostKeysConfirmedAt: '2026', hostKeys: [] })!.hostKeysConfirmed).toBe(false)
    // Une adresse de webhook sans secret n'est pas « configurée » ; un décompte invalide vaut 0.
    expect(webGitSource({ id, webhook: { url: 'https://x/y', configured: false }, appCount: 'x' })).toMatchObject({
      webhookConfigured: false,
      webhookUrl: 'https://x/y',
      appCount: 0,
    })
  })
  it('builds webhook urls only from a valid id', () => {
    expect(webGitWebhookUrl('https://app.monappli.re/', 'A'.repeat(24))).toBe(
      'https://app.monappli.re/api/web-git-sources/webhook/' + 'A'.repeat(24)
    )
    expect(webGitWebhookUrl('https://app.monappli.re', '../x')).toBe('')
  })
})

describe('api client', () => {
  const calls: [string, WebGitSendOptions | undefined][] = []
  const responses: Record<string, unknown> = {}
  const api = webGitApi(async <T>(path: string, options?: WebGitSendOptions) => {
    calls.push([path, options])
    return (responses[path] ?? { source: { id } }) as T
  })
  it('targets the planned routes with explicit methods and bodies', async () => {
    calls.length = 0
    await api.create('git@gitlab.com:g/app.git', 'p'.repeat(15))
    await api.get(id)
    await api.confirmHostKeys(id, 'nonce', [fp.slice(7)])
    await api.branches(id)
    await api.remove(id)
    expect(calls).toEqual([
      [
        '/api/web-git-sources',
        { method: 'POST', body: { url: 'git@gitlab.com:g/app.git', projectId: 'p'.repeat(15) } },
      ],
      ['/api/web-git-sources/' + id, undefined],
      [
        '/api/web-git-sources/' + id + '/confirm-host-keys',
        { method: 'POST', body: { nonce: 'nonce', fingerprints: [fp] } },
      ],
      ['/api/web-git-sources/' + id + '/branches?perPage=5000', undefined],
      ['/api/web-git-sources/' + id, { method: 'DELETE' }],
    ])
    await expect(api.get('../admin')).rejects.toThrow()
    await expect(api.create('https://github.com/a/b')).rejects.toThrow()
    // L'adresse envoyée est celle saisie (rognée), chemin absolu compris.
    await api.create('  git@git.example.com:/srv/site.git ')
    expect(calls.pop()).toEqual([
      '/api/web-git-sources',
      { method: 'POST', body: { url: 'git@git.example.com:/srv/site.git' } },
    ])
    expect(calls).toHaveLength(5)
  })
  it('normalizes scan, test, branches, rotation and webhook responses', async () => {
    // Formes exactes renvoyées par gitSource.ts (HandleWebGitSourceScan, Branches, RotateWebhookSecret).
    responses[`/api/web-git-sources/${id}/scan`] = {
      preset: true,
      matches: true,
      hostKeys: [{ type: 'ssh-ed25519', fingerprint: fp }],
      source: { id, host: 'github.com', hostKeys: [{ type: 'ssh-ed25519', fingerprint: fp, preset: true }] },
    }
    responses[`/api/web-git-sources/${id}/test`] = { ok: true, checkedAt: 'now', defaultBranch: 'main' }
    responses[`/api/web-git-sources/${id}/branches?perPage=5000`] = {
      items: [{ name: 'main', commit: 'a'.repeat(40) }, { name: '-bad' }],
      page: 1,
      perPage: 5000,
      totalItems: 2,
      hasMore: false,
      defaultBranch: 'main',
      truncated: true,
    }
    responses[`/api/web-git-sources/${id}/rotate-key`] = { publicKey, fingerprint: fp }
    responses[`/api/web-git-sources/${id}/rotate-webhook-secret`] = {
      secret: 's3cr3t',
      url: 'https://x/y',
      source: { id, webhook: { url: 'https://x/y', configured: true }, appCount: 2 },
    }
    expect(await api.scan(id)).toMatchObject({ nonce: '', preset: true, confirmed: true, source: { id } })
    expect(await api.test(id)).toMatchObject({ ok: true, defaultBranch: 'main' })
    expect(await api.branches(id)).toEqual({
      items: [{ name: 'main', commit: 'a'.repeat(40) }],
      defaultBranch: 'main',
      truncated: true,
    })
    responses[`/api/web-git-sources/${id}/branches?perPage=5000`] = { items: [], hasMore: true, truncated: false }
    expect((await api.branches(id)).truncated).toBe(true)
    expect(await api.rotateKey(id)).toEqual({ source: null, publicKey, fingerprint: fp })
    expect(calls.at(-1)).toEqual([`/api/web-git-sources/${id}/rotate-key`, { method: 'POST', body: { confirm: true } }])
    const rotated = await api.rotateWebhookSecret(id)
    expect(rotated).toMatchObject({ webhookUrl: 'https://x/y', secret: 's3cr3t' })
    expect(rotated.source).toMatchObject({ webhookConfigured: true, webhookUrl: 'https://x/y', appCount: 2 })
  })
})
