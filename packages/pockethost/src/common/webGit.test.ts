import { createHash } from 'node:crypto'
import { describe, expect, it } from 'vitest'
import {
  WEB_GIT_HOST_KEY_PRESETS,
  canonicalGitRemote,
  parseGitRemote,
  webGitBranchValid,
  webGitDisplayUrl,
  webGitFingerprintSet,
  webGitHostKeyPreset,
  webGitProvider,
  webGitRepository,
} from './webGit'

describe('parseGitRemote', () => {
  it('accepts scp-like and ssh:// forms', () => {
    expect(parseGitRemote('git@github.com:Owner/Repo.git')).toEqual({
      host: 'github.com',
      port: 22,
      sshUser: 'git',
      path: 'Owner/Repo.git',
      canonical: 'github.com/owner/repo',
    })
    expect(parseGitRemote('ssh://git@GitLab.com/group/sub/project.git')).toMatchObject({
      host: 'gitlab.com',
      path: '/group/sub/project.git',
      canonical: 'gitlab.com/group/sub/project',
    })
    expect(parseGitRemote('ssh://forgejo@code.example.org:2222/team/app')).toEqual({
      host: 'code.example.org',
      port: 2222,
      sshUser: 'forgejo',
      path: '/team/app',
      canonical: 'forgejo@code.example.org:2222//team/app',
    })
    expect(parseGitRemote('  git.example.com:srv/repo.git  ').sshUser).toBe('git')
    expect(parseGitRemote('ssh://git@git.example.com/~alice/repo.git').path).toBe('~alice/repo.git')
    expect(parseGitRemote('ssh://git@git.example.com:22/a/b').canonical).toBe('git.example.com//a/b')
  })
  it('gives the same canonical to both forms of one repository', () => {
    expect(parseGitRemote('git@github.com:o/r.git').canonical).toBe(
      parseGitRemote('ssh://git@github.com/o/r').canonical
    )
    expect(webGitRepository(parseGitRemote('git@github.com:o/r.git'))).toBe('git:github.com/o/r')
    // GitHub, GitLab.com et Bitbucket ignorent la casse : une seule source (une seule clé) par dépôt.
    expect(parseGitRemote('git@github.com:Owner/Repo.git').canonical).toBe(
      parseGitRemote('ssh://git@github.com/owner/REPO.git').canonical
    )
    expect(parseGitRemote('git@bitbucket.org:Team/Site.git').canonical).toBe('bitbucket.org/team/site')
  })
  it('never gives one canonical to distinct repositories of a plain SSH server', () => {
    const canonicals = [
      'alice@srv.example.com:app.git',
      'bob@srv.example.com:app.git',
      'git@srv.example.com:app.git',
      'git@srv.example.com:srv/app.git',
      'ssh://git@srv.example.com/srv/app.git',
      'git@srv.example.com:/srv/app.git',
      'git@srv.example.com:Srv/App.git',
      'ssh://git@srv.example.com:2222/srv/app.git',
    ].map((url) => parseGitRemote(url).canonical)
    expect(canonicals).toEqual([
      'alice@srv.example.com/app',
      'bob@srv.example.com/app',
      'srv.example.com/app',
      'srv.example.com/srv/app',
      'srv.example.com//srv/app',
      'srv.example.com//srv/app',
      'srv.example.com/Srv/App',
      'srv.example.com:2222//srv/app',
    ])
    // Même tuple de transport (absolu en scp ou en ssh://) : même source.
    expect(canonicals[4]).toBe(canonicals[5])
  })
  it.each([
    'https://github.com/o/r.git',
    'http://github.com/o/r.git',
    'git://github.com/o/r.git',
    'file:///etc/passwd',
    'ext::sh -c touch% /tmp/x',
    'ssh+git://github.com/o/r',
    'git+ssh://github.com/o/r',
    'ssh://github.com',
    'ssh://github.com/',
    'git@github.com:',
    'git@github.com:../etc',
    'git@github.com:o/../r',
    'git@github.com:o/./r',
    'git@github.com:o//r',
    'git@github.com://r',
    'git@github.com:-oProxyCommand=x',
    'git@github.com:o/r/.git',
    '-oProxyCommand=x:o/r',
    'git@-host.example.com:o/r',
    'git@127.0.0.1:o/r',
    'git@[::1]:o/r',
    'ssh://git@[::1]/o/r',
    'git@localhost:o/r',
    'git@gitlab.local:o/r',
    'git@svc.internal:o/r',
    'git@intranet:o/r',
    'ssh://git@github.com:0/o/r',
    'ssh://git@github.com:65536/o/r',
    'ssh://git@github.com:022/o/r',
    'ssh://git@github.com:abc/o/r',
    '-git@github.com:o/r',
    'g it@github.com:o/r',
    'git@github.com:o/r\nProxyCommand',
    'git@github.com:o/r\u0000',
    'git@github.com:o r',
    'git@github.com:o/r%2e',
    'git@github.com:o\\r',
    "git@github.com:o/'r",
    'a@b@github.com:o/r',
    'git@github.com:o:r',
    `git@github.com:${'a/'.repeat(130)}r`,
  ])('rejects %j', (url) => {
    expect(() => parseGitRemote(url)).toThrow(/Adresse SSH du dépôt invalide/)
  })
  it('rejects non-strings and never echoes the input', () => {
    expect(() => parseGitRemote(undefined)).toThrow()
    expect(() => parseGitRemote(42)).toThrow()
    try {
      parseGitRemote('https://secret-token@github.com/o/r')
    } catch (error) {
      expect(`${(error as Error).message}`).not.toContain('secret-token')
    }
  })
})

