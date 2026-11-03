<script lang="ts">
  import { onMount } from 'svelte'
  import { client } from '$src/pocketbase-client'
  import {
    parsePlatformFleet,
    type PlatformFleet,
    type FleetServer,
    type FleetResource,
    type FleetPeer,
  } from '$lib/platformFleet'
  import {
    resourceFormatBytes,
    resourceFormatPercent,
    resourcePercent,
    resourceSortRows,
    resourceSortAria,
    resourceSortIndicator,
    type ResourceSortDirection,
  } from '$lib/platformResources'
  import { webError } from '$lib/webApps'

  let state: PlatformFleet | null = null,
    busy = false,
    error = '',
    disposed = false,
    now = Date.now()
  let query = '',
    server: FleetPeer | 'all' = 'all',
    kind: 'all' | 'database' | 'application' = 'all'
  type SortKey = 'name' | 'server' | 'kind' | 'status' | 'cpuPercent' | 'memoryBytes' | 'diskBytes'
  let sort: SortKey = 'name'
  let direction: ResourceSortDirection = 'asc'
  type Resource = FleetResource & { server: FleetPeer; hostname: string; stale: boolean }
  const accessLabel = {
    denied: 'Accès distant refusé',
    unavailable: 'Lecture indisponible',
    notConfigured: 'Liaison à configurer',
    granted: 'Accessible',
  }
  const statusLabel = (value: string) =>
    ({
      running: 'Active',
      stopped: 'Arrêtée',
      sleeping: 'En veille',
      idle: 'Au repos',
      starting: 'Démarrage',
      failed: 'En échec',
      restarting: 'Redémarrage',
      maintenance: 'Maintenance',
    })[value] || 'État inconnu'
  const kindLabel = (value: FleetResource['kind']) => (value === 'database' ? 'Base de données' : 'Hébergement')
  const stamp = (value: string | null | undefined) =>
    value
      ? new Intl.DateTimeFormat('fr-FR', {
          timeZone: 'Indian/Reunion',
          dateStyle: 'short',
          timeStyle: 'medium',
        }).format(new Date(value))
      : 'Sans mesure'
  const aged = (value: string | null | undefined, maximum: number) =>
    !value || now - Date.parse(value) > maximum || Date.parse(value) > now + 30000
  const metricsOld = (row: FleetServer) => !row.metrics || row.metrics.stale || aged(row.metrics.collectedAt, 60000)
  const health = (row: FleetServer) =>
    row.access !== 'granted'
      ? 'unknown'
      : !row.monitoring || row.monitoring.stale || aged(row.monitoring.checkedAt, 180000)
        ? 'unknown'
        : row.monitoring.state
  const healthLabel = (row: FleetServer) =>
    row.access !== 'granted'
      ? accessLabel[row.access]
      : health(row) === 'ok'
        ? 'Contrôles OK'
        : health(row) === 'unknown'
          ? 'État à confirmer'
          : 'Points à vérifier'
  const href = (row: Resource) =>
    `https://${row.hostname}/${row.kind === 'database' ? 'instances' : 'applications'}/${row.id}`
  const changeSort = (key: SortKey) => {
    direction = sort === key && direction === 'asc' ? 'desc' : 'asc'
    sort = key
  }
  const columns: { key: SortKey; label: string }[] = [
    { key: 'name', label: 'Ressource' },
    { key: 'server', label: 'Serveur' },
    { key: 'kind', label: 'Type' },
    { key: 'status', label: 'État déclaré' },
    { key: 'cpuPercent', label: 'CPU' },
    { key: 'memoryBytes', label: 'RAM' },
    { key: 'diskBytes', label: 'Disque' },
  ]
  $: accessible = state?.servers.filter((row) => row.access === 'granted') || []
  $: totals = accessible.reduce(
    (sum, row) => ({
      databases: sum.databases + (row.counts?.databases || 0),
      applications: sum.applications + (row.counts?.applications || 0),
      queued: sum.queued + (row.counts?.deploymentsQueued || 0),
      running: sum.running + (row.counts?.deploymentsRunning || 0),
    }),
    { databases: 0, applications: 0, queued: 0, running: 0 }
  )
  $: inventory = (state?.servers || []).flatMap((row) =>
    (row.inventory || []).map((item) => ({ ...item, server: row.key, hostname: row.hostname, stale: metricsOld(row) }))
  ) as Resource[]
  $: rows = resourceSortRows(
    inventory.filter(
      (row) =>
        (server === 'all' || row.server === server) &&
        (kind === 'all' || row.kind === kind) &&
        `${row.name} ${row.server} ${statusLabel(row.status)}`
          .toLocaleLowerCase('fr')
          .includes(query.toLocaleLowerCase('fr'))
    ),
    (row) => (sort === 'kind' ? kindLabel(row.kind) : sort === 'status' ? statusLabel(row.status) : row[sort]),
    direction
  )
  const load = async () => {
    if (busy || disposed) return
    busy = true
    try {
      const raw = await client().client.send('/api/admin/fleet', { method: 'GET', requestKey: 'platform-fleet' })
      if (!disposed) {
        state = parsePlatformFleet(raw)
        now = Date.now()
        error = ''
      }
    } catch (e) {
      if (!disposed) error = webError(e)
    } finally {
      busy = false
    }
  }
  onMount(() => {
    disposed = false
    void load()
    const timer = setInterval(() => {
      now = Date.now()
      if (!document.hidden) void load()
    }, 30000)
    return () => {
      disposed = true
      clearInterval(timer)
    }
  })
