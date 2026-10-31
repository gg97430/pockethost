import { createHmac } from 'node:crypto'
import { describe, expect, it } from 'vitest'
import { isTotpRequiredError, totpAtCounter, totpUri, verifyTotp } from './totp'

const secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ' // RFC test key: ASCII 12345678901234567890

describe('TOTP SHA-1', () => {
  it.each(['755224', '287082', '359152', '969429', '338314', '254676', '287922', '162583', '399871', '520489'])(
    'matches RFC 4226 vector %#',
    (expected) =>
      expect(
        totpAtCounter(
          secret,
          ['755224', '287082', '359152', '969429', '338314', '254676', '287922', '162583', '399871', '520489'].indexOf(
            expected
          )
        )
      ).toBe(expected)
  )

  it.each([
    [59, '94287082'],
    [1111111109, '07081804'],
    [1111111111, '14050471'],
    [1234567890, '89005924'],
    [2000000000, '69279037'],
    [20000000000, '65353130'],
  ])('matches RFC 6238 at %s seconds', (seconds, expected) =>
    expect(totpAtCounter(secret, Math.floor(Number(seconds) / 30), 8)).toBe(expected)
  )

  it('matches an independent HMAC implementation beyond 32-bit counters', () => {
    for (const counter of [1, 123456, 0x100000001, 0x1fffffffff]) {
      const buffer = Buffer.alloc(8)
      buffer.writeBigUInt64BE(BigInt(counter))
      const digest = createHmac('sha1', '12345678901234567890').update(buffer).digest()
      const expected = `${(digest.readUInt32BE(digest[19]! & 15) & 0x7fffffff) % 1000000}`.padStart(6, '0')
      expect(totpAtCounter(secret, counter)).toBe(expected)
    }
  })

  it('allows one time step of drift and rejects consumed or malformed codes', () => {
    const counter = 123456
    const timestamp = counter * 30000
    expect(verifyTotp(secret, totpAtCounter(secret, counter), -1, timestamp)).toBe(counter)
    expect(verifyTotp(secret, totpAtCounter(secret, counter - 1), -1, timestamp)).toBe(counter - 1)
    expect(verifyTotp(secret, totpAtCounter(secret, counter), counter, timestamp)).toBeNull()
    expect(verifyTotp(secret, '123', -1, timestamp)).toBeNull()
    expect(verifyTotp(secret, totpAtCounter(secret, counter - 2), -1, timestamp)).toBeNull()
  })

  it('encodes labels in provisioning URIs', () => {
    expect(totpUri(secret, 'PocketHost Test', 'test@example.com')).toContain('PocketHost%20Test%3Atest%40example.com')
    expect(totpUri(secret, 'PocketHost', 'test@example.com')).toContain('algorithm=SHA1&digits=6&period=30')
  })

  it('recognizes the PocketBase validation challenge without treating generic login errors as TOTP', () => {
    expect(isTotpRequiredError({ response: { data: { totpRequired: { code: 'totp_required' } } } })).toBe(true)
    expect(isTotpRequiredError({ data: { totpRequired: { code: 'totp_required' } } })).toBe(true)
    expect(isTotpRequiredError({ response: { data: { totpRequired: { code: 'validation_invalid_value' } } } })).toBe(
      false
    )
    expect(isTotpRequiredError(new Error('Wrong password'))).toBe(false)
  })
})
