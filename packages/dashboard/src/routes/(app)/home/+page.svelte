<script lang="ts">
  import { onMount } from 'svelte'
  import { client } from '$src/pocketbase-client'
  import { userStore } from '$util/stores'
  import type { WorkspaceOverview } from '../../../../../pockethost/src/common/workspaceOverview'

  let overview: WorkspaceOverview | null = null
  let loading = true
  let error = ''
  let mounted = false
  let identity = ''
  let request = 0
  const format = (n: number) => new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 1 }).format(n)
  const percent = (n: number | null) => (n === null ? '—' : `${format(n)} %`)
  const bytes = (n: number) => `${format(n / 1024 ** 3)} Go`
  const date = (value: string) =>
    new Date(value).toLocaleString('fr-FR', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
  const status: Record<string, string> = {
    ready: 'Réussi',
    failed: 'Échoué',
    cancelled: 'Annulé',
    queued: 'En attente',
    running: 'En cours',
  }
  $: counts = overview?.counts
  $: finished = (overview?.deployments24h.ready || 0) + (overview?.deployments24h.failed || 0)
  $: success = finished ? Math.round((overview!.deployments24h.ready / finished) * 100) : null
  $: attentionCount = (counts?.applicationsFailed || 0) + (counts?.databasesFailed || 0)
  $: userKey = $userStore ? `${$userStore.id}:${$userStore.superAdmin}:${$userStore.suspension}` : ''
  $: if (mounted && userKey !== identity) {
    identity = userKey
    overview = null
    if (userKey) void refresh()
  }

  async function refresh() {
    if (loading && overview) return
    const current = ++request
    loading = true
    error = ''
    try {
      const value = await client().client.send<WorkspaceOverview>('/api/workspace/overview', { requestKey: null })
      if (current === request) overview = value
    } catch (cause) {
      if (current === request) error = 'Le récapitulatif n’a pas pu être chargé. Réessayez dans un instant.'
    } finally {
      if (current === request) loading = false
    }
  }
  onMount(() => {
    mounted = true
    const timer = setInterval(() => {
      if (!document.hidden && !loading && identity) void refresh()
    }, 60000)
    return () => {
      clearInterval(timer)
      request++
      mounted = false
    }
  })
</script>

<svelte:head><title>Tableau de bord · Kolab Hosting</title></svelte:head>

<div class="overview-home" aria-busy={loading}>
  <header class="home-heading">
    <div>
      <p class="eyebrow">VOTRE ESPACE KOLAB</p>
      <h1>Tableau de bord</h1>
      <p class="intro">Vos bases, vos hébergements et ce qui mérite votre attention.</p>
    </div>
    <div class="heading-actions">
      <span class="updated"
        >{overview
          ? `Actualisé à ${new Date(overview.collectedAt).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`
          : 'Chargement du récapitulatif'}</span
      ><button type="button" class="secondary" onclick={refresh} disabled={loading}
        ><wa-icon name="arrows-rotate" aria-hidden="true"></wa-icon>{loading ? 'Actualisation…' : 'Actualiser'}</button
      >
    </div>
  </header>

  {#if error}<div role="alert" class="error-notice">
      {error}{#if overview}
        Les dernières données affichées peuvent être anciennes.{/if}
    </div>{/if}

  {#if overview && counts}
    <section class="status-banner" class:needs-attention={attentionCount > 0} aria-label="Situation actuelle">
      <span class="signal"
        ><wa-icon name={attentionCount ? 'triangle-exclamation' : 'circle-check'} aria-hidden="true"></wa-icon></span
      >
      <div>
        <h2>
          {attentionCount
            ? `${attentionCount} élément${attentionCount > 1 ? 's' : ''} à vérifier`
            : 'Aucune erreur remontée'}
        </h2>
        <p>
          {attentionCount
            ? 'Consultez les éléments signalés ci-dessous pour retrouver leurs journaux.'
            : 'Aucune base en échec ni erreur de déploiement signalée sur vos hébergements.'}
        </p>
      </div>
      <span class="scope">{$userStore?.superAdmin ? 'Toute la plateforme' : 'Vos ressources accessibles'}</span>
    </section>

    <section class="kpis" aria-label="Indicateurs clés">
      <a class="kpi" href="/dashboard"
        ><span class="kpi-top"><span>Bases de données</span><wa-icon name="database" aria-hidden="true"></wa-icon></span
        ><strong>{format(counts.databases)}</strong><span class="kpi-foot"
          ><i class="dot good"></i>{counts.databasesRunning} actives
          <span class="muted">· {counts.databasesOff} éteintes</span></span
        ></a
      >
      <a class="kpi" href="/applications"
        ><span class="kpi-top"><span>Hébergements</span><wa-icon name="layer-group" aria-hidden="true"></wa-icon></span
        ><strong>{format(counts.applications)}</strong><span class="kpi-foot"
          >{counts.applicationsRunning} actifs <span class="muted">· {counts.projects} projets</span></span
        ></a
      >
      <a class="kpi" class:warning={counts.projectsWithErrors > 0} href="#attention"
        ><span class="kpi-top"><span>Projets à vérifier</span><wa-icon name="flag" aria-hidden="true"></wa-icon></span
        ><strong>{format(counts.projectsWithErrors)}</strong><span class="kpi-foot"
          >{counts.applicationsFailed} hébergement{counts.applicationsFailed > 1 ? 's' : ''} signalé{counts.applicationsFailed >
          1
            ? 's'
            : ''}</span
        ></a
      >
      <a class="kpi" href="#deployments"
        ><span class="kpi-top"
          ><span>Déploiements en cours</span><wa-icon name="rocket" aria-hidden="true"></wa-icon></span
        ><strong>{format(counts.deploymentsInProgress)}</strong><span class="kpi-foot"
          >{counts.deploymentsQueued} en attente <span class="muted">· file de construction</span></span
        ></a
      >
    </section>

    <div class="home-columns">
      <section class="panel deployments" id="deployments" aria-labelledby="deployments-title">
        <div class="panel-heading">
          <div>
            <p class="eyebrow">ACTIVITÉ RÉCENTE</p>
            <h2 id="deployments-title">Derniers déploiements</h2>
          </div>
          <a class="text-link" href="/applications">Les hébergements <span aria-hidden="true">↗</span></a>
        </div>
        {#if overview.recentDeployments.length}
          <div class="table-wrap">
            <table>
              <thead
                ><tr><th>Hébergement</th><th>État</th><th>Date</th><th><span class="sr-only">Ouvrir</span></th></tr
                ></thead
              ><tbody>
                {#each overview.recentDeployments as deployment (deployment.id)}
                  <tr
                    ><td
                      ><a class="app-name" href={`/applications/${deployment.appId}`}>{deployment.appName}</a><span
                        class="commit">{deployment.commit ? deployment.commit.slice(0, 7) : 'Déploiement manuel'}</span
                      ></td
                    ><td
                      ><span
                        class="badge"
                        class:good={deployment.status === 'ready'}
                        class:bad={deployment.status === 'failed'}
                        class:pending={['running', 'queued'].includes(deployment.status)}
                        ><i class="dot"></i>{status[deployment.status] || deployment.status}</span
                      ></td
                    ><td class="date"><time datetime={deployment.created}>{date(deployment.created)}</time></td><td
                      ><a
                        class="row-link"
                        href={`/applications/${deployment.appId}`}
                        aria-label={`Voir les déploiements de ${deployment.appName}`}
                        ><wa-icon name="arrow-right" aria-hidden="true"></wa-icon></a
                      ></td
                    ></tr
                  >
                {/each}
              </tbody>
            </table>
          </div>
        {:else}<div class="empty">
            <wa-icon name="rocket" aria-hidden="true"></wa-icon>
            <h3>Aucun déploiement pour le moment</h3>
            <p>Les prochains déploiements de vos hébergements apparaîtront ici.</p>
          </div>{/if}
      </section>

      <aside class="right-column">
        <section class="panel day-summary" aria-labelledby="day-title">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">LES DERNIÈRES 24 HEURES</p>
              <h2 id="day-title">Bilan des déploiements</h2>
            </div>
          </div>
          <div class="success-number">
            <strong>{success === null ? '—' : `${success} %`}</strong><span
              >{success === null
                ? 'Aucun résultat sur cette période'
                : 'de réussite sur les déploiements terminés'}</span
            >
          </div>
          {#if finished}<div
              class="outcome-bar"
              role="img"
              aria-label={`${overview.deployments24h.ready} réussis et ${overview.deployments24h.failed} échoués`}
            >
              <span style={`width:${success}%`}></span>
            </div>{/if}
          <div class="outcomes">
            <div><i class="dot good"></i><strong>{overview.deployments24h.ready}</strong> réussis</div>
            <div><i class="dot bad"></i><strong>{overview.deployments24h.failed}</strong> échoués</div>
            <div><i class="dot"></i><strong>{overview.deployments24h.cancelled}</strong> annulés</div>
          </div>
          <p class="small-note">
            {counts.autoDeploy} hébergement{counts.autoDeploy > 1 ? 's' : ''} avec déploiement automatique activé.
          </p>
        </section>
        <section class="panel quick-actions" aria-labelledby="quick-title">
          <div class="panel-heading"><h2 id="quick-title">Accès rapides</h2></div>
          {#if overview.canCreateBase}<a href="/instances/new"
              ><wa-icon name="plus" aria-hidden="true"></wa-icon>Créer une base<span aria-hidden="true">↗</span></a
            >{/if}
          {#if overview.canCreateApp}<a href="/applications"
              ><wa-icon name="rocket" aria-hidden="true"></wa-icon>Déployer un hébergement<span aria-hidden="true"
                >↗</span
              ></a
            >{/if}
          <a href="/dashboard"
            ><wa-icon name="database" aria-hidden="true"></wa-icon>Gérer mes bases<span aria-hidden="true">↗</span></a
          >
          {#if overview.webEnabled && (counts.applications || overview.canCreateApp)}<a href="/applications"
              ><wa-icon name="layer-group" aria-hidden="true"></wa-icon>Gérer mes hébergements<span aria-hidden="true"
                >↗</span
              ></a
            >{/if}
          {#if $userStore?.superAdmin}<a href="/monitoring"
              ><wa-icon name="chart-line" aria-hidden="true"></wa-icon>Supervision complète<span aria-hidden="true"
                >↗</span
              ></a
            >{/if}
        </section>
      </aside>
    </div>

    <section class="panel attention" id="attention" aria-labelledby="attention-title">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">À TRAITER</p>
          <h2 id="attention-title">Points d’attention <span class="count">{attentionCount}</span></h2>
        </div>
      </div>
      {#if overview.attention.length}<div class="attention-list">
          {#each overview.attention as item (`${item.kind}:${item.id}`)}<a
              href={item.kind === 'database' ? `/instances/${item.id}/logs` : `/applications/${item.id}`}
              ><span class="issue-icon"
                ><wa-icon name={item.kind === 'database' ? 'database' : 'layer-group'} aria-hidden="true"
                ></wa-icon></span
              >
              <div>
                <strong>{item.name}</strong>
                <p>
                  {item.kind === 'database'
                    ? 'Base en échec · consulter les journaux'
                    : item.servingPreviousVersion
                      ? 'Dernier déploiement en échec · version précédente toujours en ligne'
                      : 'Hébergement ou déploiement en erreur · consulter les journaux'}
                </p>
              </div>
              <wa-icon name="arrow-right" aria-hidden="true"></wa-icon></a
            >{/each}
        </div>
        {#if attentionCount > overview.attention.length}<p class="small-note">
            Les éléments les plus récemment modifiés sont affichés. Consultez les listes complètes pour les autres
            ressources.
          </p>{/if}
      {:else}<p class="all-clear">
          <wa-icon name="check" aria-hidden="true"></wa-icon>Rien à traiter ici pour le moment. Les ressources arrêtées
          volontairement ne sont pas considérées en erreur.
        </p>{/if}
    </section>

    {#if $userStore?.superAdmin}
      <section class="panel server" aria-labelledby="server-title">
        <div class="panel-heading">
          <div>
            <p class="eyebrow">RESSOURCES DU SERVEUR</p>
            <h2 id="server-title">Capacité globale</h2>
          </div>
          <a class="text-link" href="/monitoring">Détails et graphes <span aria-hidden="true">↗</span></a>
        </div>
        {#if overview.resources}{@const host = overview.resources.host}
          <div class="resource-grid">
            {#each [{ name: 'CPU', value: percent(host.cpuPercent), percent: host.cpuPercent, detail: `${host.cpuCount} cœurs` }, { name: 'Mémoire', value: bytes(host.memoryUsedBytes), percent: host.memoryTotalBytes > 0 ? (host.memoryUsedBytes / host.memoryTotalBytes) * 100 : null, detail: `sur ${bytes(host.memoryTotalBytes)}` }, { name: 'Disque', value: bytes(host.diskUsedBytes), percent: host.diskTotalBytes > 0 ? (host.diskUsedBytes / host.diskTotalBytes) * 100 : null, detail: `${bytes(host.diskAvailableBytes)} disponibles` }] as resource}<div
              >
                <div class="resource-top"><span>{resource.name}</span><strong>{resource.value}</strong></div>
                <div class="meter" role="img" aria-label={`${resource.name} : ${percent(resource.percent)}`}>
                  <span
                    class:high={(resource.percent || 0) > 85}
                    style={`width:${Math.max(0, Math.min(100, resource.percent || 0))}%`}
                  ></span>
                </div>
                <p>{resource.detail}</p>
              </div>{/each}
          </div>
          <p class="small-note" class:stale={overview.resources.stale}>
            {overview.resources.stale ? 'Mesures anciennes · ' : 'Dernière mesure · '}{date(
              overview.resources.collectedAt
            )}. Les compteurs ci-dessus sont actualisés indépendamment.
          </p>
        {:else}<p class="small-note">
            Les mesures du serveur ne sont pas encore disponibles. <a href="/monitoring">Ouvrir la supervision</a>
          </p>{/if}
      </section>
    {/if}
  {:else if loading}<div class="loading-state" role="status">
      <wa-icon name="chart-simple" aria-hidden="true"></wa-icon>Chargement de vos indicateurs…
    </div>
  {/if}
</div>

<style>
  .overview-home {
    --home-green: light-dark(#13734a, #6ed7a7);
    --home-red: light-dark(#b63038, #ff8b91);
    --home-amber: light-dark(#98620b, #eac070);
    display: grid;
    gap: 1.35rem;
    color: var(--app-text);
  }
  h1,
  h2,
  h3,
  p {
    margin: 0;
  }
  a {
    text-decoration: none;
  }
  button,
  a {
    -webkit-tap-highlight-color: transparent;
  }
  .home-heading {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 1rem;
    padding: 0.25rem 0 0.75rem;
  }
  .eyebrow {
    font-size: 0.625rem;
    font-weight: 650;
    letter-spacing: 0.13em;
    color: var(--app-text-faint);
    margin-bottom: 0.45rem;
  }
  h1 {
    font-size: 1.7rem;
    font-weight: 650;
    letter-spacing: -0.04em;
    color: var(--app-text-strong);
  }
  .intro {
    color: var(--app-text-muted);
    font-size: 0.8rem;
    line-height: 1.6;
    margin-top: 0.35rem;
  }
  .heading-actions {
    display: flex;
    align-items: center;
    gap: 0.8rem;
  }
  .updated {
    font-size: 0.65rem;
    color: var(--app-text-faint);
  }
  .secondary {
    display: inline-flex;
    gap: 0.5rem;
    align-items: center;
    padding: 0.55rem 0.75rem;
    font-size: 0.7rem;
    border: 1px solid var(--app-border);
    border-radius: 0.4rem;
    background: var(--app-surface);
    color: var(--app-text);
    cursor: pointer;
  }
  .secondary:disabled {
    opacity: 0.6;
    cursor: wait;
  }
  .status-banner {
    display: flex;
    gap: 0.8rem;
    align-items: center;
    border: 1px solid color-mix(in srgb, var(--home-green) 25%, var(--app-border));
    background: color-mix(in srgb, var(--home-green) 4%, var(--app-surface));
    padding: 1rem 1.2rem;
    border-radius: 0.6rem;
  }
  .status-banner h2 {
    font-size: 0.8rem;
    font-weight: 600;
  }
  .status-banner p {
    font-size: 0.72rem;
    color: var(--app-text-muted);
    margin-top: 0.2rem;
    line-height: 1.5;
  }
  .signal {
    font-size: 1.2rem;
    color: var(--home-green);
  }
  .needs-attention {
    border-color: color-mix(in srgb, var(--home-amber) 30%, var(--app-border));
    background: color-mix(in srgb, var(--home-amber) 5%, var(--app-surface));
  }
  .needs-attention .signal {
    color: var(--home-amber);
  }
  .scope {
    margin-left: auto;
    flex-shrink: 0;
    font-size: 0.6rem;
    color: var(--app-text-faint);
    border: 1px solid var(--app-border);
    background: var(--app-surface);
    border-radius: 2rem;
    padding: 0.3rem 0.6rem;
  }
  .kpis {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 1rem;
  }
  .kpi {
    background: var(--app-surface);
    border: 1px solid var(--app-border);
    border-radius: 0.6rem;
    padding: 1.1rem 1.2rem;
    color: var(--app-text);
    display: flex;
    flex-direction: column;
    gap: 0.8rem;
    transition: border-color 0.15s;
  }
  .kpi:hover {
    border-color: var(--app-accent);
  }
  .kpi-top {
    display: flex;
    justify-content: space-between;
    gap: 0.5rem;
    font-size: 0.72rem;
    color: var(--app-text-muted);
  }
  .kpi-top wa-icon {
    color: var(--app-text-faint);
  }
  .kpi strong {
    font-size: 2.1rem;
    font-weight: 600;
    letter-spacing: -0.04em;
    line-height: 1;
    color: var(--app-text-strong);
    font-variant-numeric: tabular-nums;
  }
  .kpi-foot {
    font-size: 0.65rem;
    display: flex;
    align-items: center;
    gap: 0.3rem;
    flex-wrap: wrap;
  }
  .warning strong {
    color: var(--home-amber);
  }
  .muted {
    color: var(--app-text-faint);
  }
  .home-columns {
    display: grid;
    grid-template-columns: minmax(0, 1.9fr) minmax(16rem, 1fr);
    gap: 1.35rem;
    align-items: start;
  }
  .panel {
    background: var(--app-surface);
    border: 1px solid var(--app-border);
    border-radius: 0.6rem;
    overflow: hidden;
    scroll-margin-top: 5rem;
  }
  .panel-heading {
    padding: 1.15rem 1.2rem;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 0.75rem;
  }
  h2 {
    font-size: 0.9rem;
    font-weight: 600;
    letter-spacing: -0.015em;
    color: var(--app-text-strong);
  }
  .text-link {
    font-size: 0.65rem;
    color: var(--app-accent);
    white-space: nowrap;
  }
  .text-link span {
    margin-left: 0.3rem;
  }
  .right-column {
    display: grid;
    gap: 1.35rem;
  }
  .table-wrap {
    overflow: auto;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    text-align: left;
    font-size: 0.72rem;
  }
  th {
    background: var(--app-surface-soft);
    color: var(--app-text-faint);
    font-weight: 500;
    font-size: 0.6rem;
    padding: 0.65rem 1.2rem;
    border-block: 1px solid var(--app-border);
  }
  td {
    padding: 0.8rem 1.2rem;
    border-bottom: 1px solid var(--app-border);
  }
  tr:last-child td {
    border-bottom: 0;
  }
  .app-name {
    display: block;
    color: var(--app-text-strong);
    font-weight: 550;
    overflow-wrap: anywhere;
  }
  .app-name:hover {
    color: var(--app-accent);
  }
  .commit {
    display: block;
    font:
      0.6rem ui-monospace,
      monospace;
    color: var(--app-text-faint);
    margin-top: 0.3rem;
  }
  .date {
    color: var(--app-text-muted);
    white-space: nowrap;
    font-size: 0.65rem;
  }
  .row-link {
    color: var(--app-text-faint);
  }
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.25rem 0.45rem;
    border-radius: 0.3rem;
    background: var(--app-surface-soft);
    color: var(--app-text-muted);
    font-size: 0.6rem;
    white-space: nowrap;
  }
  .badge.good {
    color: var(--home-green);
    background: color-mix(in srgb, var(--home-green) 8%, transparent);
  }
  .badge.bad {
    color: var(--home-red);
    background: color-mix(in srgb, var(--home-red) 8%, transparent);
  }
  .badge.pending {
    color: var(--home-amber);
    background: color-mix(in srgb, var(--home-amber) 8%, transparent);
  }
  .dot {
    display: inline-block;
    width: 0.35rem;
    height: 0.35rem;
    border-radius: 50%;
    background: currentColor;
    flex-shrink: 0;
  }
  .dot.good {
    color: var(--home-green);
  }
  .dot.bad {
    color: var(--home-red);
  }
  .success-number {
    padding: 0.2rem 1.2rem 0.8rem;
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
  }
  .success-number strong {
    font-size: 2rem;
    font-weight: 600;
    letter-spacing: -0.04em;
    font-variant-numeric: tabular-nums;
    color: var(--app-text-strong);
  }
  .success-number span {
    font-size: 0.65rem;
    color: var(--app-text-muted);
  }
  .outcome-bar {
    margin: 0 1.2rem;
    height: 0.45rem;
    border-radius: 1rem;
    background: var(--home-red);
    overflow: hidden;
  }
  .outcome-bar span {
    display: block;
    height: 100%;
    background: var(--home-green);
  }
  .outcomes {
    display: flex;
    gap: 1rem;
    flex-wrap: wrap;
    padding: 1rem 1.2rem;
    font-size: 0.65rem;
    color: var(--app-text-muted);
  }
  .outcomes div {
    display: flex;
    align-items: center;
    gap: 0.3rem;
  }
  .outcomes strong {
    color: var(--app-text-strong);
  }
  .small-note {
    padding: 0 1.2rem 1rem;
    color: var(--app-text-faint);
    font-size: 0.65rem;
    line-height: 1.65;
  }
  .small-note a {
    color: var(--app-accent);
  }
  .quick-actions > a {
    display: flex;
    align-items: center;
    gap: 0.7rem;
    padding: 0.8rem 1.2rem;
    border-top: 1px solid var(--app-border);
    font-size: 0.72rem;
    color: var(--app-text);
  }
  .quick-actions > a:hover {
    background: var(--app-surface-hover);
  }
  .quick-actions > a > span {
    margin-left: auto;
    color: var(--app-text-faint);
  }
  .quick-actions wa-icon {
    color: var(--app-text-faint);
  }
  .count {
    padding: 0.1rem 0.4rem;
    margin-left: 0.4rem;
    border: 1px solid var(--app-border);
    background: var(--app-surface-soft);
    border-radius: 0.3rem;
    font-size: 0.65rem;
  }
  .attention-list > a {
    display: flex;
    align-items: center;
    gap: 0.8rem;
    padding: 0.9rem 1.2rem;
    border-top: 1px solid var(--app-border);
    color: var(--app-text);
  }
  .attention-list > a:hover {
    background: var(--app-surface-hover);
  }
  .attention-list strong {
    font-size: 0.75rem;
    font-weight: 550;
  }
  .attention-list p {
    margin-top: 0.25rem;
    color: var(--app-text-muted);
    font-size: 0.65rem;
    line-height: 1.5;
  }
  .attention-list > a > wa-icon {
    margin-left: auto;
    color: var(--app-text-faint);
  }
  .issue-icon {
    padding: 0.5rem;
    border-radius: 0.35rem;
    background: color-mix(in srgb, var(--home-amber) 9%, transparent);
    color: var(--home-amber);
  }
  .all-clear {
    padding: 0.1rem 1.2rem 1.2rem;
    display: flex;
    align-items: center;
    gap: 0.6rem;
    font-size: 0.72rem;
    color: var(--app-text-muted);
    line-height: 1.6;
  }
  .all-clear wa-icon {
    color: var(--home-green);
  }
  .resource-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 2rem;
    padding: 0.2rem 1.2rem 1rem;
  }
  .resource-top {
    display: flex;
    justify-content: space-between;
    gap: 0.5rem;
    font-size: 0.72rem;
  }
  .resource-top span {
    color: var(--app-text-muted);
  }
  .resource-top strong {
    font-weight: 550;
  }
  .meter {
    height: 0.3rem;
    background: var(--app-surface-soft);
    border-radius: 0.5rem;
    margin: 0.7rem 0 0.45rem;
    overflow: hidden;
  }
  .meter span {
    display: block;
    height: 100%;
    background: var(--app-accent);
  }
  .meter .high {
    background: var(--home-amber);
  }
  .resource-grid p {
    font-size: 0.65rem;
    color: var(--app-text-faint);
  }
  .stale {
    color: var(--home-amber);
  }
  .empty,
  .loading-state {
    min-height: 14rem;
    padding: 2rem;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    gap: 0.7rem;
    text-align: center;
    color: var(--app-text-faint);
    font-size: 0.8rem;
  }
  .empty wa-icon,
  .loading-state wa-icon {
    font-size: 1.5rem;
  }
  .empty h3 {
    font-size: 0.85rem;
    font-weight: 550;
    color: var(--app-text-muted);
  }
  .empty p {
    font-size: 0.7rem;
    max-width: 22rem;
    line-height: 1.6;
  }
  .error-notice {
    padding: 1rem;
    border: 1px solid var(--home-red);
    color: var(--home-red);
    border-radius: 0.4rem;
    font-size: 0.8rem;
  }
  a:focus-visible,
  button:focus-visible {
    outline: 2px solid var(--app-accent);
    outline-offset: 3px;
  }
  @media (max-width: 1100px) {
    .home-columns {
      grid-template-columns: minmax(0, 1.6fr) minmax(15rem, 1fr);
    }
    td,
    th {
      padding-inline: 0.8rem;
    }
    .kpi {
      padding: 1rem;
    }
    .kpi-foot {
      font-size: 0.6rem;
    }
  }
  @media (max-width: 900px) {
    .kpis {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .home-columns {
      grid-template-columns: 1fr;
    }
    .right-column {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .scope {
      display: none;
    }
    .heading-actions {
      align-items: flex-end;
      flex-direction: column;
      gap: 0.4rem;
    }
  }
  @media (max-width: 540px) {
    .home-heading {
      align-items: flex-start;
      flex-direction: column;
    }
    .heading-actions {
      width: 100%;
      flex-direction: row;
      align-items: center;
      justify-content: space-between;
    }
    .right-column {
      grid-template-columns: 1fr;
    }
    .resource-grid {
      grid-template-columns: 1fr;
      gap: 1rem;
    }
    .kpis {
      gap: 0.6rem;
    }
    .kpi {
      padding: 0.9rem;
    }
    .kpi strong {
      font-size: 1.8rem;
    }
    .kpi-top {
      font-size: 0.65rem;
    }
    .date {
      white-space: normal;
    }
    .panel-heading {
      align-items: flex-start;
    }
    .text-link {
      font-size: 0.6rem;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .kpi {
      transition: none;
    }
  }
</style>
