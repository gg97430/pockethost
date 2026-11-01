import { describe, expect, it } from 'vitest'
import { WEB_SITE_SLUG, webProjectNameError, webSiteSlug, webUniqueSlug } from './webProjectGroups'

describe('web site slugs', () => {
  it('derives a short name from the application name', () => {
    expect(webSiteSlug('Back-office Été')).toBe('back-office-ete')
    expect(webSiteSlug('  ')).toBe('site')
    expect(webSiteSlug('Œuvre & Cie')).toBe('oeuvre-cie')
    expect(webSiteSlug(undefined)).toBe('site')
    expect(webSiteSlug('a'.repeat(100))).toHaveLength(60)
    for (const name of [
      'Back-office Été',
      '  ',
      'Œuvre & Cie',
      '---x---',
      '日本語',
      'a'.repeat(100),
      `${'a'.repeat(59)}-b`,
    ])
      expect(webSiteSlug(name)).toMatch(WEB_SITE_SLUG)
  })
  it('picks a free slug in the group', () => {
    expect(webUniqueSlug('vitrine', ['api'])).toBe('vitrine')
    expect(webUniqueSlug('vitrine', ['vitrine'])).toBe('vitrine-2')
    expect(webUniqueSlug('vitrine', ['vitrine', 'vitrine-2'])).toBe('vitrine-3')
    const long = 'a'.repeat(60),
      next = webUniqueSlug(long, [long])
    expect(next).toBe(`${'a'.repeat(58)}-2`)
    expect(next).toHaveLength(60)
    expect(next).toMatch(WEB_SITE_SLUG)
    const all = ['site', ...Array.from({ length: 98 }, (_, i) => `site-${i + 2}`)]
    expect(webUniqueSlug('site', all)).toBe('')
  })
})

describe('web project names', () => {
  it('requires a trimmed name of at most 80 characters', () => {
    for (const value of ['', '   ', 'a'.repeat(81), 42, null, undefined])
      expect(webProjectNameError(value)).toBe('Nom de projet requis (80 caractères maximum).')
    expect(webProjectNameError('a'.repeat(80))).toBe('')
    expect(webProjectNameError('  Client Avenir  ')).toBe('')
  })
})
