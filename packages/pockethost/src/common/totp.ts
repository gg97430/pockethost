/** RFC 4226 / RFC 6238, SHA-1; synchronous and compatible with PocketBase Goja. */
const rotate = (value: number, bits: number) => (value << bits) | (value >>> (32 - bits))
const sha1 = (bytes: number[]) => {
  const words: number[] = []
  for (let i = 0; i < bytes.length; i++) words[i >> 2] = (words[i >> 2] || 0) | (bytes[i]! << (24 - (i % 4) * 8))
  words[bytes.length >> 2] = (words[bytes.length >> 2] || 0) | (0x80 << (24 - (bytes.length % 4) * 8))
  const blockCount = Math.ceil((bytes.length + 9) / 64)
  words[blockCount * 16 - 2] = Math.floor((bytes.length * 8) / 0x100000000)
  words[blockCount * 16 - 1] = (bytes.length * 8) | 0
  let h0 = 0x67452301,
    h1 = 0xefcdab89,
    h2 = 0x98badcfe,
    h3 = 0x10325476,
    h4 = 0xc3d2e1f0
  for (let block = 0; block < blockCount; block++) {
    const w: number[] = []
    for (let i = 0; i < 16; i++) w[i] = words[block * 16 + i] || 0
    for (let i = 16; i < 80; i++) w[i] = rotate(w[i - 3]! ^ w[i - 8]! ^ w[i - 14]! ^ w[i - 16]!, 1)
    let a = h0,
      b = h1,
      c = h2,
      d = h3,
      e = h4
    for (let i = 0; i < 80; i++) {
      const f = i < 20 ? (b & c) | (~b & d) : i < 40 ? b ^ c ^ d : i < 60 ? (b & c) | (b & d) | (c & d) : b ^ c ^ d
      const k = i < 20 ? 0x5a827999 : i < 40 ? 0x6ed9eba1 : i < 60 ? 0x8f1bbcdc : 0xca62c1d6
      const next = (rotate(a, 5) + f + e + k + w[i]!) | 0
      e = d
      d = c
      c = rotate(b, 30)
      b = a
      a = next
    }
    h0 = (h0 + a) | 0
    h1 = (h1 + b) | 0
    h2 = (h2 + c) | 0
    h3 = (h3 + d) | 0
    h4 = (h4 + e) | 0
  }
  const result: number[] = []
  for (const word of [h0, h1, h2, h3, h4]) {
    for (let shift = 24; shift >= 0; shift -= 8) result.push((word >>> shift) & 255)
  }
  return result
}

const decodeBase32 = (value: string) => {
  const clean = value.toUpperCase().replace(/=+$/, '')
  if (!/^[A-Z2-7]{16,128}$/.test(clean)) throw new Error('Clé TOTP invalide.')
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  const result: number[] = []
  let bits = 0,
    accumulator = 0
  for (const char of clean) {
    accumulator = (accumulator << 5) | alphabet.indexOf(char)
    bits += 5
    if (bits >= 8) {
      bits -= 8
      result.push((accumulator >>> bits) & 255)
      accumulator &= (1 << bits) - 1
    }
  }
  return result
}

export const totpAtCounter = (secret: string, counter: number, digits: 6 | 8 = 6) => {
  if (!Number.isSafeInteger(counter) || counter < 0) throw new Error('Compteur TOTP invalide.')
  let key = decodeBase32(secret)
  if (key.length > 64) key = sha1(key)
  const inner: number[] = [],
    outer: number[] = [],
    message: number[] = []
  for (let i = 0; i < 64; i++) {
    inner.push((key[i] || 0) ^ 0x36)
    outer.push((key[i] || 0) ^ 0x5c)
  }
  for (let i = 7; i >= 0; i--) {
    message[i] = counter % 256
    counter = Math.floor(counter / 256)
  }
  const digest = sha1(outer.concat(sha1(inner.concat(message))))
  const offset = digest[19]! & 15
  const binary =
    ((digest[offset]! & 127) << 24) | (digest[offset + 1]! << 16) | (digest[offset + 2]! << 8) | digest[offset + 3]!
  return `${binary % 10 ** digits}`.padStart(digits, '0')
}

export const verifyTotp = (secret: string, code: string, lastCounter = -1, timestamp = Date.now()) => {
  if (!/^\d{6}$/.test(code)) return null
  const counter = Math.floor(timestamp / 30_000)
  for (const candidate of [counter, counter - 1, counter + 1]) {
    if (candidate <= lastCounter || candidate < 0) continue
    const expected = totpAtCounter(secret, candidate)
    let difference = 0
    for (let i = 0; i < 6; i++) difference |= code.charCodeAt(i) ^ expected.charCodeAt(i)
    if (difference === 0) return candidate
  }
  return null
}

export const totpUri = (secret: string, issuer: string, email: string) =>
  `otpauth://totp/${encodeURIComponent(`${issuer}:${email}`)}?secret=${secret}&issuer=${encodeURIComponent(issuer)}&algorithm=SHA1&digits=6&period=30`

export const isTotpRequiredError = (error: unknown) => {
  type Required = boolean | { code?: string }
  const value = error as {
    response?: { data?: { totpRequired?: Required } }
    data?: { totpRequired?: Required }
  } | null
  const required = value?.response?.data?.totpRequired || value?.data?.totpRequired
  return required === true || (typeof required === 'object' && required?.code === 'totp_required')
}
