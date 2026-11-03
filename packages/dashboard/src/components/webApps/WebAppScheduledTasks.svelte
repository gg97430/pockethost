<script lang="ts">
  import { onMount } from 'svelte'
  import { webDate, webError, webRequest } from '$lib/webApps'
  import { nextWebCron, parseWebCron, type WebScheduledTask, type WebTaskRun } from '$lib/webScheduledTasks'
  import { WEB_NO_CAPABILITIES, type WebCapabilityMap } from '$lib/webAccessView'
  export let appId: string
  export let running = false
  export let capabilities: WebCapabilityMap = WEB_NO_CAPABILITIES
  $: canManage = capabilities['tasks.manage']
  let tasks: WebScheduledTask[] = [],
    runs: WebTaskRun[] = []
  let loading = true,
    refreshing = false,
    disposed = false
  let busy = '',
    error = '',
    message = '',
    editing = '',
    confirmDelete = '',
    selectedRun = ''
  let name = '',
    command = '',
    schedule = '0 * * * *',
    enabled = true,
    timeoutSeconds = 60
  let timezone: 'UTC' | 'Indian/Reunion' = 'Indian/Reunion',
    catchUp: 'once' | 'skip' = 'once',
    maxCatchUpDelayMinutes = 15
  let page = 1,
    hasMore = false,
    taskFilter = '',
    historyBusy = false
  let worker = { ready: false, checkedAt: '' }
  const labels: Record<string, string> = {
    queued: 'En attente',
    running: 'En cours',
    succeeded: 'Réussie',
    failed: 'Échec',
    timed_out: 'Durée dépassée',
    cancelled: 'Annulée',
    missed: 'Occurrence manquée',
  }
  const atZone = (value: string, zone: 'UTC' | 'Indian/Reunion' = timezone) => {
    const date = new Date(value)
    return Number.isNaN(date.getTime())
      ? '—'
      : new Intl.DateTimeFormat('fr-FR', { timeZone: zone, dateStyle: 'short', timeStyle: 'medium' }).format(date) +
          (zone === 'UTC' ? ' UTC' : ' · La Réunion')
  }
  $: cronPreview = (() => {
    try {
      const value = nextWebCron(parseWebCron(schedule), Date.now(), timezone)
      return value ? atZone(value) : 'Aucune occurrence dans les cinq prochaines années.'
    } catch (e) {
      return e instanceof Error ? e.message : 'Expression invalide.'
    }
  })()
  const loadHistory = async (next = page) => {
    if (historyBusy || disposed) return
    historyBusy = true
    const id = appId,
      filter = taskFilter
    try {
      const value = await webRequest<{ items: WebTaskRun[]; page: number; hasMore: boolean }>(`/${id}/task-runs`, {
        query: { page: next, ...(filter ? { taskId: filter } : {}), ...(selectedRun ? { logRunId: selectedRun } : {}) },
      })
      if (!disposed && id === appId && filter === taskFilter) {
        runs = value.items
        page = value.page
        hasMore = value.hasMore
      }
    } catch (e) {
      if (!disposed) error = webError(e)
    } finally {
      historyBusy = false
    }
  }
  const load = async () => {
    if (refreshing || disposed) return
    refreshing = true
    const id = appId
    try {
      const value = await webRequest<{ items: WebScheduledTask[]; worker: typeof worker }>(`/${id}/tasks`)
      if (!disposed && id === appId) {
        tasks = value.items
        worker = value.worker
        loading = false
      }
    } catch (e) {
      if (!disposed) {
        error = webError(e)
        loading = false
      }
    } finally {
      refreshing = false
    }
  }
  const edit = (task?: WebScheduledTask) => {
    editing = task?.id || 'new'
    name = task?.name || ''
    command = task?.command || ''
    schedule = task?.schedule || '0 * * * *'
    enabled = task?.enabled ?? true
    timeoutSeconds = task?.timeoutSeconds || 60
    timezone = task?.timezone || 'Indian/Reunion'
    catchUp = task?.catchUp || 'once'
    maxCatchUpDelayMinutes = task?.maxCatchUpDelayMinutes || 15
    error = ''
    message = ''
  }
  const close = () => {
    editing = ''
    command = ''
  }
  const save = async (event: SubmitEvent) => {
    event.preventDefault()
    busy = 'save'
    error = ''
    message = ''
    try {
      parseWebCron(schedule)
      await webRequest(`/${appId}/tasks${editing === 'new' ? '' : '/' + editing}`, {
        method: editing === 'new' ? 'POST' : 'PATCH',
        body: {
          name,
          command,
          schedule,
          enabled,
          timeoutSeconds: Number(timeoutSeconds),
          timezone,
          catchUp,
          maxCatchUpDelayMinutes: Number(maxCatchUpDelayMinutes),
        },
      })
      close()
      message = 'Tâche enregistrée. Les prochaines occurrences utilisent le fuseau choisi.'
      await load()
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const action = async (task: WebScheduledTask, kind: 'run' | 'toggle' | 'delete') => {
    busy = task.id
    error = ''
    message = ''
    try {
      await webRequest(`/${appId}/tasks/${task.id}${kind === 'run' ? '/run' : ''}`, {
        method: kind === 'run' ? 'POST' : kind === 'delete' ? 'DELETE' : 'PATCH',
        ...(kind === 'delete' ? {} : { body: kind === 'run' ? {} : { enabled: !task.enabled } }),
      })
      message =
        kind === 'run'
          ? 'Exécution ajoutée à la file.'
          : kind === 'delete'
            ? 'Tâche supprimée. Son historique est conservé.'
            : task.enabled
              ? 'Planification suspendue.'
              : 'Planification activée.'
      confirmDelete = ''
      await Promise.all([load(), loadHistory(1)])
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  onMount(() => {
    disposed = false
    void load()
    void loadHistory(1)
    const timer = setInterval(() => {
      if (!document.hidden) {
        void load()
        void loadHistory()
      }
    }, 5000)
    return () => {
      disposed = true
      command = ''
      clearInterval(timer)
    }
  })
</script>

<section class="web-panel tasks-panel">
  <div class="web-row">
    <div>
      <span class="web-eyebrow">Automatisations</span>
      <h2>Tâches planifiées</h2>
    </div>
    {#if canManage}<button
        class="web-button web-button--primary"
        type="button"
        onclick={() => edit()}
        disabled={!!busy || tasks.length >= 20}>Nouvelle tâche</button
      >{/if}
  </div>
  <p class="web-help">
    Exécutez les commandes de maintenance dans le conteneur publié du site, avec son utilisateur et ses variables
    d’exécution. Une seule commande planifiée s’exécute à la fois sur ce serveur.
  </p>
  <div class="tasks-worker" data-ready={worker.ready} role="status">
    <span aria-hidden="true"></span>{worker.ready
      ? 'Le planificateur répond'
      : 'Le planificateur ne répond pas encore'}{#if worker.checkedAt}<small> · {webDate(worker.checkedAt)}</small>{/if}
  </div>
  {#if !running}<div class="web-alert">
      Le site doit être démarré pour exécuter une commande. Les tâches restent configurables.
    </div>{/if}
  {#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}{#if message}<div
      class="web-alert web-alert--success"
      role="status"
    >
      {message}
    </div>{/if}
  {#if editing && canManage}<form class="web-form task-editor" onsubmit={save}>
      <div class="web-row">
        <h3>{editing === 'new' ? 'Nouvelle tâche' : 'Modifier la tâche'}</h3>
        <button class="web-button" type="button" onclick={close} disabled={!!busy}>Annuler</button>
      </div>
      <div class="web-form-grid">
        <label class="web-field"
          ><span>Nom</span><input
            bind:value={name}
            required
            maxlength="80"
            disabled={!!busy}
            placeholder="Nettoyer les sessions"
          /></label
        ><label class="web-field"
          ><span>Durée maximale · secondes</span><input
            type="number"
            bind:value={timeoutSeconds}
            min="1"
            max="900"
            step="1"
            required
            disabled={!!busy}
          /></label
        ><label class="web-field web-field--full"
          ><span>Commande dans le conteneur · /bin/sh -lc</span><textarea
            bind:value={command}
            required
            maxlength="4000"
            rows="4"
            spellcheck="false"
            autocomplete="off"
            disabled={!!busy}
            placeholder="node scripts/maintenance.js"
          ></textarea><span class="web-help"
            >Utilisez les variables du site pour les secrets. La commande n’a pas accès au shell de l’hôte ; ses
            fichiers sont ceux du conteneur publié.</span
          ></label
        ><label class="web-field web-field--full"
          ><span>Expression cron · cinq champs</span><input
            class="cron-input"
            bind:value={schedule}
            required
            maxlength="100"
            disabled={!!busy}
            placeholder="0 2 * * *"
          /><span class="web-help"
            >minute · heure · jour du mois · mois · jour de semaine (0 ou 7 = dimanche). *, listes, plages et pas sont
            acceptés.</span
          ></label
        >
        <label class="web-field"
          ><span>Fuseau horaire</span><select bind:value={timezone} disabled={!!busy}
            ><option value="Indian/Reunion">La Réunion · UTC+4</option><option value="UTC">UTC</option></select
          ></label
        >
        <label class="web-field"
          ><span>Après une interruption</span><select bind:value={catchUp} disabled={!!busy}
            ><option value="once">Rejouer une seule occurrence</option><option value="skip"
              >Attendre le prochain horaire</option
            ></select
          ></label
        >
        {#if catchUp === 'once'}<label class="web-field"
            ><span>Retard maximal à rattraper · minutes</span><input
              type="number"
              min="1"
              max="1440"
              step="1"
              required
              bind:value={maxCatchUpDelayMinutes}
              disabled={!!busy}
            /></label
          >{/if}
      </div>
      <div class="web-actions">
        <button class="web-button" type="button" onclick={() => (schedule = '*/15 * * * *')}
          >Toutes les 15 minutes</button
        ><button class="web-button" type="button" onclick={() => (schedule = '0 * * * *')}>Chaque heure</button><button
          class="web-button"
          type="button"
          onclick={() => (schedule = '0 2 * * *')}>Chaque nuit · 02:00</button
        >
      </div>
      <p class="web-help" role="status">Prochaine occurrence : {cronPreview}</p>
      <p class="web-help">
        Si le jour du mois et le jour de semaine sont tous deux restreints, l’un des deux suffit. Les occurrences
        manquées pendant un arrêt ne sont pas rejouées en série.
      </p>
      <label class="web-check"
        ><input type="checkbox" bind:checked={enabled} disabled={!!busy} /> Activer la planification</label
      >
      <div>
        <button class="web-button web-button--primary" type="submit" disabled={!!busy}
          >{busy === 'save' ? 'Enregistrement…' : 'Enregistrer la tâche'}</button
        >
      </div>
    </form>{/if}
  {#if loading}<p class="web-muted" role="status">Chargement des tâches…</p>{:else if !tasks.length && !editing}<div
      class="tasks-empty"
    >
      <strong>Vos opérations récurrentes, au même endroit.</strong>
      <p class="web-help">Ajoutez une commande, un horaire et une limite de temps pour suivre chaque exécution.</p>
    </div>{/if}
  {#each tasks as task}<article class="task-card">
      <div class="web-row">
        <div>
          <h3>{task.name}</h3>
          <p class="web-help">
            <code>{task.schedule}</code> · {task.timezone === 'UTC' ? 'UTC' : 'La Réunion · UTC+4'} · maximum {task.timeoutSeconds}s
          </p>
        </div>
        <span class="web-status" data-tone={task.enabled ? 'success' : 'neutral'}
          >{task.enabled ? (task.late ? 'En retard' : 'Planifiée') : 'Suspendue'}</span
        >
      </div>
      <dl class="task-facts">
        <div>
          <dt>Prochaine occurrence</dt>
          <dd>{task.enabled ? atZone(task.nextRunAt, task.timezone) : 'Planification suspendue'}</dd>
        </div>
        <div>
          <dt>Dernier succès</dt>
          <dd>{task.lastSuccessAt ? atZone(task.lastSuccessAt, task.timezone) : 'Aucun succès enregistré'}</dd>
        </div>
        <div>
          <dt>Dernière occurrence manquée</dt>
          <dd>{task.lastMissedAt ? atZone(task.lastMissedAt, task.timezone) : 'Aucune enregistrée'}</dd>
        </div>
        <div>
          <dt>Dernière exécution</dt>
          <dd>
            {task.lastRun
              ? labels[task.lastRun.status] || task.lastRun.status
              : 'Jamais exécutée'}{#if task.lastRun?.finishedAt}<small>
                · {webDate(task.lastRun.finishedAt)}</small
              >{/if}
          </dd>
        </div>
      </dl>
      {#if confirmDelete === task.id}<div class="web-confirm">
          <p>Supprimer <strong>{task.name}</strong> ? Les exécutions passées restent consultables.</p>
          <div class="web-actions">
            <button
              class="web-button web-button--danger"
              type="button"
              onclick={() => action(task, 'delete')}
              disabled={!!busy}>Confirmer la suppression</button
            ><button class="web-button" type="button" onclick={() => (confirmDelete = '')}>Annuler</button>
          </div>
        </div>{:else if capabilities['tasks.run'] || canManage}<div class="web-actions">
          {#if capabilities['tasks.run']}<button
              class="web-button"
              type="button"
              onclick={() => action(task, 'run')}
              disabled={!!busy || !running || ['queued', 'running'].includes(task.lastRun?.status || '')}
              >Lancer maintenant</button
            >{/if}{#if canManage}<button class="web-button" type="button" onclick={() => edit(task)} disabled={!!busy}
              >Modifier</button
            ><button class="web-button" type="button" onclick={() => action(task, 'toggle')} disabled={!!busy}
              >{task.enabled ? 'Suspendre' : 'Activer'}</button
            ><button
              class="web-button"
              type="button"
              onclick={() => (confirmDelete = task.id)}
              disabled={!!busy || task.lastRun?.status === 'running'}>Supprimer</button
            >{/if}
        </div>{/if}
    </article>{/each}
</section>
<section class="web-panel tasks-panel">
  <div class="web-row">
    <h2>Historique des exécutions</h2>
    <button class="web-button" type="button" onclick={() => loadHistory()} disabled={historyBusy}>Actualiser</button>
  </div>
  <label class="web-field history-filter"
    ><span>Filtrer par tâche</span><select bind:value={taskFilter} onchange={() => loadHistory(1)}
      ><option value="">Toutes les tâches</option>{#each tasks as task}<option value={task.id}>{task.name}</option
        >{/each}</select
    ></label
  >
  <div class="web-table-wrap">
    <table>
      <thead><tr><th>Tâche</th><th>État</th><th>Démarrage</th><th>Durée / sortie</th><th>Journal</th></tr></thead><tbody
        >{#each runs as run}<tr
            ><td
              ><strong>{run.taskName}</strong>
              <div class="web-help">{run.trigger === 'cron' ? 'Planifiée' : 'Manuelle'}</div></td
            ><td
              ><span
                class="web-status"
                data-tone={run.status === 'succeeded'
                  ? 'success'
                  : ['failed', 'timed_out'].includes(run.status)
                    ? 'error'
                    : 'neutral'}>{labels[run.status] || run.status}</span
              ></td
            ><td>{webDate(run.startedAt || run.scheduledAt || run.created)}</td><td
              >{run.finishedAt ? `${(run.durationMs / 1000).toFixed(1)}s` : '—'}
              <div class="web-help">Code {run.exitCode ?? '—'}</div></td
            ><td
              ><button
                class="web-button"
                type="button"
                onclick={() => {
                  selectedRun = selectedRun === run.id ? '' : run.id
                  void loadHistory()
                }}>{selectedRun === run.id ? 'Masquer' : 'Voir la sortie'}</button
              ></td
            ></tr
          >{#if selectedRun === run.id}<tr
              ><td colspan="5"
                >{#if run.error}<p class="web-alert" role="status">{run.error}</p>{/if}
                <pre class="task-log" tabindex="0" role="region" aria-label={`Journal de ${run.taskName}`}>{run.log ||
                    (run.status === 'running'
                      ? 'La sortie sera disponible à la fin de la commande.'
                      : 'Aucune sortie enregistrée.')}</pre>
                {#if run.truncated}<p class="web-help">
                    Le journal est limité à ses 64 Kio les plus récents. Les valeurs connues des variables sont
                    masquées.
                  </p>{/if}</td
              ></tr
            >{/if}{:else}<tr><td colspan="5">{historyBusy ? 'Chargement…' : 'Aucune exécution pour ce filtre.'}</td></tr
          >{/each}</tbody
      >
    </table>
  </div>
  {#if page > 1 || hasMore}<nav class="web-pagination" aria-label="Pages des exécutions">
      <button
        class="web-button"
        type="button"
        onclick={() => loadHistory(page - 1)}
        disabled={page === 1 || historyBusy}>Précédent</button
      ><span>Page {page}</span><button
        class="web-button"
        type="button"
        onclick={() => loadHistory(page + 1)}
        disabled={!hasMore || historyBusy}>Suivant</button
      >
    </nav>{/if}
  <p class="web-help">
    Les commandes ne se chevauchent pas pour une même tâche. La file est partagée par les sites web de ce serveur ; les
    constructions peuvent continuer pendant leur exécution.
  </p>
</section>

<style>
  .tasks-panel {
    display: grid;
    gap: 1rem;
    min-width: 0;
  }
  .tasks-panel p {
    margin: 0;
  }
  .tasks-worker {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    flex-wrap: wrap;
    font-size: 0.78rem;
    color: var(--web-muted);
  }
  .tasks-worker > span {
    width: 0.45rem;
    height: 0.45rem;
    border-radius: 50%;
    background: #c7a35d;
  }
  .tasks-worker[data-ready='true'] > span {
    background: #77af69;
  }
  .task-editor {
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius, 6px);
    padding: 1rem;
    background: var(--web-surface-soft);
  }
  .task-editor textarea,
  .cron-input {
    font-family: ui-monospace, SFMono-Regular, monospace;
  }
  .tasks-empty {
    padding: 1.5rem 0;
  }
  .tasks-empty strong {
    font-size: 0.8125rem;
  }
  .tasks-empty p {
    margin-top: 0.4rem;
  }
  .task-card {
    display: grid;
    gap: 1rem;
    padding: 1rem;
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius, 6px);
  }
  .task-card h3 {
    margin: 0;
    font-size: 0.875rem;
  }
  .task-card h3 + p {
    margin-top: 0.4rem;
  }
  .task-facts {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1rem;
    margin: 0;
  }
  .task-facts dt {
    font-size: 0.73rem;
    color: var(--web-muted);
  }
  .task-facts dd {
    margin: 0.4rem 0 0;
    font-size: 0.8rem;
  }
  .task-facts small {
    display: block;
    color: var(--web-muted);
    margin-top: 0.3rem;
  }
  .task-log {
    max-height: 24rem;
    overflow: auto;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    background: #101012;
    color: #d6d6dc;
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius, 6px);
    padding: 1rem;
    font:
      12px/1.6 ui-monospace,
      SFMono-Regular,
      monospace;
  }
  .history-filter {
    max-width: 22rem;
  }
  @media (max-width: 600px) {
    .task-facts {
      grid-template-columns: 1fr;
    }
    .task-editor,
    .task-card {
      padding: 0.8rem;
    }
  }
</style>
