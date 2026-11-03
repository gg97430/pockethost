<script lang="ts">
  import { onMount } from 'svelte'
  import { client } from '$src/pocketbase-client'
  import { webError } from '$lib/webApps'
  import { resourceFormatBytes } from '$lib/platformResources'
  import {
    migrationPeers,
    migrationPeer,
    migrationHost,
    migrationResourceHref,
    migrationStatusLabels,
    migrationBlockerLabel,
    migrationExpired,
    migrationDnsReviewable,
    migrationDrillVerified,
    migrationHasWriteBarrier,
    migrationExportCommand,
    type MigrationPeer,
    type MigrationItem,
    type MigrationSourcePreview,
    type MigrationDnsRecord,
  } from '$lib/projectMigration'

  export let server: MigrationPeer | null = null
  let items: MigrationItem[] = [],
    projects: { id: string; name: string }[] = [],
    ownerId = '',
    selectedId = '',
    projectId = '',
    target: MigrationPeer | '' = ''
  let preview: MigrationSourcePreview | null = null,
    previewTarget: MigrationPeer | null = null,
    confirmation = ''
  let sourceOpen = false,
    sourceInitialized = false
  let keepDeliveryDisabled = false,
    sourceWorkersPaused = false,
    sourceWritesPaused = false,
    sourceCronsPaused = false,
    targetVerified = false,
    nonAtomicAccepted = false
  let loading = true,
    refreshing = false,
    busy = '',
    available = false,
    disposed = false,
    error = '',
    message = '',
    messageFor = '',
    editingFor = '',
    now = Date.now(),
    refreshedAt = ''
  $: currentServer =
    server || migrationPeer(typeof window === 'undefined' ? '' : window.location.hostname.split('.')[0])
  $: selected = items.find((item) => item.id === selectedId) || null
  const itemKey = (item: MigrationItem | null) =>
    item ? [item.id, item.revision, item.status, item.dnsPreview?.revision || ''].join(':') : ''
  $: {
    const next = itemKey(selected)
    if (next !== editingFor) {
      if (messageFor !== next) message = ''
      editingFor = next
      confirmation = ''
      keepDeliveryDisabled = false
      sourceWorkersPaused = false
      sourceWritesPaused = false
      sourceCronsPaused = false
      targetVerified = false
      nonAtomicAccepted = false
    }
  }
  const send = <T,>(path: string, options: Record<string, unknown> = {}) =>
    client().client.send<T>(path, { ...options, requestKey: null })
  const stamp = (value: string | number | undefined) => {
    const date = typeof value === 'number' ? value * 1000 : Date.parse(value || '')
    return Number.isFinite(date)
      ? new Intl.DateTimeFormat('fr-FR', {
          timeZone: 'Indian/Reunion',
          dateStyle: 'short',
          timeStyle: 'medium',
        }).format(new Date(date))
      : '—'
  }
  const load = async () => {
    if (refreshing || disposed) return
    refreshing = true
    try {
      const [state, sources] = await Promise.all([
        send<{ items: MigrationItem[]; ownerId: string }>('/api/web-project-migrations'),
        send<{ items: { id: string; name: string }[] }>('/api/web-project-migration/sources'),
      ])
      if (disposed) return
      items = state.items
      ownerId = state.ownerId
      projects = sources.items
      if (!sourceInitialized) {
        sourceOpen = items.length === 0
        sourceInitialized = true
      }
      available = true
      refreshedAt = new Date().toISOString()
      if (!selectedId && items.length) selectedId = items[0]!.id
      if (selectedId && !items.some((item) => item.id === selectedId)) selectedId = ''
      error = ''
    } catch (cause) {
      if (!disposed) {
        available = false
        error = webError(cause)
      }
    } finally {
      loading = false
      refreshing = false
    }
  }
  const refresh = () => {
    message = ''
    messageFor = ''
    return load()
  }
  const prepare = async (event: SubmitEvent) => {
    event.preventDefault()
    if (!currentServer || !target || target === currentServer || !projectId || busy || refreshing) return
    sourceOpen = true
    busy = 'preview'
    error = ''
    message = ''
    preview = null
    previewTarget = null
    try {
      const next = await send<MigrationSourcePreview>('/api/web-project-migration/sources/' + projectId + '/preview')
      if (!disposed) {
        preview = next
        previewTarget = target
      }
    } catch (cause) {
      error = webError(cause)
    } finally {
      busy = ''
    }
  }
  const clearPreview = () => {
    preview = null
    previewTarget = null
  }
  const act = async (action: 'approve' | 'release' | 'dns-approve') => {
    if (!selected || busy || refreshing || !available || confirmation !== selected.projectName) return
    if (action === 'approve' && migrationExpired(selected, now)) return
    if (
      action === 'release' &&
      (!migrationDrillVerified(selected) ||
        !keepDeliveryDisabled ||
        (selected.plan.workers.length > 0 && !sourceWorkersPaused) ||
        selected.plan.blockers.length)
    )
      return
    if (
      action === 'dns-approve' &&
      (!migrationHasWriteBarrier(selected) ||
        !migrationDnsReviewable(selected, now) ||
        !sourceWritesPaused ||
        !sourceCronsPaused ||
        !targetVerified ||
        !nonAtomicAccepted ||
        selected.plan.blockers.length)
    )
      return
    busy = action
    error = ''
    message = ''
    const original = selected
    const body = {
      revision: original.revision,
      confirmation,
      ...(action === 'release'
        ? { keepDeliveryDisabled: true, ...(original.plan.workers.length ? { sourceWorkersPaused: true } : {}) }
        : {}),
      ...(action === 'dns-approve'
        ? {
            dnsRevision: original.dnsPreview!.revision,
            sourceWritesPaused: true,
            sourceCronsPaused: true,
            targetVerified: true,
            nonAtomicAccepted: true,
          }
        : {}),
    }
    try {
      const value = await send<{ item: MigrationItem }>('/api/web-project-migrations/' + original.id + '/' + action, {
        method: 'POST',
        body,
      })
      if (disposed) return
      items = items.map((item) => (item.id === value.item.id ? value.item : item))
      messageFor = itemKey(value.item)
      message =
        action === 'approve'
          ? 'Import suspendu autorisé. L’opérateur peut créer et vérifier les ressources cibles.'
          : action === 'release'
            ? 'Copie déverrouillée. Vous pouvez démarrer et vérifier ses ressources depuis leurs fiches.'
            : 'Aperçu DNS autorisé. L’opérateur peut appliquer cette révision dans sa période de validité.'
      await load()
    } catch (cause) {
      const reason = webError(cause)
      await load()
      error = reason
    } finally {
      busy = ''
    }
  }
  const planTone = (status: string) =>
    status === 'failed'
      ? 'danger'
      : ['cutover', 'released'].includes(status)
        ? 'success'
        : ['review', 'imported_paused', 'rolling_back'].includes(status)
          ? 'warning'
          : 'muted'
  const environmentLabel = (action: string) =>
    ({ preserve: 'Conservée', 'rewrite-url': 'URL adaptée', override: 'Valeur remplacée' })[action] || 'À vérifier'
  const recordText = (record: MigrationDnsRecord) =>
    `${record.type} ${record.content}${record.proxied === true ? ' · Proxy activé' : record.proxied === false ? ' · DNS seul' : ''}`
  const dnsApproved = (item: MigrationItem) =>
    item.dnsPreview?.revision === item.dnsRevision &&
    Number.isFinite(Date.parse(item.dnsApprovedAt)) &&
    Date.parse(item.dnsApprovedAt) + 900000 > now
  const appName = (item: MigrationItem, id: string) =>
    item.plan.applications.find((app) => app.targetId === id)?.name || 'Application'
  onMount(() => {
    void load()
    const timer = setInterval(() => {
      now = Date.now()
      if (!document.hidden && !busy) void load()
    }, 15000)
    return () => {
      disposed = true
      clearInterval(timer)
    }
  })
