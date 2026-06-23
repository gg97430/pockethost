<script lang="ts">
  import { goto } from '$app/navigation'
  import AlertBar from '$components/AlertBar.svelte'
  import FeatureTab from '$components/FeatureTab.svelte'
  import InstanceRuntimeBadge from '$components/InstanceRuntimeBadge.svelte'
  import { INSTANCE_ADMIN_URL, INSTANCE_URL } from '$lib/appEnv'
  import { client, type InstanceOverview } from '$src/pocketbase-client'
  import { isInstanceFullyOff } from '$util/instancePower'
  import { StreamNames } from 'pockethost/common'
  import { onMount } from 'svelte'
  import InstanceDeveloperSnippet from './InstanceDeveloperSnippet.svelte'
  import InstanceHealthCards from './InstanceHealthCards.svelte'
  import InstanceQuickActions from './InstanceQuickActions.svelte'
  import { instance } from './store'

  type PreviewLog = {
    time: string
    stream: StreamNames
    message: string
  }

  let overview: InstanceOverview | undefined
  let isLoadingOverview = true
  let errorMessage = ''
  let successMessage = ''
  let busyAction = ''
  let copiedKey = ''
  let liveLogs: PreviewLog[] = []

  $: ({ id, subdomain, cname, power } = $instance)
  $: displayName = cname || subdomain
  $: publicUrl = INSTANCE_URL($instance)
  $: adminUrl = INSTANCE_ADMIN_URL($instance)
  $: latestBackup = overview?.backups.latest
  $: latestBackupFailed = latestBackup?.status === 'failed'
  $: canDuplicate = isInstanceFullyOff($instance)

  const formatBytes = (bytes: number | null | undefined) => {
    if (bytes === null || bytes === undefined) return 'Indisponible'
    if (!bytes) return '0 o'
    const units = ['o', 'Ko', 'Mo', 'Go', 'To']
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
    const value = bytes / 1024 ** index
    return `${new Intl.NumberFormat('fr-FR', { maximumFractionDigits: value >= 10 ? 0 : 1 }).format(value)} ${units[index]}`
  }

  const formatDate = (value: string | undefined) => {
    if (!value) return 'Aucune donnée'
    return new Intl.DateTimeFormat('fr-FR', {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(value))
  }

  const backupStatusLabel = (status: string | undefined) => {
    if (status === 'ready') return 'Prête'
    if (status === 'running') return 'En cours'
    if (status === 'failed') return 'Échec'
    return 'Aucune'
  }

  const logClass = (stream: StreamNames) => {
    if (stream === StreamNames.StdErr) return 'overview-log-line--error'
    return 'overview-log-line--stdout'
  }

  const normalizeLogMessage = (message: string) => {
    try {
      const parsed = JSON.parse(message)
      return typeof parsed === 'string' ? parsed : JSON.stringify(parsed)
    } catch {
      return message
    }
  }

  const parseError = (error: unknown) => {
    return error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
  }

  const loadOverview = async () => {
    isLoadingOverview = true
    try {
      overview = await client().getInstanceOverview(id)
    } catch (error) {
      errorMessage = parseError(error)
    } finally {
      isLoadingOverview = false
    }
  }

  const copyText = async (key: string, value: string) => {
    errorMessage = ''
    try {
      await navigator.clipboard.writeText(value)
      copiedKey = key
      window.setTimeout(() => {
        if (copiedKey === key) copiedKey = ''
      }, 1600)
    } catch {
      errorMessage = 'Impossible de copier dans le presse-papiers.'
    }
  }

  const createBackup = async () => {
    if (busyAction) return

    const confirmed = window.confirm(
      `Créer une sauvegarde complète de ${displayName} ?${power ? "\n\nL'instance sera arrêtée puis redémarrée automatiquement." : ''}`
    )
    if (!confirmed) return

    busyAction = 'backup'
    errorMessage = ''
    successMessage = ''
    try {
      await client().createInstanceBackup(id)
      successMessage = 'Sauvegarde créée.'
      await loadOverview()
    } catch (error) {
      errorMessage = parseError(error)
    } finally {
      busyAction = ''
    }
  }

  const duplicateInstance = async () => {
    if (busyAction) return
    if (!canDuplicate) {
      errorMessage = "Éteignez l'instance avant de dupliquer la base."
      return
    }

    const confirmed = window.confirm(`Dupliquer la base de ${displayName} vers une nouvelle instance éteinte ?`)
    if (!confirmed) return

    busyAction = 'duplicate'
    errorMessage = ''
    successMessage = ''
    try {
      const result = await client().duplicateInstance(id)
      await goto(`/instances/${result.instance.id}`)
    } catch (error) {
      errorMessage = parseError(error)
    } finally {
      busyAction = ''
    }
  }

  onMount(() => {
    void loadOverview()

    if (!power) return

    const unwatch = client().watchInstanceLog($instance, (newLog) => {
      const log = {
        time: '<no time>',
        stream: StreamNames.StdOut,
        message: '<no message>',
        ...newLog,
      } as PreviewLog
      liveLogs = [...liveLogs, log].slice(-6)
    }, 30)

    return () => unwatch()
  })
</script>

<FeatureTab title="Vue d'ensemble" bind:errorMessage {successMessage} successFlash>
  <svelte:fragment slot="alerts">
    {#if latestBackupFailed}
      <AlertBar
        type="warning"
        message="La dernière sauvegarde est en échec. Ouvrez l’onglet Sauvegardes pour consulter le détail."
      />
    {/if}
    {#if !cname}
      <AlertBar type="info" message="Aucun domaine personnalisé n’est configuré pour cette instance." />
    {/if}
  </svelte:fragment>

  <svelte:fragment slot="cta">
    <InstanceQuickActions
      instance={$instance}
      {busyAction}
      {copiedKey}
      onCopy={copyText}
      onBackup={createBackup}
      onDuplicate={duplicateInstance}
    />
  </svelte:fragment>

  <section class="overview-cockpit">
    <div class="overview-cockpit-main">
      <span class="overview-eyebrow">Console instance</span>
      <h3>{displayName}</h3>
      <div class="overview-url-row">
        <code>{publicUrl}</code>
        <button type="button" onclick={() => copyText('public-url', publicUrl)}>
          <wa-icon name={copiedKey === 'public-url' ? 'check' : 'copy'}></wa-icon>
          {copiedKey === 'public-url' ? 'Copié' : 'Copier'}
        </button>
      </div>
    </div>

    <div class="overview-cockpit-status">
      <InstanceRuntimeBadge instance={$instance} />
      <a href={adminUrl} target="_blank" rel="noreferrer">
        Admin PocketBase
        <wa-icon name="arrow-up-right-from-square"></wa-icon>
      </a>
    </div>
  </section>

  <InstanceHealthCards instance={$instance} {overview} />

  <div class="overview-grid">
    <section class="overview-panel">
      <div class="overview-panel-head">
        <div>
          <span class="overview-eyebrow">Sauvegarde</span>
          <h3>Dernier point connu</h3>
        </div>
        <a href={`/instances/${id}/backups`}>Tout voir</a>
      </div>

      {#if isLoadingOverview && !overview}
        <p class="overview-muted">Chargement des sauvegardes...</p>
      {:else if latestBackup}
        <div class="overview-backup">
          <span class="overview-backup-status overview-backup-status--{latestBackup.status}">
            {backupStatusLabel(latestBackup.status)}
          </span>
          <strong>{latestBackup.filename || latestBackup.id}</strong>
          <dl>
            <div>
              <dt>Date</dt>
              <dd>{formatDate(latestBackup.created)}</dd>
            </div>
            <div>
              <dt>Compressé</dt>
              <dd>{formatBytes(latestBackup.compressedBytes)}</dd>
            </div>
            <div>
              <dt>Source</dt>
              <dd>{formatBytes(latestBackup.sizeBytes)}</dd>
            </div>
          </dl>
          {#if latestBackup.remoteKey}
            <p class="overview-muted">Copie externe : {latestBackup.remoteKey}</p>
          {:else if latestBackup.remoteError}
            <p class="overview-warning">Copie externe : {latestBackup.remoteError}</p>
          {/if}
        </div>
      {:else}
        <div class="overview-empty">
          <wa-icon name="box-archive"></wa-icon>
          <span>Aucune sauvegarde créée.</span>
        </div>
      {/if}
    </section>

    <section class="overview-panel">
      <div class="overview-panel-head">
        <div>
          <span class="overview-eyebrow">Logs live</span>
          <h3>Activité récente</h3>
        </div>
        <a href={`/instances/${id}/logs`}>Plein écran</a>
      </div>

      {#if !power}
        <div class="overview-empty">
          <wa-icon name="power-off"></wa-icon>
          <span>Instance éteinte.</span>
        </div>
      {:else if liveLogs.length === 0}
        <p class="overview-muted">En attente de nouvelles lignes...</p>
      {:else}
        <div class="overview-log-preview">
          {#each liveLogs as log, index (`${index}-${log.time}-${log.message}`)}
            <p class="overview-log-line {logClass(log.stream)}">
              <span>{log.time}</span>
              {normalizeLogMessage(log.message)}
            </p>
          {/each}
        </div>
      {/if}
    </section>
  </div>

  <section class="overview-panel overview-panel--wide">
    <div class="overview-panel-head">
      <div>
        <span class="overview-eyebrow">Configuration</span>
        <h3>Réglages critiques</h3>
      </div>
    </div>

    <div class="overview-settings">
      <a href={`/instances/${id}/version`}>
        <wa-icon name="code-branch"></wa-icon>
        <span>Version</span>
        <strong>v{$instance.version}</strong>
      </a>
      <a href={`/instances/${id}/domain`}>
        <wa-icon name="globe"></wa-icon>
        <span>Domaine</span>
        <strong>{cname ? 'Personnalisé' : 'Interne'}</strong>
      </a>
      <a href={`/instances/${id}/admin-sync`}>
        <wa-icon name="arrows-rotate"></wa-icon>
        <span>Synchro admin</span>
        <strong>{$instance.syncAdmin ? 'Activée' : 'Désactivée'}</strong>
      </a>
      <a href={`/instances/${id}/auto-vacuum`}>
        <wa-icon name="broom"></wa-icon>
        <span>Nettoyage auto</span>
        <strong>{$instance.autoVacuum !== false ? 'Activé' : 'Désactivé'}</strong>
      </a>
    </div>
  </section>

  <InstanceDeveloperSnippet instance={$instance} />
</FeatureTab>

<style>
  .overview-cockpit {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 1rem;
    margin-bottom: 0.875rem;
    border: 1px solid rgb(30 184 84 / 0.25);
    border-radius: 0.875rem;
    background:
      linear-gradient(135deg, rgb(30 184 84 / 0.16), transparent 42%),
      linear-gradient(120deg, rgb(59 130 246 / 0.12), transparent 58%),
      var(--app-surface);
    padding: 1rem;
    box-shadow: var(--app-shadow-sm);
  }

  .overview-cockpit-main {
    min-width: 0;
  }

  .overview-eyebrow {
    display: block;
    color: var(--app-text-muted);
    font-size: 0.72rem;
    font-weight: 900;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .overview-cockpit h3,
  .overview-panel h3 {
    margin: 0.2rem 0 0;
    color: var(--app-text-strong);
    font-size: 1.15rem;
    font-weight: 900;
    letter-spacing: 0;
  }

  .overview-url-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    align-items: center;
    margin-top: 0.85rem;
  }

  .overview-url-row code {
    min-width: 0;
    max-width: 100%;
    overflow: hidden;
    border: 1px solid var(--app-border);
    border-radius: 0.5rem;
    background: rgb(0 0 0 / 0.22);
    color: var(--app-text);
    padding: 0.45rem 0.6rem;
    font-size: 0.83rem;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .overview-url-row button,
  .overview-cockpit-status a,
  .overview-panel-head a {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    border: 1px solid var(--app-border);
    border-radius: 0.5rem;
    background: var(--app-surface-soft);
    color: var(--app-text-strong);
    font-size: 0.78rem;
    font-weight: 800;
    line-height: 1;
    text-decoration: none;
  }

  .overview-url-row button {
    min-height: 2rem;
    padding: 0 0.7rem;
    cursor: pointer;
  }

  .overview-cockpit-status {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-start;
    gap: 0.625rem;
  }

  .overview-cockpit-status a,
  .overview-panel-head a {
    min-height: 2rem;
    padding: 0 0.7rem;
  }

  .overview-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 0.875rem;
    margin-top: 0.875rem;
  }

  .overview-panel {
    min-width: 0;
    border: 1px solid var(--app-border);
    border-radius: 0.75rem;
    background: var(--app-surface);
    padding: 1rem;
    box-shadow: var(--app-shadow-sm);
  }

  .overview-panel--wide {
    margin-top: 0.875rem;
  }

  .overview-panel-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 1rem;
  }

  .overview-muted {
    margin: 0;
    color: var(--app-text-muted);
    font-size: 0.88rem;
    line-height: 1.55;
  }

  .overview-warning {
    margin: 0.75rem 0 0;
    color: #fbbf24;
    font-size: 0.84rem;
    line-height: 1.45;
  }

  .overview-backup {
    min-width: 0;
  }

  .overview-backup-status {
    display: inline-flex;
    align-items: center;
    min-height: 1.45rem;
    border-radius: 999px;
    padding: 0 0.55rem;
    font-size: 0.72rem;
    font-weight: 900;
  }

  .overview-backup-status--ready {
    background: rgb(30 184 84 / 0.14);
    color: #4ade80;
  }

  .overview-backup-status--running {
    background: rgb(251 191 36 / 0.14);
    color: #fbbf24;
  }

  .overview-backup-status--failed {
    background: rgb(239 68 68 / 0.14);
    color: #f87171;
  }

  .overview-backup strong {
    display: block;
    overflow: hidden;
    margin-top: 0.7rem;
    color: var(--app-text-strong);
    font-size: 0.95rem;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .overview-backup dl {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.5rem;
    margin: 0.85rem 0 0;
  }

  .overview-backup dl div {
    min-width: 0;
    border-radius: 0.5rem;
    background: var(--app-surface-soft);
    padding: 0.65rem;
  }

  .overview-backup dt {
    color: var(--app-text-muted);
    font-size: 0.7rem;
    font-weight: 800;
  }

  .overview-backup dd {
    overflow: hidden;
    margin: 0.2rem 0 0;
    color: var(--app-text-strong);
    font-size: 0.82rem;
    font-weight: 850;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .overview-empty {
    display: flex;
    min-height: 8rem;
    align-items: center;
    justify-content: center;
    gap: 0.55rem;
    border: 1px dashed var(--app-border);
    border-radius: 0.625rem;
    color: var(--app-text-muted);
    font-size: 0.9rem;
    text-align: center;
  }

  .overview-log-preview {
    display: grid;
    gap: 0.35rem;
    min-height: 8rem;
    max-height: 13rem;
    overflow: auto;
    border-radius: 0.625rem;
    background: rgb(0 0 0 / 0.28);
    padding: 0.75rem;
  }

  .overview-log-line {
    margin: 0;
    color: var(--app-text);
    font-family: ui-monospace, SFMono-Regular, 'SF Mono', Consolas, 'Liberation Mono', Menlo, monospace;
    font-size: 0.78rem;
    line-height: 1.45;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .overview-log-line span {
    margin-right: 0.5rem;
    color: var(--app-text-faint);
  }

  .overview-log-line--error {
    color: #f87171;
  }

  .overview-settings {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
    gap: 0.625rem;
  }

  .overview-settings a {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: 0.15rem 0.6rem;
    align-items: center;
    border: 1px solid var(--app-border);
    border-radius: 0.625rem;
    background: var(--app-surface-soft);
    padding: 0.8rem;
    color: var(--app-text);
    text-decoration: none;
    transition:
      border-color 120ms ease,
      background-color 120ms ease;
  }

  .overview-settings a:hover {
    border-color: rgb(30 184 84 / 0.35);
    background: rgb(30 184 84 / 0.08);
  }

  .overview-settings wa-icon {
    grid-row: span 2;
    color: #22c55e;
  }

  .overview-settings span {
    color: var(--app-text-muted);
    font-size: 0.74rem;
    font-weight: 800;
  }

  .overview-settings strong {
    overflow: hidden;
    color: var(--app-text-strong);
    font-size: 0.92rem;
    font-weight: 900;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  @media (min-width: 880px) {
    .overview-cockpit {
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: start;
      padding: 1.15rem;
    }

    .overview-grid {
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    }
  }

  @media (max-width: 640px) {
    .overview-backup dl {
      grid-template-columns: 1fr;
    }
  }

  :global(html[data-theme='light']) .overview-url-row code,
  :global(html[data-theme='light']) .overview-log-preview {
    background: var(--app-surface-soft);
  }

  :global(html[data-theme='light']) .overview-backup-status--ready {
    color: #15803d;
  }
</style>
