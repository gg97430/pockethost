import { describe, expect, it } from 'vitest'
import { webCapabilities } from '../../../pockethost/src/common/webAccess'
import {
  WEB_UNASSIGNED_GROUP,
  WEB_UNASSIGNED_LABEL,
  webGroupOpen,
  webMembershipLabel,
  webMoveDefaultSlug,
  webMoveSummary,
  webMoveTargets,
  webParseGroupState,
  webSiteCountLabel,
  webSortGroups,
} from './webProjectGroupsView'

type Role = Parameters<typeof webCapabilities>[0]
const site = (projectId: string, slug: string, role: Role, environment = 'prod', name = slug) => ({
  name,
  access: { projectId, capabilities: webCapabilities(role) },
  membership: { environment: environment as 'prod', slug, previewNumber: 0 },
})
const group = (id: string, name: string, role: Role, apps: ReturnType<typeof site>[] = []) => ({
  project: { id, name, siteCount: apps.length, previewCount: 0, capabilities: webCapabilities(role) },
  apps,
})

describe('webSiteCountLabel', () => {
  it('accorde sites et previews', () => {
    expect(webSiteCountLabel(0)).toBe('Aucun site')
    expect(webSiteCountLabel(1)).toBe('1 site')
    expect(webSiteCountLabel(3)).toBe('3 sites')
    expect(webSiteCountLabel(2, 1)).toBe('2 sites · 1 preview')
    expect(webSiteCountLabel(1, 4)).toBe('1 site · 4 previews')
  })
  it('libelle les environnements', () => {
    expect(webMembershipLabel({ environment: 'staging', previewNumber: 0 })).toBe('Préproduction')
    expect(webMembershipLabel({ environment: 'preview', previewNumber: 12 })).toBe('Preview PR #12')
  })
})

describe('webMoveTargets', () => {
  const vitrine = site('a', 'vitrine', 'owner')
  it('ne déplace pas une application ni une cible en cours de suppression', () => {
    const deleting = {
      ...group('b', 'B', 'owner'),
      project: { ...group('b', 'B', 'owner').project, deletion: { status: 'queued' } },
    }
    expect(webMoveTargets([deleting], vitrine, webCapabilities('owner'))).toEqual([])
    expect(
      webMoveTargets([group('b', 'B', 'owner')], { ...vitrine, deletion: {} }, webCapabilities('platform'))
    ).toEqual([])
  })
  it('owner de A et de B : B seulement', () => {
    const groups = [group('a', 'A', 'owner', [vitrine]), group('b', 'B', 'owner'), group('c', 'C', 'viewer')]
    expect(webMoveTargets(groups, vitrine, webCapabilities('owner'))).toEqual([
      { projectId: 'b', label: 'B · Aucun site' },
    ])
  })
  it('admin de B (ou admin de A) : aucune cible', () => {
    expect(webMoveTargets([group('a', 'A', 'owner', [vitrine]), group('b', 'B', 'admin')], vitrine, {})).toEqual([])
    const asAdmin = site('a', 'vitrine', 'admin')
    expect(webMoveTargets([group('a', 'A', 'admin', [asAdmin]), group('b', 'B', 'owner')], asAdmin, {})).toEqual([])
  })
  it('plateforme : tous les projets sauf l’actuel, puis « Sans projet »', () => {
    const app = site('a', 'vitrine', 'platform')
    const groups = [group('a', 'A', 'platform', [app]), group('b', 'B', 'platform'), group('c', 'C', 'platform')]
    expect(webMoveTargets(groups, app, webCapabilities('platform')).map((t) => t.projectId)).toEqual(['b', 'c', null])
    expect(webMoveTargets(groups, app, webCapabilities('platform')).at(-1)?.label).toBe(WEB_UNASSIGNED_LABEL)
  })
  it('application sans projet : tous les projets pour la plateforme, rien sinon', () => {
    const app = { name: 'Api', access: { projectId: '', capabilities: webCapabilities('platform') }, membership: null }
    const groups = [group('a', 'A', 'platform'), group('b', 'B', 'platform')]
    expect(webMoveTargets(groups, app, webCapabilities('platform')).map((t) => t.projectId)).toEqual(['a', 'b'])
    expect(webMoveTargets(groups, app, webCapabilities('owner'))).toEqual([])
  })
  it('preview : jamais déplaçable', () => {
    const preview = site('a', 'pr-3', 'platform', 'preview')
    const groups = [group('a', 'A', 'platform', [preview]), group('b', 'B', 'platform')]
    expect(webMoveTargets(groups, preview, webCapabilities('platform'))).toEqual([])
  })
})

