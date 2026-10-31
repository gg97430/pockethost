import { describe, expect, it } from 'vitest'
import {
  WEB_PERMISSION_NAMES,
  WEB_ROLES,
  isWebPlatformPermission,
  webCanManageRole,
  webCapabilities,
  webMemberChangeError,
  webRoleAllows,
  webRoleRank,
} from './webAccess'

describe('web project roles', () => {
  it('ranks roles and rejects unknown values', () => {
    expect(WEB_ROLES.map(webRoleRank)).toEqual([0, 1, 2, 3])
    for (const value of ['', 'platform', 'superAdmin', 'Owner', null, undefined, 3]) expect(webRoleRank(value)).toBe(-1)
  })
  it('applies the permission table cumulatively', () => {
    expect(webRoleAllows('viewer', 'app.read')).toBe(true)
    expect(webRoleAllows('viewer', 'variables.read')).toBe(true)
    expect(webRoleAllows('viewer', 'variables.write')).toBe(false)
    expect(webRoleAllows('developer', 'app.deploy')).toBe(true)
    expect(webRoleAllows('developer', 'variables.write')).toBe(true)
    expect(webRoleAllows('developer', 'variables.reveal')).toBe(false)
    expect(webRoleAllows('developer', 'terminal.open')).toBe(false)
    expect(webRoleAllows('admin', 'variables.reveal')).toBe(true)
    expect(webRoleAllows('admin', 'members.manage')).toBe(true)
    expect(webRoleAllows('admin', 'members.manageAdmins')).toBe(false)
    expect(webRoleAllows('admin', 'project.rename')).toBe(false)
    expect(webRoleAllows('owner', 'project.rename')).toBe(true)
    expect(webRoleAllows('', 'app.read')).toBe(false)
    expect(webRoleAllows(null, 'app.read')).toBe(false)
    expect(webRoleAllows('owner', 'nope' as any)).toBe(false)
    // Déplacer un site entre deux projets : owner (exigé sur l'origine et sur la cible), jamais admin.
    expect(webRoleAllows('owner', 'app.move')).toBe(true)
    expect(webRoleAllows('admin', 'app.move')).toBe(false)
    expect(webRoleAllows('owner', 'app.detach')).toBe(false)
  })
  it('never opens platform-only permissions to project roles', () => {
    const platform = WEB_PERMISSION_NAMES.filter(isWebPlatformPermission)
    expect(platform).toEqual(
      expect.arrayContaining([
        'platform.github',
        'platform.recovery',
        'project.create',
        'app.attach',
        'app.detach',
        'app.repository',
      ])
    )
    expect(platform).not.toContain('app.move')
    for (const permission of platform) {
      for (const role of WEB_ROLES) expect(webRoleAllows(role, permission)).toBe(false)
      expect(webRoleAllows('platform', permission)).toBe(true)
    }
    expect(Object.values(webCapabilities('platform')).every(Boolean)).toBe(true)
    expect(Object.values(webCapabilities('')).some(Boolean)).toBe(false)
    expect(webCapabilities('viewer')['app.read']).toBe(true)
  })
})

describe('membership escalation rules', () => {
  it('lets admins manage only lower roles', () => {
    expect(webCanManageRole('admin', 'developer')).toBe(true)
    expect(webCanManageRole('admin', 'admin')).toBe(false)
    expect(webCanManageRole('admin', 'owner')).toBe(false)
    expect(webCanManageRole('owner', 'owner')).toBe(true)
    expect(webCanManageRole('platform', 'owner')).toBe(true)
    expect(webCanManageRole('developer', 'viewer')).toBe(false)
  })
  it('validates additions', () => {
    expect(webMemberChangeError('admin', { kind: 'add', role: 'developer' })).toBe('')
    expect(webMemberChangeError('admin', { kind: 'add', role: 'admin' })).toMatchObject({ code: 'forbidden' })
    expect(webMemberChangeError('owner', { kind: 'add', role: 'owner' })).toBe('')
    expect(webMemberChangeError('developer', { kind: 'add', role: 'viewer' })).not.toBe('')
    expect(webMemberChangeError('owner', { kind: 'add', role: 'root' })).toMatchObject({ code: 'invalid' })
    expect(webMemberChangeError('platform', { kind: 'add', role: 'owner' })).toBe('')
  })
  it('protects admin and owner rows from admins', () => {
    expect(webMemberChangeError('admin', { kind: 'change', from: 'viewer', role: 'developer', ownerCount: 1 })).toBe('')
    expect(webMemberChangeError('admin', { kind: 'change', from: 'developer', role: 'admin', ownerCount: 1 })).not.toBe(
      ''
    )
    expect(webMemberChangeError('admin', { kind: 'change', from: 'admin', role: 'viewer', ownerCount: 1 })).not.toBe('')
    expect(webMemberChangeError('admin', { kind: 'remove', from: 'owner', ownerCount: 2 })).not.toBe('')
    expect(webMemberChangeError('admin', { kind: 'remove', from: 'viewer', ownerCount: 1 })).toBe('')
    expect(webMemberChangeError('owner', { kind: 'change', from: 'admin', role: 'owner', ownerCount: 1 })).toBe('')
  })
  it('always keeps one owner', () => {
    expect(
      webMemberChangeError('owner', { kind: 'change', from: 'owner', role: 'admin', ownerCount: 1 })
    ).toMatchObject({
      code: 'last-owner',
    })
    expect(webMemberChangeError('owner', { kind: 'change', from: 'owner', role: 'admin', ownerCount: 2 })).toBe('')
    expect(webMemberChangeError('owner', { kind: 'change', from: 'owner', role: 'owner', ownerCount: 1 })).toBe('')
    expect(webMemberChangeError('platform', { kind: 'remove', from: 'owner', ownerCount: 1 })).not.toBe('')
    expect(webMemberChangeError('owner', { kind: 'remove', from: 'owner', ownerCount: 2 })).toBe('')
    expect(webMemberChangeError('', { kind: 'leave', from: 'owner', ownerCount: 1 })).not.toBe('')
    expect(webMemberChangeError('', { kind: 'leave', from: 'owner', ownerCount: 2 })).toBe('')
    expect(webMemberChangeError('', { kind: 'leave', from: 'viewer', ownerCount: 0 })).toBe('')
  })
})
