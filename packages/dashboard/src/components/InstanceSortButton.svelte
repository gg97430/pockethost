<script lang="ts">
  import type { InstanceListSortBy, InstanceListSortDirection } from '$util/instanceListPrefs'
  export let column: InstanceListSortBy
  export let label: string
  export let active: InstanceListSortBy
  export let direction: InstanceListSortDirection
  export let onSelect: (column: InstanceListSortBy) => void
</script>

<button
  type="button"
  class:active={active === column}
  onclick={() => onSelect(column)}
  aria-label={`Trier par ${label}`}
  aria-pressed={active === column}
>
  {label}<span aria-hidden="true">{active === column ? (direction === 'asc' ? '↑' : '↓') : '↕'}</span>
</button>

<style>
  button {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    border: 0;
    padding: 0.3rem 0;
    background: transparent;
    color: inherit;
    font: inherit;
    text-transform: inherit;
    letter-spacing: inherit;
    cursor: pointer;
  }
  span {
    opacity: 0.5;
  }
  button:hover,
  .active {
    color: var(--app-text-strong);
  }
  .active span {
    opacity: 1;
    color: var(--app-accent);
  }
  button:focus-visible {
    outline: 2px solid var(--app-accent);
    outline-offset: 3px;
  }
</style>
