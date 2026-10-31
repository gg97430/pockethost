<script lang="ts">
  import WebGitHubHealth from './WebGitHubHealth.svelte'
  import {
    webError,
    webGithubConnectionDelete,
    webGithubConnectionUpdate,
    type WebGitHubConnectionDeleted,
    type WebGitHubConnectionInput,
    type WebGitHubConnectionSummary,
    type WebGitHubHealth as WebGitHubHealthValue,
  } from '$lib/webApps'
  import {
    WEB_GITHUB_LABEL_MAX,
    webGithubAppCountLabel,
    webGithubConnectionAnchor,
    webGithubConnectionLabel,
    webGithubConnectionStatus,
    webGithubDeliveryLabel,
    webGithubLabelError,
  } from '$lib/webGithubConnections'

  export let connection: WebGitHubConnectionSummary
  export let refreshKey = 0
  /** Un assistant GitHub est en cours d'ouverture (désactive « Recréer la GitHub App »). */
  export let connecting = false
  /** Liste rechargée après un renommage, une (dés)activation ou une saisie manuelle. */
  export let onchanged: ((message: string) => void) | undefined = undefined
  export let ondeleted:
    | ((result: WebGitHubConnectionDeleted, connection: WebGitHubConnectionSummary) => void)
    | undefined = undefined
  /** Remplacement explicite par l'assistant (mode `replace`). */
  export let onreplace:
    | ((connection: WebGitHubConnectionSummary, input: { organization: string }) => void)
    | undefined = undefined

  type Panel = '' | 'rename' | 'disable' | 'replace' | 'manual' | 'delete'
  let panel: Panel = ''
  let busy = ''
  let error = ''
  let copied = false
  let health: WebGitHubHealthValue | null = null
  let healthBusy = false
  let draftLabel = ''
  let replaceOwner: 'personal' | 'organization' = 'personal'
  let replaceOrganization = ''
  let appId = '',
    slug = '',
    installationId = '',
    privateKey = '',
    webhookSecret = ''

  $: label = webGithubConnectionLabel(connection)
  $: anchor = webGithubConnectionAnchor(connection.id)
  $: status = webGithubConnectionStatus(connection, health, healthBusy)
  $: labelError = panel === 'rename' ? webGithubLabelError(draftLabel) : ''

  const open = (next: Panel) => {
    error = ''
    if (panel === next) {
      panel = ''
      return
    }
    panel = next
    if (next === 'rename') draftLabel = connection.label || label
    if (next === 'replace') {
      replaceOwner = 'personal'
      replaceOrganization = ''
    }
    if (next === 'manual') {
      appId = connection.app?.id || ''
      slug = connection.app?.slug || ''
      installationId = connection.installationId || ''
      privateKey = ''
      webhookSecret = ''
    }
  }
  const update = async (key: string, body: WebGitHubConnectionInput, message: string) => {
    busy = key
    error = ''
    try {
      await webGithubConnectionUpdate(connection.id, body)
      panel = ''
      privateKey = ''
      webhookSecret = ''
      onchanged?.(message)
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const rename = (event: SubmitEvent) => {
    event.preventDefault()
    const value = draftLabel.trim()
    const invalid = webGithubLabelError(value)
    if (invalid) {
      error = invalid
      return
    }
    void update('rename', { label: value }, `Connexion renommée en « ${value} ».`)
  }
  const setDisabled = (disabled: boolean) =>
    update(
      'disable',
      { disabled },
      disabled ? `Connexion « ${label} » désactivée.` : `Connexion « ${label} » réactivée.`
    )
  const saveManual = (event: SubmitEvent) => {
    event.preventDefault()
    const body: WebGitHubConnectionInput = {
      githubAppId: appId.trim(),
      githubAppSlug: slug.trim(),
      githubInstallationId: installationId.trim(),
    }
    // Un secret laissé vide conserve la valeur enregistrée.
    if (privateKey.trim()) body.privateKey = privateKey
    if (webhookSecret) body.webhookSecret = webhookSecret
    void update('manual', body, `Connexion « ${label} » enregistrée. Les champs secrets ont été masqués.`)
  }
  const remove = async () => {
    busy = 'delete'
    error = ''
    try {
      const result = await webGithubConnectionDelete(connection.id)
      panel = ''
      ondeleted?.(result, connection)
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const replace = (event: SubmitEvent) => {
    event.preventDefault()
    const organization = replaceOwner === 'organization' ? replaceOrganization.trim() : ''
    if (replaceOwner === 'organization' && !organization) {
      error = 'Indiquez le nom de l’organisation propriétaire de la GitHub App.'
      return
    }
    error = ''
    onreplace?.(connection, { organization })
  }
  const copy = async () => {
    if (!connection.webhookUrl) return
    try {
      await navigator.clipboard.writeText(connection.webhookUrl)
      copied = true
      setTimeout(() => (copied = false), 2000)
    } catch {
      error = 'La copie automatique est indisponible. Sélectionnez et copiez l’URL du webhook.'
    }
  }
</script>

<article class="web-panel github-card" id={anchor || undefined} data-disabled={connection.disabled}>
  <div class="web-row github-card-head">
    <div>
      <h3>
        {label}{#if connection.isPrincipal}<span class="web-status github-card-badge">Principale</span>{/if}
      </h3>
      <p class="web-help">
        Compte : {connection.app?.owner || '—'} · GitHub App : {#if connection.app?.slug}<a
            href={`https://github.com/apps/${encodeURIComponent(connection.app.slug)}`}
            target="_blank"
            rel="noopener noreferrer">{connection.app.slug}</a
          >{:else}—{/if}
      </p>
    </div>
    <span class="web-status" data-tone={status.tone} role="status"
      ><span class="web-status-dot" aria-hidden="true"></span>{status.label}</span
    >
  </div>
  <dl class="github-card-facts">
    <div class="github-card-webhook">
      <dt>Webhook</dt>
      <dd>
        {#if connection.webhookUrl}<code>{connection.webhookUrl}</code><button
            class="web-button"
            type="button"
            onclick={copy}>{copied ? 'Copiée' : 'Copier'}</button
          >{:else}—{/if}
      </dd>
    </div>
    <div>
      <dt>Dernière livraison</dt>
      <dd>{webGithubDeliveryLabel(connection.lastDelivery)}</dd>
    </div>
    <div>
      <dt>Utilisation</dt>
      <dd>{webGithubAppCountLabel(connection.appCount)}</dd>
    </div>
  </dl>
  {#if connection.configured}
    <WebGitHubHealth
      compact
      connectionId={connection.id}
      {refreshKey}
      onhealth={(value, checking) => {
        health = value
        healthBusy = checking
      }}
    />
  {:else}<p class="web-help" role="status">
      Connexion incomplète : terminez la saisie manuelle ou recréez la GitHub App avec l’assistant.
    </p>{/if}
  {#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}
  <div class="web-actions github-card-actions">
    <button class="web-button" type="button" aria-expanded={panel === 'rename'} onclick={() => open('rename')}
      >Renommer</button
    >{#if connection.disabled}<button
        class="web-button"
        type="button"
        onclick={() => setDisabled(false)}
        disabled={!!busy}>{busy === 'disable' ? 'Réactivation…' : 'Réactiver'}</button
      >{:else}<button
        class="web-button"
        type="button"
        aria-expanded={panel === 'disable'}
        onclick={() => open('disable')}>Désactiver</button
      >{/if}<button
      class="web-button"
      type="button"
      aria-expanded={panel === 'replace'}
      onclick={() => open('replace')}
      disabled={connecting}>Recréer la GitHub App</button
    ><button class="web-button" type="button" aria-expanded={panel === 'manual'} onclick={() => open('manual')}
      >Saisie manuelle</button
    ><button
      class="web-button web-button--danger"
      type="button"
      aria-expanded={panel === 'delete'}
      onclick={() => open('delete')}>Supprimer</button
    >
  </div>

  {#if panel === 'rename'}
    <form class="web-form github-card-panel" onsubmit={rename}>
      <label class="web-field"
        ><span>Libellé de la connexion</span><input
          bind:value={draftLabel}
          required
          maxlength={WEB_GITHUB_LABEL_MAX}
          autocomplete="off"
          disabled={!!busy}
        />{#if labelError && draftLabel}<span class="web-help">{labelError}</span>{/if}</label
      >
      <div class="web-actions">
        <button class="web-button web-button--primary" type="submit" disabled={!!busy || !!labelError}
          >{busy === 'rename' ? 'Enregistrement…' : 'Renommer'}</button
        ><button class="web-button" type="button" onclick={() => (panel = '')} disabled={!!busy}>Annuler</button>
      </div>
    </form>
  {:else if panel === 'disable'}
    <div class="web-confirm github-card-panel" role="alertdialog" aria-label="Désactiver la connexion">
      <p>
        Désactiver « {label} » ? Les pushs et pull requests de cette App seront ignorés et ses applications ne pourront plus
        être déployées.
      </p>
      <div class="web-actions">
        <button class="web-button web-button--danger" type="button" onclick={() => setDisabled(true)} disabled={!!busy}
          >{busy === 'disable' ? 'Désactivation…' : 'Désactiver'}</button
        ><button class="web-button" type="button" onclick={() => (panel = '')} disabled={!!busy}>Annuler</button>
      </div>
    </div>
  {:else if panel === 'replace'}
    <form class="web-confirm github-card-panel" onsubmit={replace}>
      <p>
        <strong>Recréer la GitHub App de « {label} »</strong><br />La GitHub App actuelle continue de servir les
        déploiements jusqu’à la validation complète de la nouvelle.
      </p>
      <fieldset class="web-fieldset">
        <legend>Propriétaire de la nouvelle GitHub App</legend>
        <label class="web-check"
          ><input type="radio" bind:group={replaceOwner} value="personal" disabled={connecting} /> Mon compte personnel</label
        >
        <label class="web-check"
          ><input type="radio" bind:group={replaceOwner} value="organization" disabled={connecting} /> Une organisation</label
        >
      </fieldset>
      {#if replaceOwner === 'organization'}<label class="web-field"
          ><span>Nom de l’organisation</span><input
            bind:value={replaceOrganization}
            pattern="[A-Za-z0-9\-]*"
            maxlength="39"
            autocomplete="off"
            placeholder={connection.app?.owner || 'mon-organisation'}
            disabled={connecting}
          /></label
        >{/if}
      <div class="web-actions">
        <button class="web-button web-button--primary" type="submit" disabled={connecting}
          >{connecting ? 'Ouverture de GitHub…' : 'Recréer sur GitHub ↗'}</button
        ><button class="web-button" type="button" onclick={() => (panel = '')} disabled={connecting}>Annuler</button>
      </div>
    </form>
  {:else if panel === 'manual'}
    <form class="web-form github-card-panel" onsubmit={saveManual}>
      <p class="web-help">
        Modifiez les identifiants de cette GitHub App. Une valeur secrète laissée vide conserve la valeur enregistrée.
      </p>
      <div class="web-form-grid">
        <label class="web-field"
          ><span>App ID GitHub</span><input
            bind:value={appId}
            inputmode="numeric"
            pattern="[0-9]*"
            required
            disabled={!!busy}
            autocomplete="off"
          /></label
        ><label class="web-field"
          ><span>Nom de l’application GitHub</span><input
            bind:value={slug}
            pattern="[A-Za-z0-9\-]*"
            required
            disabled={!!busy}
            autocomplete="off"
          /></label
        ><label class="web-field web-field--full"
          ><span>Installation ID GitHub</span><input
            bind:value={installationId}
            inputmode="numeric"
            pattern="[0-9]*"
            required
            disabled={!!busy}
            autocomplete="off"
          /></label
        ><label class="web-field web-field--full"
          ><span>Clé privée de la GitHub App</span><textarea
            bind:value={privateKey}
            placeholder={connection.hasPrivateKey
              ? 'Clé enregistrée · laissez vide pour la conserver'
              : '-----BEGIN RSA PRIVATE KEY-----'}
            disabled={!!busy}
            autocomplete="off"
            spellcheck="false"
            rows="4"
          ></textarea></label
        ><label class="web-field web-field--full"
          ><span>Secret de signature des webhooks</span><input
            type="password"
            bind:value={webhookSecret}
            placeholder={connection.hasWebhookSecret
              ? 'Secret enregistré · laissez vide pour le conserver'
              : 'Le même secret que dans les paramètres GitHub'}
            disabled={!!busy}
            autocomplete="new-password"
            minlength="32"
            maxlength="256"
          /></label
        >
      </div>
      <p class="web-help">
        Sur GitHub, l’URL du webhook de cette App doit être <code>{connection.webhookUrl || '—'}</code>.
      </p>
      <div class="web-actions">
        <button class="web-button web-button--primary" type="submit" disabled={!!busy}
          >{busy === 'manual' ? 'Enregistrement…' : 'Enregistrer'}</button
        ><button class="web-button" type="button" onclick={() => (panel = '')} disabled={!!busy}>Annuler</button>
      </div>
    </form>
  {:else if panel === 'delete'}
    <div class="web-confirm github-card-panel" role="alertdialog" aria-label="Supprimer la connexion">
      <p>
        Supprimer « {label} » ? La clé privée et le secret webhook seront effacés de Kolab Hosting. Supprimez aussi la GitHub
        App sur GitHub.
      </p>
      {#if connection.appCount > 0}<p class="web-help">
          Cette connexion GitHub est encore utilisée par {connection.appCount} application(s). Rattachez-les à une autre connexion
          avant de la supprimer.
        </p>{/if}
      <div class="web-actions">
        <button class="web-button web-button--danger" type="button" onclick={remove} disabled={!!busy}
          >{busy === 'delete' ? 'Suppression…' : 'Supprimer définitivement'}</button
        ><button class="web-button" type="button" onclick={() => (panel = '')} disabled={!!busy}>Annuler</button>
      </div>
    </div>
  {/if}
</article>

<style>
  .github-card {
    display: grid;
    gap: 1rem;
    scroll-margin-top: 5rem;
  }
  .github-card[data-disabled='true'] {
    opacity: 0.85;
  }
  .github-card:target {
    border-color: #75a263;
    box-shadow: 0 0 0 3px #75a26322;
  }
  .github-card h3 {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
    margin: 0;
    font-size: 1rem;
    overflow-wrap: anywhere;
  }
  .github-card-head p {
    margin: 0.35rem 0 0;
  }
  .github-card-badge {
    font-size: 0.7rem;
  }
  .github-card-facts {
    display: grid;
    grid-template-columns: minmax(0, 2fr) minmax(0, 1fr) minmax(0, 1fr);
    gap: 1rem;
    margin: 0;
  }
  .github-card-facts > div {
    display: grid;
    gap: 0.4rem;
    min-width: 0;
  }
  .github-card-facts dt {
    font-size: 0.72rem;
    color: var(--web-muted);
  }
  .github-card-facts dd {
    margin: 0;
    font-size: 0.84rem;
    overflow-wrap: anywhere;
  }
  .github-card-webhook dd {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    flex-wrap: wrap;
  }
  .github-card-webhook code {
    overflow-wrap: anywhere;
    font-size: 0.78rem;
  }
  .github-card-actions {
    padding-top: 0.7rem;
    border-top: 1px solid var(--web-border);
  }
  .github-card-panel p {
    margin: 0;
  }
  @media (max-width: 750px) {
    .github-card-facts {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
