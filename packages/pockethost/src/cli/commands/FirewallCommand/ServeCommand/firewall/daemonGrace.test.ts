import express from 'express'
import { LoggerService } from 'src/common'
import request from 'supertest'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createDaemonProxyErrorHandler, createDaemonWaiter, isDaemonUnreachableError } from './daemonGrace'

afterEach(() => {
  vi.useRealTimers()
})

describe('isDaemonUnreachableError', () => {
  it('matches connection refused', () => {
    expect(isDaemonUnreachableError(new Error('connect ECONNREFUSED 127.0.0.1:3000'))).toBe(true)
  })

  it('matches reset and hang up', () => {
    expect(isDaemonUnreachableError(new Error('read ECONNRESET'))).toBe(true)
    expect(isDaemonUnreachableError(new Error('socket hang up'))).toBe(true)
  })

  it('rejects unrelated errors', () => {
    expect(isDaemonUnreachableError(new Error('instance powered off'))).toBe(false)
  })
})

describe('createDaemonProxyErrorHandler', () => {
  it('answers a fixed text and keeps the detail out of the response', async () => {
    const app = express()
    app.use((_req, _res, next) => next(new Error('open /home/ubuntu/.env: permission denied')))
    app.use(createDaemonProxyErrorHandler(LoggerService()))
    const res = await request(app).get('/')
    expect(res.status).toBe(502)
    expect(res.text).not.toContain('/home/ubuntu')
    expect(res.text).not.toContain('permission denied')
  })
})

describe('createDaemonWaiter', () => {
  it('shares a single probe between all waiting requests', async () => {
    let finish: (ready: boolean) => void = () => undefined
    const probe = vi.fn(() => new Promise<boolean>((resolve) => (finish = resolve)))
    const wait = createDaemonWaiter(probe, () => 10)
    const waiting = Array.from({ length: 50 }, () => wait(1000))
    expect(probe).toHaveBeenCalledTimes(1)
    finish(true)
    expect(await Promise.all(waiting)).toEqual(Array(50).fill(true))
  })

  it('retries once per interval during an outage, whatever the number of waiting requests', async () => {
    vi.useFakeTimers()
    const probe = vi.fn(async () => false)
    const onProbeFailed = vi.fn()
    const wait = createDaemonWaiter(probe, () => 100, onProbeFailed)
    const waiting = Array.from({ length: 20 }, () => wait(350))
    await vi.advanceTimersByTimeAsync(350)
    expect(await Promise.all(waiting)).toEqual(Array(20).fill(false))
    expect(probe.mock.calls.length).toBeLessThanOrEqual(4)
    expect(onProbeFailed).toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1000)
    const calls = probe.mock.calls.length
    await vi.advanceTimersByTimeAsync(1000)
    expect(probe).toHaveBeenCalledTimes(calls)
  })

  it('releases requests as soon as the daemon is back, each keeping its own deadline', async () => {
    vi.useFakeTimers()
    let ready = false
    const wait = createDaemonWaiter(
      async () => ready,
      () => 100
    )
    const short = wait(150)
    const long = wait(5000)
    await vi.advanceTimersByTimeAsync(150)
    expect(await short).toBe(false)
    ready = true
    await vi.advanceTimersByTimeAsync(100)
    expect(await long).toBe(true)
  })
})
