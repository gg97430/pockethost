<script lang="ts">
  import { onMount } from 'svelte'
  import { client } from '$src/pocketbase-client'
  import ResourceHistoryChart from './ResourceHistoryChart.svelte'
  import {
    HOST_SERIES,
    RESOURCE_CATEGORIES,
    RESOURCE_GIB,
    RESOURCE_RANGES,
    resourceFormatBytes,
    resourceFormatDate,
    resourceFormatPercent,
    resourceHistory,
    resourceHref,
    resourcePercent,
    resourceSortRows,
    resourceSortAria,
    resourceSortIndicator,
    type ResourceSortDirection,
    type PlatformResourceCategory,
    type PlatformResourceCategoryId,
    type PlatformResourceRange,
    type PlatformResourceResponse,
    type PlatformResourceRow,
  } from '$lib/platformResources'
  export let active = true
  export let refreshKey = 0
  export let busy = false
  let response: PlatformResourceResponse | null = null
  let range: PlatformResourceRange = '1h'
  let selected = ['host', ...RESOURCE_CATEGORIES.map((item) => item.id)]
  let category: PlatformResourceCategoryId | 'all' = 'all'
  let query = ''
  const categoryColumns = [
    { key: 'name', label: 'Catégorie', numeric: false },
    { key: 'resourceCount', label: 'Ressources', numeric: true },
    { key: 'cpuPercent', label: 'CPU', numeric: true },
    { key: 'memoryBytes', label: 'RAM', numeric: true },
    { key: 'memoryPercent', label: 'Part RAM', numeric: true },
    { key: 'diskBytes', label: 'Disque estimé', numeric: true },
  ] as const
  const resourceColumns = [
    { key: 'name', label: 'Ressource', numeric: false },
    { key: 'category', label: 'Catégorie', numeric: false },
    { key: 'state', label: 'État', numeric: false },
    { key: 'cpuPercent', label: 'CPU', numeric: true },
    { key: 'memoryBytes', label: 'RAM', numeric: true },
    { key: 'diskBytes', label: 'Disque estimé', numeric: true },
  ] as const
  const filesystemColumns = [
    { key: 'mount', label: 'Volume', numeric: false },
    { key: 'usedBytes', label: 'Occupé', numeric: true },
    { key: 'totalBytes', label: 'Capacité', numeric: true },
    { key: 'availableBytes', label: 'Disponible', numeric: true },
    { key: 'usedPercent', label: 'Utilisation', numeric: true },
  ] as const
  type CategorySortKey = (typeof categoryColumns)[number]['key']
  type ResourceSortKey = (typeof resourceColumns)[number]['key']
  type FilesystemSortKey = (typeof filesystemColumns)[number]['key']
  let categorySort: CategorySortKey = 'name'
  let categoryDirection: ResourceSortDirection = 'asc'
  let sort: ResourceSortKey = 'memoryBytes'
  let sortDirection: ResourceSortDirection = 'desc'
  let filesystemSort: FilesystemSortKey = 'mount'
  let filesystemDirection: ResourceSortDirection = 'asc'
  let error = ''
  let mounted = false
  let disposed = false
  let attempted = false
  let observedRefresh = refreshKey
  let lastRead = 0
  let now = Date.now()
  const states = { running: 'En service', stopped: 'Arrêté', unknown: 'Sans état' }
  const kinds = { application: 'Application', database: 'Base de données', builder: 'Builder', service: 'Service' }
  const categoryById = Object.fromEntries(RESOURCE_CATEGORIES.map((item) => [item.id, item]))
  $: sample = response?.sample
  $: start = now - RESOURCE_RANGES.find((item) => item.id === range)!.duration
  $: points = response?.range === range ? resourceHistory(response.history.points, start, now) : []
  $: series = [HOST_SERIES, ...RESOURCE_CATEGORIES.map((item) => ({ ...item, category: item.id }))].filter((item) =>
    selected.includes(item.id)
  )
  $: categories = resourceSortRows(
    RESOURCE_CATEGORIES.map((item) => ({ ...item, values: sample?.categories.find((value) => value.id === item.id) })),
    (row) =>
      categorySort === 'name'
        ? row.label
        : categorySort === 'memoryPercent'
          ? resourcePercent(row.values?.memoryBytes, sample?.host.memoryTotalBytes)
          : row.values?.[categorySort],
    categoryDirection
  )
  $: resources = resourceSortRows(
    (sample?.resources || []).filter(
      (row) =>
        (category === 'all' || row.category === category) &&
        `${row.name || ''} ${kinds[row.kind]} ${row.id}`.toLowerCase().includes(query.toLowerCase())
    ),
    (row) =>
      sort === 'name'
        ? rowName(row)
        : sort === 'category'
          ? categoryById[row.category]?.label
          : sort === 'state'
            ? states[row.state]
            : row[sort],
    sortDirection
  )
  $: filesystems = resourceSortRows(sample?.filesystems || [], (row) => row[filesystemSort], filesystemDirection)
  const sortCategory = (key: CategorySortKey) => {
    categoryDirection =
      categorySort === key
        ? categoryDirection === 'asc'
          ? 'desc'
          : 'asc'
        : categoryColumns.find((column) => column.key === key)?.numeric
          ? 'desc'
          : 'asc'
    categorySort = key
  }
  const sortResource = (key: ResourceSortKey) => {
    sortDirection =
      sort === key
        ? sortDirection === 'asc'
          ? 'desc'
          : 'asc'
        : resourceColumns.find((column) => column.key === key)?.numeric
          ? 'desc'
          : 'asc'
    sort = key
  }
  const sortFilesystem = (key: FilesystemSortKey) => {
    filesystemDirection =
      filesystemSort === key
        ? filesystemDirection === 'asc'
          ? 'desc'
          : 'asc'
        : filesystemColumns.find((column) => column.key === key)?.numeric
          ? 'desc'
          : 'asc'
    filesystemSort = key
  }
  $: if (mounted && active && !attempted) void load()
  $: if (mounted && active && refreshKey !== observedRefresh) {
    observedRefresh = refreshKey
    void load()
  }

  async function load() {
    if (!active || busy || disposed || !mounted || document.hidden) return
    busy = true
    attempted = true
    const requestedRange = range
    try {
      const value = await client().client.send<PlatformResourceResponse>('/api/admin/monitoring/resources', {
        method: 'GET',
        query: { range: requestedRange },
        requestKey: null,
      })
      if (disposed || requestedRange !== range) return
      response = value
      error = ''
      now = Date.now()
    } catch {
      if (!disposed && requestedRange === range)
        error = 'La lecture des ressources n’a pas abouti. La dernière mesure disponible est conservée.'
    } finally {
      busy = false
      lastRead = Date.now()
      if (!disposed && active && requestedRange !== range) void load()
    }
  }
  const changeRange = (value: PlatformResourceRange) => {
    if (range === value) return
    range = value
    now = Date.now()
    void load()
  }
  const toggle = (id: string) => {
    selected = selected.includes(id) ? selected.filter((item) => item !== id) : [...selected, id]
  }
  const uptime = (seconds: number | undefined) => {
    if (seconds === undefined) return '—'
    const days = Math.floor(seconds / 86400),
      hours = Math.floor((seconds % 86400) / 3600)
    return days ? `${days} j ${hours} h` : `${hours} h ${Math.floor((seconds % 3600) / 60)} min`
  }
  const rowName = (row: PlatformResourceRow) =>
    row.name || (row.kind === 'builder' ? 'Constructeur d’images' : `${kinds[row.kind]} · ${row.id}`)
  onMount(() => {
    mounted = true
    const timer = setInterval(() => {
      now = Date.now()
      if (active && !document.hidden) void load()
    }, 15000)
    const visible = () => {
      if (active && !document.hidden && Date.now() - lastRead >= 15000) void load()
    }
    document.addEventListener('visibilitychange', visible)
    return () => {
      disposed = true
      mounted = false
      clearInterval(timer)
      document.removeEventListener('visibilitychange', visible)
    }
  })