</script>

<div class="fleet-view">
  <div class="web-row fleet-heading">
    <p class="web-help">Lecture seule · actualisation toutes les 30 secondes · heures de La Réunion</p>
    <button class="web-button" type="button" onclick={load} disabled={busy}
      >{busy ? 'Actualisation…' : 'Actualiser les serveurs'}</button
    >
  </div>
  {#if error}<p class="web-alert web-alert--error" role="alert">
      {error}
      {#if state}Les dernières données restent affichées avec leur horodatage.{/if}
    </p>{/if}
  {#if state}
    <section class="fleet-totals" aria-label="Récapitulatif des serveurs accessibles">
      <div><span>Serveurs accessibles</span><strong>{accessible.length}<small> / 3</small></strong></div>
      <div><span>Bases de données</span><strong>{totals.databases}</strong></div>
      <div><span>Hébergements</span><strong>{totals.applications}</strong></div>
      <div>
        <span>Déploiements</span><strong>{totals.running}<small> en cours · {totals.queued} en file</small></strong>
      </div>
    </section>
    {#if accessible.length < 3}<p class="web-help">
        Le récapitulatif couvre les serveurs accessibles. Un compte avec la même adresse e-mail vérifiée doit être
        superadmin actif sur chaque serveur pour en voir les données.
      </p>{/if}
    <section class="fleet-servers" aria-label="État de chaque serveur">
      {#each state.servers as host (host.key)}
        <article class="web-panel fleet-server">
          <header>
            <div>
              <span class="web-eyebrow">{host.key}{host.key === state.self ? ' · ici' : ''}</span>
              <h2>
                <a href={`https://${host.hostname}/monitoring`} target="_blank" rel="noreferrer"
                  >{host.hostname}<span aria-hidden="true"> ↗</span></a
                >
              </h2>
            </div>
            <span
              class="web-status"
              data-tone={health(host) === 'ok' ? 'success' : health(host) === 'error' ? 'danger' : 'warning'}
              >{healthLabel(host)}</span
            >
          </header>
          {#if host.access === 'granted'}
            <dl class="fleet-numbers">
              <div>
                <dt>Bases actives</dt>
                <dd>{host.counts?.databasesRunning} <small>/ {host.counts?.databases}</small></dd>
              </div>
              <div>
                <dt>Hébergements actifs</dt>
                <dd>{host.counts?.applicationsRunning} <small>/ {host.counts?.applications}</small></dd>
              </div>
            </dl>
            {#if host.metrics}
              {@const memoryPercent = resourcePercent(host.metrics.memoryUsedBytes, host.metrics.memoryTotalBytes)}
              {@const diskPercent = resourcePercent(host.metrics.diskUsedBytes, host.metrics.diskTotalBytes)}
              <div class="fleet-meters" class:old={metricsOld(host)}>
                <div>
                  <label for={`fleet-cpu-${host.key}`}
                    >CPU <strong>{resourceFormatPercent(host.metrics.cpuPercent)}</strong></label
                  >{#if host.metrics.cpuPercent !== null}<meter
                      id={`fleet-cpu-${host.key}`}
                      min="0"
                      max="100"
                      value={host.metrics.cpuPercent}>{resourceFormatPercent(host.metrics.cpuPercent)}</meter
                    >{:else}<span class="web-muted">Sans mesure</span>{/if}
                </div>
                <div>
                  <label for={`fleet-memory-${host.key}`}
                    >RAM <strong
                      >{resourceFormatBytes(host.metrics.memoryUsedBytes)}
                      <small>/ {resourceFormatBytes(host.metrics.memoryTotalBytes)}</small></strong
                    ></label
                  >{#if memoryPercent !== null}<meter
                      id={`fleet-memory-${host.key}`}
                      min="0"
                      max="100"
                      value={memoryPercent}>{resourceFormatPercent(memoryPercent)}</meter
                    >{:else}<span class="web-muted">Sans mesure</span>{/if}
                </div>
                <div>
                  <label for={`fleet-disk-${host.key}`}
                    >Disque <strong
                      >{resourceFormatBytes(host.metrics.diskUsedBytes)}
                      <small>/ {resourceFormatBytes(host.metrics.diskTotalBytes)}</small></strong
                    ></label
                  >{#if diskPercent !== null}<meter id={`fleet-disk-${host.key}`} min="0" max="100" value={diskPercent}
                      >{resourceFormatPercent(diskPercent)}</meter
                    >{:else}<span class="web-muted">Sans mesure</span>{/if}
                </div>
              </div>
              <p class="fleet-stamp">
                {metricsOld(host) ? 'Mesures anciennes · ' : 'Mesurées le '}{stamp(host.metrics.collectedAt)}
              </p>
            {:else}<p class="web-help">
                Mesures de ressources indisponibles. Les états déclarés restent consultables.
              </p>{/if}
            <div class="fleet-deployments">
              <span>{host.counts?.deploymentsRunning} déploiement(s) en cours</span><span
                >{host.counts?.deploymentsQueued} en file</span
              ><span>{host.counts?.deploymentsFailed24h} échec(s) sur 24 h</span>
            </div>
            <p class="fleet-stamp">
              Contrôle de santé : {stamp(host.monitoring?.checkedAt)}{#if host.monitoring && health(host) !== 'unknown'}
                · {host.monitoring.counts.error} erreur(s), {host.monitoring.counts.warning} alerte(s){/if}
            </p>
          {:else}<p class="web-help">
              {host.access === 'denied'
                ? 'Votre compte n’est pas autorisé sur ce serveur. Les droits restent propres à chaque installation.'
                : host.access === 'notConfigured'
                  ? 'La connexion en lecture seule entre ces serveurs n’est pas encore configurée.'
                  : 'Le serveur n’a pas pu être lu. Cela ne confirme pas une panne de ses sites ou de ses bases.'}
            </p>{/if}
          <footer>
            {host.access === 'granted' ? 'Inventaire lu le' : 'Tentative de lecture le'}
            {stamp(host.checkedAt)}
          </footer>
        </article>
      {/each}
    </section>
    <section class="web-panel fleet-inventory">
      <div class="web-row">
        <div>
          <h2>Ressources des trois serveurs</h2>
          <p class="web-help">Les liens ouvrent le serveur concerné avec ses propres droits de connexion.</p>
        </div>
        <span class="web-muted">{rows.length} résultat(s)</span>
      </div>
      <div class="fleet-filters">
        <label class="web-field"
          ><span>Rechercher</span><input type="search" placeholder="Nom, serveur ou état…" bind:value={query} /></label
        ><label class="web-field"
          ><span>Serveur</span><select bind:value={server}
            ><option value="all">Tous les serveurs accessibles</option>{#each state.servers as host}<option
                value={host.key}>{host.key}</option
              >{/each}</select
          ></label
        ><label class="web-field"
          ><span>Type</span><select bind:value={kind}
            ><option value="all">Toutes les ressources</option><option value="database">Bases de données</option><option
              value="application">Hébergements</option
            ></select
          ></label
        >
      </div>
      <div class="web-table-wrap">
        <table>
          <thead
            ><tr
              >{#each columns as column}<th scope="col" aria-sort={resourceSortAria(column.key, sort, direction)}
                  ><button type="button" onclick={() => changeSort(column.key)}
                    >{column.label}<span aria-hidden="true">
                      {resourceSortIndicator(column.key, sort, direction)}</span
                    ></button
                  ></th
                >{/each}</tr
            ></thead
          ><tbody
            >{#each rows as row (`${row.server}:${row.kind}:${row.id}`)}<tr
                ><td><a href={href(row)} target="_blank" rel="noreferrer">{row.name} ↗</a></td><td>{row.server}</td><td
                  >{kindLabel(row.kind)}</td
                ><td>{statusLabel(row.status)}</td><td class:old={row.stale}
                  >{resourceFormatPercent(row.cpuPercent)}{#if row.stale && row.cpuPercent !== null}<span
                      class="sr-only"
                    >
                      · mesure ancienne</span
                    >{/if}</td
                ><td class:old={row.stale}>{resourceFormatBytes(row.memoryBytes)}</td><td class:old={row.stale}
                  >{resourceFormatBytes(row.diskBytes)}</td
                ></tr
              >{:else}<tr
                ><td colspan="7" class="fleet-empty">Aucune ressource accessible ne correspond aux filtres.</td></tr
              >{/each}</tbody
          >
        </table>
      </div>
      {#if accessible.some((host) => host.truncated)}<p class="web-help">
          L’inventaire est limité aux 500 premières ressources par serveur. Les totaux restent complets ; ouvrez le
          serveur concerné pour consulter le reste.
        </p>{/if}
      <p class="web-help">
        « État déclaré » vient du contrôleur. Les sondes de santé et les mesures ont leur propre horodatage. Une liaison
        indisponible ou une mesure ancienne ne permet pas de conclure à une panne.
      </p>
    </section>
  {:else if busy}<p class="web-muted" role="status">Lecture des trois serveurs…</p>{/if}
</div>

<style>
  .fleet-view {
    display: grid;
    gap: 1.1rem;
    min-width: 0;
  }
  .fleet-heading p {
    margin: 0;
  }
  .fleet-totals {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    border: 1px solid var(--web-border);
    border-radius: 6px;
    background: var(--web-surface);
  }
  .fleet-totals > div {
    display: grid;
    gap: 0.5rem;
    padding: 1.1rem 1.2rem;
    border-right: 1px solid var(--web-border);
  }
  .fleet-totals > div:last-child {
    border: 0;
  }
  .fleet-totals span,
  .fleet-totals small {
    font-size: 0.75rem;
    color: var(--web-muted);
    font-weight: 400;
    letter-spacing: 0;
  }
  .fleet-totals strong {
    font-size: 1.8rem;
    letter-spacing: -0.04em;
    font-weight: 600;
    line-height: 1.2;
  }
  .fleet-servers {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 1rem;
  }
  .fleet-server {
    gap: 1rem;
    align-content: start;
    min-width: 0;
  }
  header {
    display: flex;
    flex-wrap: wrap;
    align-items: start;
    justify-content: space-between;
    gap: 0.75rem;
  }
  h2 {
    font-size: 0.95rem;
    margin: 0.3rem 0;
  }
  h2 a {
    color: inherit;
    text-decoration: none;
    overflow-wrap: anywhere;
  }
  h2 a:hover {
    text-decoration: underline;
  }
  .fleet-numbers {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.8rem;
    margin: 0;
  }
  dt {
    font-size: 0.72rem;
    color: var(--web-muted);
  }
  dd {
    font-size: 1.45rem;
    font-weight: 600;
    margin: 0.25rem 0 0;
    font-variant-numeric: tabular-nums;
  }
  small {
    font-size: 0.75rem;
    color: var(--web-muted);
    font-weight: 400;
  }
  .fleet-meters {
    display: grid;
    gap: 0.85rem;
  }
  .fleet-meters > div {
    display: grid;
    gap: 0.3rem;
  }
  .fleet-meters label {
    display: flex;
    gap: 0.5rem;
    justify-content: space-between;
    font-size: 0.75rem;
    color: var(--web-muted);
  }
  .fleet-meters strong {
    color: var(--web-text);
    font-weight: 500;
    font-variant-numeric: tabular-nums;
  }
  meter {
    display: block;
    width: 100%;
    height: 7px;
    border: 0;
    border-radius: 4px;
    background: var(--web-border);
  }
  meter::-webkit-meter-bar {
    border: 0;
    background: var(--web-border);
    border-radius: 4px;
  }
  meter::-webkit-meter-optimum-value {
    background: var(--web-accent);
    border-radius: 4px;
  }
  meter::-moz-meter-bar {
    background: var(--web-accent);
    border-radius: 4px;
  }
  .old {
    opacity: 0.62;
  }
  .fleet-stamp,
  footer {
    font-size: 0.68rem;
    color: var(--web-muted);
    margin: 0;
    line-height: 1.6;
  }
  footer {
    padding-top: 0.6rem;
    border-top: 1px solid var(--web-border);
  }
  .fleet-deployments {
    display: flex;
    gap: 0.5rem 1rem;
    flex-wrap: wrap;
    font-size: 0.72rem;
    color: var(--web-muted);
  }
  .fleet-inventory {
    gap: 1rem;
    min-width: 0;
  }
  .web-table-wrap {
    position: relative;
    min-width: 0;
    max-width: 100%;
  }
  .fleet-filters {
    display: grid;
    grid-template-columns: minmax(160px, 2fr) minmax(160px, 1fr) minmax(160px, 1fr);
    gap: 1rem;
  }
  th button {
    border: 0;
    color: inherit;
    background: transparent;
    font: inherit;
    padding: 0;
    cursor: pointer;
    white-space: nowrap;
    min-height: 30px;
  }
  th button:focus-visible {
    outline: 2px solid var(--web-accent);
    outline-offset: 3px;
  }
  td {
    font-variant-numeric: tabular-nums;
  }
  td:first-child {
    min-width: 170px;
    max-width: 300px;
    overflow-wrap: anywhere;
  }
  td:not(:first-child) {
    white-space: nowrap;
  }
  .fleet-empty {
    text-align: center;
    padding: 2rem;
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
  @media (max-width: 1100px) {
    .fleet-servers {
      grid-template-columns: 1fr;
    }
    .fleet-server {
      grid-template-columns: 1fr 1fr;
    }
    .fleet-server header,
    .fleet-server footer {
      grid-column: 1/-1;
    }
  }
  @media (max-width: 700px) {
    .fleet-totals {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .fleet-totals > div:nth-child(2) {
      border-right: 0;
    }
    .fleet-totals > div:nth-child(-n + 2) {
      border-bottom: 1px solid var(--web-border);
    }
    .fleet-totals strong small {
      display: block;
      margin-top: 0.3rem;
    }
    .fleet-filters,
    .fleet-server {
      grid-template-columns: 1fr;
    }
    .fleet-server header,
    .fleet-server footer {
      grid-column: auto;
    }
  }
</style>
