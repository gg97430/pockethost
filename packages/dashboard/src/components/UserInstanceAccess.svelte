<script lang="ts">
  import type { OperatorInstance } from '$src/pocketbase-client'

  export let instances: OperatorInstance[] = []
  export let selected: string[] = []
  export let userId = ''
  export let superAdmin = false
  export let disabled = false
  let search = ''

  $: visible = instances.filter((instance) => instance.subdomain.toLowerCase().includes(search.toLowerCase()))
  $: accessibleCount = instances.filter((instance) => instance.uid === userId || selected.includes(instance.id)).length

  function toggle(id: string, checked: boolean) {
    selected = checked ? [...new Set([...selected, id])] : selected.filter((value) => value !== id)
  }
</script>

<fieldset class="base-access" {disabled}>
  <legend>Bases rattachées</legend>
  {#if superAdmin}
    <p class="access-global"><wa-icon name="shield-halved"></wa-icon> Toutes les bases · accès superuser</p>
    <p class="help">
      Un superuser administre toute la plateforme. Les rattachements restent conservés si son rôle change.
    </p>
  {:else}
    <p class="help">Accès à la gestion et à la console des bases choisies. Leur propriétaire reste inchangé.</p>
    {#if instances.length}
      <label class="search">
        <wa-icon name="magnifying-glass" aria-hidden="true"></wa-icon>
        <span class="sr-only">Rechercher une base</span>
        <input type="search" placeholder="Nom de la base…" bind:value={search} />
      </label>
      <div class="base-list">
        {#each visible as instance (instance.id)}
          {@const owned = !!userId && instance.uid === userId}
          <label class="base-option">
            <input
              type="checkbox"
              checked={owned || selected.includes(instance.id)}
              disabled={disabled || owned}
              onchange={(event) => toggle(instance.id, event.currentTarget.checked)}
            />
            <span class="base-name"><wa-icon name="database" aria-hidden="true"></wa-icon>{instance.subdomain}</span>
            {#if owned}<small>Propriétaire</small>{/if}
          </label>
        {:else}
          <p class="empty">Aucune base ne correspond à la recherche.</p>
        {/each}
      </div>
      <p class="access-summary">
        <span
          ><strong>{accessibleCount}</strong> base{accessibleCount > 1 ? 's' : ''} accessible{accessibleCount > 1
            ? 's'
            : ''}</span
        ><span>{visible.length} sur {instances.length}</span>
      </p>
    {:else}
      <p class="empty">Aucune base disponible. Vous pourrez en rattacher une après sa création.</p>
    {/if}
  {/if}
</fieldset>

<style>
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
  .base-option small {
    flex-shrink: 0;
    margin-left: auto;
    padding: 0.12rem 0.35rem;
    border: 1px solid var(--app-border);
    border-radius: 0.25rem;
    color: var(--app-text-muted);
    background: var(--app-surface-soft);
    font-size: 0.6rem;
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
