import express from 'express'
import request from 'supertest'
import { describe, expect, it } from 'vitest'
import { createEnforceHttps } from './expressMiddleware'

const app = (trusted: boolean) => {
  const server = express()
  server.use(createEnforceHttps(() => trusted))
  server.use((_req, res) => {
    res.send('served')
  })
  return server
}

describe('createEnforceHttps', () => {
  it('redirects a direct client that claims X-Forwarded-Proto: https', async () => {
    const res = await request(app(false)).get('/x?y=1').set('Host', 'example.com').set('X-Forwarded-Proto', 'https')
    expect(res.status).toBe(301)
    expect(res.headers.location).toBe('https://example.com/x?y=1')
  })

  it('keeps serving Cloudflare Flexible traffic, HTTP towards the origin', async () => {
    const res = await request(app(true)).get('/').set('Host', 'example.com').set('X-Forwarded-Proto', 'https')
    expect(res.status).toBe(200)
    expect(res.text).toBe('served')
  })

  it('still redirects a trusted proxy that reports plain HTTP', async () => {
    const res = await request(app(true)).get('/').set('Host', 'example.com').set('X-Forwarded-Proto', 'http')
    expect(res.status).toBe(301)
  })
})
