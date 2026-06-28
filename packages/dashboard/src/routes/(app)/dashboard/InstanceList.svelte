<script lang="ts">
  import { globalInstancesStore, globalInstancesStoreReady } from '$util/stores'
  import {
    DEFAULT_INSTANCE_LIST_PREFS,
    instanceListPrefsHasUrlParams,
    loadInstanceFavoritesFromStorage,
    loadInstanceListPrefsFromStorage,
    loadInstanceListPrefsFromUrl,
    saveInstanceFavorites,
    saveInstanceListPrefs,
    toggleInstanceFavorite,
    type InstanceListSortDirection,
    type InstanceListViewMode,
  } from '$util/instanceListPrefs'
  import { type InstanceFields } from 'pockethost/common'
  import { client, type DashboardInstanceMetric } from '$src/pocketbase-client'
  import InstanceCard from './InstanceCard.svelte'
  import InstanceTableRow from './InstanceTableRow.svelte'
  import { page } from '$app/state'
  import { browser } from '$app/environment'
  import { onDestroy, onMount } from 'svelte'
  import { goto } from '$app/navigation'

  const initialPrefs = instanceListPrefsHasUrlParams(page.url.searchParams)
    ? loadInstanceListPrefsFromUrl(page.url.searchParams)
    : DEFAULT_INSTANCE_LIST_PREFS

  let searchQuery = initialPrefs.searchQuery
  let sortDirection: InstanceListSortDirection = initialPrefs.sortDirection
  let viewMode: InstanceListViewMode = initialPrefs.viewMode
  let favoriteIds: string[] = browser ? loadInstanceFavoritesFromStorage() : []
  let syncReady = instanceListPrefsHasUrlParams(page.url.searchParams)
  let instanceMetrics: Record<string, DashboardInstanceMetric> = {}
  let metricsTimer: ReturnType<typeof setInterval> | undefined

  const refreshInstanceMetrics = async () => {
    try {
      const result = await client().getDashboardInstanceMetrics()
      instanceMetrics = result.instances
    } catch {
      instanceMetrics = {}
    }
  }

  onMount(() => {
    if (!syncReady) {
      const stored = loadInstanceListPrefsFromStorage()
      searchQuery = stored.searchQuery
      sortDirection = stored.sortDirection
      viewMode = stored.viewMode
      syncReady = true
    }

    void refreshInstanceMetrics()
    metricsTimer = setInterval(refreshInstanceMetrics, 30_000)
  })

  onDestroy(() => {
    if (metricsTimer) clearInterval(metricsTimer)
  })

  $: validInstanceIds = new Set(Object.keys($globalInstancesStore))

  $: if ($globalInstancesStoreReady && favoriteIds.some((id) => !validInstanceIds.has(id))) {
    favoriteIds = favoriteIds.filter((id) => validInstanceIds.has(id))
    saveInstanceFavorites(favoriteIds)
  }

  const handleToggleFavorite = (id: string) => {
    favoriteIds = toggleInstanceFavorite(favoriteIds, id)
    saveInstanceFavorites(favoriteIds)
  }

  const updateUrl = () => {
    const params = new URLSearchParams()
    if (sortDirection !== DEFAULT_INSTANCE_LIST_PREFS.sortDirection) params.set('sort', sortDirection)
    if (searchQuery) params.set('q', searchQuery)
    if (viewMode !== DEFAULT_INSTANCE_LIST_PREFS.viewMode) params.set('view', viewMode)
    const qs = params.toString()
    goto(qs ? `?${qs}` : '/dashboard', { replaceState: true, keepFocus: true, noScroll: true })
  }

  $: if (syncReady) {
    saveInstanceListPrefs({ searchQuery, sortDirection, viewMode })
    updateUrl()
  }

  const toggleSortDirection = () => {
    sortDirection = sortDirection === 'asc' ? 'desc' : 'asc'
  }

  const compareInstanceNames = (a: InstanceFields, b: InstanceFields, multiplier: number) =>
    (a.cname || a.subdomain).localeCompare(b.cname || b.subdomain, undefined, { sensitivity: 'base' }) * multiplier

  $: sortMultiplier = sortDirection === 'asc' ? 1 : -1
  $: favoriteSet = new Set(favoriteIds)

  $: filteredInstances = Object.values($globalInstancesStore)
    .filter((instance) => {
      const target = (instance.cname || instance.subdomain).toLowerCase()
      return target.includes(searchQuery.toLowerCase())
    })
    .sort((a, b) => {
      const aFav = favoriteSet.has(a.id)
      const bFav = favoriteSet.has(b.id)
      if (aFav !== bFav) return aFav ? -1 : 1
      return compareInstanceNames(a, b, sortMultiplier)
    })
</script>