describe('helpers', () => {
  it('canonical drops .git and default port, keeps path case off the case-insensitive forges', () => {
    expect(canonicalGitRemote({ host: 'Git.Example.com', port: 22, path: '/A/B.git' })).toBe('git.example.com//A/B')
    expect(canonicalGitRemote({ host: 'Git.Example.com', port: 22, path: 'A/B.git' })).toBe('git.example.com/A/B')
    expect(canonicalGitRemote({ host: 'GitHub.com', port: 22, path: '/O/R.GIT' })).toBe('github.com/o/r')
    expect(canonicalGitRemote({ host: 'h.example.com', port: 22, sshUser: 'deploy', path: 'r' })).toBe(
      'deploy@h.example.com/r'
    )
    expect(canonicalGitRemote({ host: 'git.example.com', port: 2200, path: 'a/b' })).toBe('git.example.com:2200/a/b')
  })
  it('display URL round-trips through the parser', () => {
    for (const url of [
      'git@github.com:o/r.git',
      'ssh://git@code.example.org:2222/team/app',
      'git@h.example.com:/srv/r.git',
    ]) {
      const remote = parseGitRemote(url)
      expect(parseGitRemote(webGitDisplayUrl(remote))).toEqual(remote)
    }
  })
  it('detects providers and validates branches', () => {
    expect(webGitProvider('github.com')).toBe('github')
    expect(webGitProvider('GitLab.com')).toBe('gitlab')
    expect(webGitProvider('bitbucket.org')).toBe('bitbucket')
    expect(webGitProvider('git.example.com')).toBe('other')
    for (const ok of ['main', 'feature/x', 'release-1.2', 'a_b']) expect(webGitBranchValid(ok)).toBe(true)
    for (const bad of ['', '-x', '/x', 'x/', 'a..b', 'a b', 'x.lock', 'a'.repeat(201), 3])
      expect(webGitBranchValid(bad)).toBe(false)
  })
  it('normalizes fingerprint sets', () => {
    const a = 'SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU',
      b = 'SHA256:eUXGGm1YGsMAS7vkcx6JOJdOGHPem5gQp4taiCfCLB8'
    expect(webGitFingerprintSet([b, a, a, 'MD5:xx', 1])).toEqual([a, b])
    expect(webGitFingerprintSet('nope')).toEqual([])
  })
})

describe('host key presets', () => {
  it('pins only on port 22 for published hosts', () => {
    expect(webGitHostKeyPreset('github.com').length).toBe(3)
    expect(webGitHostKeyPreset('github.com', 443)).toEqual([])
    expect(webGitHostKeyPreset('git.example.com')).toEqual([])
    webGitHostKeyPreset('github.com')[0]!.key = 'mutated'
    expect(WEB_GIT_HOST_KEY_PRESETS['github.com']![0]!.key).not.toBe('mutated')
  })
  it('every preset fingerprint matches its key blob', () => {
    for (const [host, keys] of Object.entries(WEB_GIT_HOST_KEY_PRESETS)) {
      expect(keys.map((k) => k.type).sort(), host).toEqual(['ecdsa-sha2-nistp256', 'ssh-ed25519', 'ssh-rsa'])
      for (const k of keys) {
        const blob = Buffer.from(k.key, 'base64')
        expect(blob.subarray(4, 4 + blob.readUInt32BE(0)).toString(), host).toBe(k.type)
        expect(`SHA256:${createHash('sha256').update(blob).digest('base64').replace(/=+$/, '')}`).toBe(k.fingerprint)
      }
    }
    expect(WEB_GIT_HOST_KEY_PRESETS['github.com']![0]!.fingerprint).toBe(
      'SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU'
    )
    expect(WEB_GIT_HOST_KEY_PRESETS['gitlab.com']![0]!.fingerprint).toBe(
      'SHA256:eUXGGm1YGsMAS7vkcx6JOJdOGHPem5gQp4taiCfCLB8'
    )
  })
})
