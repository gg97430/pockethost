<script lang="ts">
  import { onMount } from 'svelte'
  import { page } from '$app/stores'
  import FeatureTab from '$components/FeatureTab.svelte'
  import { client } from '$src/pocketbase-client'
  import { instance } from '../store'

  type Index = {
    name: string
    unique: boolean
    partial: boolean
    columns: { name: string; descending: boolean; collation: string }[]
  }
  type Collection = {
    name: string
    type: string
    fields: { name: string; type: string }[]
    indexes: Index[]
    calls: number
    slow: number
    maxMs: number
    hookFiles: string[]
    usage: {
      withoutFields: number
      withTotal: number
      largePages: number
      expanded: number
      writes: number
      peakWritesPerSecond: number
      repeatedWithinSecond: number
    }
  }
  type Pattern = {
    collection: string
    count: number
    averageMs: number
    maxMs: number
    filterFields: { name: string; operator: string }[]
    sortFields: { name: string; direction: string }[]
    supported: boolean
    beforePlan: string[]
    afterPlan: string[]
    sql: string
    proposal: null | { name: string; sql: string; columns: { name: string; direction: string }[] }
    note: string
    codeSearch: string
  }
  type Diagnostic = {
    available: boolean
    logsAvailable: boolean
    truncated: boolean
    budgetLimited: boolean
    sampledRows: number
    sampleLimit: number
    collections: Collection[]
    patterns: Pattern[]
    patternCount: number
    planLimit: number
    analyzedAt: string
    warnings: string[]
    sqliteVersion: string
  }
  let report: Diagnostic | null = null
  let hours = '24'
  let threshold = '500'
  let collection = ''
  let search = ''
  let currentPage = 1
  let busy = false
  let error = ''
  let choices: string[] = []
  $: filtered = (report?.collections || []).filter((c) => c.name.toLowerCase().includes(search.toLowerCase()))
  $: pages = Math.max(1, Math.ceil(filtered.length / 25))
  $: visible = filtered.slice((currentPage - 1) * 25, currentPage * 25)
  $: proposals = report?.patterns.filter((p) => p.proposal).length || 0
  const ms = (value: number) => `${value.toLocaleString('fr-FR', { maximumFractionDigits: 1 })} ms`
  const analyze = async () => {
    busy = true
    error = ''
    report = null
    currentPage = 1
    try {
      const value = await client().client.send<Diagnostic>(`/api/instance/${$instance.id}/diagnostics`, {
        method: 'GET',
        query: { hours, thresholdMs: threshold, collection: collection.trim() },
        requestKey: null,
      })
      report = value
      choices = [...new Set([...choices, ...value.collections.map((c) => c.name)])].sort()
    } catch (e) {
      error = e instanceof Error ? client().parseError(e)[0] || e.message : `${e}`
    } finally {
      busy = false
    }
  }
  const inspect = (name: string) => {
    collection = name
    search = ''
    void analyze()
  }
  const download = () => {
    if (!report) return
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `diagnostic-${$instance.subdomain}.json`
    link.click()
    URL.revokeObjectURL(url)
  }
  onMount(() => {
    const query = $page.url.searchParams
    collection = query.get('collection') || ''
    hours = ['1', '24', '168'].includes(query.get('hours') || '') ? query.get('hours')! : '24'
    threshold = ['100', '500', '1000', '3000'].includes(query.get('thresholdMs') || '')
      ? query.get('thresholdMs')!
      : '500'
    void analyze()
  })
</script>