describe('webMoveDefaultSlug', () => {
  it('garde le nom court actuel s’il est libre, sinon suffixe', () => {
    const app = site('a', 'vitrine', 'owner', 'prod', 'Vitrine')
    expect(webMoveDefaultSlug(app, group('b', 'B', 'owner', [site('b', 'api', 'owner')]))).toBe('vitrine')
    expect(webMoveDefaultSlug(app, group('b', 'B', 'owner', [site('b', 'vitrine', 'owner')]))).toBe('vitrine-2')
    expect(webMoveDefaultSlug(app, null)).toBe('')
  })
  it('dérive le nom court du nom pour une application sans projet', () => {
    const app = { name: 'Back-office Été', access: { projectId: '' }, membership: null }
    expect(webMoveDefaultSlug(app, group('b', 'B', 'owner'))).toBe('back-office-ete')
  })
})

describe('repli des groupes', () => {
  it('projets ouverts, « Sans projet » fermé par défaut', () => {
    expect(webGroupOpen({}, 'a', false)).toBe(true)
    expect(webGroupOpen({}, WEB_UNASSIGNED_GROUP, true)).toBe(false)
    expect(webGroupOpen({ a: false, [WEB_UNASSIGNED_GROUP]: true }, 'a', false)).toBe(false)
    expect(webGroupOpen({ a: false, [WEB_UNASSIGNED_GROUP]: true }, WEB_UNASSIGNED_GROUP, true)).toBe(true)
  })
  it('tolère un état mémorisé invalide', () => {
    expect(webParseGroupState(null)).toEqual({})
    expect(webParseGroupState('{pas du json')).toEqual({})
    expect(webParseGroupState('[true]')).toEqual({})
    expect(webParseGroupState('{"a":false,"b":"oui"}')).toEqual({ a: false })
  })
  it('trie les groupes par nom, sans tenir compte de la casse ni des accents', () => {
    const sorted = webSortGroups([
      { project: { name: 'zèbre' } },
      { project: { name: 'Été' } },
      { project: { name: 'abc' } },
    ])
    expect(sorted.map((g) => g.project.name)).toEqual(['abc', 'Été', 'zèbre'])
  })
})

describe('webMoveSummary', () => {
  const base = { droppedVariables: [], copiedVariables: [], previewsDisabled: false, notificationsReset: false }
  it('liste les variables qui ne s’appliquent plus (noms seulement)', () => {
    expect(webMoveSummary({ ...base, droppedVariables: ['X_SHARED', 'API_URL'] }, 'A', 'B')).toBe(
      'Site déplacé vers « B ». 2 variables partagées ne s’appliquent plus : X_SHARED, API_URL.'
    )
    expect(webMoveSummary({ ...base, droppedVariables: ['X_SHARED'] }, 'A', 'B')).toBe(
      'Site déplacé vers « B ». 1 variable partagée ne s’applique plus : X_SHARED.'
    )
  })
  it('signale copies, previews et alertes, et le retrait vers « Sans projet »', () => {
    expect(
      webMoveSummary({ ...base, copiedVariables: ['X'], previewsDisabled: true, notificationsReset: true }, 'A', 'B')
    ).toBe(
      'Site déplacé vers « B ». Variables copiées : X. Les previews de « A » ont été désactivées : ce site en était le modèle. Les canaux d’alerte du site ont été réinitialisés.'
    )
    expect(webMoveSummary(base, 'A', null)).toBe('Site retiré du projet « A ».')
  })
})