</script>

<div class="resources-overview" aria-busy={busy}>
  {#if error}<p class="resource-notice" role="status">{error}</p>{/if}
  <div class="resource-context">
    <div>
      <span class="resource-live-dot" class:pending={!response?.available || response?.stale}></span><strong
        >{sample?.host.hostname || 'Serveur actuel'}</strong
      ><span
        >{sample
          ? `${sample.host.cpuCount} cœurs · démarré depuis ${uptime(sample.host.uptimeSeconds)}`
          : 'Premier relevé attendu'}</span
      >
    </div>
    <span
      >{response?.stale ? 'Mesure périmée · ' : ''}{busy
        ? 'Actualisation…'
        : sample
          ? resourceFormatDate(sample.collectedAt)
          : 'Collecte en attente'}</span
    >
  </div>
  {#if response && (!response.available || !sample)}<p class="resource-notice">
      Les ressources apparaîtront après la première collecte. Aucune consommation n’est estimée à zéro en son absence.
    </p>{/if}
  <section class="resource-kpis" aria-label="Consommation du serveur entier">
    <article>
      <div class="kpi-top"><span>CPU du serveur</span><wa-icon name="microchip"></wa-icon></div>
      <strong>{resourceFormatPercent(sample?.host.cpuPercent)}</strong><small
        >{sample
          ? `${sample.host.cpuCount} cœurs = 100 % de capacité`
          : 'Tous les cœurs, sans mesure disponible'}</small
      >
    </article>
    <article>
      <div class="kpi-top"><span>RAM utilisée</span><wa-icon name="memory"></wa-icon></div>
      <strong
        >{resourceFormatBytes(sample?.host.memoryUsedBytes)}<span
          >{resourceFormatPercent(resourcePercent(sample?.host.memoryUsedBytes, sample?.host.memoryTotalBytes))}</span
        ></strong
      ><small
        >{sample
          ? `sur ${resourceFormatBytes(sample.host.memoryTotalBytes)} · ${resourceFormatBytes(sample.host.memoryAvailableBytes)} disponibles`
          : 'Capacité du serveur en attente'}</small
      >
    </article>
    <article>
      <div class="kpi-top"><span>Disque occupé</span><wa-icon name="hard-drive"></wa-icon></div>
      <strong
        >{resourceFormatBytes(sample?.host.diskUsedBytes)}<span
          >{resourceFormatPercent(resourcePercent(sample?.host.diskUsedBytes, sample?.host.diskTotalBytes))}</span
        ></strong
      ><small
        >{sample
          ? `sur ${resourceFormatBytes(sample.host.diskTotalBytes)} · ${resourceFormatBytes(sample.host.diskAvailableBytes)} disponibles`
          : 'Capacité du serveur en attente'}</small
      >
    </article>
  </section>

  <section class="resource-history" aria-labelledby="resource-history-title">
    <header class="resource-section-heading">
      <div>
        <h2 id="resource-history-title">Évolution des ressources</h2>
        <p>Le serveur entier et les catégories partagent la même échelle.</p>
      </div>
      <nav class="resource-ranges" aria-label="Période des graphiques">
        {#each RESOURCE_RANGES as item}<button
            class:active={range === item.id}
            aria-pressed={range === item.id}
            onclick={() => changeRange(item.id)}>{item.label}</button
          >{/each}
      </nav>
    </header>
    <div class="resource-legend" aria-label="Courbes affichées">
      {#each [HOST_SERIES, ...RESOURCE_CATEGORIES] as item}<button
          aria-pressed={selected.includes(item.id)}
          class:unselected={!selected.includes(item.id)}
          onclick={() => toggle(item.id)}
          ><span style={`--series-color:${item.color}`}></span>{item.label}<span class="legend-check" aria-hidden="true"
            >{selected.includes(item.id) ? '✓' : '+'}</span
          ></button
        >{/each}
    </div>
    <div class="resource-chart-grid">
      <ResourceHistoryChart
        metric="cpuPercent"
        loading={busy && (!response || response.range !== range)}
        {points}
        {series}
        {start}
        end={now}
        maximum={100}
        {range}
        intervalSeconds={response?.history.intervalSeconds || 15}
      />
      <ResourceHistoryChart
        metric="memoryBytes"
        loading={busy && (!response || response.range !== range)}
        {points}
        {series}
        {start}
        end={now}
        maximum={sample ? sample.host.memoryTotalBytes / RESOURCE_GIB : 1}
        {range}
        intervalSeconds={response?.history.intervalSeconds || 15}
      />
      <ResourceHistoryChart
        metric="diskBytes"
        loading={busy && (!response || response.range !== range)}
        {points}
        {series}
        {start}
        end={now}
        maximum={sample ? sample.host.diskTotalBytes / RESOURCE_GIB : 1}
        {range}
        intervalSeconds={response?.history.intervalSeconds || 15}
      />
    </div>
    <p class="resource-footnote">
      {#if response?.history.firstCollectedAt}Historique disponible depuis le {resourceFormatDate(
          response.history.firstCollectedAt
        )}.{:else}L’historique se construit à partir des premières mesures collectées.{/if} Les interruptions de collecte
      restent visibles, sans interpolation.
    </p>
  </section>

  <section class="resource-breakdown" aria-labelledby="resource-breakdown-title">
    <header class="resource-section-heading">
      <div>
        <h2 id="resource-breakdown-title">Répartition par catégorie</h2>
        <p>CPU et RAM exprimés par rapport à la capacité totale du serveur.</p>
      </div>
    </header>
    <div class="resource-table-scroll">
      <table>
        <thead
          ><tr
            >{#each categoryColumns as column}<th
                scope="col"
                aria-sort={resourceSortAria(column.key, categorySort, categoryDirection)}
                ><button type="button" class="column-sort" onclick={() => sortCategory(column.key)}
                  >{column.label}<span aria-hidden="true"
                    >{resourceSortIndicator(column.key, categorySort, categoryDirection)}</span
                  ></button
                ></th
              >{/each}</tr
          ></thead
        ><tbody>
          {#each categories as item}<tr class:selected={category === item.id}
              ><th scope="row"
                ><button
                  class="category-link"
                  onclick={() => (category = category === item.id ? 'all' : item.id)}
                  aria-pressed={category === item.id}
                  ><span style={`--series-color:${item.color}`}></span>{item.label}<wa-icon name="arrow-down-right"
                  ></wa-icon></button
                ></th
              ><td>{item.values?.resourceCount ?? '—'}</td><td>{resourceFormatPercent(item.values?.cpuPercent)}</td><td
                >{resourceFormatBytes(item.values?.memoryBytes)}</td
              ><td>{resourceFormatPercent(resourcePercent(item.values?.memoryBytes, sample?.host.memoryTotalBytes))}</td
              ><td>{resourceFormatBytes(item.values?.diskBytes)}</td></tr
            >{/each}
        </tbody>
      </table>
    </div>
    <p class="resource-footnote">
      Disque : attribution approximative par dossiers, relevée {sample?.diskCollectedAt
        ? `le ${resourceFormatDate(sample.diskCollectedAt)}`
        : 'à une date encore indisponible'}. Les espaces partagés peuvent empêcher une égalité exacte avec le total du
      serveur.
    </p>
  </section>

  <section class="resource-detail" aria-labelledby="resource-detail-title">
    <header class="resource-section-heading">
      <div>
        <h2 id="resource-detail-title">
          {category === 'all' ? 'Détail des ressources' : categoryById[category]?.label || 'Détail des ressources'}
        </h2>
        <p>Applications, bases, builders et services mesurés.</p>
      </div>
      {#if category !== 'all'}<button class="web-button" onclick={() => (category = 'all')}
          >Toutes les catégories</button
        >{/if}
    </header>
    <div class="resource-filters">
      <label
        ><span class="sr-only">Rechercher une ressource</span><input
          type="search"
          bind:value={query}
          placeholder="Rechercher une base, une application, un service…"
        /></label
      ><label
        ><span>Trier par</span><select
          bind:value={sort}
          onchange={() => {
            sortDirection = resourceColumns.find((column) => column.key === sort)?.numeric ? 'desc' : 'asc'
          }}
          >{#each resourceColumns as column}<option value={column.key}>{column.label}</option>{/each}</select
        ></label
      >
      <button
        type="button"
        class="web-button sort-direction"
        onclick={() => (sortDirection = sortDirection === 'asc' ? 'desc' : 'asc')}
        aria-label={sortDirection === 'asc' ? 'Ordre croissant, inverser le tri' : 'Ordre décroissant, inverser le tri'}
        >{sortDirection === 'asc' ? 'Croissant ↑' : 'Décroissant ↓'}</button
      >
    </div>
    <div class="resource-table-scroll">
      <table>
        <thead
          ><tr
            >{#each resourceColumns as column}<th
                scope="col"
                aria-sort={resourceSortAria(column.key, sort, sortDirection)}
                ><button type="button" class="column-sort" onclick={() => sortResource(column.key)}
                  >{column.label}<span aria-hidden="true">{resourceSortIndicator(column.key, sort, sortDirection)}</span
                  ></button
                ></th
              >{/each}</tr
          ></thead
        ><tbody>
          {#each resources as row (row.category + ':' + row.id)}<tr
              ><th scope="row"
                >{#if resourceHref(row.href)}<a href={resourceHref(row.href)}
                    >{rowName(row)}<wa-icon name="arrow-up-right"></wa-icon></a
                  >{:else}{rowName(row)}{/if}<small
                  >{kinds[row.kind]}{#if row.memoryLimitBytes !== null}
                    · limite RAM {resourceFormatBytes(row.memoryLimitBytes)}{/if}</small
                ></th
              ><td
                ><span class="category-mark" style={`--series-color:${categoryById[row.category]?.color}`}
                ></span>{categoryById[row.category]?.label || 'Sans catégorie'}</td
              ><td><span class="resource-state" class:running={row.state === 'running'}>{states[row.state]}</span></td
              ><td>{resourceFormatPercent(row.cpuPercent)}</td><td
                >{resourceFormatBytes(row.memoryBytes)}<small
                  >{resourceFormatPercent(resourcePercent(row.memoryBytes, sample?.host.memoryTotalBytes))} du serveur</small
                ></td
              ><td>{resourceFormatBytes(row.diskBytes)}</td></tr
            >
          {:else}<tr
              ><td colspan="6" class="resource-empty"
                >{sample
                  ? 'Aucune ressource ne correspond à ce filtre.'
                  : 'Le détail apparaîtra après la première collecte.'}</td
              ></tr
            >{/each}
        </tbody>
      </table>
    </div>
    {#if sample?.errors.length}<p class="resource-notice">
        Collecte partielle : {sample.errors.length} source{sample.errors.length > 1 ? 's' : ''} de mesures indisponible{sample
          .errors.length > 1
          ? 's'
          : ''}. Les données manquantes restent indiquées par « — ».
      </p>{/if}
  </section>

  <section class="resource-filesystems" aria-labelledby="resource-fs-title">
    <header class="resource-section-heading">
      <div>
        <h2 id="resource-fs-title">Systèmes de fichiers</h2>
        <p>Occupation réelle des volumes du serveur, distincte de l’estimation par catégorie.</p>
      </div>
    </header>
    <div class="resource-table-scroll">
      <table>
        <thead
          ><tr
            >{#each filesystemColumns as column}<th
                scope="col"
                aria-sort={resourceSortAria(column.key, filesystemSort, filesystemDirection)}
                ><button type="button" class="column-sort" onclick={() => sortFilesystem(column.key)}
                  >{column.label}<span aria-hidden="true"
                    >{resourceSortIndicator(column.key, filesystemSort, filesystemDirection)}</span
                  ></button
                ></th
              >{/each}</tr
          ></thead
        ><tbody
          >{#each filesystems as fs (fs.mount)}<tr
              ><th scope="row"><code>{fs.mount}</code></th><td>{resourceFormatBytes(fs.usedBytes)}</td><td
                >{resourceFormatBytes(fs.totalBytes)}</td
              ><td>{resourceFormatBytes(fs.availableBytes)}</td><td
                ><div class="filesystem-usage">
                  <span>{resourceFormatPercent(fs.usedPercent)}</span><span class="filesystem-track"
                    ><span style={`width:${Math.min(100, Math.max(0, fs.usedPercent))}%`}></span></span
                  >
                </div></td
              ></tr
            >{:else}<tr><td colspan="5" class="resource-empty">Capacités de stockage en attente de collecte.</td></tr
            >{/each}</tbody
        >
      </table>
    </div>
  </section>
  <p class="resource-footnote">
    Lecture du cache de supervision toutes les 15 secondes, uniquement quand cet onglet est visible. « Système » couvre
    le reste du serveur, au-delà des bases de données.
  </p>
</div>

<style>
  .resources-overview {
    display: grid;
    gap: 1.5rem;
    min-width: 0;
  }
  .resource-context,
  .resource-context > div {
    display: flex;
    align-items: center;
    gap: 0.65rem;
    flex-wrap: wrap;
  }
  .resource-context {
    justify-content: space-between;
    color: var(--web-muted);
    font-size: 0.72rem;
  }
  .resource-context strong {
    color: var(--app-text-strong);
    font-weight: 600;
  }
  .resource-context > div > span:last-child {
    padding-left: 0.65rem;
    border-left: 1px solid var(--web-border);
  }
  .resource-live-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--web-success);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--web-success) 10%, transparent);
  }
  .resource-live-dot.pending {
    background: #d4b56e;
    box-shadow: none;
  }
  .resource-kpis {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.8rem;
  }
  .resource-kpis article {
    padding: 1.2rem;
    min-width: 0;
    border: 1px solid var(--web-border);
    border-radius: 7px;
    background: var(--web-surface);
  }
  .kpi-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    color: var(--web-muted);
    font-size: 0.72rem;
  }
  .kpi-top wa-icon {
    font-size: 1rem;
    opacity: 0.7;
  }
  .resource-kpis strong {
    display: flex;
    align-items: baseline;
    gap: 0.7rem;
    margin: 0.9rem 0 0.55rem;
    font-size: clamp(1.5rem, 2.8vw, 2.15rem);
    font-weight: 550;
    line-height: 1.15;
    letter-spacing: -0.045em;
    font-variant-numeric: tabular-nums;
  }
  .resource-kpis strong > span {
    font-size: 0.9rem;
    letter-spacing: -0.015em;
    font-weight: 400;
    color: var(--web-muted);
  }
  .resource-kpis small {
    color: var(--web-muted);
    font-size: 0.69rem;
    line-height: 1.5;
  }
  .resource-history,
  .resource-breakdown,
  .resource-detail,
  .resource-filesystems {
    display: grid;
    gap: 0.85rem;
    min-width: 0;
  }
  .resource-section-heading {
    display: flex;
    gap: 1rem;
    align-items: flex-start;
    justify-content: space-between;
    flex-wrap: wrap;
  }
  .resource-section-heading h2 {
    font-size: 0.9rem;
  }
  .resource-section-heading p {
    margin-top: 0.3rem;
    color: var(--web-muted);
    font-size: 0.73rem;
  }
  .resource-ranges {
    display: flex;
    padding: 3px;
    border: 1px solid var(--web-border);
    border-radius: 6px;
    background: var(--web-surface-soft);
  }
  .resource-ranges button {
    min-height: 30px;
    padding: 0.35rem 0.7rem;
    border: 0;
    border-radius: 4px;
    color: var(--web-muted);
    background: transparent;
    font: inherit;
    font-size: 0.73rem;
    cursor: pointer;
  }
  .resource-ranges button.active {
    color: var(--app-text-strong);
    background: var(--web-surface);
    box-shadow: 0 0 0 1px var(--web-border);
  }
  .resource-legend {
    display: flex;
    gap: 0.4rem;
    flex-wrap: wrap;
  }
  .resource-legend button {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    min-height: 30px;
    border: 1px solid var(--web-border);
    border-radius: 5px;
    padding: 0.35rem 0.6rem;
    color: var(--app-text);
    background: var(--web-surface);
    font: inherit;
    font-size: 0.7rem;
    cursor: pointer;
  }
  .resource-legend button.unselected {
    color: var(--web-muted);
    background: transparent;
  }
  .resource-legend button.unselected > span:first-child {
    opacity: 0.3;
  }
  .resource-legend button > span:first-child,
  .category-link > span,
  .category-mark {
    display: inline-block;
    width: 7px;
    height: 7px;
    flex: 0 0 auto;
    border-radius: 50%;
    background: var(--series-color);
  }
  .legend-check {
    opacity: 0.65;
    font-size: 0.65rem;
    margin-left: 0.15rem;
  }
  .resource-chart-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.8rem;
  }
  .resource-chart-grid > :global(:last-child) {
    grid-column: 1 / -1;
  }
  .resource-table-scroll {
    overflow-x: auto;
    min-width: 0;
    border: 1px solid var(--web-border);
    border-radius: 7px;
  }
  table {
    border-collapse: collapse;
    width: 100%;
    font-size: 0.75rem;
  }
  th,
  td {
    padding: 0.9rem 1rem;
    text-align: left;
    border-bottom: 1px solid var(--web-border);
  }
  thead th {
    background: var(--web-surface);
    font-size: 0.66rem;
    font-weight: 500;
    color: var(--web-muted);
    white-space: nowrap;
  }
  .column-sort {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    min-height: 26px;
    padding: 0;
    border: 0;
    background: transparent;
    color: inherit;
    font: inherit;
    cursor: pointer;
    text-align: left;
  }
  .column-sort span {
    color: var(--web-muted);
    opacity: 0.6;
    min-width: 0.65rem;
  }
  th[aria-sort='ascending'] .column-sort,
  th[aria-sort='descending'] .column-sort {
    color: var(--app-text-strong);
  }
  th[aria-sort='ascending'] .column-sort span,
  th[aria-sort='descending'] .column-sort span {
    color: var(--web-accent);
    opacity: 1;
  }
  .resource-filters .sort-direction {
    min-height: 35px;
    font-size: 0.7rem;
  }
  tbody th {
    font-weight: 500;
    min-width: 200px;
  }
  tbody td {
    font-variant-numeric: tabular-nums;
    color: var(--app-text);
    white-space: nowrap;
  }
  tbody tr:last-child > * {
    border-bottom: 0;
  }
  tbody tr.selected {
    background: color-mix(in srgb, var(--web-accent) 7%, transparent);
  }
  tbody small {
    display: block;
    font-size: 0.65rem;
    line-height: 1.55;
    color: var(--web-muted);
    font-weight: 400;
    margin-top: 0.25rem;
  }
  tbody a {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    text-decoration: none;
  }
  tbody a:hover {
    text-decoration: underline;
  }
  tbody a wa-icon {
    font-size: 0.65rem;
    color: var(--web-muted);
  }
  .category-link {
    display: inline-flex;
    align-items: center;
    gap: 0.55rem;
    padding: 0;
    border: 0;
    background: transparent;
    color: inherit;
    font: inherit;
    cursor: pointer;
  }
  .category-link wa-icon {
    color: var(--web-muted);
    font-size: 0.7rem;
    margin-left: 0.3rem;
  }
  .category-mark {
    margin-right: 0.45rem;
    width: 5px;
    height: 5px;
  }
  .resource-state {
    color: var(--web-muted);
    font-size: 0.68rem;
  }
  .resource-state.running {
    color: var(--web-success);
  }
  .resource-filters {
    display: flex;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 0.75rem;
  }
  .resource-filters label {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    color: var(--web-muted);
    font-size: 0.7rem;
  }
  .resource-filters label:first-child {
    flex: 1;
    max-width: 440px;
  }
  .resource-filters input,
  .resource-filters select {
    min-height: 35px;
    width: 100%;
    padding: 0.45rem 0.65rem;
    border: 1px solid var(--web-border);
    border-radius: 5px;
    background: var(--web-surface);
    color: var(--app-text);
    font: inherit;
    font-size: 0.73rem;
  }
  .filesystem-usage {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    min-width: 170px;
  }
  .filesystem-usage > span:first-child {
    min-width: 45px;
  }
  .filesystem-track {
    width: 100%;
    height: 4px;
    overflow: hidden;
    border-radius: 9px;
    background: var(--web-border);
  }
  .filesystem-track > span {
    display: block;
    height: 100%;
    background: var(--web-accent);
    border-radius: inherit;
  }
  .resource-footnote {
    color: var(--web-muted);
    font-size: 0.69rem;
    line-height: 1.6;
  }
  .resource-notice {
    padding: 0.8rem 1rem;
    color: var(--web-muted);
    border: 1px solid var(--web-border);
    border-radius: 6px;
    background: var(--web-surface);
    font-size: 0.75rem;
  }
  .resource-empty {
    text-align: center;
    padding: 2rem 1rem;
    color: var(--web-muted);
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }
  button:focus-visible,
  input:focus-visible,
  select:focus-visible,
  a:focus-visible {
    outline: 2px solid var(--web-accent);
    outline-offset: 3px;
  }
  @media (max-width: 1050px) {
    .resource-kpis {
      gap: 0.6rem;
    }
    .resource-kpis article {
      padding: 1rem;
    }
    .resource-chart-grid {
      grid-template-columns: 1fr;
    }
    .resource-chart-grid > :global(:last-child) {
      grid-column: auto;
    }
  }
  @media (max-width: 650px) {
    .resource-kpis {
      grid-template-columns: 1fr;
    }
    .resource-kpis strong {
      font-size: 1.8rem;
      margin-block: 0.65rem 0.45rem;
    }
    .resource-context {
      align-items: flex-start;
      flex-direction: column;
    }
    .resource-context > div > span:last-child {
      width: 100%;
      border: 0;
      padding-left: 1rem;
    }
    th,
    td {
      padding: 0.8rem;
    }
    .resource-filters label:first-child {
      flex-basis: 100%;
      max-width: none;
    }
  }
</style>
