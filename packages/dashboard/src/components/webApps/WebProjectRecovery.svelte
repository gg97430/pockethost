<script lang="ts">
  import { onMount } from 'svelte'
  import { client } from '$src/pocketbase-client'
  import { webDate, webError } from '$lib/webApps'

  type Project = { id: string; name: string }
  type Preview = {
    projectId: string
    projectName: string
    revision: string
    apps: {
      id: string
      name: string
      databases: string[]
      scheduledTasks: number
      domains: { host: string; status: string }[]
      externalDependencies: string[]
    }[]
    databases: { id: string; name: string; version: string }[]
    blockers: { appId: string; code: string; name: string }[]
  }
  type Run = {
    id: string
    projectId: string
    status: string
    stage: string
    created: string
    startedAt: string
    finishedAt: string
    errorCode: string
    result: {
      completeArtifacts?: boolean
      runtimeStarted?: boolean
      applicationVerified?: boolean
      cleanedUp?: boolean
      apps?: number
      databases?: number
      images?: number
      archiveBytes?: number
      services?: { appId: string; name: string; status: string }[]
    }
  }
  const send = <T,>(path: string, options: Record<string, unknown> = {}) =>
    client().client.send<T>('/api/web-project-recovery' + path, { ...options, requestKey: null })
  let projects: Project[] = [],
    runs: Run[] = [],
    online = false,
    projectId = '',
    preview: Preview | null = null
  let loading = true,
    busy = false,
    error = '',
    message = '',
    disposed = false
  const load = async () => {
    try {
      const state = await send<{ projects: Project[]; runs: Run[]; worker: { online: boolean } }>('')
      if (disposed) return
      projects = state.projects
      runs = state.runs
      online = state.worker.online
    } catch (cause) {
      if (!disposed) {
        online = false
        error = webError(cause)
      }
    } finally {
      loading = false
    }
  }
  const prepare = async () => {
    busy = true
    error = ''
    message = ''
    preview = null
    try {
      preview = await send<Preview>('/projects/' + projectId + '/preview')
    } catch (cause) {
      error = webError(cause)
    } finally {
      busy = false
    }
  }
  const launch = async () => {
    if (!preview || busy) return
    busy = true
    error = ''
    message = ''
    try {
      await send('/projects/' + preview.projectId + '/runs', {
        method: 'POST',
        body: { operation: 'drill', revision: preview.revision },
      })
      message = 'Essai isolé ajouté à la file. Le projet publié reste en service.'
      preview = null
      await load()
    } catch (cause) {
      error = webError(cause)
    } finally {
      busy = false
    }
  }
  const labels: Record<string, string> = {
    queued: 'En attente',
    running: 'En cours',
    ready: 'Reconstruction vérifiée',
    partial: 'Recette à compléter',
    failed: 'Échec',
    cancelled: 'Annulé',
  }
  const stages: Record<string, string> = {
    capture: 'Capture sans interruption',
    restore: 'Reconstruction des copies',
    probe: 'Essai sans réseau sortant',
    report: 'Enregistrement du résultat',
    complete: 'Terminée',
  }
  const projectName = (id: string) => projects.find((item) => item.id === id)?.name || id
  const size = (value = 0) =>
    value >= 1024 ** 3 ? (value / 1024 ** 3).toFixed(1) + ' Gio' : Math.round(value / 1024 ** 2) + ' Mio'
  onMount(() => {
    void load()
    const timer = setInterval(() => void load(), 10000)
    return () => {
      disposed = true
      clearInterval(timer)
    }
  })
</script>

