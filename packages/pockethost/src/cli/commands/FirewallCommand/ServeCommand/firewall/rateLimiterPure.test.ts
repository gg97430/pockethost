import { describe, expect, it } from 'vitest'
import {
  consumeWeightForPath,
  createConcurrencyLimiter,
  isHealthProbePath,
  isPocketBaseFilesPath,
  normalizedRateLimitHost,
  toMicroPointLimit,
  toProxyCidrString,
  WEIGHT_DEN,
} from './rateLimiterPure'

describe('rateLimiterPure', () => {
  it('normalizes proxy CIDR strings', () => {
    expect(toProxyCidrString('203.0.113.1')).toBe('203.0.113.1/32')
    expect(toProxyCidrString('10.0.0.0/8')).toBe('10.0.0.0/8')
  })

  it('normalizes hostnames used as limiter keys', () => {
    expect(normalizedRateLimitHost('Example.COM.')).toBe('example.com')
    expect(normalizedRateLimitHost('example.com')).toBe('example.com')
    expect(normalizedRateLimitHost(undefined)).toBeUndefined()
    expect(normalizedRateLimitHost('.')).toBeUndefined()
    expect(normalizedRateLimitHost('a'.repeat(254))).toBeUndefined()
  })

  it('tracks in-flight weight and forgets a key once it is idle', () => {
    const limiter = createConcurrencyLimiter(20)
    expect(limiter.tryAcquire('k', 10)).toBe(true)
    expect(limiter.tryAcquire('k', 10)).toBe(true)
    expect(limiter.tryAcquire('k', 1)).toBe(false)
    limiter.release('k', 10)
    expect(limiter.tryAcquire('k', 1)).toBe(true)
    limiter.release('k', 10)
    limiter.release('k', 1)
    expect(limiter.size()).toBe(0)
    for (let i = 0; i < 1000; i++) {
      limiter.tryAcquire(`host-${i}`, 10)
      limiter.release(`host-${i}`, 10)
    }
    expect(limiter.size()).toBe(0)
  })

  it('classifies health probe paths', () => {
    expect(isHealthProbePath('/api/firewall/health')).toBe(true)
    expect(isHealthProbePath('/_api/daemon/health')).toBe(true)
    expect(isHealthProbePath('/api/collections')).toBe(false)
  })

  it('applies lower weight for file routes', () => {
    expect(isPocketBaseFilesPath('/api/files/x/y')).toBe(true)
    expect(consumeWeightForPath('/api/files/x')).toBeLessThan(consumeWeightForPath('/api/collections'))
    expect(toMicroPointLimit({ points: 1000, duration: 3600 }).points).toBe(1000 * WEIGHT_DEN)
  })
})
