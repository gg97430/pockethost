<script lang="ts">
  import type { OperatorWebProject, OperatorWebProjectAssignment } from '$src/pocketbase-client'
  import { WEB_ROLES, WEB_ROLE_LABELS, type WebRole } from '$lib/webAccess'

  export let projects: OperatorWebProject[] = []
  export let selected: OperatorWebProjectAssignment[] = []
  export let superAdmin = false
  export let disabled = false
  let search = ''
  $: visible = projects.filter((p) => `${p.name} ${p.repository}`.toLowerCase().includes(search.toLowerCase()))
  const descriptions: Record<WebRole, string> = {
    viewer: 'Consulter les sites et leurs journaux.',
    developer: 'Consulter, déployer et modifier les variables sans révéler les secrets.',
    admin: 'Configurer les sites, les domaines, les tâches et les accès ; consulter les secrets.',
    owner: 'Tous les droits du projet, y compris la suppression des sites et du projet.',
  }
  function toggle(projectId: string, checked: boolean) {
    selected = checked
      ? [...selected, { projectId, role: 'viewer' }]
      : selected.filter((p) => p.projectId !== projectId)
  }
  function changeRole(projectId: string, role: WebRole) {
    selected = selected.map((p) => (p.projectId === projectId ? { ...p, role } : p))
  }
</script>

