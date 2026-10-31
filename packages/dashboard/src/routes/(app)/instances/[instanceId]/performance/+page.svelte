<script lang="ts">
  import { onMount } from 'svelte'
  import FeatureTab from '$components/FeatureTab.svelte'
  import { client } from '$src/pocketbase-client'
  import { instance } from '../store'

  type Group = {
    method: string
    endpoint: string
    collection: string
    count: number
    slowCount: number
    errors: number
    averageMs: number
    p95Ms: number
    maxMs: number
  }
  type Pagination = { page: number; perPage: number; totalItems: number; totalPages: number }
  type Report = {
    snapshot: string
    slowPagination: Pagination
    groupPagination: Pagination
    available: boolean
    truncated: boolean
    sampleLimit: number
    total: number
    slow: number
    errors: number
    averageMs: number
    p95Ms: number
    maxMs: number
    analyzedAt: string
    oldestAt: string | null
    newestAt: string | null
    groups: Group[]
    slowest: { created: string; method: string; endpoint: string; status: number; durationMs: number }[]
  }
  let report: Report | null = null
  let hours = '24'
  let threshold = '500'
  let sort: 'maxMs' | 'p95Ms' | 'count' = 'maxMs'
  let busy = false
  let error = ''
  let generation = 0
  let slowPage = 1
  let groupPage = 1
  let snapshot = ''
  $: groups = report?.groups || []
  const ms = (value: number) => `${value.toLocaleString('fr-FR', { maximumFractionDigits: 1 })} ms`
  const date = (value: string) => {
    const iso = value.replace(' ', 'T')
    const parsed = new Date(/(Z|[+-]\d{2}:\d{2})$/i.test(iso) ? iso : `${iso}Z`)
    return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleString('fr-FR')
  }
  const refresh = async () => {
    slowPage = 1
    groupPage = 1
    snapshot = ''
    report = null
    await load()
  }
  const load = async () => {
    const current = ++generation
    busy = true
    error = ''
    try {
      const value = await client().client.send<Report>(`/api/instance/${$instance.id}/performance`, {
        method: 'GET',
        query: { hours, thresholdMs: threshold, slowPage, groupPage, sort, snapshot },
        requestKey: null,
      })
      if (current === generation) {
        report = value
        snapshot = value.snapshot
        slowPage = value.slowPagination.page
        groupPage = value.groupPagination.page
      }
    } catch (e) {
      if (current === generation) error = e instanceof Error ? client().parseError(e)[0] || e.message : `${e}`
    } finally {
      if (current === generation) busy = false
    }
  }
  onMount(() => {
    void refresh()
  })
</script>

<svelte:head><title>Performances API - Kolab Hosting</title></svelte:head>