<div class="instance-list-toolbar">
  <div class="instance-list-search">
    <wa-icon name="magnifying-glass" class="instance-list-search-icon"></wa-icon>
    <input
      type="search"
      placeholder="Rechercher des instances..."
      bind:value={searchQuery}
      class="instance-list-search-input"
      aria-label="Rechercher des instances"
    />
  </div>

  <div class="instance-list-toolbar-actions">
    <wa-button
      variant="neutral"
      size="s"
      appearance="outline"
      onclick={toggleSortDirection}
      aria-label="Changer le sens du tri"
    >
      <wa-icon name={sortDirection === 'desc' ? 'arrow-down-z-a' : 'arrow-down-a-z'}></wa-icon>
    </wa-button>

    <div class="instance-list-view-toggle" role="group" aria-label="Mode d'affichage">
      <button
        type="button"
        class="instance-list-view-btn"
        class:instance-list-view-btn--active={viewMode === 'list'}
        aria-pressed={viewMode === 'list'}
        aria-label="Affichage en liste"
        onclick={() => (viewMode = 'list')}
      >
        <wa-icon name="list"></wa-icon>
      </button>
      <button
        type="button"
        class="instance-list-view-btn"
        class:instance-list-view-btn--active={viewMode === 'grid'}
        aria-pressed={viewMode === 'grid'}
        aria-label="Affichage en grille"
        onclick={() => (viewMode = 'grid')}
      >
        <wa-icon name="table-cells"></wa-icon>
      </button>
    </div>
  </div>
</div>

{#if filteredInstances.length === 0}
  <div class="instance-list-empty">
    {#if Object.keys($globalInstancesStore).length === 0}
      <p>Aucune instance pour le moment. Créez-en une pour commencer.</p>
    {:else}
      <p>Aucune instance ne correspond à votre recherche.</p>
    {/if}
  </div>
{:else if viewMode === 'list'}
  <div class="instance-table-wrap">
    <table class="instance-table">
      <thead>
        <tr>
          <th>Nom</th>
          <th>État</th>
          <th>Ressources</th>
          <th>Version</th>
          <th>Actions</th>
          <th>Alimentation</th>
        </tr>
      </thead>
      <tbody>
        {#each filteredInstances as instance (instance.id)}
          <InstanceTableRow
            {instance}
            metrics={instanceMetrics[instance.id]}
            isFavorite={favoriteIds.includes(instance.id)}
            onToggleFavorite={() => handleToggleFavorite(instance.id)}
          />
        {/each}
      </tbody>
    </table>
  </div>
{:else}
  <div class="instance-grid">
    {#each filteredInstances as instance (instance.id)}
      <InstanceCard
        {instance}
        metrics={instanceMetrics[instance.id]}
        isFavorite={favoriteIds.includes(instance.id)}
        onToggleFavorite={() => handleToggleFavorite(instance.id)}
      />
    {/each}
  </div>
{/if}

<style>
  .instance-list-toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.75rem;
    margin-top: 2rem;
    margin-bottom: 1.25rem;
  }

  .instance-list-search {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex: 1 1 14rem;
    min-width: 0;
    padding: 0.5rem 0.75rem;
    border-radius: 0.5rem;
    border: 1px solid var(--app-border);
    background: var(--app-surface);
    box-shadow: var(--app-shadow-sm);
    transition:
      border-color 120ms ease,
      box-shadow 120ms ease;
  }

  .instance-list-search:focus-within {
    border-color: #1eb854;
    box-shadow: 0 0 0 3px rgb(30 184 84 / 0.14);
  }

  .instance-list-search-icon {
    color: var(--app-text-faint);
    flex-shrink: 0;
  }

  .instance-list-search-input {
    width: 100%;
    border: none;
    background: transparent;
    color: var(--app-text-strong);
    font-size: 0.875rem;
    outline: none;
  }

  .instance-list-search-input::placeholder {
    color: var(--app-text-faint);
  }

  .instance-list-toolbar-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
    margin-left: auto;
  }

  .instance-list-view-toggle {
    display: inline-flex;
    border: 1px solid var(--app-border);
    border-radius: 0.375rem;
    background: var(--app-surface);
    box-shadow: var(--app-shadow-sm);
    overflow: hidden;
  }

  .instance-list-view-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 2rem;
    height: 2rem;
    border: none;
    background: transparent;
    color: var(--app-text-muted);
    cursor: pointer;
    transition:
      background-color 120ms ease,
      color 120ms ease;
  }

  .instance-list-view-btn:hover {
    background: var(--app-surface-hover);
    color: var(--app-text-strong);
  }

  .instance-list-view-btn + .instance-list-view-btn {
    border-left: 1px solid var(--app-border);
  }

  .instance-list-view-btn--active {
    background: rgb(30 184 84 / 0.12);
    color: #15803d;
  }

  .instance-list-empty {
    padding: 3rem 1rem;
    text-align: center;
    color: var(--app-text-muted);
    font-size: 0.9375rem;
  }

  .instance-table-wrap {
    overflow-x: auto;
    border: 1px solid var(--app-border);
    border-radius: 0.75rem;
    background: var(--app-surface);
    box-shadow: var(--app-shadow);
  }

  .instance-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.875rem;
  }

  .instance-table th {
    padding: 0.625rem 1rem;
    text-align: left;
    font-size: 0.6875rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--app-text-muted);
    border-bottom: 1px solid var(--app-border);
    background: var(--app-surface-soft);
    white-space: nowrap;
  }

  .instance-table th:last-child {
    text-align: right;
  }

  .instance-table :global(td) {
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--app-border);
    vertical-align: middle;
  }

  .instance-table :global(tr:last-child td) {
    border-bottom: none;
  }

  .instance-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(min(100%, 18rem), 1fr));
    gap: 0.75rem;
  }
</style>
