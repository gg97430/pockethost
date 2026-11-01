<script lang="ts">
  import { onMount } from 'svelte'
  import { webRequest, webError, webDate, webLabels, type WebGitHubHealth } from '$lib/webApps'
  import { githubManageUrl } from '$lib/webConsoleFlow'
  import { webGithubConnectionQuery } from '$lib/webGithubConnections'
  export let refreshKey = 0
  /** Connexion à vérifier ; vide ou 'default' = connexion principale. */
  export let connectionId = ''
  /** Affichage intégré à une carte de connexion (sans panneau ni titre). */
  export let compact = false
  /** Résultat de chaque vérification (badge de la carte). */
  export let onhealth: ((value: WebGitHubHealth | null, busy: boolean) => void) | undefined = undefined
  let health: WebGitHubHealth | null = null
  let busy = false
  let error = ''
  let disposed = false
  let initialized = false
  let observed = 0
  let observedConnection = ''
  let request = 0
  $: if (initialized && (observed !== refreshKey || observedConnection !== connectionId)) {
    const changed = observedConnection !== connectionId
    observed = refreshKey
    observedConnection = connectionId
    if (changed) {
      health = null
      ++request
      busy = false
    }
    void verify()
  }
  $: onhealth?.(health, busy)
  $: manageUrl = githubManageUrl(health?.manageUrl)
  const verify = async () => {
    if (busy || disposed) return
    const generation = ++request
    busy = true
    error = ''
    try {
      const value = await webRequest<WebGitHubHealth>('/github/health', {
        query: webGithubConnectionQuery(connectionId),
      })
      if (!disposed && generation === request) health = value
    } catch (e) {
      if (!disposed && generation === request) error = webError(e)
    } finally {
      if (generation === request) busy = false
    }
  }
  onMount(() => {
    initialized = true
    observed = refreshKey
    observedConnection = connectionId
    void verify()
    return () => {
      disposed = true
    }
  })
</script>

