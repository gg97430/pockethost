<script lang="ts">
  export let isFavorite = false
  export let onToggle: () => void

  const handleClick = (e: MouseEvent) => {
    e.stopPropagation()
    onToggle()
  }

  const handleKeydown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.stopPropagation()
      e.preventDefault()
      onToggle()
    }
  }
</script>

<button
  type="button"
  class="instance-favorite-btn"
  class:instance-favorite-btn--active={isFavorite}
  aria-label={isFavorite ? 'Retirer des favoris' : 'Ajouter aux favoris'}
  aria-pressed={isFavorite}
  onclick={handleClick}
  onkeydown={handleKeydown}
>
  <wa-icon name="star" class="instance-favorite-icon"></wa-icon>
</button>

<style>
  .instance-favorite-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    width: 1.75rem;
    height: 1.75rem;
    padding: 0;
    border: none;
    border-radius: 0.375rem;
    background: transparent;
    color: var(--app-text-faint);
    cursor: pointer;
    transition:
      color 120ms ease,
      background-color 120ms ease;
  }

  .instance-favorite-btn:hover {
    color: var(--app-text);
    background: var(--app-surface-hover);
  }

  .instance-favorite-btn--active {
    color: #facc15;
  }

  .instance-favorite-btn--active:hover {
    color: #fde047;
  }

  .instance-favorite-btn:focus-visible {
    outline: none;
    box-shadow: 0 0 0 2px rgb(30 184 84 / 0.3);
  }

  .instance-favorite-icon {
    font-size: 0.875rem;
  }

  .instance-favorite-btn--active .instance-favorite-icon {
    font-weight: 900;
  }
</style>
