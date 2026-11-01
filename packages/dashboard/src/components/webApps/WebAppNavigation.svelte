<script lang="ts">
  import { tick } from 'svelte'
  export let items: { id: string; label: string }[] = []
  export let selected = ''
  export let onselect: (id: string) => void
  let expanded = false
  let toggle: HTMLButtonElement
  const groups = [
    { title: 'Publication', ids: ['deployments', 'environments', 'domains', 'ci', 'webhooks'] },
    { title: 'Exploitation', ids: ['runtime-logs', 'health', 'notifications', 'tasks', 'backups', 'terminal'] },
    { title: 'Configuration', ids: ['runtime-profile', 'storage', 'environment', 'members', 'settings', 'delete'] },
  ]
  const choose = async (id: string) => {
    onselect(id)
    if (expanded) {
      expanded = false
      await tick()
      toggle?.focus()
    }
  }
  const keys = (event: KeyboardEvent, id: string) => {
    const ordered = groups.flatMap((g) => g.ids).filter((value) => items.some((item) => item.id === value))
    const index = ordered.indexOf(id)
    const next =
      event.key === 'ArrowDown'
        ? (index + 1) % ordered.length
        : event.key === 'ArrowUp'
          ? (index + ordered.length - 1) % ordered.length
          : event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? ordered.length - 1
              : -1
    if (event.key === 'Escape' && expanded) {
      expanded = false
      toggle?.focus()
      return
    }
    if (next < 0) return
    event.preventDefault()
    onselect(ordered[next]!)
    document.getElementById(`web-tab-${ordered[next]}`)?.focus()
  }
</script>

<nav class="application-navigation" aria-label="Gestion de l’application">
  <button
    class="application-navigation-toggle web-button"
    bind:this={toggle}
    type="button"
    aria-expanded={expanded}
    aria-controls="application-sections"
    onclick={() => (expanded = !expanded)}
  >
    <span><span class="navigation-caption">Section</span>{items.find((item) => item.id === selected)?.label}</span>
    <span aria-hidden="true">{expanded ? '−' : '+'}</span>
  </button>
  <div
    id="application-sections"
    class:expanded
    role="tablist"
    aria-label="Sections de l’application"
    aria-orientation="vertical"
  >
    {#each groups as group}
      {#if items.some((item) => group.ids.includes(item.id))}
        <span class="navigation-group">{group.title}</span>
        {#each group.ids as id}
          {@const item = items.find((item) => item.id === id)}
          {#if item}<button
              type="button"
              role="tab"
              id={`web-tab-${id}`}
              aria-selected={selected === id}
              aria-controls={`web-panel-${id}`}
              tabindex={selected === id ? 0 : -1}
              class:danger={id === 'delete'}
              onclick={() => choose(id)}
              onkeydown={(event) => keys(event, id)}
            >
              {item.label}
            </button>{/if}
        {/each}
      {/if}
    {/each}
  </div>
</nav>

<style>
  .application-navigation {
    position: sticky;
    top: 1.25rem;
    min-width: 0;
  }
  [role='tablist'] {
    display: grid;
    gap: 3px;
    padding: 0 1rem 0 0;
    border-right: 1px solid var(--web-border);
  }
  .navigation-group {
    margin: 1.25rem 0 0.4rem;
    padding: 0 0.75rem;
    font-size: 10px;
    font-weight: 650;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--web-muted);
  }
  .navigation-group:first-child {
    margin-top: 0.25rem;
  }
  [role='tab'] {
    position: relative;
    width: 100%;
    min-height: 36px;
    padding: 0.55rem 0.75rem;
    border: 0;
    border-radius: 5px;
    background: transparent;
    text-align: left;
    font: inherit;
    font-size: 12px;
    color: var(--web-muted);
    cursor: pointer;
  }
  [role='tab']:hover {
    background: var(--app-surface-hover, #232327);
    color: var(--app-text, #ededef);
  }
  [role='tab'][aria-selected='true'] {
    color: var(--web-accent);
    background: color-mix(in srgb, var(--web-accent) 10%, transparent);
    font-weight: 600;
  }
  [role='tab'][aria-selected='true']::before {
    content: '';
    position: absolute;
    left: 0;
    top: 8px;
    bottom: 8px;
    width: 2px;
    border-radius: 2px;
    background: currentColor;
  }
  [role='tab'].danger {
    color: var(--app-danger, #f87171);
  }
  [role='tab'].danger[aria-selected='true'] {
    background: color-mix(in srgb, var(--app-danger, #f87171) 10%, transparent);
  }
  [role='tab']:focus-visible {
    outline: 2px solid var(--web-accent);
    outline-offset: 1px;
  }
  .application-navigation-toggle {
    display: none;
  }
  @media (max-width: 767px) {
    .application-navigation {
      position: static;
    }
    .application-navigation-toggle {
      display: flex;
      justify-content: space-between;
      width: 100%;
      text-align: left;
    }
    .application-navigation-toggle > span:first-child {
      display: grid;
      gap: 2px;
    }
    .navigation-caption {
      font-size: 10px;
      color: var(--web-muted);
    }
    [role='tablist'] {
      display: none;
      padding: 0.75rem;
      margin-top: 0.5rem;
      border: 1px solid var(--web-border);
      border-radius: var(--web-radius);
      background: var(--web-surface);
    }
    [role='tablist'].expanded {
      display: grid;
    }
    [role='tab'] {
      min-height: 42px;
    }
  }
</style>