<section class="web-panel project-recovery">
  <div class="web-row">
    <div>
      <h2>Reprise d’un projet</h2>
      <p class="web-muted">Vérifier que les bases, fichiers, images et volumes peuvent être reconstruits ensemble.</p>
    </div>
    <span class:offline={!online} class="service-status">{online ? 'Service disponible' : 'Service indisponible'}</span>
  </div>
  <div class="recovery-explanation">
    <span aria-hidden="true">↻</span>
    <p>
      La capture laisse le projet publié en service. L’essai utilise des copies sans accès au réseau extérieur. Les
      e-mails et crons sont désactivés ; les workers sont conservés sans exécution.
    </p>
  </div>
  {#if error}<p class="web-error" role="alert">{error}</p>{/if}
  {#if message}<p class="web-success" role="status">{message}</p>{/if}
  {#if loading}<p class="web-muted">Chargement des projets…</p>{:else}
    <form
      class="recovery-select"
      onsubmit={(event) => {
        event.preventDefault()
        void prepare()
      }}
    >
      <label class="web-field"
        ><span>Projet à vérifier</span><select
          class="web-input"
          bind:value={projectId}
          onchange={() => (preview = null)}
          ><option value="">Choisir un projet</option>{#each projects as project}<option value={project.id}
              >{project.name}</option
            >{/each}</select
        ></label
      >
      <button class="web-button" type="submit" disabled={!projectId || busy}
        >{busy ? 'Préparation…' : 'Préparer l’essai'}</button
      >
    </form>
    {#if preview}
      <div class="recovery-preview">
        <div class="web-row">
          <h3>{preview.projectName}</h3>
          <span class="web-muted"
            >{preview.apps.length} hébergement{preview.apps.length > 1 ? 's' : ''} · {preview.databases.length} base{preview
              .databases.length > 1
              ? 's'
              : ''}</span
          >
        </div>
        <div class="recovery-items">
          {#each preview.apps as app}<div>
              <strong>{app.name}</strong><span
                >{app.databases.length} liaison{app.databases.length > 1 ? 's' : ''} BDD · {app.scheduledTasks} tâche{app.scheduledTasks >
                1
                  ? 's'
                  : ''} planifiée{app.scheduledTasks > 1 ? 's' : ''}</span
              ><small>{app.domains.map((item) => item.host).join(' · ') || 'Domaine généré'}</small>
            </div>{/each}
        </div>
        {#if preview.blockers.length}
          <div class="web-error" role="alert">
            <strong>Capture complète impossible pour le moment</strong>
            <p>
              Des connexions BDD ou des stockages externes ne sont pas couverts. Il faut ajouter leur copie au périmètre
              avant de lancer cet essai.
            </p>
            {#each preview.blockers as blocker}<p>
                {preview.apps.find((app) => app.id === blocker.appId)?.name || blocker.appId} ·
                <code>{blocker.name}</code>
              </p>{/each}
          </div>
        {:else}
          <p class="web-muted">
            Les fichiers doivent rester stables pendant leur copie. Si des écritures les modifient, l’essai s’arrête et
            vous pourrez le relancer. Une reconstruction réussie ne valide pas à elle seule tous les parcours métier.
          </p>
          <button
            class="web-button web-primary"
            type="button"
            onclick={launch}
            disabled={!online || busy || runs.some((run) => ['queued', 'running'].includes(run.status))}
            >Lancer la reconstruction isolée</button
          >
        {/if}
      </div>
    {/if}
    <div class="recovery-history">
      <h3>Derniers essais</h3>
      {#if !runs.length}<p class="web-muted">Aucun essai de reprise de projet pour le moment.</p>{/if}
      {#each runs as run}
        <details class="recovery-run">
          <summary
            ><span
              ><strong>{projectName(run.projectId)}</strong><small
                >{webDate(run.finishedAt || run.startedAt || run.created)}</small
              ></span
            ><span class:ok={run.status === 'ready'} class:bad={run.status === 'failed'} class="run-status"
              >{labels[run.status] || run.status}</span
            ></summary
          >
          <div class="run-body">
            {#if run.status === 'running'}<p>{stages[run.stage] || 'Opération en cours'}.</p>{/if}
            {#if run.errorCode}<p class="web-error">
                {run.errorCode === 'project_changed'
                  ? 'Le projet a changé depuis l’aperçu. Préparez un nouvel essai.'
                  : run.errorCode === 'cleanup_incomplete'
                    ? 'Le nettoyage des copies reste à vérifier par l’administrateur.'
                    : 'La recette isolée a échoué. Le projet publié reste en service.'}
              </p>{/if}
            {#if run.result?.completeArtifacts}
              <div class="recovery-kpis">
                <div><strong>{run.result.apps || 0}</strong><span>hébergements</span></div>
                <div><strong>{run.result.databases || 0}</strong><span>bases</span></div>
                <div><strong>{run.result.images || 0}</strong><span>images</span></div>
                <div><strong>{size(run.result.archiveBytes)}</strong><span>archive chiffrée</span></div>
              </div>
              <p>
                {run.result.runtimeStarted
                  ? '✓ Copies frontales démarrées et santé HTTP vérifiée sans réseau sortant.'
                  : 'Les artefacts sont reconstruits ; la recette de démarrage reste à compléter.'}
              </p>
              <p class="web-muted">
                Parcours métier : recette distincte requise. Workers conservés sans exécution. {run.result.cleanedUp
                  ? 'Copies supprimées après l’essai.'
                  : 'Nettoyage à vérifier.'}
              </p>
              {#each run.result.services || [] as service}<p>
                  <code>{service.name}</code> · {service.status === 'inert-worker-preserved'
                    ? 'worker conservé, non exécuté'
                    : service.status === 'running-isolated'
                      ? 'santé HTTP vérifiée'
                      : 'démarrage non confirmé'}
                </p>{/each}
            {/if}
          </div>
        </details>
      {/each}
    </div>
  {/if}
</section>

<style>
  .project-recovery {
    display: block;
  }
  .project-recovery h2,
  .project-recovery h3,
  .project-recovery p {
    margin: 0;
  }
  .project-recovery h2 {
    font-size: 1.08rem;
  }
  .project-recovery h3 {
    font-size: 1rem;
  }
  .service-status,
  .run-status {
    font-size: 0.78rem;
    padding: 0.3rem 0.6rem;
    border: 1px solid var(--app-border);
    border-radius: 999px;
    white-space: nowrap;
  }
  .service-status {
    color: var(--app-text-muted);
  }
  .offline {
    opacity: 0.8;
  }
  .recovery-explanation {
    display: flex;
    gap: 1rem;
    padding: 1rem;
    margin: 1rem 0;
    border: 1px solid var(--app-border);
    border-radius: 10px;
    background: var(--app-surface-soft);
  }
  .recovery-explanation > span {
    font-size: 1.6rem;
    color: #6287ff;
  }
  .recovery-explanation p {
    font-size: 0.87rem;
    line-height: 1.6;
  }
  .recovery-select {
    display: flex;
    gap: 0.75rem;
    align-items: flex-end;
    margin: 1rem 0;
  }
  .recovery-select label {
    flex: 1;
    max-width: 440px;
  }
  .recovery-preview {
    border: 1px solid var(--app-border);
    border-radius: 10px;
    padding: 1rem;
    margin: 1rem 0;
    display: grid;
    gap: 1rem;
  }
  .recovery-items {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 0.6rem;
  }
  .recovery-items > div {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    padding: 0.8rem;
    border-radius: 8px;
    background: var(--app-surface-soft);
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .recovery-items span,
  .recovery-items small {
    color: var(--app-text-muted);
    font-size: 0.8rem;
  }
  .recovery-history {
    margin-top: 1.5rem;
  }
  .recovery-history h3 {
    margin-bottom: 0.75rem;
  }
  .recovery-run {
    border-top: 1px solid var(--app-border);
  }
  .recovery-run summary {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    cursor: pointer;
    padding: 0.9rem 0;
    align-items: center;
  }
  .recovery-run summary > span:first-child {
    display: grid;
    gap: 0.3rem;
  }
  .recovery-run small {
    color: var(--app-text-muted);
  }
  .run-body {
    display: grid;
    gap: 0.8rem;
    padding: 0 0 1rem;
  }
  .recovery-kpis {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 0.6rem;
  }
  .recovery-kpis div {
    display: grid;
    gap: 0.3rem;
    background: var(--app-surface-soft);
    padding: 0.8rem;
    border-radius: 8px;
  }
  .recovery-kpis strong {
    font-size: 1.2rem;
  }
  .recovery-kpis span {
    font-size: 0.75rem;
    color: var(--app-text-muted);
  }
  .run-status.ok {
    color: #3da56b;
  }
  .run-status.bad {
    color: #d56565;
  }
  .project-recovery > .web-error,
  .project-recovery > .web-success {
    margin: 1rem 0;
  }
  @media (max-width: 600px) {
    .recovery-select {
      align-items: stretch;
      flex-direction: column;
    }
    .recovery-select label {
      max-width: none;
    }
    .recovery-kpis {
      grid-template-columns: repeat(2, 1fr);
    }
    .recovery-run summary {
      align-items: flex-start;
      flex-direction: column;
    }
    .project-recovery > .web-row {
      align-items: flex-start;
      flex-direction: column;
    }
    .recovery-items {
      grid-template-columns: 1fr;
    }
    .service-status {
      margin-top: 0.5rem;
    }
  }
</style>
