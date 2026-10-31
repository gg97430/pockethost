import { describe, expect, it } from 'vitest'
import { webCapabilities } from '../../../pockethost/src/common/webAccess'
import {
  WEB_NO_CAPABILITIES,
  webAppCapabilities,
  webAppTabs,
  webAssignableRoles,
  webMemberActions,
  webNavVisible,
  webRoleLabel,
} from './webAccessView'

const me = (overrides: Record<string, unknown> = {}) => ({
  enabled: true,
  isPlatformAdmin: false,
  hasAccess: true,
  projects: [{ id: 'p1', capabilities: webCapabilities('developer') }],
  ...overrides,
})

describe('Web access navigation', () => {
  it('shows the module to active superAdmins and to project members only', () => {
    expect(webNavVisible(true, { superAdmin: true }, null)).toBe(true)
    expect(webNavVisible(true, { superAdmin: false }, null)).toBe(false)
    expect(webNavVisible(true, { superAdmin: false }, me())).toBe(true)
    expect(webNavVisible(true, { superAdmin: false }, me({ hasAccess: false, projects: [] }))).toBe(false)
    expect(webNavVisible(false, { superAdmin: true }, me())).toBe(false)
    expect(webNavVisible(true, { superAdmin: true, suspension: 'abuse' }, me())).toBe(false)
    expect(webNavVisible(true, undefined, me())).toBe(false)
  })
})

describe('Web app capabilities', () => {
  it('prefers the server access view, then the project from GET me, and fails closed', () => {
    const viewer = webCapabilities('viewer')
    expect(webAppCapabilities({ capabilities: viewer }, me({ isPlatformAdmin: true }))['app.deploy']).toBe(false)
    expect(webAppCapabilities(undefined, me(), 'p1')['app.deploy']).toBe(true)
    expect(webAppCapabilities(undefined, me(), 'p1')['variables.reveal']).toBe(false)
    expect(webAppCapabilities(undefined, me(), 'other')).toEqual(WEB_NO_CAPABILITIES)
    expect(webAppCapabilities(undefined, null)).toEqual(WEB_NO_CAPABILITIES)
    expect(webAppCapabilities(undefined, me({ isPlatformAdmin: true }))['app.repository']).toBe(true)
    expect(webAppCapabilities({ capabilities: { 'app.read': true } }, null)['app.deploy']).toBe(false)
  })
  it('hides tabs a role cannot open, and members without a project', () => {
    const ids = (role: Parameters<typeof webCapabilities>[0], project = true) =>
      webAppTabs(webCapabilities(role), project).map((tab) => tab.id)
    expect(ids('viewer')).toContain('members')
    expect(ids('viewer')).toContain('environment')
    expect(ids('viewer')).not.toContain('terminal')
    expect(ids('viewer')).not.toContain('ci')
    expect(ids('developer')).not.toContain('terminal')
    expect(ids('admin')).toContain('terminal')
    expect(ids('admin')).toContain('ci')
    expect(ids('platform', false)).not.toContain('members')
    expect(ids('')).toEqual([])
  })
})

describe('Web member management', () => {
  it('lists only the roles an actor may grant', () => {
    expect(webAssignableRoles('owner')).toEqual(['viewer', 'developer', 'admin', 'owner'])
    expect(webAssignableRoles('platform')).toEqual(['viewer', 'developer', 'admin', 'owner'])
    expect(webAssignableRoles('admin')).toEqual(['viewer', 'developer'])
    expect(webAssignableRoles('developer')).toEqual([])
  })
  it('mirrors the server escalation and last-owner rules', () => {
    expect(webMemberActions('admin', { role: 'admin', self: false }, 1)).toMatchObject({
      canChange: false,
      canRemove: false,
    })
    expect(webMemberActions('admin', { role: 'viewer', self: false }, 1)).toMatchObject({
      roles: ['viewer', 'developer'],
      canChange: true,
      canRemove: true,
    })
    const lastOwner = webMemberActions('owner', { role: 'owner', self: true }, 1)
    expect(lastOwner.canLeave).toBe(false)
    expect(lastOwner.roles).toEqual(['owner'])
    expect(lastOwner.canChange).toBe(false)
    expect(webMemberActions('owner', { role: 'owner', self: true }, 2)).toMatchObject({
      canLeave: true,
      canChange: true,
    })
    expect(webMemberActions('viewer', { role: 'viewer', self: true }, 0)).toMatchObject({
      canLeave: true,
      canRemove: false,
      canChange: false,
    })
  })
  it('labels roles in French', () => {
    expect(webRoleLabel('platform')).toBe('Administration plateforme')
    expect(webRoleLabel('viewer')).toBe('Lecture')
    expect(webRoleLabel('')).toBe('Aucun accès')
  })
})
