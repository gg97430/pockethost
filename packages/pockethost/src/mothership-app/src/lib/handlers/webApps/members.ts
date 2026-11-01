import {
  WEB_PERMISSIONS,
  WEB_ROLES,
  type WebMemberChangeError,
  type WebRole,
  isWebRole,
  webCapabilities,
  webMemberChangeError,
} from '../../../../../common/webAccess'
import {
  WEB_MEMBERS,
  isWebPlatformAdmin,
  listWebMemberships,
  requirePlatformAdmin,
  requireWebProjectPermission,
  webProjectMemberRecord,
} from './access'
import { webEnabled, webInput, webJson } from './api'
import { auditWebProject } from './projects'

const ID = /^[a-z0-9]{15}$/
const fail = (error: WebMemberChangeError) => {
  if (!error) return
  if (error.code === 'forbidden') throw new ForbiddenError(error.message)
  throw new BadRequestError(error.message)
}
const userSummary = (id: string, store: any = $app) => {
  if (!id) return null
  try {
    const user = store.findRecordById('users', id)
    return { id: user.id, email: user.getString('email') }
  } catch {
    return null
  }
}
const memberView = (row: core.Record, selfId: string, store: any = $app) => ({
  id: row.id,
  projectId: row.getString('project'),
  userId: row.getString('user'),
  email: userSummary(row.getString('user'), store)?.email || '',
  role: row.getString('role') as WebRole,
  invitedBy: userSummary(row.getString('invitedBy'), store),
  self: row.getString('user') === selfId,
  created: row.getString('created'),
  updated: row.getString('updated'),
})
const ownerCount = (projectId: string, store: any) =>
  store.findRecordsByFilter(WEB_MEMBERS, 'project={:id} && role="owner"', '', 1000, 0, { id: projectId }).length
const memberOf = (e: core.RequestEvent, projectId: string, store: any) => {
  const id = e.request!.pathValue('memberId')
  if (!ID.test(id || '')) throw new BadRequestError('Membre invalide.')
  let row: core.Record
  try {
    row = store.findRecordById(WEB_MEMBERS, id)
  } catch {
    throw new NotFoundError('Membre introuvable.')
  }
  if (row.getString('project') !== projectId) throw new NotFoundError('Membre introuvable.')
  return row
}
const projectView = (row: core.Record, role: WebRole | '', platform: boolean) => ({
  id: row.id,
  name: row.getString('name'),
  repository: row.getString('repository'),
  multiRepository: row.getBool('multiRepository'),
  role: platform ? 'platform' : role,
  memberRole: role || null,
  capabilities: webCapabilities(platform ? 'platform' : role),
})

/** GET /api/web-access/me — droits de l'utilisateur courant, consommé par le dashboard. */
export const HandleWebAccessMe = (e: core.RequestEvent) => {
  const user = e.auth
  if (!user) throw new UnauthorizedError('Authentification requise.')
  const usable = webEnabled() && user.collection().name === 'users' && !user.getString('suspension'),
    platform = usable && isWebPlatformAdmin(user)
  // superAdmin : aucun listage global (pas de plafond ni de coût) ; `allProjects` signifie « tout est permis ».
  // `projects` ne contient que ses adhésions explicites. La liste complète vient de GET /api/web-projects (paginée).
  const projects: ReturnType<typeof projectView>[] = []
  if (usable)
    for (const m of listWebMemberships(user))
      if (isWebRole(m.role))
        try {
          projects.push(projectView($app.findRecordById('web_projects', m.projectId), m.role, platform))
        } catch {}
  projects.sort((a, b) => a.name.localeCompare(b.name))
  return webJson(e, {
    enabled: webEnabled(),
    isPlatformAdmin: platform,
    allProjects: platform,
    hasAccess: platform || projects.length > 0,
    roles: WEB_ROLES,
    permissions: WEB_PERMISSIONS,
    globalCapabilities: webCapabilities(platform ? 'platform' : ''),
    projects,
  })
}
export const HandleWebProjectMembersList = (e: core.RequestEvent) => {
  const { user, project, role } = requireWebProjectPermission(e, 'members.read'),
    rows = $app.findRecordsByFilter(WEB_MEMBERS, 'project={:id}', 'created', 500, 0, {
      id: project!.id,
    }) as core.Record[]
  const order = (r: core.Record) => -WEB_ROLES.indexOf(r.getString('role') as WebRole)
  return webJson(e, {
    projectId: project!.id,
    role,
    capabilities: webCapabilities(role),
    items: rows.sort((a, b) => order(a) - order(b)).map((r) => memberView(r, user.id)),
  })
}
/**
 * Chaque tentative d'ajout (réussie ou non) est comptée par auteur : un admin de projet ne peut pas sonder en masse
 * quels e-mails ont un compte sur ce serveur. Les superAdmins (attribution par utilisateur) ne sont pas limités.
 */
const ADD_RATE = 'web_member_add_rate',
  ADD_RATE_MAX = 20,
  ADD_RATE_WINDOW = 3600000