<fieldset class="base-access" {disabled}>
  <legend>Hébergements · accès par projet</legend>
  {#if superAdmin}
    <p class="access-global"><wa-icon name="shield-halved"></wa-icon> Tous les hébergements · accès superuser</p>
    <p class="help">Les rattachements sont conservés si le rôle du compte change.</p>
  {:else}
    <p class="help">Choisissez les projets accessibles. Le rôle s’applique à tous leurs sites et environnements.</p>
    {#if projects.length}
      <label class="search">
        <wa-icon name="magnifying-glass" aria-hidden="true"></wa-icon>
        <span class="sr-only">Rechercher un projet d’hébergement</span>
        <input type="search" placeholder="Nom du projet…" bind:value={search} />
      </label>
      <div class="base-list">
        {#each visible as project (project.id)}
          {@const assignment = selected.find((p) => p.projectId === project.id)}
          <div class="base-option">
            <label class="base-name">
              <input
                type="checkbox"
                checked={!!assignment}
                onchange={(event) => toggle(project.id, event.currentTarget.checked)}
              />
              <wa-icon name="server" aria-hidden="true"></wa-icon>{project.name}
            </label>
            {#if assignment}
              <select
                aria-label={`Rôle dans ${project.name}`}
                value={assignment.role}
                title={descriptions[assignment.role]}
                onchange={(event) => changeRole(project.id, event.currentTarget.value as WebRole)}
              >
                {#each WEB_ROLES as role}<option value={role}>{WEB_ROLE_LABELS[role]}</option>{/each}
              </select>
            {/if}
          </div>
          {#if assignment}<p class="help role-help">{descriptions[assignment.role]}</p>{/if}
        {:else}<p class="empty">Aucun projet ne correspond à la recherche.</p>{/each}
      </div>
      <p class="access-summary">
        <span
          ><strong>{selected.length}</strong> projet{selected.length > 1 ? 's' : ''} sélectionné{selected.length > 1
            ? 's'
            : ''}</span
        ><span>{visible.length} sur {projects.length}</span>
      </p>
    {:else}<p class="empty">Aucun projet d’hébergement disponible. Créez d’abord un projet dans Hébergements.</p>{/if}
    <p class="help">
      Aucun projet coché : aucun accès aux hébergements existants. Les accès aux bases se règlent séparément.
    </p>
  {/if}
</fieldset>

<style>
  .role-help {
    padding: 0 0.65rem 0.5rem;
    margin: 0;
    border-bottom: 1px solid var(--app-border);
  }

  .base-access {
    --access-accent: var(--app-accent, #3b7cff);
    margin: 0;
    padding: 0.9rem;
    min-width: 0;
    border: 1px solid var(--app-border);
    border-radius: 0.4rem;
    background: var(--app-surface-soft);
  }
  legend {
    padding: 0 0.35rem;
    color: var(--app-text);
    font-size: 0.6875rem;
    font-weight: 550;
  }
  .help,
  .empty {
    margin: 0.35rem 0;
    color: var(--app-text-faint);
    font-size: 0.6875rem;
    line-height: 1.55;
  }
  .search {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin: 0.75rem 0 0.5rem;
    padding: 0.45rem 0.6rem;
    border: 1px solid var(--app-border-strong, var(--app-border));
    border-radius: 0.35rem;
    background: var(--app-surface-strong);
    color: var(--app-text-faint);
  }
  .search:focus-within {
    border-color: var(--access-accent);
  }
  .search > wa-icon {
    font-size: 0.75rem;
    flex-shrink: 0;
  }
  .search input {
    width: 100%;
    min-width: 0;
    padding: 0;
    border: 0;
    color: var(--app-text-strong);
    background: transparent;
    font: inherit;
    font-size: 0.6875rem;
    outline: none;
  }
  .search input::placeholder {
    color: var(--app-text-faint);
    opacity: 1;
  }
  .base-list {
    max-height: 13rem;
    overflow: auto;
    display: grid;
    border: 1px solid var(--app-border);
    border-radius: 0.35rem;
    background: var(--app-surface);
    scrollbar-width: thin;
    scrollbar-color: var(--app-border-strong) transparent;
  }
  .base-option {
    display: flex;
    align-items: center;
    gap: 0.65rem;
    min-height: 2.35rem;
    padding: 0.5rem 0.65rem;
    border-bottom: 1px solid var(--app-border);
    font-size: 0.75rem;
    color: var(--app-text);
    cursor: pointer;
    transition: background-color 100ms ease;
  }
  .base-option:last-child {
    border-bottom: 0;
  }
  .base-option:hover {
    background: var(--app-surface-hover);
  }
  .base-option:has(input:checked) {
    background: color-mix(in srgb, var(--access-accent) 6%, transparent);
  }
  .base-option:has(input:disabled) {
    cursor: default;
  }
  .base-option input {
    appearance: auto;
    padding: 0;
    margin: 0;
    width: 1rem;
    height: 1rem;
    flex: 0 0 1rem;
    accent-color: var(--access-accent);
    cursor: inherit;
  }
  .base-name {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .base-name wa-icon {
    flex-shrink: 0;
    color: var(--app-text-faint);
    font-size: 0.6875rem;
  }
  .base-option select {
    margin-left: auto;
    width: auto;
    max-width: 50%;
    padding: 0.3rem 0.4rem;
    color: var(--app-text);
    background: var(--app-surface);
    border: 1px solid var(--app-border);
    border-radius: 0.3rem;
    font-size: 0.7rem;
  }
  .base-list > .empty {
    padding: 1rem 0.75rem;
    text-align: center;
  }
  .access-global {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin: 0.25rem 0 0.5rem;
    color: var(--app-text);
    font-size: 0.75rem;
    font-weight: 500;
  }
  .access-global wa-icon {
    color: var(--access-accent);
  }
  .access-summary {
    display: flex;
    justify-content: space-between;
    gap: 0.5rem;
    margin-top: 0.55rem;
    color: var(--app-text-faint);
    font-size: 0.625rem;
    line-height: 1.5;
  }
  .access-summary strong {
    color: var(--app-text-muted);
    font-weight: 550;
  }
  input[type='checkbox']:focus-visible {
    outline: 2px solid var(--access-accent);
    outline-offset: 2px;
  }
  .base-access:disabled {
    opacity: 0.6;
  }
  @media (prefers-reduced-motion: reduce) {
    .base-option {
      transition: none;
    }
  }
</style>
