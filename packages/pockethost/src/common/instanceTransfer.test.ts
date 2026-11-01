import { describe, expect, it } from 'vitest'
import {
  assertTransferPeerAllowed,
  INSTANCE_TRANSFER_PROTOCOL,
  parseInstanceTransferLink,
  validateTransferMetadata,
} from './instanceTransfer'

const id = 'abcdefghijklmno'
const code = 'A'.repeat(48)
const link = `https://source.example/api/instance-transfers/${id}#${code}`

describe('instance transfer protocol', () => {
  it('keeps the credential out of the HTTP URL and accepts a separately pasted code', () => {
    expect(parseInstanceTransferLink(link)).toMatchObject({ origin: 'https://source.example', id, code })
    expect(parseInstanceTransferLink(link.split('#')[0]!, code)).toEqual(parseInstanceTransferLink(link))
  })

  it.each([
    `https://user:password@source.example/api/instance-transfers/${id}#${code}`,
    `https://source.example/internal/../../api/instance-transfers/${id}#${code}`,
    `file:///api/instance-transfers/${id}#${code}`,
    `https://source.example/api/instance-transfers/${id}?code=${code}`,
    link.replace(code, '123456'),
  ])('rejects malformed references: %s', (value) => {
    expect(() => parseInstanceTransferLink(value)).toThrow()
  })

  it('only connects to an explicitly allowed server and requires HTTPS', () => {
    expect(() => assertTransferPeerAllowed(parseInstanceTransferLink(link), 'source.example')).not.toThrow()
    expect(() => assertTransferPeerAllowed(parseInstanceTransferLink(link), 'source.example.attacker.test')).toThrow(
      'non autorisé'
    )
    const local = parseInstanceTransferLink(link.replace('https://source.example', 'http://127.0.0.1:1234'))
    expect(() => assertTransferPeerAllowed(local, '127.0.0.1:1234')).toThrow('HTTPS')
    expect(() => assertTransferPeerAllowed(local, '127.0.0.1:1234', true)).not.toThrow()
  })

  const metadata = () => ({
    protocol: INSTANCE_TRANSFER_PROTOCOL,
    id,
    status: 'ready',
    expiresAt: new Date(Date.now() + 60_000).toISOString(),
    instance: { subdomain: 'ma-base', version: '0.39.*', syncAdmin: false, autoVacuum: true },
    archive: { filename: 'backup.tar.gz', checksum: 'a'.repeat(64), compressedBytes: 1024, sizeBytes: 2048 },
  })

  it('validates identity, expiry, integrity metadata and archive size before creating a target', () => {
    const valid = metadata()
    expect(validateTransferMetadata(valid, id, 4096)).toBe(valid)
    expect(() => validateTransferMetadata(valid, 'othertransferid', 4096)).toThrow('compatible')
    expect(() => validateTransferMetadata({ ...valid, expiresAt: 'invalid' }, id, 4096)).toThrow('expiré')
    expect(() => validateTransferMetadata({ ...valid, status: 'complete' }, id, 4096)).toThrow('déjà été utilisé')
    expect(() => validateTransferMetadata(valid, id, 512)).toThrow('volumineuse')
    expect(() =>
      validateTransferMetadata({ ...valid, archive: { ...valid.archive, checksum: 'bad' } }, id, 4096)
    ).toThrow('invalide')
  })
})