const limitMemberAdds = (actorId: string) => {
  let limited = false
  $app.store().setFunc(ADD_RATE, (raw: unknown) => {
    const at = Date.now(),
      all: Record<string, number[]> = raw ? JSON.parse(`${raw}`) : {}
    for (const id of Object.keys(all)) {
      all[id] = (all[id] || []).filter((t) => t > at - ADD_RATE_WINDOW)
      if (!all[id]!.length) delete all[id]
    }
    const hits = all[actorId] || []
    limited = hits.length >= ADD_RATE_MAX
    if (!limited) all[actorId] = [...hits, at]
    return JSON.stringify(all)
  })
  if (limited) throw new TooManyRequestsError('Trop d’ajouts de membres. Réessayez dans une heure.')
}
export const HandleWebProjectMemberAdd = (e: core.RequestEvent) => {
  const access = requireWebProjectPermission(e, 'members.manage'),
    input = webInput(e),
    projectId = access.project!.id
  if (!access.platform) limitMemberAdds(access.user.id)
  if (Object.keys(input).some((key) => !['email', 'role'].includes(key))) throw new BadRequestError('Champs invalides.')
  // Même normalisation qu'operatorAdmin (trim + minuscules), correspondance exacte uniquement.
  const email = typeof input.email === 'string' ? input.email.trim().toLowerCase() : ''
  if (!email || email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))
    throw new BadRequestError('Adresse e-mail invalide.')
  fail(webMemberChangeError(access.role, { kind: 'add', role: input.role }))
  // Réponse identique pour un compte inexistant, non vérifié ou suspendu : aucune fuite d'adresse.
  let target: core.Record | null = null
  try {
    target = $app.findFirstRecordByData('users', 'email', email)
  } catch {}
  if (!target || !target.getBool('verified') || target.getString('suspension'))
    throw new BadRequestError(
      'Impossible d’ajouter cette personne. Elle doit déjà avoir un compte actif et vérifié sur ce serveur.'
    )
  const invitee = target
  let member: any
  $app.runInTransaction((tx) => {
    if (webProjectMemberRecord(projectId, invitee.id, tx))
      throw new BadRequestError('Cette personne est déjà membre du projet.')
    const row = new Record(tx.findCollectionByNameOrId(WEB_MEMBERS))
    row.set('project', projectId)
    row.set('user', invitee.id)
    row.set('role', input.role)
    row.set('invitedBy', access.user.id)
    tx.save(row)
    auditWebProject(projectId, access.user.id, 'member-added', { userId: invitee.id, email, role: input.role }, tx)
    member = memberView(row, access.user.id, tx)
  })
  return webJson(e, { member }, 201)
}
export const HandleWebProjectMemberUpdate = (e: core.RequestEvent) => {
  const access = requireWebProjectPermission(e, 'members.manage'),
    input = webInput(e),
    projectId = access.project!.id
  if (Object.keys(input).some((key) => key !== 'role')) throw new BadRequestError('Champs invalides.')
  let member: any
  $app.runInTransaction((tx) => {
    const row = memberOf(e, projectId, tx),
      from = row.getString('role') as WebRole
    fail(
      webMemberChangeError(access.role, {
        kind: 'change',
        from,
        role: input.role,
        ownerCount: ownerCount(projectId, tx),
      })
    )
    if (from !== input.role) {
      row.set('role', input.role)
      tx.save(row)
      auditWebProject(
        projectId,
        access.user.id,
        'member-role-changed',
        {
          userId: row.getString('user'),
          email: userSummary(row.getString('user'), tx)?.email || '',
          from,
          role: input.role,
        },
        tx
      )
    }
    member = memberView(row, access.user.id, tx)
  })
  return webJson(e, { member })
}
export const HandleWebProjectMemberRemove = (e: core.RequestEvent) => {
  const access = requireWebProjectPermission(e, 'members.manage'),
    projectId = access.project!.id
  $app.runInTransaction((tx) => {
    const row = memberOf(e, projectId, tx),
      from = row.getString('role') as WebRole
    fail(webMemberChangeError(access.role, { kind: 'remove', from, ownerCount: ownerCount(projectId, tx) }))
    auditWebProject(
      projectId,
      access.user.id,
      'member-removed',
      { userId: row.getString('user'), email: userSummary(row.getString('user'), tx)?.email || '', role: from },
      tx
    )
    tx.delete(row)
  })
  return webJson(e, { removed: true })
}
export const HandleWebProjectMemberLeave = (e: core.RequestEvent) => {
  const access = requireWebProjectPermission(e, 'project.read'),
    projectId = access.project!.id
  $app.runInTransaction((tx) => {
    const row = webProjectMemberRecord(projectId, access.user.id, tx)
    if (!row) throw new BadRequestError('Vous n’êtes pas membre de ce projet.')
    const from = row.getString('role') as WebRole
    fail(webMemberChangeError(from, { kind: 'leave', from, ownerCount: ownerCount(projectId, tx) }))
    auditWebProject(projectId, access.user.id, 'member-left', { userId: access.user.id, role: from }, tx)
    tx.delete(row)
  })
  return webJson(e, { left: true })
}
/** Historique des changements d'accès du projet (ajouts, rôles, retraits, départs). */
export const HandleWebProjectMembersAudit = (e: core.RequestEvent) => {
  const { project } = requireWebProjectPermission(e, 'members.manage'),
    page = Math.max(1, Math.min(10000, Math.floor(Number(e.request!.url!.query().get('page')) || 1))),
    rows = $app.findRecordsByFilter(
      'web_project_audit',
      'projectId={:id} && action ~ "member-"',
      '-created',
      26,
      (page - 1) * 25,
      { id: project!.id }
    ) as core.Record[]
  return webJson(e, {
    page,
    hasMore: rows.length > 25,
    items: rows.slice(0, 25).map((r) => ({
      id: r.id,
      action: r.getString('action'),
      actor: userSummary(r.getString('actorId')),
      details: JSON.parse(r.getString('details') || '{}'),
      created: r.getString('created'),
    })),
  })
}

