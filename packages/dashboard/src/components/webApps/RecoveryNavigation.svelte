<script lang="ts">
  export let active: 'server' | 'projects' | 'migrations'
  const sections = [
    { id: 'server', label: 'Serveur', icon: 'server', href: '/applications/recovery' },
    { id: 'projects', label: 'Reprise des projets', icon: 'boxes-stacked', href: '/applications/recovery/projects' },
    {
      id: 'migrations',
      label: 'Migration de projet',
      icon: 'arrow-right-arrow-left',
      href: '/applications/recovery/migrations',
    },
  ]
</script>

<nav class="recovery-navigation" aria-label="Reprise et migration">
  {#each sections as section}
    <a
      href={section.href}
      class:active={active === section.id}
      aria-current={active === section.id ? 'page' : undefined}
    >
      <wa-icon name={section.icon}></wa-icon>{section.label}
    </a>
  {/each}
</nav>

<style>
  .recovery-navigation {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    padding-bottom: 0.8rem;
    border-bottom: 1px solid var(--web-border);
    min-width: 0;
  }
  a {
    display: inline-flex;
    align-items: center;
    gap: 0.55rem;
    padding: 0.65rem 0.9rem;
    border: 1px solid transparent;
    border-radius: 0.5rem;
    color: var(--web-muted);
    font-size: 0.85rem;
    text-decoration: none;
  }
  a:hover {
    color: var(--web-text);
    background: var(--web-surface);
  }
  a.active {
    color: var(--web-text);
    background: var(--web-surface);
    border-color: var(--web-border);
  }
  a:focus-visible {
    outline: 2px solid var(--web-accent);
    outline-offset: 2px;
  }
</style>
