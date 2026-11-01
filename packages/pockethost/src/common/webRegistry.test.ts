import { describe, expect, it } from 'vitest'
import { webImageDigestValid, webRegistryHostAllowed } from './webRegistry'

const SHA = '@sha256:' + 'a'.repeat(64)

describe('webRegistryHostAllowed', () => {
  it('accepte les registres publics, avec ou sans port', () => {
    for (const host of ['ghcr.io', 'docker.io', 'registry.example.com', 'registry.example.com:5000'])
      expect(webRegistryHostAllowed(host)).toBe(true)
  })
  it('refuse la boucle locale, les IP, les hôtes courts et les suffixes internes', () => {
    for (const host of [
      '',
      'localhost',
      'localhost:5000',
      'registry',
      'registry:5000',
      '127.0.0.1',
      '127.0.0.1:5000',
      '10.0.0.1',
      '169.254.169.254',
      '0x7f.0.0.1',
      '127.1',
      'registry.local',
      'registry.local:5000',
      'registry.internal',
      'registry.corp.internal',
      'foo.localhost',
      'host.localdomain',
      '1.0.0.127.in-addr.arpa',
      'nas.lan',
      'ghcr.io:0',
      'ghcr.io:70000',
      'ghcr.io/x',
      'ghcr..io',
      '-ghcr.io',
      null,
      42,
    ])
      expect(webRegistryHostAllowed(host), `${host}`).toBe(false)
  })
})
describe('webImageDigestValid', () => {
  it('accepte Docker Hub et les registres publics', () => {
    for (const image of ['nginx', 'library/nginx', 'foo.bar', 'ghcr.io/org/site', 'registry.example.com:5000/org/site'])
      expect(webImageDigestValid(image + SHA), image).toBe(true)
  })
  it('refuse un registre privé, y compris par la branche « sans hôte » de l’expression', () => {
    for (const image of [
      'localhost/site',
      'localhost:5000/site',
      'registry:5000/site',
      '127.0.0.1/site',
      '127.0.0.1:5000/site',
      '10.0.0.1/org/site',
      '169.254.169.254/site',
      'registry.local/site',
      'registry.internal/org/site',
      'registry.internal:5000/site',
    ])
      expect(webImageDigestValid(image + SHA), image).toBe(false)
  })
  it('exige toujours un digest complet', () => {
    expect(webImageDigestValid('ghcr.io/org/site:latest')).toBe(false)
    expect(webImageDigestValid('ghcr.io/org/site@sha256:abc')).toBe(false)
    expect(webImageDigestValid(undefined)).toBe(false)
  })
})
