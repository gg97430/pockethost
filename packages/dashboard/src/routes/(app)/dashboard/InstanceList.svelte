<script lang="ts">
  import InstanceSortButton from '$components/InstanceSortButton.svelte'
  import { compareInstanceList } from '$util/instanceListOrder'
  import { globalInstancesStore, globalInstancesStoreReady } from '$util/stores'
  import {
    DEFAULT_INSTANCE_LIST_PREFS,
    INSTANCE_SORT_OPTIONS,
    type InstanceListSortBy,
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

  let sortBy = initialPrefs.sortBy
  let groupFilter = initialPrefs.groupFilter
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
      sortBy = stored.sortBy
      groupFilter = stored.groupFilter
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
    if (sortBy !== DEFAULT_INSTANCE_LIST_PREFS.sortBy) params.set('by', sortBy)
    if (groupFilter !== 'all') params.set('group', groupFilter)
    if (sortDirection !== DEFAULT_INSTANCE_LIST_PREFS.sortDirection) params.set('sort', sortDirection)
    if (searchQuery) params.set('q', searchQuery)
    if (viewMode !== DEFAULT_INSTANCE_LIST_PREFS.viewMode) params.set('view', viewMode)
    const qs = params.toString()
    goto(qs ? `?${qs}` : '/dashboard', { replaceState: true, keepFocus: true, noScroll: true })
  }

  $: if (syncReady) {
    saveInstanceListPrefs({ searchQuery, sortDirection, viewMode, sortBy, groupFilter })
    updateUrl()
  }

  const toggleSortDirection = () => {
    sortDirection = sortDirection === 'asc' ? 'desc' : 'asc'
  }

  const selectSort = (column: InstanceListSortBy) => {
    if (sortBy === column) toggleSortDirection()
    else {
      sortBy = column
      sortDirection = ['cpu', 'memory', 'disk', 'power'].includes(column) ? 'desc' : 'asc'
    }
  }
  const ariaSort = (column: InstanceListSortBy) =>
    sortBy === column ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'
  $: favoriteSet = new Set(favoriteIds)
  $: projectLabels = [
    ...new Set(
      Object.values($globalInstancesStore)
        .map((row) => row.projectLabel || '')
        .filter(Boolean)
    ),
  ].sort((a, b) => a.localeCompare(b, 'fr'))
  $: selectedMissingLabel =
    groupFilter.startsWith('label:') && !projectLabels.includes(groupFilter.slice(6)) ? groupFilter.slice(6) : ''
  $: filteredInstances = Object.values($globalInstancesStore)
    .filter((instance) => {
      const target = [instance.cname, instance.subdomain, instance.projectLabel].filter(Boolean).join(' ').toLowerCase()
      const groupMatches =
        groupFilter === 'all' ||
        (groupFilter === 'ungrouped' ? !instance.projectLabel : instance.projectLabel === groupFilter.slice(6))
      return groupMatches && target.includes(searchQuery.toLowerCase())
    })
    .sort((a, b) => {
      if (sortBy === 'favorites' && favoriteSet.has(a.id) !== favoriteSet.has(b.id))
        return favoriteSet.has(a.id) ? -1 : 1
      return compareInstanceList(a, b, sortBy, sortDirection, instanceMetrics)
    })
</script>