<svelte:head><title>Diagnostic des collections - Kolab Hosting</title></svelte:head>
<FeatureTab title="Diagnostic des collections et des index" errorMessage={error}>
  <svelte:fragment slot="summary"
    ><p>
      Reliez les appels lents aux index, aux usages API et aux hooks à examiner dans votre projet. Le diagnostic lit les
      métadonnées et les journaux, sans modifier la base.
    </p></svelte:fragment
  >
  <div class="grid gap-6">
    <form
      class="flex flex-wrap items-end gap-4"
      onsubmit={(event) => {
        event.preventDefault()
        void analyze()
      }}
    >
      <div class="auth-field-group">
        <label class="auth-label" for="diagnostic-collection">Collection</label><input
          id="diagnostic-collection"
          class="auth-field"
          list="diagnostic-collections"
          bind:value={collection}
          placeholder="Toutes les collections"
          maxlength="100"
          disabled={busy}
        /><datalist id="diagnostic-collections"
          >{#each choices as name}<option value={name}></option>{/each}</datalist
        >
      </div>
      <div class="auth-field-group">
        <label class="auth-label" for="diagnostic-hours">Période</label><select
          id="diagnostic-hours"
          class="auth-field"
          bind:value={hours}
          disabled={busy}
          ><option value="1">Dernière heure</option><option value="24">Dernières 24 heures</option><option value="168"
            >7 derniers jours</option
          ></select
        >
      </div>
      <div class="auth-field-group">
        <label class="auth-label" for="diagnostic-threshold">Appels lents à partir de</label><select
          id="diagnostic-threshold"
          class="auth-field"
          bind:value={threshold}
          disabled={busy}
          ><option value="100">100 ms</option><option value="500">500 ms</option><option value="1000">1 seconde</option
          ><option value="3000">3 secondes</option></select
        >
      </div>
      <wa-button type="submit" variant="brand" loading={busy} disabled={busy}>Analyser</wa-button>
      <wa-button variant="neutral" href={`/instances/${$instance.id}/performance`}>Appels lents</wa-button>
    </form>
    {#if busy}<p role="status">Lecture des index et analyse des profils d’appels…</p>
      <wa-progress-bar indeterminate label="Diagnostic en lecture seule"></wa-progress-bar>
    {:else if report}
      {#if !report.available}<wa-callout variant="neutral"
          >Les métadonnées de cette base ne sont pas disponibles. Le diagnostic nécessite une base PocketBase présente
          sur ce serveur.</wa-callout
        >
      {:else}
        <div class="flex flex-wrap items-center justify-between gap-3">
          <p class="text-sm text-muted">
            Analyse du {new Date(report.analyzedAt).toLocaleString('fr-FR')} · cache de 60 secondes · {report.sampledRows.toLocaleString(
              'fr-FR'
            )} lignes de journaux examinées.
          </p>
          <wa-button variant="neutral" size="small" onclick={download}>Exporter le rapport</wa-button>
        </div>
        {#if report.truncated || report.budgetLimited}<wa-callout variant="warning"
            >Diagnostic partiel : lecture limitée aux {report.sampleLimit.toLocaleString('fr-FR')} dernières lignes et à un
            budget de temps. Choisissez une collection ou une période plus courte pour cibler l’analyse.</wa-callout
          >{/if}
        {#each report.warnings as warning}<wa-callout variant="warning">{warning}</wa-callout>{/each}
        {#if !report.logsAvailable}<wa-callout variant="neutral"
            >Aucun journal exploitable : l’inventaire des index reste disponible, mais les propositions ne peuvent pas
            être reliées à des appels observés.</wa-callout
          >{/if}
        <wa-callout variant="neutral"
          >{proposals} proposition{proposals > 1 ? 's' : ''} d’index à tester. Les durées observées sont celles des appels
          HTTP complets. Un plan SQL simplifié ne prouve pas que l’index explique la lenteur.</wa-callout
        >
        <section class="grid gap-4" aria-label="Propositions et preuves">
          <h3 class="text-lg font-medium">Profils d’appels lents et preuves</h3>
          <p class="text-sm text-muted">
            {report.patternCount} profils regroupés sans les valeurs des filtres ; au maximum {report.planLimit} profils,
            classés par fréquence puis durée maximale, sont examinés. Ciblez une collection pour affiner.
          </p>
          {#if report.patterns.length === 0}<p>
              Aucun profil de liste lente exploitable sur cette période. Les appels en erreur et les routes
              personnalisées ne produisent pas de proposition d’index automatique.
            </p>{/if}
          {#each report.patterns as pattern, index}
            <wa-card
              ><div class="wa-card-body grid gap-4">
                <div class="flex flex-wrap items-center justify-between gap-3">
                  <h4 class="font-medium">{pattern.collection}</h4>
                  <wa-badge variant={pattern.proposal ? 'warning' : 'neutral'}
                    >{pattern.proposal
                      ? 'Index à tester'
                      : pattern.supported
                        ? 'À examiner'
                        : 'Analyse manuelle'}</wa-badge
                  >
                </div>
                <p class="text-sm">
                  {pattern.count.toLocaleString('fr-FR')} appels lents · moyenne {ms(pattern.averageMs)} · maximum {ms(
                    pattern.maxMs
                  )}
                </p>
                <p class="text-sm text-muted">
                  Filtres : {pattern.filterFields.map((f) => `${f.name} ${f.operator} ?`).join(' ET ') ||
                    'non traduit / aucun'} · Tri : {pattern.sortFields
                    .map((f) => `${f.name} ${f.direction}`)
                    .join(', ') || 'non traduit / aucun'}
                </p>
                <p>{pattern.note}</p>
                {#if pattern.sql}<wa-details summary="Voir les plans et leurs limites"
                    ><div class="grid gap-3">
                      <p class="text-sm">
                        Plan avant : compilé par EXPLAIN QUERY PLAN sur le schéma réel en lecture seule, avec des
                        paramètres fictifs. Le moteur de diagnostic peut différer de celui embarqué par PocketBase. La
                        requête SELECT n’est pas exécutée. Les règles API, les relations complexes et les hooks ne sont
                        pas reproduits.
                      </p>
                      <pre class="overflow-x-auto text-sm"><code>{pattern.sql}</code></pre>
                      <pre class="overflow-x-auto text-sm"><code>{pattern.beforePlan.join('\n')}</code></pre>
                      {#if pattern.afterPlan.length}<p class="text-sm">
                          Projection après : index créé uniquement en mémoire sur un schéma simplifié, sans données ni
                          statistiques de production. Les types et les collations ne sont pas entièrement reproduits.
                          Cette projection indique une possibilité ; elle ne mesure aucun gain réel.
                        </p>
                        <pre class="overflow-x-auto text-sm"><code>{pattern.afterPlan.join('\n')}</code></pre>{/if}
                    </div></wa-details
                  >{/if}
                {#if pattern.proposal}<div class="grid gap-3">
                    <p class="text-sm font-medium">Proposition SQL pour une migration PocketBase à valider en test</p>
                    <pre class="overflow-x-auto text-sm"><code>{pattern.proposal.sql}</code></pre>
                    <p class="text-sm text-muted">
                      Ajouter l’index via la définition de collection / une migration PocketBase. Comparer les mêmes
                      appels en test, vérifier le coût des écritures et la taille, puis préparer le déploiement et le
                      retour arrière. Aucun index n’est appliqué ici.
                    </p>
                  </div>{/if}
                <wa-details summary="Retrouver les appels dans le projet"
                  ><div class="grid gap-3">
                    <p class="text-sm">
                      Piste de recherche dans le dépôt de l’application. Aucun dépôt applicatif n’est inspecté
                      automatiquement par ce diagnostic.
                    </p>
                    <pre class="overflow-x-auto text-sm"><code>{pattern.codeSearch}</code></pre>
                    <p class="text-sm">
                      Examiner les appels getList / getFullList à cette collection, les filtres et les tris ci-dessus,
                      ainsi que les règles API et les hooks. Les valeurs sensibles ont été retirées du rapport.
                    </p>
                  </div></wa-details
                >
              </div></wa-card
            >
          {/each}
        </section>
        <section class="grid gap-4" aria-label="Inventaire des collections">
          <div class="flex flex-wrap items-end justify-between gap-3">
            <h3 class="text-lg font-medium">Collections, index et usages à vérifier</h3>
            <div class="auth-field-group">
              <label class="auth-label" for="diagnostic-search">Rechercher une collection</label><input
                id="diagnostic-search"
                class="auth-field"
                type="search"
                bind:value={search}
                oninput={() => {
                  currentPage = 1
                }}
              />
            </div>
          </div>
          {#each visible as item}
            <wa-details summary={`${item.name} · ${item.indexes.length} index · ${item.slow} appels lents`}>
              <div class="grid gap-4">
                <div class="flex flex-wrap items-center justify-between gap-3">
                  <p>
                    {item.calls.toLocaleString('fr-FR')} appels observés · maximum {ms(item.maxMs)} · type {item.type}
                  </p>
                  <wa-button variant="neutral" size="small" onclick={() => inspect(item.name)}
                    >Cibler cette collection</wa-button
                  >
                </div>
                <div class="overflow-x-auto">
                  <table class="w-full text-left text-sm">
                    <thead
                      ><tr
                        ><th class="p-2">Index</th><th class="p-2">Colonnes et ordre</th><th class="p-2"
                          >Particularités</th
                        ></tr
                      ></thead
                    ><tbody
                      >{#each item.indexes as idx}<tr
                          ><td class="p-2"><code>{idx.name}</code></td><td class="p-2"
                            >{idx.columns
                              .map((c) => `${c.name} ${c.descending ? 'DESC' : 'ASC'} (${c.collation})`)
                              .join(', ')}</td
                          ><td class="p-2">{idx.unique ? 'Unique ' : ''}{idx.partial ? 'Partiel' : ''}</td></tr
                        >{/each}</tbody
                    >
                  </table>
                </div>
                <p class="text-sm text-muted">Champs : {item.fields.map((f) => `${f.name} (${f.type})`).join(', ')}</p>
                <ul class="list-disc pl-5 text-sm">
                  {#if item.usage.withoutFields}<li>
                      {item.usage.withoutFields} listes sans sélection de champs : vérifier si fields peut réduire les données
                      retournées.
                    </li>{/if}
                  {#if item.usage.withTotal}<li>
                      {item.usage.withTotal} listes demandent le total : envisager skipTotal uniquement si l’interface n’utilise
                      pas ce total.
                    </li>{/if}
                  {#if item.usage.largePages}<li>
                      {item.usage.largePages} listes demandent plus de 200 éléments : vérifier la pagination des écrans, en
                      distinguant les imports et exports qui ont besoin de lots.
                    </li>{/if}
                  {#if item.usage.expanded}<li>
                      {item.usage.expanded} listes utilisent expand : vérifier les relations réellement nécessaires.
                    </li>{/if}
                  {#if item.usage.repeatedWithinSecond}<li>
                      {item.usage.repeatedWithinSecond} répétitions de la même URL dans une seconde : rechercher des appels
                      répétés ou un N+1 dans le projet. Plusieurs utilisateurs ou un polling volontaire peuvent aussi l’expliquer.
                    </li>{/if}
                  {#if item.usage.writes}<li>
                      {item.usage.writes} appels HTTP d’écriture ; pic de {item.usage.peakWritesPerSecond} par seconde. Vérifier
                      la concurrence des imports. Ce compteur ne mesure pas les transactions SQL simultanées.
                    </li>{/if}
                </ul>
                {#if item.hookFiles.length}<p class="text-sm">
                    Fichiers de hooks mentionnant littéralement cette collection : {item.hookFiles.join(', ')}.
                    Références textuelles seulement, sans preuve que ces fichiers ont été exécutés pour l’appel lent.
                  </p>{:else}<p class="text-sm text-muted">
                    Aucune référence littérale trouvée dans les fichiers de hooks examinés. Les références dynamiques et
                    le code de l’application restent à vérifier.
                  </p>{/if}
              </div>
            </wa-details>
          {/each}
          <nav class="flex flex-wrap items-center justify-between gap-3" aria-label="Pagination des collections">
            <p class="text-sm text-muted">
              {filtered.length} collections · page {currentPage} sur {pages} · 25 par page
            </p>
            <div class="flex gap-2">
              <wa-button variant="neutral" size="small" disabled={currentPage <= 1} onclick={() => currentPage--}
                >Précédent</wa-button
              ><wa-button variant="neutral" size="small" disabled={currentPage >= pages} onclick={() => currentPage++}
                >Suivant</wa-button
              >
            </div>
          </nav>
        </section>
        <p class="text-sm text-muted">
          Moteur de diagnostic SQLite {report.sqliteVersion}.
          <a href="https://www.sqlite.org/eqp.html" target="_blank" rel="noreferrer">Lire un plan SQLite</a>
          ·
          <a href="https://pocketbase.io/docs/collections/" target="_blank" rel="noreferrer"
            >Collections et index PocketBase</a
          >.
        </p>
      {/if}
    {/if}
  </div>
</FeatureTab>