<FeatureTab title="Performances API" errorMessage={error}>
  <svelte:fragment slot="summary">
    <p>
      Repérez les appels API lents dans les journaux PocketBase. Les durées incluent les hooks et le traitement HTTP ;
      elles ne mesurent pas chaque requête SQL.
    </p>
  </svelte:fragment>
  <div class="grid gap-6">
    <div>
      <wa-button
        variant="neutral"
        href={`/instances/${$instance.id}/diagnostics?hours=${hours}&thresholdMs=${threshold}`}
        >Diagnostiquer les collections et les index</wa-button
      >
    </div>
    <div class="flex flex-wrap items-end gap-4">
      <div class="auth-field-group">
        <label class="auth-label" for="performance-period">Période</label><select
          id="performance-period"
          class="auth-field"
          bind:value={hours}
          onchange={refresh}
          ><option value="1">Dernière heure</option><option value="24">Dernières 24 heures</option><option value="168"
            >7 derniers jours</option
          ></select
        >
      </div>
      <div class="auth-field-group">
        <label class="auth-label" for="performance-threshold">Appels lents à partir de</label><select
          id="performance-threshold"
          class="auth-field"
          bind:value={threshold}
          onchange={refresh}
          ><option value="100">100 ms</option><option value="500">500 ms</option><option value="1000">1 seconde</option
          ><option value="3000">3 secondes</option></select
        >
      </div>
      <wa-button variant="neutral" onclick={refresh} loading={busy} disabled={busy}
        ><wa-icon slot="start" name="arrows-rotate"></wa-icon>Actualiser</wa-button
      >
    </div>
    {#if busy && !report}
      <p role="status">Analyse des journaux…</p>
      <wa-progress-bar indeterminate label="Analyse des journaux"></wa-progress-bar>
    {:else if report}
      {#if !report.available}
        <wa-callout variant="neutral"
          >Aucun journal de requêtes disponible. Vérifiez la conservation des logs dans la console PocketBase.</wa-callout
        >
      {:else}
        {#if report.truncated}<wa-callout variant="warning"
            >Volume important : l’analyse couvre les {report.sampleLimit.toLocaleString('fr-FR')} dernières lignes des journaux.
            Les résultats portent sur cet échantillon.</wa-callout
          >{/if}
        <div class="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <wa-card
            ><div class="wa-card-body">
              <p class="text-sm text-muted">Appels analysés</p>
              <strong class="text-2xl">{report.total.toLocaleString('fr-FR')}</strong>
            </div></wa-card
          >
          <wa-card
            ><div class="wa-card-body">
              <p class="text-sm text-muted">Appels lents</p>
              <strong class="text-2xl">{report.slow.toLocaleString('fr-FR')}</strong>
            </div></wa-card
          >
          <wa-card
            ><div class="wa-card-body">
              <p class="text-sm text-muted">95 % des appels sous</p>
              <strong class="text-2xl">{ms(report.p95Ms)}</strong>
            </div></wa-card
          >
          <wa-card
            ><div class="wa-card-body">
              <p class="text-sm text-muted">Durée maximale</p>
              <strong class="text-2xl">{ms(report.maxMs)}</strong>
            </div></wa-card
          >
        </div>
        <p class="text-sm text-muted">
          {#if report.oldestAt && report.newestAt}Appels observés du {date(report.oldestAt)} au {date(report.newestAt)}.
          {/if}{' '}Analyse à {date(report.analyzedAt)} · cache de 15 secondes. Filtres, identifiants de dossiers et paramètres
          des URL masqués.
        </p>
        {#if report.total === 0}
          <p>
            Aucun appel HTTP enregistré sur cette période. Les journaux supprimés ou les requêtes non journalisées ne
            peuvent pas être analysés.
          </p>
        {:else}
          <div class="flex flex-wrap items-end justify-between gap-3">
            <h3 class="text-lg font-medium">Appels par route</h3>
            <div class="auth-field-group">
              <label class="auth-label" for="performance-sort">Classer par</label><select
                id="performance-sort"
                class="auth-field"
                bind:value={sort}
                onchange={() => {
                  groupPage = 1
                  void load()
                }}
                ><option value="maxMs">Durée maximale</option><option value="p95Ms">Durée au 95e percentile</option
                ><option value="count">Nombre d’appels</option></select
              >
            </div>
          </div>
          <div class="overflow-x-auto rounded-lg border border-neutral-500/20">
            <table class="w-full text-left text-sm">
              <thead
                ><tr class="border-b border-neutral-500/20"
                  ><th class="p-3">Route API</th><th class="p-3">Appels</th><th class="p-3">Lents</th><th class="p-3"
                    >Moyenne</th
                  ><th class="p-3">P95</th><th class="p-3">Maximum</th><th class="p-3">Erreurs HTTP</th></tr
                ></thead
              ><tbody
                >{#each groups as group}<tr class="border-b border-neutral-500/10"
                    ><td class="p-3"
                      ><span class="font-medium">{group.method}</span>
                      <code class="break-all">{group.endpoint}</code>{#if group.collection}<a
                          class="ml-2"
                          href={`/instances/${$instance.id}/diagnostics?collection=${encodeURIComponent(group.collection)}&hours=${hours}&thresholdMs=${threshold}`}
                          >Diagnostic</a
                        >{/if}</td
                    ><td class="p-3">{group.count.toLocaleString('fr-FR')}</td><td class="p-3">{group.slowCount}</td><td
                      class="whitespace-nowrap p-3">{ms(group.averageMs)}</td
                    ><td class="whitespace-nowrap p-3">{ms(group.p95Ms)}</td><td class="whitespace-nowrap p-3"
                      >{ms(group.maxMs)}</td
                    ><td class="p-3">{group.errors}</td></tr
                  >{/each}</tbody
              >
            </table>
          </div>
          <nav class="flex flex-wrap items-center justify-between gap-3" aria-label="Pagination des routes">
            <p class="text-sm text-muted">
              {report.groupPagination.totalItems.toLocaleString('fr-FR')} résultats · page {report.groupPagination.page} sur
              {report.groupPagination.totalPages} · 50 par page
            </p>
            <div class="flex flex-wrap items-center gap-2">
              <wa-button
                size="small"
                variant="neutral"
                disabled={busy || groupPage <= 1}
                onclick={() => {
                  groupPage -= 1
                  void load()
                }}>Précédent</wa-button
              >
              <form
                class="flex items-center gap-2"
                onsubmit={(event) => {
                  event.preventDefault()
                  void load()
                }}
              >
                <label class="text-sm" for="groupPage-input">Page</label><input
                  id="groupPage-input"
                  aria-label="Page des routes"
                  class="auth-field"
                  type="number"
                  min="1"
                  max={report.groupPagination.totalPages}
                  step="1"
                  bind:value={groupPage}
                  required
                  style="width: 5rem"
                />
                <wa-button size="small" variant="neutral" type="submit" disabled={busy}>Aller</wa-button>
              </form>
              <wa-button
                size="small"
                variant="neutral"
                disabled={busy || groupPage >= report.groupPagination.totalPages}
                onclick={() => {
                  groupPage += 1
                  void load()
                }}>Suivant</wa-button
              >
            </div>
          </nav>
          <h3 class="text-lg font-medium">Les appels les plus lents</h3>
          {#if report.slowest.length === 0}<p>Aucun appel ne dépasse le seuil sélectionné.</p>{:else}
            <div class="overflow-x-auto rounded-lg border border-neutral-500/20">
              <table class="w-full text-left text-sm">
                <thead
                  ><tr class="border-b border-neutral-500/20"
                    ><th class="p-3">Date</th><th class="p-3">Appel</th><th class="p-3">Statut</th><th class="p-3"
                      >Durée</th
                    ></tr
                  ></thead
                ><tbody
                  >{#each report.slowest as row}<tr class="border-b border-neutral-500/10"
                      ><td class="whitespace-nowrap p-3">{date(row.created)}</td><td class="p-3"
                        ><span class="font-medium">{row.method}</span> <code class="break-all">{row.endpoint}</code></td
                      ><td class="p-3">{row.status || '—'}</td><td class="whitespace-nowrap p-3"
                        >{ms(row.durationMs)}</td
                      ></tr
                    >{/each}</tbody
                >
              </table>
            </div>
          {/if}
          <nav class="flex flex-wrap items-center justify-between gap-3" aria-label="Pagination des appels lents">
            <p class="text-sm text-muted">
              {report.slowPagination.totalItems.toLocaleString('fr-FR')} résultats · page {report.slowPagination.page} sur
              {report.slowPagination.totalPages} · 50 par page
            </p>
            <div class="flex flex-wrap items-center gap-2">
              <wa-button
                size="small"
                variant="neutral"
                disabled={busy || slowPage <= 1}
                onclick={() => {
                  slowPage -= 1
                  void load()
                }}>Précédent</wa-button
              >
              <form
                class="flex items-center gap-2"
                onsubmit={(event) => {
                  event.preventDefault()
                  void load()
                }}
              >
                <label class="text-sm" for="slowPage-input">Page</label><input
                  id="slowPage-input"
                  aria-label="Page des appels lents"
                  class="auth-field"
                  type="number"
                  min="1"
                  max={report.slowPagination.totalPages}
                  step="1"
                  bind:value={slowPage}
                  required
                  style="width: 5rem"
                />
                <wa-button size="small" variant="neutral" type="submit" disabled={busy}>Aller</wa-button>
              </form>
              <wa-button
                size="small"
                variant="neutral"
                disabled={busy || slowPage >= report.slowPagination.totalPages}
                onclick={() => {
                  slowPage += 1
                  void load()
                }}>Suivant</wa-button
              >
            </div>
          </nav>
          <p class="text-sm text-muted">
            Tous les résultats de l’analyse sont accessibles par pages de 50. La pagination conserve le même instantané
            pendant dix minutes. Une route lente est un point de départ pour examiner ses filtres, ses index et ses
            hooks.
          </p>
        {/if}
      {/if}
    {/if}
  </div>
</FeatureTab>