</script>

<section class="web-panel project-migration">
  <header class="web-row">
    <div>
      <span class="web-eyebrow">D’un serveur à l’autre</span>
      <h2>Migrer un projet</h2>
      <p class="web-muted">Préparer sa copie, vérifier le résultat et choisir le moment de la bascule.</p>
    </div>
    <button class="web-button" type="button" onclick={refresh} disabled={refreshing || !!busy}
      >{refreshing ? 'Actualisation…' : 'Actualiser'}</button
    >
  </header>
  <div class="migration-route" aria-label="Étapes de migration">
    <span><b>1</b> Périmètre</span><i aria-hidden="true">→</i><span><b>2</b> Plan approuvé</span><i aria-hidden="true"
      >→</i
    ><span><b>3</b> Copie vérifiée</span><i aria-hidden="true">→</i><span><b>4</b> Bascule DNS</span>
  </div>
  <p class="migration-intro">
    La préparation conserve le projet source en service. La copie reçoit de nouveaux identifiants ; ses e-mails, tâches
    planifiées et démarrages automatiques restent suspendus pendant la recette.
  </p>
  {#if error}<p class="web-alert web-alert--error" role="alert">
      {error}{#if items.length}
        Les données affichées datent du {stamp(refreshedAt)}.{/if}
    </p>{/if}
  {#if message}<p class="web-alert web-alert--success" role="status">{message}</p>{/if}
  {#if loading}<p class="web-muted" role="status">Chargement des projets et plans…</p>{:else}
    <details class="migration-section" bind:open={sourceOpen}>
      <summary
        ><span>Préparer un départ depuis {currentServer || 'ce serveur'}</span><small
          >Choisir le projet et sa destination</small
        ></summary
      >
      <div class="migration-body">
        <form class="migration-source" onsubmit={prepare}>
          <label class="web-field"
            ><span>Projet source</span><select
              bind:value={projectId}
              onchange={clearPreview}
              disabled={!!busy || !available}
              ><option value="">Choisir un projet</option>{#each projects as project}<option value={project.id}
                  >{project.name}</option
                >{/each}</select
            ></label
          >
          <label class="web-field"
            ><span>Serveur de destination</span><select
              bind:value={target}
              onchange={clearPreview}
              disabled={!!busy || !available}
              ><option value="">Choisir une destination</option
              >{#each migrationPeers.filter((peer) => peer !== currentServer) as peer}<option value={peer}
                  >{migrationHost(peer)}</option
                >{/each}</select
            ></label
          >
          <button
            class="web-button"
            type="submit"
            disabled={refreshing ||
              !available ||
              !currentServer ||
              !projectId ||
              !target ||
              target === currentServer ||
              !!busy}>{busy === 'preview' ? 'Préparation…' : 'Examiner le périmètre'}</button
          >
        </form>
        {#if !projects.length}<p class="web-help">Les projets regroupant des hébergements apparaîtront ici.</p>{/if}
        {#if preview && previewTarget}
          <div class="migration-preview">
            <div class="web-row">
              <h3>{preview.projectName}</h3>
              <span class="migration-destination"
                >{currentServer} <span aria-hidden="true">→</span> {previewTarget}</span
              >
            </div>
            <div class="migration-kpis">
              <div><strong>{preview.apps.length}</strong><span>hébergements</span></div>
              <div><strong>{preview.databases.length}</strong><span>bases liées</span></div>
              <div>
                <strong>{preview.apps.reduce((sum, app) => sum + app.scheduledTasks, 0)}</strong><span
                  >tâches planifiées</span
                >
              </div>
            </div>
            <div class="migration-resource-list">
              {#each preview.apps as app}<div>
                  <strong>{app.name}</strong><span
                    >{app.databases.length} liaison(s) BDD · {app.scheduledTasks} tâche(s)</span
                  ><small>{app.domains.map((domain) => domain.host).join(' · ') || 'Domaine généré'}</small>
                </div>{/each}
            </div>
            {#if preview.blockers.length}<div class="migration-blockers" role="alert">
                <strong>Périmètre à compléter avant le transfert</strong>{#each preview.blockers as blocker}<p>
                    {migrationBlockerLabel(blocker.code)}{#if blocker.name}<code>{blocker.name}</code>{/if}
                  </p>{/each}
              </div>{:else}<p class="web-help">
                Ce périmètre est prêt pour la préparation opérateur. Le plan détaillé apparaîtra sur le serveur cible
                après le transfert chiffré et ses contrôles de capacité.
              </p>{/if}
            <a
              class="web-button"
              href={`https://${migrationHost(previewTarget)}/applications/recovery/migrations`}
              target="_blank"
              rel="noreferrer">Ouvrir {previewTarget} ↗</a
            >
            <details class="migration-operator">
              <summary>Préparation opérateur</summary>
              <div>
                <p>
                  Exporter cette révision depuis {currentServer}. La capsule et sa clé temporaire concernent uniquement
                  ce projet ; le transfert utilise l’accès SSH administrateur.
                </p>
                {#if migrationExportCommand(preview)}<pre><code>{migrationExportCommand(preview)}</code></pre>{/if}
                <p>
                  Sur {previewTarget}, l’opérateur restaure la capsule, prépare le mapping et soumet le plan. L’import
                  attend ensuite votre accord sur ce serveur.
                </p>
                <p class="web-help">
                  Procédure : <code>docs/hosting/project-migration.md</code>. Les valeurs des variables et les clés
                  restent dans les fichiers privés de l’opérateur.
                </p>
              </div>
            </details>
          </div>
        {/if}
      </div>
    </details>
    <section class="migration-incoming">
      <div class="web-row">
        <div>
          <h3>Plans reçus sur {currentServer || 'ce serveur'}</h3>
          <p class="web-help">Chaque accord porte sur la révision exacte du plan affiché.</p>
        </div>
        <span class="web-muted">{items.length} plan(s)</span>
      </div>
      {#if !items.length}<p class="migration-empty">
          Aucun plan de migration reçu. Commencez par choisir un projet source et une destination.
        </p>{:else}
        <label class="web-field migration-plan-picker"
          ><span>Plan à examiner</span><select bind:value={selectedId} disabled={!!busy}
            >{#each items as item}<option value={item.id}
                >{item.projectName} · {item.source} → {item.target} · {migrationStatusLabels[item.status] ||
                  'État à vérifier'}</option
              >{/each}</select
          ></label
        >
      {/if}
      {#if selected}
        <article class="migration-plan">
          <div class="web-row">
            <div>
              <span class="migration-destination"
                >{selected.source} <span aria-hidden="true">→</span> {selected.target}</span
              >
              <h3>{selected.projectName}</h3>
            </div>
            <span class="web-status" data-tone={planTone(selected.status)}
              >{migrationStatusLabels[selected.status] || 'État à vérifier'}</span
            >
          </div>
          <div class="migration-kpis">
            <div><strong>{selected.plan.applications.length}</strong><span>hébergements</span></div>
            <div><strong>{selected.plan.databases.length}</strong><span>bases de données</span></div>
            <div>
              <strong>{resourceFormatBytes(selected.plan.requiredBytes)}</strong><span>espace libre requis</span>
            </div>
            <div>
              <strong>{resourceFormatBytes(selected.plan.requiredMemoryMiB * 1024 ** 2)}</strong><span
                >budget RAM à vérifier</span
              >
            </div>
          </div>
          {#if selected.plan.blockers.length}<div class="migration-blockers" role="alert">
              <strong>{selected.plan.blockers.length} point(s) à résoudre avant le déverrouillage</strong
              >{#each selected.plan.blockers as blocker}<p>
                  {migrationBlockerLabel(blocker.code)}{#if blocker.name}<code>{blocker.name}</code>{/if}
                </p>{/each}
            </div>{/if}
          <div class="migration-scope">
            <div>
              <strong>Reconstruction isolée</strong>
              <p>
                Le déverrouillage exige une recette de cette capsule dans le moteur dédié, sans réseau sortant. Les
                workers sont conservés sans exécution.
              </p>
            </div>
            {#if selected.plan.drill}<span
                class="web-status"
                data-tone={migrationDrillVerified(selected) ? 'success' : 'warning'}
                >{migrationDrillVerified(selected)
                  ? 'Copies isolées vérifiées et nettoyées'
                  : 'Recette à compléter'}</span
              >{:else}<span class="web-status" data-tone="muted">Recette isolée attendue pendant l’import</span>{/if}
          </div>
          <div class="migration-mappings">
            <div>
              <h4>Hébergements</h4>
              {#each selected.plan.applications as app}{@const href = migrationResourceHref(
                  selected.target,
                  'application',
                  app.targetId
                )}
                <p>
                  {#if href && ['imported_paused', 'released', 'cutover'].includes(selected.status)}<a
                      {href}
                      target="_blank"
                      rel="noreferrer">{app.name} ↗</a
                    >{:else}<strong>{app.name}</strong>{/if}<span
                    >{app.hasFront ? 'Application web' : 'Worker'} · nouvelle copie sur {selected.target}</span
                  >
                </p>{/each}
            </div>
            <div>
              <h4>Bases de données</h4>
              {#each selected.plan.databases as database}{@const href = migrationResourceHref(
                  selected.target,
                  'database',
                  database.targetId
                )}
                <p>
                  {#if href && ['imported_paused', 'released', 'cutover'].includes(selected.status)}<a
                      {href}
                      target="_blank"
                      rel="noreferrer">{database.subdomain} ↗</a
                    >{:else}<strong>{database.subdomain}</strong>{/if}<span
                    >{database.version} · {database.targetHost}</span
                  >{#if database.memoryReservationMiB}<small
                      >{resourceFormatBytes(database.memoryReservationMiB * 1024 ** 2)} de RAM réservée</small
                    >{/if}
                </p>{:else}<p class="web-help">Aucune base liée dans ce plan.</p>{/each}
            </div>
          </div>
          <details class="migration-section">
            <summary
              ><span>Variables et liaisons</span><small
                >{selected.plan.environment.length} variable(s) · {selected.plan.bindings.length} liaison(s) privées</small
              ></summary
            >
            <div class="migration-body">
              <p class="web-help">
                Les noms et les modifications sont affichés pour revue. Les valeurs restent dans la capsule chiffrée.
              </p>
              <div class="web-table-wrap">
                <table>
                  <thead
                    ><tr
                      ><th scope="col">Application</th><th scope="col">Variable</th><th scope="col">Modification</th><th
                        scope="col">Utilisation</th
                      ></tr
                    ></thead
                  ><tbody
                    >{#each selected.plan.environment as entry}<tr
                        ><td>{appName(selected, entry.appTargetId)}</td><td><code>{entry.name}</code></td><td
                          >{environmentLabel(entry.action)}</td
                        ><td
                          >{[entry.build ? 'Construction' : '', entry.runtime ? 'Exécution' : '']
                            .filter(Boolean)
                            .join(' · ') || 'Aucune'}</td
                        ></tr
                      >{:else}<tr><td colspan="4">Aucune variable dans ce plan.</td></tr>{/each}</tbody
                  >
                </table>
              </div>
              {#each selected.plan.bindings as binding}<p class="web-help">
                  {appName(selected, binding.appTargetId)} → {selected.plan.databases.find(
                    (database) => database.targetId === binding.instanceTargetId
                  )?.subdomain || 'Base de la copie'} · port privé {binding.port}
                </p>{/each}
            </div>
          </details>
          <details class="migration-section">
            <summary
              ><span>Volumes, workers et tâches planifiées</span><small
                >{selected.plan.volumes.length} volume(s) · {selected.plan.workers.length} worker(s) · {selected.plan
                  .scheduledTasks.length} tâche(s)</small
              ></summary
            >
            <div class="migration-body">
              {#each selected.plan.volumes as volume}<p>
                  {appName(selected, volume.appTargetId)} · <code>{volume.name}</code><span class="web-muted">
                    · {resourceFormatBytes(volume.bytes)}</span
                  >
                </p>{/each}{#each selected.plan.workers as worker}<p>
                  {appName(selected, worker.appTargetId)} · <code>{worker.service}</code> · conservé sans démarrage automatique
                </p>{/each}{#each selected.plan.scheduledTasks as task}<p>
                  <strong>{task.name}</strong> · <code>{task.schedule}</code> · {task.timezone === 'Indian/Reunion'
                    ? 'La Réunion'
                    : 'UTC'}<span class="web-muted"> · désactivée sur la copie</span>
                </p>{/each}{#if !selected.plan.volumes.length && !selected.plan.workers.length && !selected.plan.scheduledTasks.length}<p
                  class="web-help"
                >
                  Aucun élément supplémentaire dans ce plan.
                </p>{/if}
            </div>
          </details>
          {#if migrationExpired(selected, now)}<p class="web-alert web-alert--error" role="alert">
              Le délai d’autorisation de ce plan est expiré. L’opérateur doit préparer une révision actuelle avant
              l’import.
            </p>{/if}
          {#if selected.status === 'review'}
            <form
              class="migration-consent"
              onsubmit={(event) => {
                event.preventDefault()
                void act('approve')
              }}
            >
              <h4>Autoriser une copie suspendue</h4>
              <p>Les identifiants, ressources et remappages affichés seront utilisés pour créer la copie cible.</p>
              <label class="web-field"
                ><span>Confirmer le nom exact : {selected.projectName}</span><input
                  type="text"
                  bind:value={confirmation}
                  autocomplete="off"
                  disabled={!!busy || !available}
                /></label
              ><button
                class="web-button web-button--primary"
                type="submit"
                disabled={refreshing ||
                  !!busy ||
                  !available ||
                  migrationExpired(selected, now) ||
                  confirmation !== selected.projectName}
                >{busy === 'approve' ? 'Enregistrement…' : 'Autoriser l’import suspendu'}</button
              >
            </form>
          {:else if selected.status === 'approved' || selected.status === 'importing'}<p
              class="migration-next"
              role="status"
            >
              {selected.status === 'approved'
                ? 'L’import suspendu est autorisé. L’opérateur peut lancer la copie et la recette de cette révision.'
                : 'La copie et ses vérifications sont en cours. Cet écran actualise l’état automatiquement.'}
            </p>
          {:else if selected.status === 'imported_paused'}
            <form
              class="migration-consent"
              onsubmit={(event) => {
                event.preventDefault()
                void act('release')
              }}
            >
              <h4>Déverrouiller pour la recette finale</h4>
              <p>Les ressources cibles pourront être démarrées depuis leurs fiches après cet accord.</p>
              <label class="web-check"
                ><input type="checkbox" bind:checked={keepDeliveryDisabled} disabled={!!busy || !available} />Conserver
                les e-mails et tâches planifiées désactivés pendant la recette.</label
              >{#if selected.plan.workers.length}<label class="web-check"
                  ><input type="checkbox" bind:checked={sourceWorkersPaused} disabled={!!busy || !available} />Les
                  workers d’origine sont suspendus pour éviter leur exécution en double.</label
                >{/if}<label class="web-field"
                ><span>Confirmer le nom exact : {selected.projectName}</span><input
                  type="text"
                  bind:value={confirmation}
                  autocomplete="off"
                  disabled={!!busy || !available}
                /></label
              ><button
                class="web-button web-button--primary"
                type="submit"
                disabled={refreshing ||
                  !!busy ||
                  !available ||
                  !!selected.plan.blockers.length ||
                  !migrationDrillVerified(selected) ||
                  !keepDeliveryDisabled ||
                  (selected.plan.workers.length > 0 && !sourceWorkersPaused) ||
                  confirmation !== selected.projectName}
                >{busy === 'release' ? 'Enregistrement…' : 'Déverrouiller la copie'}</button
              >
              {#if !migrationDrillVerified(selected)}<p class="web-alert web-alert--error" role="alert">
                  La recette complète de cette capsule reste à vérifier avant le déverrouillage.
                </p>{/if}
            </form>
          {:else if selected.status === 'released'}
            <section class="migration-consent">
              <h4>
                {selected.plan.domains.length ? 'Préparer la bascule Cloudflare' : 'Vérifier les nouvelles adresses'}
              </h4>
              {#if selected.plan.domains.length}<p>
                  Vérifiez les ressources cibles et préparez l’aperçu DNS avec l’opérateur. Les domaines de ce plan :
                </p>
                <div class="migration-domains">
                  {#each selected.plan.domains as domain}<span
                      >{domain.host}<small>Proxy activé sur la cible</small></span
                    >{/each}
                </div>{:else}<p class="web-help">
                  Ce plan ne contient aucun domaine personnalisé. La recette peut utiliser les nouvelles adresses
                  générées des applications.
                </p>{/if}
              {#if migrationHasWriteBarrier(selected)}<p class="web-help">
                  Le gel des écritures, crons et workers source a été déclaré par l’opérateur avant cette capture, le {stamp(
                    selected.plan.writeBarrier?.attestedAt
                  )}. Cette déclaration doit être confirmée par votre vérification métier.
                </p>{:else if selected.plan.domains.length}<p class="web-alert web-alert--error" role="alert">
                  Cette capsule a été préparée pour la recette. Une nouvelle capture après gel déclaré des écritures,
                  crons et workers source est requise pour autoriser la bascule.
                </p>{/if}
              {#if selected.dnsPreview}
                <div class="web-row">
                  <strong>Changements DNS à approuver</strong><span class="web-muted"
                    >Valable jusqu’au {stamp(selected.dnsPreview.expiresAt)}</span
                  >
                </div>
                <div class="web-table-wrap">
                  <table>
                    <thead
                      ><tr><th scope="col">Domaine</th><th scope="col">Avant</th><th scope="col">Après</th></tr></thead
                    ><tbody
                      >{#each selected.dnsPreview.changes as change}<tr
                          ><td>{change.host}</td><td
                            >{#each change.before as record}<span class="migration-dns-record"
                                >{recordText(record)}</span
                              >{:else}<span class="web-muted">Aucune origine enregistrée</span>{/each}</td
                          ><td>{recordText(change.after)}</td></tr
                        >{/each}</tbody
                    >
                  </table>
                </div>
                {#if !migrationDnsReviewable(selected, now)}<p class="web-alert web-alert--error" role="alert">
                    Cet aperçu DNS est expiré ou correspond à une autre révision. Préparez un nouvel aperçu.
                  </p>{:else if dnsApproved(selected)}<p class="web-alert web-alert--success" role="status">
                    Cet aperçu est autorisé. L’opérateur peut appliquer sa révision avant le {stamp(
                      selected.dnsPreview.expiresAt
                    )}.
                  </p>{:else}
                  <form
                    class="migration-dns-approval"
                    onsubmit={(event) => {
                      event.preventDefault()
                      void act('dns-approve')
                    }}
                  >
                    <label class="web-check"
                      ><input type="checkbox" bind:checked={sourceWritesPaused} disabled={!!busy || !available} />Les
                      écritures sont suspendues sur toutes les sources concernées.</label
                    ><label class="web-check"
                      ><input type="checkbox" bind:checked={sourceCronsPaused} disabled={!!busy || !available} />Les
                      tâches planifiées d’origine sont suspendues.</label
                    ><label class="web-check"
                      ><input type="checkbox" bind:checked={targetVerified} disabled={!!busy || !available} />Les bases,
                      sites et parcours nécessaires ont été vérifiés sur la cible.</label
                    ><label class="web-check"
                      ><input
                        type="checkbox"
                        bind:checked={nonAtomicAccepted}
                        disabled={!!busy || !available}
                      />J’accepte qu’un retour DNS ne fusionne pas les nouvelles écritures de la cible.</label
                    ><label class="web-field"
                      ><span>Confirmer le nom exact : {selected.projectName}</span><input
                        type="text"
                        bind:value={confirmation}
                        autocomplete="off"
                        disabled={!!busy || !available}
                      /></label
                    ><button
                      class="web-button web-button--primary"
                      type="submit"
                      disabled={refreshing ||
                        !!busy ||
                        !available ||
                        !!selected.plan.blockers.length ||
                        !migrationHasWriteBarrier(selected) ||
                        confirmation !== selected.projectName ||
                        !sourceWritesPaused ||
                        !sourceCronsPaused ||
                        !targetVerified ||
                        !nonAtomicAccepted}
                      >{busy === 'dns-approve' ? 'Enregistrement…' : 'Autoriser cet aperçu DNS'}</button
                    >
                  </form>
                {/if}
              {:else if selected.plan.domains.length}<p class="web-help">
                  L’aperçu doit présenter les valeurs avant et après la bascule pour pouvoir être approuvé ici. Sa
                  préparation reste à effectuer par l’opérateur.
                </p>{/if}
              {#if selected.dnsApprovedAt}<p class="web-help">
                  Dernier accord DNS enregistré le {stamp(selected.dnsApprovedAt)}. Révision
                  <code>{selected.dnsRevision.slice(0, 16)}…</code>.
                </p>{/if}
            </section>
          {:else if selected.status === 'cutover'}<p class="migration-next">
              La bascule DNS a été enregistrée. Vérifiez les accès publics avant de réactiver explicitement les envois,
              workers et tâches planifiées nécessaires.
            </p>
          {:else if ['failed', 'rolling_back', 'rolled_back'].includes(selected.status)}<p class="migration-next">
              {selected.status === 'rolled_back'
                ? 'La copie a été retirée. Le projet source est conservé.'
                : selected.status === 'rolling_back'
                  ? 'L’opérateur retire uniquement les copies encore suspendues et intactes.'
                  : 'L’import a été interrompu. L’opérateur peut vérifier son journal et retirer les copies intactes avant une nouvelle tentative.'}
            </p>{/if}
          <details class="migration-operator">
            <summary>Révisions et suivi opérateur</summary>
            <div>
              <dl>
                <div>
                  <dt>Plan</dt>
                  <dd><code>{selected.id}</code></dd>
                </div>
                <div>
                  <dt>Révision approuvable</dt>
                  <dd><code>{selected.revision}</code></dd>
                </div>
                <div>
                  <dt>Archive de ce projet</dt>
                  <dd><code>{selected.plan.archiveSha256}</code></dd>
                </div>
                <div>
                  <dt>Propriétaire cible</dt>
                  <dd><code>{selected.plan.ownerId}</code></dd>
                </div>
              </dl>
              {#if selected.plan.drill}<p class="web-help">
                  Exécuteur de recette dédié : {selected.plan.drill.sandboxUid}. {selected.plan.drill.services} service(s)
                  couverts ; réseau extérieur {selected.plan.drill.noExternalNetwork ? 'désactivé' : 'à vérifier'},
                  nettoyage {selected.plan.drill.cleanedUp ? 'confirmé' : 'à vérifier'}.
                </p>{/if}
              {#if ownerId}<p class="web-help">
                  Compte actuellement connecté sur ce serveur : <code>{ownerId}</code>.
                </p>{/if}
              <p>
                Les commandes <code>import</code>, <code>rollback</code> et <code>dns-preview</code> de l’outil opérateur
                utilisent les fichiers privés correspondant à ces révisions. Les actions de cet écran valident le plan ; l’application
                du DNS est une opération distincte et journalisée.
              </p>
              {#each selected.plan.limitations as limitation}<p class="web-help">{limitation}</p>{/each}
            </div>
          </details>
        </article>
      {/if}
    </section>
  {/if}
</section>

<style>
  .project-migration {
    display: grid;
    gap: 1.2rem;
    min-width: 0;
  }
  h2,
  h3,
  h4,
  p {
    margin: 0;
  }
  h2 {
    font-size: 1.08rem;
    margin: 0.3rem 0 0.5rem;
  }
  h3 {
    font-size: 1rem;
  }
  h4 {
    font-size: 0.87rem;
  }
  header {
    align-items: flex-start;
  }
  .migration-route {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 1rem;
    padding: 1rem 0;
    border-block: 1px solid var(--web-border);
    color: var(--web-muted);
    font-size: 0.78rem;
  }
  .migration-route span {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .migration-route b {
    display: grid;
    place-items: center;
    width: 1.5rem;
    height: 1.5rem;
    border: 1px solid var(--web-border);
    border-radius: 50%;
    color: var(--web-text);
    font-size: 0.72rem;
  }
  .migration-route i {
    font-style: normal;
    color: var(--web-muted);
  }
  .migration-intro {
    font-size: 0.85rem;
    line-height: 1.65;
    color: var(--web-muted);
    max-width: 85ch;
  }
  .migration-section {
    display: block;
    min-width: 0;
    border: 1px solid var(--web-border);
    border-radius: 6px;
    background: var(--web-surface);
  }
  summary {
    cursor: pointer;
    line-height: 1.55;
  }
  summary small {
    color: var(--web-muted);
    font-size: 0.72rem;
  }
  .migration-section > summary {
    padding: 0.8rem 1rem;
    font-weight: 600;
  }
  .migration-section > summary small {
    display: block;
    margin-left: 1rem;
    font-weight: 400;
  }
  .migration-body {
    display: grid;
    gap: 1rem;
    padding: 1rem;
    min-width: 0;
  }
  .migration-source {
    display: grid;
    grid-template-columns: minmax(160px, 1fr) minmax(160px, 1fr) auto;
    gap: 0.85rem;
    align-items: end;
  }
  .migration-preview,
  .migration-plan {
    display: grid;
    gap: 1.2rem;
    padding: 1.1rem;
    border: 1px solid var(--web-border);
    border-radius: 6px;
    min-width: 0;
  }
  .migration-destination {
    display: inline-flex;
    gap: 0.8rem;
    align-items: center;
    color: var(--web-muted);
    font-size: 0.8rem;
  }
  .migration-plan h3 {
    margin-top: 0.5rem;
  }
  .migration-kpis {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(125px, 1fr));
    border: 1px solid var(--web-border);
    border-radius: 6px;
    overflow: hidden;
  }
  .migration-kpis > div {
    display: grid;
    gap: 0.35rem;
    padding: 0.8rem 1rem;
    border-right: 1px solid var(--web-border);
    min-width: 0;
  }
  .migration-kpis > div:last-child {
    border: 0;
  }
  .migration-kpis strong {
    font-size: 1.3rem;
    letter-spacing: -0.03em;
    font-variant-numeric: tabular-nums;
  }
  .migration-kpis span {
    font-size: 0.72rem;
    color: var(--web-muted);
  }
  .migration-resource-list {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
    gap: 0.6rem;
  }
  .migration-resource-list > div {
    display: grid;
    gap: 0.35rem;
    padding: 0.8rem;
    background: var(--web-surface-soft);
    border-radius: 5px;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .migration-resource-list span,
  .migration-resource-list small {
    font-size: 0.75rem;
    color: var(--web-muted);
  }
  .migration-blockers {
    display: grid;
    gap: 0.65rem;
    border: 1px solid var(--web-border);
    border-left: 3px solid var(--web-warning, #c99537);
    border-radius: 5px;
    padding: 0.9rem 1rem;
    background: var(--web-surface-soft);
    font-size: 0.81rem;
    line-height: 1.6;
  }
  .migration-blockers code {
    display: block;
    overflow-wrap: anywhere;
    font-size: 0.75rem;
    color: var(--web-muted);
  }
  .migration-incoming {
    display: grid;
    gap: 1.1rem;
    min-width: 0;
  }
  .migration-plan-picker {
    max-width: 100%;
  }
  .migration-empty {
    border: 1px dashed var(--web-border);
    border-radius: 6px;
    padding: 1.3rem;
    color: var(--web-muted);
    font-size: 0.85rem;
    line-height: 1.6;
  }
  .migration-scope {
    display: flex;
    flex-wrap: wrap;
    align-items: start;
    gap: 0.8rem;
    padding: 1rem;
    border-left: 3px solid var(--web-accent);
    background: var(--web-surface-soft);
    border-radius: 5px;
  }
  .migration-scope > div {
    flex: 1;
    min-width: 190px;
  }
  .migration-scope strong {
    font-size: 0.83rem;
  }
  .migration-scope p {
    margin-top: 0.4rem;
    font-size: 0.78rem;
    line-height: 1.6;
    color: var(--web-muted);
  }
  .migration-mappings {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 1rem;
  }
  .migration-mappings > div {
    min-width: 0;
  }
  .migration-mappings h4 {
    margin-bottom: 0.7rem;
  }
  .migration-mappings p {
    display: grid;
    gap: 0.3rem;
    padding: 0.6rem 0;
    border-top: 1px solid var(--web-border);
    font-size: 0.81rem;
    overflow-wrap: anywhere;
  }
  .migration-mappings span,
  .migration-mappings small {
    font-size: 0.74rem;
    color: var(--web-muted);
  }
  .migration-consent {
    display: grid;
    gap: 0.9rem;
    padding: 1rem;
    border: 1px solid var(--web-accent-border, var(--web-border));
    border-radius: 6px;
    background: var(--web-surface-soft);
    font-size: 0.82rem;
    line-height: 1.65;
  }
  .migration-consent .web-field {
    max-width: 520px;
  }
  .migration-consent button {
    justify-self: start;
  }
  .migration-consent .web-check,
  .migration-dns-approval .web-check {
    align-items: flex-start;
    gap: 0.65rem;
    line-height: 1.6;
  }
  .migration-consent input[type='checkbox'] {
    appearance: auto;
    width: 16px;
    height: 16px;
    flex: 0 0 auto;
    margin-top: 0.2rem;
  }
  .migration-next {
    padding: 1rem;
    border-left: 3px solid var(--web-accent);
    background: var(--web-surface-soft);
    border-radius: 5px;
    font-size: 0.84rem;
    line-height: 1.6;
  }
  .migration-dns-approval {
    display: grid;
    gap: 0.85rem;
  }
  .migration-domains {
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
  }
  .migration-domains > span {
    display: grid;
    gap: 0.2rem;
    padding: 0.6rem 0.8rem;
    border: 1px solid var(--web-border);
    border-radius: 5px;
    font-size: 0.8rem;
    overflow-wrap: anywhere;
  }
  .migration-domains small {
    font-size: 0.7rem;
    color: var(--web-muted);
  }
  .web-table-wrap {
    position: relative;
    min-width: 0;
    max-width: 100%;
  }
  .migration-dns-record {
    display: block;
  }
  .migration-operator {
    display: block;
    min-width: 0;
    border-top: 1px solid var(--web-border);
    padding-top: 0.8rem;
  }
  .migration-operator summary {
    font-size: 0.76rem;
    color: var(--web-muted);
  }
  .migration-operator > div {
    display: grid;
    gap: 0.8rem;
    margin-top: 1rem;
    font-size: 0.78rem;
    line-height: 1.65;
    min-width: 0;
  }
  .migration-operator dl {
    display: grid;
    gap: 0.7rem;
    margin: 0;
  }
  .migration-operator dt {
    color: var(--web-muted);
    font-size: 0.7rem;
  }
  .migration-operator dd {
    margin: 0.2rem 0 0;
    overflow-wrap: anywhere;
  }
  .migration-operator pre {
    margin: 0;
    padding: 0.9rem;
    background: var(--web-surface-soft);
    border: 1px solid var(--web-border);
    border-radius: 5px;
    overflow: auto;
    max-width: 100%;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font-size: 0.72rem;
  }
  .migration-operator code {
    overflow-wrap: anywhere;
  }
  @media (max-width: 900px) {
    .migration-source {
      grid-template-columns: 1fr 1fr;
    }
    .migration-source button {
      grid-column: 1/-1;
      justify-self: start;
    }
    .migration-kpis {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .migration-kpis > div:nth-child(2) {
      border-right: 0;
    }
    .migration-kpis > div:nth-child(-n + 2) {
      border-bottom: 1px solid var(--web-border);
    }
  }
  @media (max-width: 600px) {
    header {
      flex-direction: column;
      gap: 0.75rem;
    }
    .migration-source,
    .migration-mappings,
    .migration-resource-list {
      grid-template-columns: 1fr;
    }
    .migration-source button {
      grid-column: auto;
      justify-self: stretch;
    }
    .migration-preview,
    .migration-plan,
    .migration-consent {
      padding: 0.85rem;
    }
    .migration-route {
      gap: 0.65rem 0.8rem;
    }
    .migration-route i {
      display: none;
    }
    .migration-consent button {
      justify-self: stretch;
    }
    .migration-preview > .web-row,
    .migration-plan > .web-row {
      align-items: flex-start;
      flex-direction: column;
      gap: 0.65rem;
    }
    .migration-scope > div {
      min-width: 0;
      flex-basis: 100%;
    }
  }
</style>