<section class="github-health" class:web-panel={!compact} class:github-health--compact={compact}>
  <div class="web-row">
    {#if compact}<span class="web-eyebrow">Santé de la connexion</span>{:else}<div>
        <span class="web-eyebrow">État de la source</span>
        <h2>Vérifier l’accès à votre code</h2>
      </div>{/if}
    <button class="web-button" onclick={verify} disabled={busy} type="button"
      >{busy ? 'Vérification…' : 'Vérifier maintenant'}</button
    >
  </div>
  {#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}
  {#if health}
    <div class="health-banner" data-ok={health.ok} role="status">
      <span class="health-dot" aria-hidden="true"></span>
      <div>
        <strong
          >{!health.configured
            ? 'GitHub attend votre connexion'
            : health.ok
              ? 'La connexion répond'
              : 'L’accès GitHub demande un contrôle'}</strong
        >
        <p>
          {health.error ||
            (!health.configured
              ? 'Commencez l’assistant de connexion, puis choisissez les dépôts à autoriser sur GitHub.'
              : health.ok
                ? 'Les droits et l’installation ont été vérifiés auprès de GitHub.'
                : 'Vérifiez les autorisations de l’installation et le compte propriétaire.')}
        </p>
      </div>
    </div>
    {#if health.disabled}<div class="web-alert" role="note">
        Connexion désactivée : les pushs et pull requests de cette GitHub App sont ignorés et ses applications ne
        peuvent pas être déployées.
      </div>{/if}
    {#if health.webhook?.urlMismatch}<div class="web-alert web-alert--error" role="alert" data-notice="off">
        <strong
          >L’URL du webhook configurée sur GitHub ne correspond pas à cette connexion : les pushs ne sont pas reçus.</strong
        >
        <p class="web-help">
          Attendue : <code>{health.webhook.url || '—'}</code>{#if health.webhook.configuredUrl}<br />Sur GitHub :
            <code>{health.webhook.configuredUrl}</code>{/if}
        </p>
      </div>{/if}
    {#if health.configured && health.ok && health.previewSupported === false}
      <div class="web-alert health-preview-warning" role="note">
        <strong>Les previews de pull requests attendent votre autorisation</strong>
        <p>
          Les déploiements sur push restent disponibles. Pour créer les previews, autorisez la lecture des pull requests
          : ouvrez l’installation sur GitHub, puis « Review request » et acceptez les nouveaux droits. Revenez ensuite
          cliquer sur « Vérifier maintenant ».
        </p>
        {#if manageUrl}<a class="web-button" href={manageUrl} target="_blank" rel="noopener noreferrer"
            >Vérifier les autorisations sur GitHub ↗</a
          >{/if}
      </div>
    {/if}
    {#if health.configured}
      <dl class="health-facts">
        <div>
          <dt>Compte GitHub</dt>
          <dd>{health.installation?.account || health.app?.owner || 'À vérifier'}</dd>
        </div>
        <div>
          <dt>Dépôts accessibles</dt>
          <dd>{health.repositoryCount == null ? 'À vérifier' : health.repositoryCount.toLocaleString('fr-FR')}</dd>
        </div>
        <div>
          <dt>Code source</dt>
          <dd>
            {health.permissions?.contents === 'read' ? 'Lecture seule' : health.permissions?.contents || 'À vérifier'}
          </dd>
        </div>
        <div>
          <dt>Installation</dt>
          <dd>{health.installation?.id || 'À vérifier'}</dd>
        </div>
      </dl>
      <div class="health-foot">
        <p class="web-help">
          Dernière vérification : {webDate(health.checkedAt)}{#if health.webhook?.lastReceivedAt}<br />Dernière
            notification : {webDate(health.webhook.lastReceivedAt)} · {webLabels[health.webhook.lastStatus || ''] ||
              health.webhook.lastStatus ||
              'reçue'}{/if}
        </p>
        {#if manageUrl}<a class="web-button" href={manageUrl} target="_blank" rel="noopener noreferrer"
            >Gérer les dépôts autorisés ↗</a
          >{/if}
      </div>
    {/if}
  {:else if busy}<p class="web-help" role="status">Contrôle du compte et de l’installation…</p>{/if}
</section>

<style>
  .github-health {
    display: grid;
    gap: 1rem;
  }
  .github-health--compact {
    gap: 0.75rem;
  }
  .github-health--compact .health-facts > div {
    padding: 0.3rem 0;
  }
  .health-banner {
    display: flex;
    align-items: start;
    gap: 0.7rem;
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius, 6px);
    padding: 1rem;
    background: var(--web-surface-soft);
  }
  .health-banner[data-ok='true'] {
    border-color: #79ac6a70;
    background: #79ac6a0a;
  }
  .health-dot {
    margin-top: 0.35rem;
    flex: 0 0 0.45rem;
    width: 0.45rem;
    height: 0.45rem;
    border-radius: 50%;
    background: #c7a35d;
  }
  .health-banner[data-ok='true'] .health-dot {
    background: #79ac6a;
  }
  .health-banner strong {
    font-size: 0.8125rem;
  }
  .health-banner p {
    font-size: 0.8rem;
    color: var(--web-muted);
    margin: 0.35rem 0 0;
    line-height: 1.6;
  }
  .health-preview-warning {
    display: grid;
    gap: 0.65rem;
    justify-items: start;
  }
  .health-preview-warning p {
    font-size: 0.82rem;
  }
  .health-facts {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 1rem;
    margin: 0;
  }
  .health-facts > div {
    display: grid;
    gap: 0.45rem;
    min-width: 0;
    padding: 0.8rem 0;
  }
  dt {
    font-size: 0.72rem;
    color: var(--web-muted);
  }
  dd {
    margin: 0;
    font-size: 0.875rem;
    font-weight: 550;
    overflow-wrap: anywhere;
  }
  .health-foot {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.8rem;
    padding-top: 0.7rem;
    border-top: 1px solid var(--web-border);
  }
  .health-foot p {
    margin: 0;
  }
  @media (max-width: 750px) {
    .health-facts {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
</style>
