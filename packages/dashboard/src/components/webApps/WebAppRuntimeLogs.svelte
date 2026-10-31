<script lang="ts">
  import { onMount, tick } from 'svelte'
  import { webRequest, webError, webDate } from '$lib/webApps'
  import type { RuntimeLogPage, RuntimeLogRecord } from '$lib/webObservability'
  export let appId: string
  export let running = false
  let records: RuntimeLogRecord[] = [],
    data: RuntimeLogPage | null = null
  let busy = false,
    paused = false,
    follow = true,
    error = '',
    notice = '',
    q = '',
    level = 'all',
    stream = 'all',
    from = '',
    to = '',
    copied = false
  let applied = { q: '', level: 'all', stream: 'all', from: '', to: '' },
    disposed = false,
    generation = 0
  let panel: HTMLPreElement | undefined
  const format = (items = records) =>
    items.map((row) => `${row.timestamp} [${row.stream}] [${row.level}] ${row.message.trimEnd()}`).join('\n')
  const read = async (direction: 'older' | 'newer' = 'newer', reset = false) => {
    if (busy || disposed) return
    busy = true
    error = ''
    const request = ++generation
    try {
      const cursor =
        !reset && records.length && data?.generation
          ? `${data.generation}:${direction === 'older' ? records[0]!.id : records[records.length - 1]!.id}`
          : ''
      const value = await webRequest<RuntimeLogPage>(`/${appId}/runtime-logs`, {
        query: { ...applied, direction: cursor ? direction : 'older', ...(cursor ? { cursor } : {}), limit: 200 },
      })
      if (disposed || request !== generation) return
      const replace = reset || value.reset || (!!data?.generation && data.generation !== value.generation)
      if (value.reset)
        notice = 'La publication ou la rétention du journal a changé. La portion disponible a été rechargée.'
      const combined = replace
        ? value.items
        : direction === 'older'
          ? [...value.items, ...records]
          : [...records, ...value.items]
      records = [...new Map(combined.map((row) => [row.id, row])).values()]
      while (records.length > 1000 || format(records).length > 500000) {
        if (direction === 'older') records.pop()
        else records.shift()
        notice =
          'L’affichage conserve au maximum 1 000 lignes et 500 Ko. Les autres lignes retenues restent consultables par pagination.'
      }
      data = value
      if (follow && direction === 'newer') {
        await tick()
        if (panel) panel.scrollTop = panel.scrollHeight
      }
    } catch (e) {
      if (!disposed) error = webError(e)
    } finally {
      busy = false
    }
  }
  const filter = async (event: SubmitEvent) => {
    event.preventDefault()
    applied = {
      q,
      level,
      stream,
      from: from ? new Date(from).toISOString() : '',
      to: to ? new Date(to).toISOString() : '',
    }
    records = []
    data = null
    notice = ''
    await read('newer', true)
  }
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(format())
      copied = true
    } catch {
      error = 'Sélectionnez les lignes du journal pour les copier.'
    }
  }
  const download = () => {
    const url = URL.createObjectURL(new Blob([format()], { type: 'text/plain;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `runtime-${appId}.log`
    anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  const older = () => {
    paused = true
    follow = false
    void read('older')
  }
  onMount(() => {
    disposed = false
    void read('newer', true)
    const timer = setInterval(() => {
      if (!document.hidden && !paused) void read()
    }, 5000)
    return () => {
      disposed = true
      ++generation
      clearInterval(timer)
      records = []
    }
  })
</script>

<section class="web-panel runtime-panel">
  <div class="web-row">
    <div>
      <span class="web-eyebrow">Version publiée · stdout / stderr</span>
      <h2>Journaux du site en service</h2>
    </div>
    <span class="web-status" data-tone={data?.state === 'stale' ? 'warning' : running ? 'success' : 'neutral'}
      >{data?.state === 'stale'
        ? 'Collecte en retard'
        : running
          ? 'Conteneur en service'
          : 'Derniers journaux disponibles'}</span
    >
  </div>
  <p class="web-help">
    Suivez les erreurs et messages de l’application pendant son exécution, y compris lorsqu’une nouvelle version se
    construit. Les valeurs connues des variables sont masquées.
  </p>
  <form class="runtime-filters" onsubmit={filter}>
    <label class="web-field"
      ><span>Rechercher un texte</span><input
        bind:value={q}
        maxlength="100"
        placeholder="Erreur, route, identifiant…"
      /></label
    ><label class="web-field"
      ><span>Niveau détecté</span><select bind:value={level}
        ><option value="all">Tous les niveaux</option><option value="error">Erreur</option><option value="warn"
          >Avertissement</option
        ><option value="info">Information</option><option value="debug">Debug</option><option value="unknown"
          >Sans niveau</option
        ></select
      ></label
    ><label class="web-field"
      ><span>Flux</span><select bind:value={stream}
        ><option value="all">stdout + stderr</option><option value="stdout">stdout</option><option value="stderr"
          >stderr</option
        ></select
      ></label
    ><label class="web-field"><span>Depuis · heure locale</span><input type="datetime-local" bind:value={from} /></label
    ><label class="web-field"><span>Jusqu’à · heure locale</span><input type="datetime-local" bind:value={to} /></label
    ><button class="web-button" type="submit" disabled={busy}>Appliquer les filtres</button>
  </form>
  {#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}{#if notice}<div
      class="web-alert"
      role="status"
    >
      {notice}
    </div>{/if}
  <div class="web-row">
    <div class="web-actions">
      <button class="web-button" type="button" onclick={() => (paused = !paused)}
        >{paused ? 'Reprendre le suivi' : 'Mettre en pause'}</button
      ><button class="web-button" type="button" onclick={() => read()} disabled={busy}>Actualiser</button><label
        class="web-check"><input type="checkbox" bind:checked={follow} /> Suivre les nouvelles lignes</label
      >
    </div>
    <div class="web-actions">
      <button class="web-button" type="button" onclick={copy} disabled={!records.length}
        >{copied ? 'Copié' : 'Copier la portion'}</button
      ><button class="web-button" type="button" onclick={download} disabled={!records.length}
        >Télécharger la portion</button
      >
    </div>
  </div>
  <div class="web-actions">
    <button class="web-button" type="button" onclick={older} disabled={busy || !data?.hasOlder}
      >Charger les lignes précédentes</button
    >{#if paused}<button
        class="web-button"
        type="button"
        onclick={() => {
          paused = false
          follow = true
          void read('newer', true)
        }}
        disabled={busy}>Revenir au plus récent</button
      >{/if}
  </div>
  <pre
    class="runtime-log"
    role="region"
    tabindex="0"
    aria-label="Journal d’exécution du site"
    bind:this={panel}
    onscroll={() => {
      if (panel && panel.scrollHeight - panel.scrollTop - panel.clientHeight > 64) follow = false
    }}>{records.length
      ? format()
      : busy
        ? 'Chargement du journal…'
        : data?.state === 'unpublished'
          ? 'Publiez une première version pour consulter ses messages.'
          : 'Aucun message disponible pour ces filtres.'}</pre>
  <div class="web-row runtime-footer">
    <span
      >{records.length} ligne(s) affichée(s) · {paused ? 'Suivi en pause' : 'Actualisation toutes les 5 secondes'}</span
    ><span>Dernière collecte : {webDate(data?.checkedAt)}</span>
  </div>
  {#if data?.hasNewer}<button class="web-button" type="button" onclick={() => read()} disabled={busy}
      >Charger la suite</button
    >{/if}
  <p class="web-help">
    Rétention locale : 24 heures et 2 Mio par site. La copie et le téléchargement portent sur la portion affichée. Le
    niveau est déduit des messages ; il peut être absent.
  </p>
</section>

<style>
  .runtime-panel {
    display: grid;
    gap: 1rem;
    min-width: 0;
  }
  .runtime-panel p {
    margin: 0;
  }
  .runtime-filters {
    display: grid;
    grid-template-columns: 2fr 1fr 1fr;
    gap: 0.8rem;
    align-items: end;
  }
  .runtime-log {
    min-height: 24rem;
    max-height: 38rem;
    overflow: auto;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    border: 1px solid var(--web-border);
    background: #101012;
    color: #d6d6dc;
    border-radius: var(--web-radius, 6px);
    padding: 1rem;
    font:
      12px/1.7 ui-monospace,
      SFMono-Regular,
      Consolas,
      monospace;
    margin: 0;
  }
  .runtime-footer {
    font-size: 0.75rem;
    color: var(--web-muted);
  }
  @media (max-width: 750px) {
    .runtime-filters {
      grid-template-columns: 1fr;
    }
    .runtime-log {
      min-height: 20rem;
      padding: 0.7rem;
    }
  }
</style>