/**
 * Attribution des projets à un utilisateur par un superAdmin (même parcours que UserInstanceAccess pour les bases). GET
 * : tous les projets avec le rôle actuel de l'utilisateur. PUT { projects: [{ projectId, role }] } remplace l'ensemble
 * ; un projet absent de la liste est retiré, sauf s'il s'agit de son dernier propriétaire.
 */
const assignedUser = (e: core.RequestEvent) => {
  const id = e.request!.pathValue('userId')
  if (!ID.test(id || '')) throw new BadRequestError('Utilisateur invalide.')
  try {
    return $app.findRecordById('users', id)
  } catch {
    throw new NotFoundError('Utilisateur introuvable.')
  }
}
const assignments = (user: core.Record) => {
  const roles: Record<string, WebRole> = {}
  for (const m of listWebMemberships(user)) if (isWebRole(m.role)) roles[m.projectId] = m.role
  return {
    userId: user.id,
    superAdmin: user.getBool('superAdmin'),
    items: $app.findRecordsByFilter('web_projects', '', 'name', 1000, 0).map((row) => ({
      projectId: row!.id,
      name: row!.getString('name'),
      repository: row!.getString('repository'),
      role: roles[row!.id] || null,
    })),
  }
}
export const HandleWebUserProjectsGet = (e: core.RequestEvent) => {
  requirePlatformAdmin(e)
  return webJson(e, assignments(assignedUser(e)))
}
export const HandleWebUserProjectsPut = (e: core.RequestEvent) => {
  const actor = requirePlatformAdmin(e),
    user = assignedUser(e),
    input = webInput(e)
  if (
    Object.keys(input).some((key) => key !== 'projects') ||
    !Array.isArray(input.projects) ||
    input.projects.length > 1000 ||
    input.projects.some(
      (p: any) =>
        !p ||
        typeof p !== 'object' ||
        Object.keys(p).some((key) => !['projectId', 'role'].includes(key)) ||
        !ID.test(p.projectId || '') ||
        !isWebRole(p.role)
    )
  )
    throw new BadRequestError('Liste attendue : [{ projectId, role }] avec un rôle viewer, developer, admin ou owner.')
  const wanted: Record<string, WebRole> = {}
  for (const p of input.projects) wanted[p.projectId] = p.role
  $app.runInTransaction((tx) => {
    const current: Record<string, core.Record> = {}
    for (const row of tx.findRecordsByFilter(WEB_MEMBERS, 'user={:user}', '', 1000, 0, { user: user.id }))
      current[row!.getString('project')] = row!
    for (const projectId of Object.keys(wanted)) {
      try {
        tx.findRecordById('web_projects', projectId)
      } catch {
        throw new BadRequestError('Projet introuvable.')
      }
      const role = wanted[projectId]!,
        row = current[projectId]
      if (!row) {
        const created = new Record(tx.findCollectionByNameOrId(WEB_MEMBERS))
        created.set('project', projectId)
        created.set('user', user.id)
        created.set('role', role)
        created.set('invitedBy', actor.id)
        tx.save(created)
        auditWebProject(
          projectId,
          actor.id,
          'member-added',
          { userId: user.id, email: user.getString('email'), role },
          tx
        )
        continue
      }
      const from = row.getString('role') as WebRole
      if (from === role) continue
      fail(webMemberChangeError('platform', { kind: 'change', from, role, ownerCount: ownerCount(projectId, tx) }))
      row.set('role', role)
      tx.save(row)
      auditWebProject(
        projectId,
        actor.id,
        'member-role-changed',
        { userId: user.id, email: user.getString('email'), from, role },
        tx
      )
    }
    for (const projectId of Object.keys(current)) {
      if (wanted[projectId]) continue
      const row = current[projectId]!,
        from = row.getString('role') as WebRole
      fail(webMemberChangeError('platform', { kind: 'remove', from, ownerCount: ownerCount(projectId, tx) }))
      auditWebProject(
        projectId,
        actor.id,
        'member-removed',
        { userId: user.id, email: user.getString('email'), role: from },
        tx
      )
      tx.delete(row)
    }
  })
  return webJson(e, assignments(user))
}
