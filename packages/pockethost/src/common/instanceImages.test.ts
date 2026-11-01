import { describe, expect, it } from 'vitest'
import { parseInstanceImageOverrides, resolveInstanceImage, selectedInstanceImages } from './instanceImages'
const a = 'a'.repeat(15),
  b = 'b'.repeat(15),
  c = 'c'.repeat(15)
const imageB = 'sha256:' + 'b'.repeat(64),
  fallback = 'benallfree/pockethost-instance'

describe('operator instance image policy', () => {
  it('keeps the default reference and pins only explicitly assigned instance IDs', () => {
    const policy = JSON.stringify({ [b]: imageB })
    expect(resolveInstanceImage(a, fallback, policy)).toBe(fallback)
    expect(resolveInstanceImage(b, fallback, policy)).toBe(imageB)
    expect(resolveInstanceImage(c, fallback, policy)).toBe(fallback)
    expect(resolveInstanceImage(a, fallback)).toBe(fallback)
    expect(resolveInstanceImage(a, fallback, '')).toBe(fallback)
  })
  it('pins the eight imported B instances while the six A instances keep the default reference', () => {
    const idsB = Array.from({ length: 8 }, (_, i) => 'b'.repeat(14) + i.toString(36))
    const idsA = Array.from({ length: 6 }, (_, i) => 'a'.repeat(14) + i.toString(36))
    const imported = 'sha256:0c34c911fdf93a96c81c47f2914adc34ee9e758829999622b8122fb61c21f849'
    const raw = JSON.stringify(Object.fromEntries(idsB.map((id) => [id, imported])))
    for (const id of idsB) expect(resolveInstanceImage(id, fallback, raw)).toBe(imported)
    for (const id of idsA) expect(resolveInstanceImage(id, fallback, raw)).toBe(fallback)
  })
  it('selects private-provider assignments only from the granted IDs', () => {
    const policy = JSON.stringify({ [a]: 'sha256:' + 'a'.repeat(64), [b]: imageB })
    expect({ ...selectedInstanceImages([b, c], policy) }).toEqual({ [b]: imageB })
  })
  it('rejects malformed or non-flat maps and duplicate assignments instead of falling back', () => {
    for (const value of [
      ' ',
      '{',
      'null',
      '[]',
      'true',
      '1',
      JSON.stringify({ [a]: null }),
      JSON.stringify({ [a]: { image: imageB } }),
      `{"${a}":"${imageB}","${a}":"${imageB}"}`,
    ])
      expect(() => resolveInstanceImage(a, fallback, value)).toThrow()
    expect(() => parseInstanceImageOverrides(null)).toThrow()
    expect(() => parseInstanceImageOverrides({ [a]: imageB })).toThrow()
  })
  it('accepts only lower-case full SHA256 image IDs, never tags, registries or shell values', () => {
    for (const value of [
      'b'.repeat(64),
      'sha256:' + 'b'.repeat(63),
      imageB.toUpperCase(),
      'moby/buildkit@' + imageB,
      fallback,
      fallback + ':latest',
      imageB + ':evil',
      imageB + ';id',
    ])
      expect(() => parseInstanceImageOverrides(JSON.stringify({ [b]: value }))).toThrow()
    for (const id of ['A'.repeat(15), b.slice(1), b + 'x', '__proto__', 'pockethost-mothership'])
      expect(() => parseInstanceImageOverrides(JSON.stringify({ [id]: imageB }))).toThrow()
    expect(() => resolveInstanceImage('unknown/malformed', fallback, '{}')).toThrow()
  })
  it('bounds the raw JSON and number of assignments', () => {
    expect(() => parseInstanceImageOverrides(' '.repeat(32769))).toThrow()
    const map = Object.fromEntries(Array.from({ length: 257 }, (_, n) => [n.toString(36).padStart(15, '0'), imageB]))
    expect(() => parseInstanceImageOverrides(JSON.stringify(map))).toThrow()
  })
})
