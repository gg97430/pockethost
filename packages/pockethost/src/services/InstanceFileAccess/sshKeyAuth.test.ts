import { describe, expect, it } from 'vitest'
import { sshKeyInstanceIds } from './sshKeyAuth'

describe('SSH key relation compatibility', () => {
  const first = 'abcdefghijklmno'
  const second = '123456789012345'
  it('keeps a single relation ID whole instead of spreading its characters', () => {
    expect(sshKeyInstanceIds(first)).toEqual([first])
  })
  it('keeps explicitly selected multiple IDs', () => {
    expect(sshKeyInstanceIds([first, second, first])).toEqual([first, second])
  })
  it('fails closed for missing and malformed scopes', () => {
    for (const value of [null, undefined, '', true, {}, 'all', ['*', null, 7]]) {
      expect(sshKeyInstanceIds(value)).toEqual([])
    }
  })
})
