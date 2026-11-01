import { describe, expect, it } from 'vitest'
import { githubDestination, updateWebLogBuffer } from './webAppFlow'

describe('GitHub provider destinations', () => {
  it('accepts only the personal or organization app creation and installation paths', () => {
    expect(githubDestination('https://github.com/settings/apps/new?state=once', 'create')).toContain('?state=once')
    expect(
      githubDestination('https://github.com/organizations/kolab/settings/apps/new?state=once', 'create')
    ).toContain('/organizations/kolab/')
    expect(
      githubDestination('https://github.com/apps/pockethost-demo/installations/new?state=next', 'install')
    ).toContain('/pockethost-demo/')
  })
  it('rejects redirected credentials, provider lookalikes and unrelated GitHub paths', () => {
    for (const url of [
      'javascript:alert(1)',
      'https://github.com.evil.invalid/settings/apps/new',
      'https://attacker@github.com/settings/apps/new',
      'http://github.com/settings/apps/new',
      'https://github.com:444/settings/apps/new',
      'https://github.com/login/oauth/authorize',
      'https://github.com/apps/another%2Fpath/installations/new',
    ]) {
      expect(() => githubDestination(url, 'create')).toThrow()
      expect(() => githubDestination(url, 'install')).toThrow()
    }
  })
})
describe('Incremental deployment log buffer', () => {
  const empty = { text: '', truncated: false, hasMore: false }
  it('appends only after an actual cursor and preserves backend pagination', () => {
    const first = updateWebLogBuffer(empty, { text: 'début\n', cursor: 'opaque-a', hasMore: true })
    const second = updateWebLogBuffer(first, { text: 'suite\n', cursor: 'opaque-b' }, 'opaque-a')
    expect(second.text).toBe('début\nsuite\n')
    expect(second.hasMore).toBe(false)
    expect(second.cursor).toBe('opaque-b')
  })
  it('replaces stale logs after server rotation and supports the older complete-text API', () => {
    const previous = { text: 'old\n', cursor: 'old', truncated: true, hasMore: false }
    expect(updateWebLogBuffer(previous, { text: 'rotated\n', cursor: 'new', reset: true }, 'old').text).toBe(
      'rotated\n'
    )
    expect(updateWebLogBuffer(previous, { text: 'legacy complete\n' }, 'old').text).toBe('legacy complete\n')
    expect(updateWebLogBuffer(previous, { text: 'rotated\n', cursor: 'new', reset: true }, 'old').truncated).toBe(false)
  })
  it('bounds browser memory while retaining the newest lines and truncation evidence', () => {
    const previous = { text: 'a'.repeat(190000), cursor: 'offset', truncated: false, hasMore: false }
    const next = updateWebLogBuffer(previous, { text: 'b'.repeat(30000), cursor: 'next' }, 'offset')
    expect(next.text.length).toBe(200000)
    expect(next.text.endsWith('b'.repeat(30000))).toBe(true)
    expect(next.truncated).toBe(true)
  })
})