<div class="database-list-controls">
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

  <label class="instance-filter">
    <span>Projet / label</span>
    <select bind:value={groupFilter}>
      <option value="all">Tous les labels</option>
      <option value="ungrouped">Sans label</option>
      {#if selectedMissingLabel}<option value={groupFilter}>{selectedMissingLabel} (aucune base)</option>{/if}
      {#each projectLabels as label}<option value={`label:${label}`}>{label}</option>{/each}
    </select>
  </label>
  <div class="database-list-actions">
    <label class="instance-filter">
      <span>Trier par</span>
      <select bind:value={sortBy}>
        {#each INSTANCE_SORT_OPTIONS as option}<option value={option.value}>{option.label}</option>{/each}
      </select>
    </label>
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
      <colgroup>
        <col class="instance-col-name" />
        <col class="instance-col-status" />
        <col class="instance-col-resources" />
        <col class="instance-col-version" />
        <col class="instance-col-actions" />
        <col class="instance-col-power" />
      </colgroup>
      <thead>
        <tr>
          <th aria-sort={ariaSort('name')}
            ><InstanceSortButton
              column="name"
              label="Nom"
              active={sortBy}
              direction={sortDirection}
              onSelect={selectSort}
            /></th
          >
          <th aria-sort={ariaSort('status')}
            ><InstanceSortButton
              column="status"
              label="État"
              active={sortBy}
              direction={sortDirection}
              onSelect={selectSort}
            /></th
          >
          <th aria-sort={['cpu', 'memory', 'disk'].includes(sortBy) ? 'other' : 'none'}>
            <div class="resource-sort-headings">
              <InstanceSortButton
                column="cpu"
                label="CPU"
                active={sortBy}
                direction={sortDirection}
                onSelect={selectSort}
              />
              <InstanceSortButton
                column="memory"
                label="RAM"
                active={sortBy}
                direction={sortDirection}
                onSelect={selectSort}
              />
              <InstanceSortButton
                column="disk"
                label="Disque"
                active={sortBy}
                direction={sortDirection}
                onSelect={selectSort}
              />
            </div>
          </th>
          <th aria-sort={ariaSort('version')}
            ><InstanceSortButton
              column="version"
              label="Version"
              active={sortBy}
              direction={sortDirection}
              onSelect={selectSort}
            /></th
          >
          <th>Actions</th>
          <th aria-sort={ariaSort('power')}
            ><InstanceSortButton
              column="power"
              label="Alimentation"
              active={sortBy}
              direction={sortDirection}
              onSelect={selectSort}
            /></th
          >
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
  .instance-filter {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    color: var(--app-text-muted);
    font-size: 0.7rem;
  }
  .instance-filter select {
    width: 100%;
    min-width: 0;
    padding: 0.45rem 0.6rem;
    border: 1px solid var(--app-border);
    border-radius: 0.375rem;
    background: var(--app-surface);
    color: var(--app-text-strong);
    font-size: 0.8rem;
  }
  .instance-filter select:focus-visible {
    outline: 2px solid var(--app-accent);
    outline-offset: 2px;
  }
  .resource-sort-headings {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.7rem;
  }

  .database-list-controls {
    display: grid;
    grid-template-columns: minmax(12rem, 1fr) minmax(10rem, 14rem) auto;
    align-items: end;
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
    box-shadow: none;
    transition:
      border-color 120ms ease,
      box-shadow 120ms ease;
  }

  .instance-list-search:focus-within {
    border-color: var(--app-accent, #3b7cff);
    box-shadow: 0 0 0 2px color-mix(in srgb, var(--app-accent, #3b7cff) 20%, transparent);
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

  .database-list-actions {
    display: flex;
    flex-wrap: nowrap;
    align-items: end;
    gap: 0.5rem;
    margin-left: auto;
  }

  .instance-list-view-toggle {
    flex-shrink: 0;
    display: inline-flex;
    border: 1px solid var(--app-border);
    border-radius: 0.375rem;
    background: var(--app-surface);
    box-shadow: none;
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
    background: color-mix(in srgb, var(--app-accent, #3b7cff) 12%, transparent);
    color: var(--app-accent, #3b7cff);
  }

  .instance-list-empty {
    padding: 3rem 1rem;
    text-align: center;
    color: var(--app-text-muted);
    font-size: 0.9375rem;
  }

  .instance-table-wrap {
    overflow: hidden;
    border: 1px solid var(--app-border);
    border-radius: 0.375rem;
    background: var(--app-surface);
    box-shadow: none;
  }

  .instance-table {
    width: 100%;
    table-layout: fixed;
    border-collapse: collapse;
    font-size: 0.875rem;
  }

  .instance-table :global(.instance-col-name) {
    width: 22%;
  }

  .instance-table :global(.instance-col-status) {
    width: 8.5rem;
  }

  .instance-table :global(.instance-col-resources) {
    width: 28%;
  }

  .instance-table :global(.instance-col-version) {
    width: 6.5rem;
  }

  .instance-table :global(.instance-col-actions) {
    width: 18.5rem;
  }

  .instance-table :global(.instance-col-power) {
    width: 11rem;
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
    min-width: 0;
  }

  .instance-table :global(tr:last-child td) {
    border-bottom: none;
  }

  .instance-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(min(100%, 21rem), 1fr));
    gap: 0.75rem;
  }

  @media (max-width: 1180px) {
    .instance-table-wrap {
      overflow: visible;
      border: 0;
      background: transparent;
      box-shadow: none;
    }

    .instance-table colgroup,
    .instance-table,
    .instance-table tbody,
    .instance-table :global(tr),
    .instance-table :global(td) {
      display: block;
      width: 100%;
    }

    .instance-table thead {
      display: none;
    }

    .instance-table :global(tr) {
      margin-bottom: 0.75rem;
      border: 1px solid var(--app-border);
      border-radius: 0.375rem;
      background: var(--app-surface);
      box-shadow: none;
      overflow: hidden;
    }

    .instance-table :global(td) {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      padding: 0.75rem 1rem;
      border-bottom: 1px solid var(--app-border);
    }

    .instance-table :global(td::before) {
      flex: 0 0 7.5rem;
      color: var(--app-text-faint);
      content: attr(data-label);
      font-size: 0.68rem;
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }

    .instance-table :global(td:first-child) {
      align-items: flex-start;
      background: var(--app-surface-soft);
    }

    .instance-table :global(td:last-child) {
      border-bottom: 0;
    }
  }

  @media (max-width: 1000px) {
    .database-list-controls {
      grid-template-columns: minmax(0, 1fr) auto;
    }
    .instance-list-search {
      grid-column: 1 / -1;
    }
  }
  @media (max-width: 640px) {
    .database-list-controls {
      grid-template-columns: minmax(0, 1fr);
    }
    .database-list-actions {
      width: 100%;
      margin-left: 0;
      justify-content: flex-start;
    }
    .database-list-actions .instance-filter {
      flex: 1;
    }

    .instance-table :global(td) {
      display: grid;
      grid-template-columns: 1fr;
      gap: 0.45rem;
      justify-items: stretch;
    }

    .instance-table :global(td::before) {
      flex-basis: auto;
    }
  }
</style>
