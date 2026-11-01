<script lang="ts">
  import { onMount } from 'svelte'
  import { webError, webDate } from '$lib/webApps'
  import {
    webProjectAccessAudit,
    webProjectLeave,
    webProjectMemberAdd,
    webProjectMemberRemove,
    webProjectMemberRole,
    webProjectMembers,
    type WebAccessAuditEntry,
    type WebEffectiveRole,
    type WebProjectMember,
    type WebProjectMembers,
    type WebRole,
  } from '$lib/webAccess'
  import { refreshWebAccess } from '$lib/webAccessStore'
  import { WEB_ROLE_HELP, webAssignableRoles, webMemberActions, webRoleLabel } from '$lib/webAccessView'
  export let projectId: string
  export let projectName = ''
  /** Appelé après avoir quitté le projet (l'accès est perdu). */
  export let onleft: () => void | Promise<void> = () => {}

  let data: WebProjectMembers | null = null
  let busy = '',
    error = '',
    message = '',
    email = '',
    role: WebRole = 'developer',
    removing = '',
    leaving = false,
    loadedFor = '',
    mounted = false,
    disposed = false,
    generation = 0
  let audit: WebAccessAuditEntry[] = [],
    auditPage = 1,
    auditMore = false,
    auditLoaded = false,
    auditError = ''
  $: ownerCount = data?.items.filter((m) => m.role === 'owner').length || 0
  let actor: WebEffectiveRole | '' = ''
  $: actor = data?.role || ''
  $: canManage = !!data?.capabilities['members.manage']
  $: assignable = webAssignableRoles(actor)
  $: if (assignable.length && !assignable.includes(role)) role = assignable[0]!
  $: self = data?.items.find((m) => m.self)
  $: selfLeave = self ? webMemberActions(actor, self, ownerCount).canLeave : false

  const load = async () => {
    const request = ++generation,
      id = projectId
    busy = busy || 'load'
    try {
      const value = await webProjectMembers(id)
      if (disposed || request !== generation || id !== projectId) return
      data = value
      error = ''
    } catch (e) {
      if (request === generation) error = webError(e)
    } finally {
      if (request === generation && busy === 'load') busy = ''
    }
  }
  $: if (mounted && projectId && loadedFor !== projectId) {
    loadedFor = projectId
    data = null
    audit = []
    auditLoaded = false
    removing = ''
    leaving = false
    void load()
  }
  const run = async (key: string, action: () => Promise<unknown>, text: string) => {
    busy = key
    error = ''
    message = ''
    try {
      await action()
      message = text
      busy = ''
      await load()
      if (auditLoaded) await loadAudit(1)
    } catch (e) {
      error = webError(e)
      await load()
    } finally {
      busy = ''
    }
  }
  const add = (event: SubmitEvent) => {
    event.preventDefault()
    const address = email.trim()
    void run(
      'add',
      async () => {
        await webProjectMemberAdd(projectId, address, role)
        email = ''
      },
      `${address} a rejoint le projet (${webRoleLabel(role)}).`
    )
  }
  const changeRole = (member: WebProjectMember, next: WebRole) => {
    if (next === member.role) return
    void run(
      member.id,
      () => webProjectMemberRole(projectId, member.id, next),
      `${member.email} : ${webRoleLabel(next)}.`
    )
  }
  const remove = (member: WebProjectMember) => {
    removing = ''
    void run(member.id, () => webProjectMemberRemove(projectId, member.id), `${member.email} n’a plus accès au projet.`)
  }
  const leave = async () => {
    busy = 'leave'
    error = ''
    try {
      await webProjectLeave(projectId)
      leaving = false
      await refreshWebAccess()
      await onleft()
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const loadAudit = async (next: number) => {
    auditError = ''
    try {
      const value = await webProjectAccessAudit(projectId, next)
      audit = value.items
      auditPage = value.page
      auditMore = value.hasMore
      auditLoaded = true
    } catch (e) {
      auditError = webError(e)
    }
  }
  const auditText = (entry: WebAccessAuditEntry) => {
    const who = entry.details.email || 'Un membre'
    if (entry.action === 'member-added') return `${who} ajouté · ${webRoleLabel(entry.details.role)}`
    if (entry.action === 'member-role-changed')
      return `${who} · ${webRoleLabel(entry.details.from)} → ${webRoleLabel(entry.details.role)}`
    if (entry.action === 'member-removed') return `${who} retiré`
    return `${entry.actor?.email || who} a quitté le projet`
  }
  onMount(() => {
    mounted = true
    disposed = false
    return () => {
      disposed = true
      generation++
    }
  })
</script>

<section class="web-panel members-panel" aria-busy={!!busy}>
  <div class="web-row">
    <div>
      <span class="web-eyebrow">Membres du projet{projectName ? ` · ${projectName}` : ''}</span>
      <h2>Qui peut intervenir sur ce projet</h2>
    </div>
    {#if data}<span class="web-status" data-tone={actor === 'viewer' ? 'neutral' : 'success'}
        >Votre rôle : {webRoleLabel(actor)}</span
      >{/if}
  </div>
  {#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}
  {#if message}<div class="web-alert web-alert--success" role="status">{message}</div>{/if}
  {#if canManage}<form class="members-add" onsubmit={add}>
      <label class="web-field"
        ><span>Adresse du compte</span><input
          type="email"
          bind:value={email}
          required
          maxlength="254"
          placeholder="prenom.nom@exemple.fr"
          autocomplete="off"
          disabled={!!busy}
        /></label
      ><label class="web-field"
        ><span>Rôle</span><select bind:value={role} disabled={!!busy}
          >{#each assignable as item}<option value={item}>{webRoleLabel(item)}</option>{/each}</select
        ></label
      ><button class="web-button web-button--primary" type="submit" disabled={!!busy || !email.trim()}
        >{busy === 'add' ? 'Ajout…' : 'Ajouter'}</button
      >
    </form>
    <p class="web-help">{WEB_ROLE_HELP[role]} La personne doit déjà avoir un compte sur ce serveur.</p>{/if}
  {#if !data}<p class="web-muted" role="status">{busy ? 'Chargement des membres…' : ''}</p>
  {:else}
    <div class="web-table-wrap">
      <table>
        <thead
          ><tr><th>Membre</th><th>Rôle</th><th>Ajouté</th><th><span class="web-muted">Actions</span></th></tr></thead
        ><tbody
          >{#each data.items as member (member.id)}{@const actions = webMemberActions(actor, member, ownerCount)}<tr
              ><td
                ><strong>{member.email}</strong>{#if member.self}<span class="web-help"> · vous</span>{/if}
                {#if member.invitedBy}<div class="web-help">par {member.invitedBy.email}</div>{/if}</td
              ><td
                >{#if canManage && actions.canChange}<select
                    aria-label={`Rôle de ${member.email}`}
                    value={member.role}
                    onchange={(event) => changeRole(member, event.currentTarget.value as WebRole)}
                    disabled={!!busy}
                    >{#each actions.roles as item}<option value={item}>{webRoleLabel(item)}</option>{/each}</select
                  >{:else}{webRoleLabel(member.role)}{/if}</td
              ><td>{webDate(member.created)}</td><td
                >{#if member.self}{#if selfLeave}{#if leaving}<div class="web-actions">
                        <button class="web-button web-button--danger" onclick={leave} disabled={!!busy}
                          >{busy === 'leave' ? 'Départ…' : 'Confirmer le départ'}</button
                        ><button class="web-button" onclick={() => (leaving = false)} disabled={!!busy}>Annuler</button>
                      </div>{:else}<button class="web-button" onclick={() => (leaving = true)} disabled={!!busy}
                        >Quitter le projet</button
                      >{/if}{:else if member.role === 'owner'}<span class="web-help"
                      >Nommez un autre propriétaire avant de partir.</span
                    >{/if}{:else if canManage && actions.canRemove}{#if removing === member.id}<div class="web-actions">
                      <button class="web-button web-button--danger" onclick={() => remove(member)} disabled={!!busy}
                        >Confirmer le retrait</button
                      ><button class="web-button" onclick={() => (removing = '')} disabled={!!busy}>Annuler</button>
                    </div>{:else}<button class="web-button" onclick={() => (removing = member.id)} disabled={!!busy}
                      >Retirer</button
                    >{/if}{/if}</td
              ></tr
            >{:else}<tr
              ><td colspan="4"
                >Aucun membre. {data.role === 'platform'
                  ? 'Ajoutez un propriétaire pour confier ce projet à une équipe.'
                  : ''}</td
              ></tr
            >{/each}</tbody
        >
      </table>
    </div>
    {#if data.role === 'platform' && data.items.length && !ownerCount}<p class="web-help">
        Ce projet n’a pas de propriétaire : seuls les administrateurs de la plateforme gèrent ses administrateurs.
      </p>{/if}
    {#if canManage}<details
        class="web-details"
        ontoggle={(event) => {
          if (event.currentTarget.open && !auditLoaded) void loadAudit(1)
        }}
      >
        <summary>Historique des accès</summary>
        {#if auditError}<div class="web-alert web-alert--error" role="alert">{auditError}</div>{/if}
        <ul class="members-audit">
          {#each audit as entry (entry.id)}<li>
              <span>{auditText(entry)}</span><span class="web-help"
                >{webDate(entry.created)}{entry.actor && entry.action !== 'member-left'
                  ? ` · par ${entry.actor.email}`
                  : ''}</span
              >
            </li>{:else}<li class="web-muted">{auditLoaded ? 'Aucun changement enregistré.' : 'Chargement…'}</li>{/each}
        </ul>
        {#if auditPage > 1 || auditMore}<nav class="web-pagination" aria-label="Pages de l’historique des accès">
            <button class="web-button" onclick={() => loadAudit(auditPage - 1)} disabled={auditPage === 1}
              >Précédent</button
            ><span>Page {auditPage}</span><button
              class="web-button"
              onclick={() => loadAudit(auditPage + 1)}
              disabled={!auditMore}>Suivant</button
            >
          </nav>{/if}
      </details>{/if}
  {/if}
</section>

<style>
  .members-panel {
    display: grid;
    gap: 1rem;
    min-width: 0;
  }
  .members-panel p {
    margin: 0;
  }
  .members-add {
    display: grid;
    grid-template-columns: minmax(0, 2fr) minmax(0, 1fr) auto;
    align-items: end;
    gap: 0.75rem;
  }
  .members-audit {
    display: grid;
    gap: 0.5rem;
    margin: 0.8rem 0 0;
    padding: 0;
    list-style: none;
    font-size: 0.85rem;
  }
  .members-audit li {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    gap: 0.5rem;
  }
  @media (max-width: 640px) {
    .members-add {
      grid-template-columns: 1fr;
    }
  }
</style>
