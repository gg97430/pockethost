import { describe, expect, it } from 'vitest'
import {
  boundedDeploymentAttachment,
  deploymentPublicConfiguration,
  sanitizeDeploymentEmailLog,
} from './deploymentFailureEmail'

const state = (env: Record<string, string>, runtime = 'nextjs') => ({
  env: Object.fromEntries(Object.entries(env).map(([name, value]) => [name, { value }])),
  runtime: { kind: runtime },
})

describe('deployment failure email disclosure boundary', () => {
  it('preserves known literal configuration but lets secret names override public value collisions', () => {
    const publicValues = {
      NODE_ENV: 'production',
      PGSSLMODE: 'require',
      PORT: '3000',
      FEATURE_ENABLED: 'true',
      REQUEST_TIMEOUT_MS: '5000',
      NEXT_PUBLIC_SITE_NAME: 'My public site',
    }
    const text = Object.values(publicValues).join(' ')
    expect(sanitizeDeploymentEmailLog(text, state(publicValues), state({}))).toBe(text)
    expect(sanitizeDeploymentEmailLog('require', state({ PGSSLMODE: 'require', PASSWORD: 'require' }), state({}))).toBe(
      '[masqué]'
    )
    expect(deploymentPublicConfiguration('RAILPACK_BUILD_CMD', 'pnpm build', true)).toBe(true)
    expect(deploymentPublicConfiguration('RAILPACK_AUTH_TOKEN', 'pnpm build', true)).toBe(false)
    expect(deploymentPublicConfiguration('NODE_ENV', 'unrecognized-secret-value')).toBe(false)
  })

  it('masks frozen and current environment plus prefixed multiline key lines', () => {
    const frozen = state({ OLD_PASSWORD: 'old-secret', PEM_KEY: 'header\nprivate-key-line-at-least-twelve\nfooter' })
    const current = state({ NEW_TOKEN: 'new-secret' })
    expect(
      sanitizeDeploymentEmailLog('#10 private-key-line-at-least-twelve\nold-secret new-secret', frozen, current)
    ).toBe('#10 [masqué]\n[masqué] [masqué]')
    expect(() => sanitizeDeploymentEmailLog('cannot release', null, current)).toThrow()
    expect(() => sanitizeDeploymentEmailLog('cannot release', { env: { SECRET: {} } }, current)).toThrow()
  })

  it('masks short passwords once without recursively masking its replacement', () => {
    expect(sanitizeDeploymentEmailLog('a+q', state({ PASSWORD: 'a', SECRET: 'q' }), state({}))).toBe(
      '[masqué]+[masqué]'
    )
    expect(sanitizeDeploymentEmailLog('[masqué] a', state({ PASSWORD: 'a' }), state({}))).toBe('[masqué] [masqué]')
    expect(sanitizeDeploymentEmailLog('a.b [ab]', state({ PASSWORD: 'a.b', TOKEN: '[ab]' }), state({}))).toBe(
      '[masqué] [masqué]'
    )
  })

  it('removes terminal controls, bidi characters and URL credentials', () => {
    expect(
      sanitizeDeploymentEmailLog(
        '\x1b[31mÉchec\x1b[0m\r\nhttps://alice:password@host.test/db\u202e\x00',
        state({}),
        state({})
      )
    ).toBe('Échec\nhttps://[masqué]@host.test/db')
    expect(sanitizeDeploymentEmailLog('\x1b]8;;https://evil.test\x07text\x1b]8;;\x07', state({}), state({}))).toBe(
      'text'
    )
    expect(
      sanitizeDeploymentEmailLog('https://alice:password@host.test/db', state({ PASSWORD: ':' }), state({}))
    ).not.toContain('alice')
  })

  it('bounds the final UTF-8 bytes after masking expansion and keeps complete tail lines', () => {
    const masked = sanitizeDeploymentEmailLog(
      'x\n'.repeat(300000) + 'Échec final 😀\n',
      state({ PASSWORD: 'x' }),
      state({})
    )
    const text = boundedDeploymentAttachment(masked, false)
    expect(Buffer.byteLength(text, 'utf8')).toBeLessThanOrEqual(1048576)
    expect(text.startsWith('[Journal tronqué :')).toBe(true)
    expect(text).toContain('[masqué]\n')
    expect(text.endsWith('Échec final 😀\n')).toBe(true)
    expect(text).not.toContain('\ufffd')
    expect(boundedDeploymentAttachment('normal\n', false)).toBe('normal\n')
    expect(boundedDeploymentAttachment('x'.repeat(2000000), false)).toBe(
      '[Journal tronqué : seules les dernières lignes sont jointes.]\n'
    )
  })
})
