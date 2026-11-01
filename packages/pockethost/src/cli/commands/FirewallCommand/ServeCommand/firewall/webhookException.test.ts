import { describe, expect, it } from 'vitest'
import { isWebhookException } from './webhookException'

const hosts = new Set(['app.example.com'])
const id = 'A'.repeat(12) + 'b'.repeat(6) + '012345'
const cid = 'abcdefghij12345'
const gh = '/api/web-apps/github/webhook'

describe('isWebhookException', () => {
  it('accepts the exact GitHub App and Git deploy-key webhook paths', () => {
    expect(isWebhookException('POST', '/api/web-apps/github/webhook', 'app.example.com', hosts)).toBe(true)
    expect(isWebhookException('POST', `/api/web-git-sources/webhook/${id}`, 'app.example.com:443', hosts)).toBe(true)
  })

  it('refuses other methods, malformed ids and other routes', () => {
    expect(isWebhookException('GET', `/api/web-git-sources/webhook/${id}`, 'app.example.com', hosts)).toBe(false)
    expect(isWebhookException('POST', `/api/web-git-sources/webhook/${id.slice(1)}`, 'app.example.com', hosts)).toBe(
      false
    )
    expect(isWebhookException('POST', `/api/web-git-sources/webhook/${id}x`, 'app.example.com', hosts)).toBe(false)
    expect(isWebhookException('POST', '/api/web-git-sources/webhook/x/../../x', 'app.example.com', hosts)).toBe(false)
    expect(isWebhookException('POST', `/api/web-git-sources/webhook/${id}/`, 'app.example.com', hosts)).toBe(false)
    expect(isWebhookException('POST', '/api/web-git-sources/abc/scan', 'app.example.com', hosts)).toBe(false)
    expect(isWebhookException('POST', '/api/web-git-sources', 'app.example.com', hosts)).toBe(false)
  })

  it('refuses an unknown Host', () => {
    expect(isWebhookException('POST', `/api/web-git-sources/webhook/${id}`, 'evil.example.net', hosts)).toBe(false)
    expect(isWebhookException('POST', '/api/web-apps/github/webhook', '', hosts)).toBe(false)
  })

  describe('GitHub connection webhooks', () => {
    it('accepts the exact per-connection path and keeps the legacy path', () => {
      expect(isWebhookException('POST', `${gh}/${cid}`, 'app.example.com', hosts)).toBe(true)
      expect(isWebhookException('POST', `${gh}/${cid}`, 'app.example.com:443', hosts)).toBe(true)
      expect(isWebhookException('POST', gh, 'app.example.com', hosts)).toBe(true)
    })

    it('refuses other methods', () => {
      expect(isWebhookException('GET', `${gh}/${cid}`, 'app.example.com', hosts)).toBe(false)
      expect(isWebhookException('PUT', `${gh}/${cid}`, 'app.example.com', hosts)).toBe(false)
    })

    it('refuses malformed connection ids', () => {
      for (const bad of [cid.slice(1), `${cid}x`, cid.toUpperCase(), 'abcdefghiJ12345', 'abcdefghij-2345', '']) {
        expect(isWebhookException('POST', `${gh}/${bad}`, 'app.example.com', hosts)).toBe(false)
      }
    })

    it('refuses /webhook/default, trailing segments and traversal', () => {
      for (const path of [
        `${gh}/default`,
        `${gh}/${cid}/`,
        `${gh}/${cid}/x`,
        `${gh}/x/../..`,
        `${gh}/../../${cid}`,
        `${gh}/`,
        `${gh}/${cid}?x=1`,
      ]) {
        expect(isWebhookException('POST', path, 'app.example.com', hosts)).toBe(false)
      }
    })

    it('refuses neighbouring routes', () => {
      expect(isWebhookException('POST', `/api/web-apps/github/webhooks/${cid}`, 'app.example.com', hosts)).toBe(false)
      expect(isWebhookException('POST', `${gh}/connections`, 'app.example.com', hosts)).toBe(false)
      expect(isWebhookException('POST', '/api/web-apps/github/connections', 'app.example.com', hosts)).toBe(false)
      expect(isWebhookException('POST', `/api/web-apps/github/connections/${cid}`, 'app.example.com', hosts)).toBe(
        false
      )
    })

    it('refuses an unknown or empty Host', () => {
      expect(isWebhookException('POST', `${gh}/${cid}`, 'evil.example.net', hosts)).toBe(false)
      expect(isWebhookException('POST', `${gh}/${cid}`, '', hosts)).toBe(false)
    })
  })
})
