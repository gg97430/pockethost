<script lang="ts">
  import { theme, toggleTheme } from '$lib/theme'

  export let showLabel = false
  export let className = ''

  $: isLight = $theme === 'light'
  $: label = isLight ? 'Activer le mode sombre' : 'Activer le mode clair'
</script>

<button
  type="button"
  class="theme-toggle {className}"
  class:theme-toggle--label={showLabel}
  aria-label={label}
  title={label}
  onclick={toggleTheme}
>
  <wa-icon name={isLight ? 'moon' : 'sun'}></wa-icon>
  <span class:sr-only={!showLabel}>{isLight ? 'Mode sombre' : 'Mode clair'}</span>
</button>

<style>
  .theme-toggle {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    width: 2.25rem;
    height: 2.25rem;
    border: 1px solid var(--app-border);
    border-radius: 0.5rem;
    background: var(--app-surface-soft);
    color: var(--app-text-muted);
    box-shadow: var(--app-shadow-sm);
    cursor: pointer;
    transition:
      background-color 150ms ease,
      border-color 150ms ease,
      color 150ms ease,
      transform 150ms ease;
  }

  .theme-toggle:hover {
    border-color: var(--app-border-strong);
    background: var(--app-surface-hover);
    color: var(--app-text);
  }

  .theme-toggle:active {
    transform: translateY(1px);
  }

  .theme-toggle:focus-visible {
    outline: 2px solid rgb(30 184 84 / 0.72);
    outline-offset: 2px;
  }

  .theme-toggle--label {
    width: 100%;
    justify-content: flex-start;
    padding-inline: 0.625rem;
    font-size: 0.875rem;
    font-weight: 500;
  }
</style>
