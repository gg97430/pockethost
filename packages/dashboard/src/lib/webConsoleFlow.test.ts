import { describe, expect, it } from 'vitest'
import { githubManageUrl, terminalClientFrame, webTerminalUrl } from './webConsoleFlow'
describe('web terminal browser boundaries', () => {
  it('uses the fixed same-origin path without a token or query', () => {
    expect(webTerminalUrl('https://app.monappli.re', '/api/web-terminal/connect')).toBe(
      'wss://app.monappli.re/api/web-terminal/connect'
    )
    expect(webTerminalUrl('http://127.0.0.1:5179', '/api/web-terminal/connect')).toBe(
      'ws://127.0.0.1:5179/api/web-terminal/connect'
    )
    for (const path of ['https://evil.example/connect', '/api/web-terminal/connect?token=private', '//evil.example'])
      expect(() => webTerminalUrl('https://app.monappli.re', path)).toThrow()
    expect(() => webTerminalUrl('http://public.example', '/api/web-terminal/connect')).toThrow()
  })
  it('bounds pasted UTF8 text and validates PTY size/control frames', () => {
    expect(JSON.parse(terminalClientFrame({ type: 'input', data: 'echo $VALUE\r' }))).toEqual({
      type: 'input',
      data: 'echo $VALUE\r',
    })
    expect(() => terminalClientFrame({ type: 'input', data: 'é'.repeat(33000) })).toThrow()
    for (const size of [
      { cols: 0, rows: 20 },
      { cols: 80, rows: 1000 },
      { cols: 80.5, rows: 24 },
    ])
      expect(() => terminalClientFrame({ type: 'resize', ...size })).toThrow()
    expect(terminalClientFrame({ type: 'resize', cols: 80, rows: 24 })).toContain('resize')
    expect(terminalClientFrame({ type: 'close' })).toBe('{"type":"close"}')
    expect(() => terminalClientFrame({ type: 'exec', command: 'host command' })).toThrow()
  })
})

describe('GitHub installation links', () => {
  it('allows only GitHub installation management destinations', () => {
    expect(githubManageUrl('https://github.com/settings/installations/123')).toBe(
      'https://github.com/settings/installations/123'
    )
    expect(githubManageUrl('https://github.com/organizations/demo/settings/installations/123')).toBeTruthy()
    expect(githubManageUrl('https://github.com/apps/pockethost-demo/installations/new')).toBeTruthy()
    for (const url of [
      'https://github.com.evil.example/settings/installations/123',
      'javascript:alert(1)',
      'https://github.com/settings/installations/123?token=secret',
      'https://user:password@github.com/settings/installations/123',
      'https://github.com/settings/apps/demo',
    ])
      expect(githubManageUrl(url)).toBe('')
  })
})
