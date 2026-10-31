<script lang="ts">
  import { onDestroy, onMount } from 'svelte'
  import { webRequest, webError, type WebAppDomain } from '$lib/webApps'
  export let appId: string
  export let domain: WebAppDomain
  export let onapplied: () => void = () => {}
  type DnsRecord = { id?: string; type: string; name: string; content: string; proxied?: boolean; ttl?: number }
  type Preview = {
    previewId: string
    expiresAt: number
    hostname: string
    zone: { name: string }
    proxyEnabled: boolean
    proxyDeferred: boolean
    current: DnsRecord[]
    changes: { action: string; before: DnsRecord | null; after: DnsRecord | null }[]
  }
  let configured = false,
    loaded = false,
    editing = false,
    busy = '',
    error = '',
    message = '',
    token = '',
    confirmation = ''
  let https: { ready: boolean; error: string } | null = null
  let preview: Preview | null = null
  let proxyEnabled = true
  let proxyEdited = false
  let domainKey = ''
  let generation = 0
  let disposed = false
  const cloudflareLabels = {
    pending_validation: 'Validation DNS automatique en cours',
    pending_https: 'HTTPS en cours, puis activation du proxy',
    active: 'Proxy Cloudflare activé',
    disabled: 'Proxy Cloudflare désactivé · DNS uniquement',
    error: 'Configuration Cloudflare à vérifier',
  }
  $: if (domainKey !== `${appId}/${domain.id}`) {
    domainKey = `${appId}/${domain.id}`
    ++generation
    proxyEnabled = domain.cloudflare?.enabled ?? true
    proxyEdited = false
    preview = null
    confirmation = ''
    error = ''
    message = ''
    token = ''
    editing = false
    busy = ''
  }
  $: if (!proxyEdited && !preview && !busy && !editing && domain.cloudflare) {
    proxyEnabled = domain.cloudflare.enabled
  }
  const current = (request: number) => !disposed && request === generation
  const path = () => `/${appId}/domains/${domain.id}/cloudflare`
  onMount(() => {
    void load()
  })
  onDestroy(() => {
    disposed = true
    ++generation
    token = ''
    preview = null
    confirmation = ''
  })
  async function load() {
    const request = generation
    try {
      const value = await webRequest<{ configured: boolean; https: { ready: boolean; error: string } }>(
        '/cloudflare/settings'
      )
      if (!current(request)) return
      configured = value.configured
      https = value.https
      loaded = true
    } catch (e) {
      if (!current(request)) return
      error = webError(e)
      loaded = true
    }
  }
  async function save() {
    if (busy) return
    const request = generation
    busy = 'save'
    error = ''
    preview = null
    try {
      await webRequest('/cloudflare/settings', { method: 'PUT', body: { token } })
      if (!current(request)) return
      token = ''
      editing = false
      await load()
      if (current(request)) message = 'Jeton enregistré. Préparez un aperçu pour vérifier l’accès à cette zone.'
    } catch (e) {
      if (current(request)) error = webError(e)
    } finally {
      if (current(request)) busy = ''
    }
  }
  function changeProxy() {
    proxyEdited = true
    preview = null
    confirmation = ''
    error = ''
    message = ''
  }
  async function prepare() {
    if (busy) return
    const request = generation
    const requestedProxy = proxyEnabled
    const requestPath = path()
    busy = 'preview'
    error = ''
    message = ''
    preview = null
    confirmation = ''
    try {
      const value = await webRequest<Preview>(requestPath + '/preview', {
        method: 'POST',
        body: { proxyEnabled: requestedProxy },
      })
      if (!current(request) || proxyEnabled !== requestedProxy) return
      preview = value
    } catch (e) {
      if (current(request)) error = webError(e)
    } finally {
      if (current(request)) busy = ''
    }
  }
  async function apply() {
    if (busy || !preview || confirmation !== domain.hostname) return
    const request = generation
    const prepared = preview
    const requestPath = path()
    busy = 'apply'
    error = ''
    message = ''
    try {
      const value = await webRequest<{ message: string }>(requestPath + '/apply', {
        method: 'POST',
        body: { previewId: prepared.previewId, confirmHostname: confirmation },
      })
      if (!current(request)) return
      message = value.message
      proxyEdited = true
      preview = null
      confirmation = ''
      await onapplied()
    } catch (e) {
      if (!current(request)) return
      error = webError(e)
      preview = null
      confirmation = ''
    } finally {
      if (current(request)) busy = ''
    }
  }
</script>

<div class="cloudflare-dns">
  <div class="web-row">
    <div>
      <h3>Configurer avec Cloudflare</h3>
      <p class="web-help">Lecture de la zone et aperçu avant toute modification DNS.</p>
    </div>
    <button class="web-button" disabled={!!busy || !loaded} on:click={() => (editing = !editing)}
      >{configured ? 'Connexion Cloudflare' : 'Connecter Cloudflare'}</button
    >
  </div>
  {#if https && !https.ready}<div class="web-alert" role="status">{https.error}</div>{/if}
  {#if editing}
    <form on:submit|preventDefault={save} class="cloudflare-connect">
      <p class="web-help">
        Jeton API avec Zone:Read et DNS:Edit, limité aux zones à gérer. Le jeton est chiffré sur ce serveur et n’est
        jamais renvoyé au navigateur. Réservé aux administrateurs de la plateforme.
      </p>
      <label class="web-field"
        ><span>Jeton API Cloudflare {configured ? '(remplacer la connexion)' : ''}</span><input
          type="password"
          bind:value={token}
          autocomplete="new-password"
          required
          disabled={!!busy}
        /></label
      >
      <button class="web-button" disabled={!!busy || !token.trim()}
        >{busy === 'save' ? 'Enregistrement…' : 'Enregistrer le jeton'}</button
      >
    </form>
  {/if}
  <label class="cloudflare-proxy">
    <input
      type="checkbox"
      bind:checked={proxyEnabled}
      on:change={changeProxy}
      disabled={!!busy}
      aria-describedby={`cloudflare-proxy-help-${domain.id}`}
    />
    <span>
      <strong>Activer le proxy Cloudflare</strong>
      <span class="web-help" id={`cloudflare-proxy-help-${domain.id}`}>
        Activé par défaut. Lors de la première validation HTTPS, le DNS reste direct, puis le proxy s’active
        automatiquement. Un certificat déjà actif permet de conserver le proxy.
      </span>
    </span>
  </label>
  {#if domain.cloudflare}
    <div class="cloudflare-state" role="status">
      {domain.cloudflare.status === 'pending_https' && !domain.cloudflare.enabled
        ? 'Validation HTTPS automatique en cours'
        : cloudflareLabels[domain.cloudflare.status]}
    </div>
    {#if domain.cloudflare.status === 'pending_validation' || domain.cloudflare.status === 'pending_https'}
      <p class="web-help">Le serveur poursuit la configuration automatiquement, même si vous fermez cette page.</p>
    {/if}
    {#if domain.cloudflare.status === 'error'}
      <div class="web-alert web-alert--error" role="alert">
        {domain.cloudflare.lastError || 'La configuration automatique a été interrompue.'} Repréparez un aperçu pour vérifier
        les DNS et réessayer.
      </div>
    {/if}
  {/if}
  <div class="web-actions">
    <button class="web-button web-button--primary" on:click={prepare} disabled={!configured || !!busy}
      >{busy === 'preview'
        ? 'Lecture de Cloudflare…'
        : domain.cloudflare?.status === 'error'
          ? 'Repréparer les DNS dans Cloudflare'
          : 'Préparer les DNS dans Cloudflare'}</button
    >
  </div>
  {#if preview}
    <div class="web-confirm">
      <h3>Zone accessible : {preview.zone.name}</h3>
      <p class="web-help">
        Nom concerné : <strong>{preview.hostname}</strong>. Aperçu valable 10 minutes.
      </p>
      <p class="web-help">
        {#if preview.proxyEnabled && preview.proxyDeferred}
          Le DNS restera direct pendant la validation automatique DNS et HTTPS. Le proxy s’activera ensuite, même si
          vous fermez cette page.
        {:else if preview.proxyEnabled}
          Le proxy Cloudflare sera activé. Les vérifications se poursuivent automatiquement sur le serveur.
        {:else}
          Le domaine restera en DNS uniquement. Les vérifications DNS et HTTPS se poursuivent automatiquement sur le
          serveur.
        {/if}
      </p>
      <h4>Configuration actuelle dans Cloudflare</h4>
      {#if !preview.current.length}<p class="web-help">
          Aucun enregistrement explicite pour ce nom ou son TXT de propriété.
        </p>{:else}<div class="cloudflare-records">
          {#each preview.current as record}<div>
              <strong>{record.type}</strong><code>{record.name}</code><code>{record.content}</code><span
                >{record.type === 'A' || record.type === 'AAAA' || record.type === 'CNAME'
                  ? record.proxied
                    ? 'Proxy activé'
                    : 'DNS uniquement'
                  : ''}</span
              >
            </div>{/each}
        </div>{/if}
      <h4>Changements proposés</h4>
      {#if !preview.changes.length}<p class="web-help">
          Les DNS sont déjà conformes. Aucune modification nécessaire.
        </p>{:else}<div class="cloudflare-records">
          {#each preview.changes as change}<div>
              <strong
                >{change.action === 'create' ? 'Créer' : change.action === 'update' ? 'Modifier' : 'Supprimer'}</strong
              ><code>{(change.after || change.before)?.type} · {(change.after || change.before)?.name}</code><code
                >{change.before?.content || 'Absent'} → {change.after?.content || 'Supprimé'}</code
              >{#if change.after?.type === 'A'}<span
                  >{change.after.proxied ? 'Proxy activé' : 'DNS uniquement'} · TTL automatique</span
                >{/if}
            </div>{/each}
        </div>{/if}
      <p class="web-help">
        Les entrées affichées peuvent servir à un autre site : les modifier change sa destination. Les enregistrements
        des autres noms et les TXT sans rapport sont conservés.
      </p>
      <label class="web-field"
        ><span>Confirmer en saisissant {domain.hostname}</span><input
          bind:value={confirmation}
          autocomplete="off"
          disabled={!!busy}
        /></label
      >
      <div class="web-actions">
        <button
          class="web-button web-button--primary"
          on:click={apply}
          disabled={!!busy || confirmation !== domain.hostname}
          >{busy === 'apply'
            ? 'Application…'
            : preview.changes.length
              ? 'Confirmer et appliquer les DNS'
              : 'Confirmer la configuration'}</button
        ><button
          class="web-button"
          disabled={!!busy}
          on:click={() => {
            preview = null
            confirmation = ''
          }}>Annuler</button
        >
      </div>
    </div>
  {/if}
  {#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}
  {#if message}<div class="web-alert" role="status">{message}</div>{/if}
</div>

<style>
  .cloudflare-dns {
    margin-block: 16px;
    padding: 14px;
    border: 1px solid var(--app-border);
    border-radius: 6px;
    background: var(--app-surface-soft);
    display: grid;
    gap: 12px;
  }
  .cloudflare-connect {
    display: grid;
    gap: 12px;
  }
  .cloudflare-proxy {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    margin: 0;
  }
  .cloudflare-proxy input {
    width: 16px;
    height: 16px;
    flex-shrink: 0;
    margin-top: 3px;
  }
  .cloudflare-proxy > span {
    display: grid;
    gap: 4px;
    min-width: 0;
  }
  .cloudflare-proxy strong,
  .cloudflare-state {
    font-size: 13px;
  }
  .cloudflare-records {
    display: grid;
    gap: 8px;
    margin-block: 10px;
  }
  .cloudflare-records > div {
    display: grid;
    grid-template-columns: 70px minmax(0, 1fr);
    gap: 5px 10px;
    padding: 10px;
    border: 1px solid var(--app-border);
    border-radius: 4px;
    font-size: 12px;
  }
  .cloudflare-records strong {
    grid-column: 1;
  }
  .cloudflare-records code {
    grid-column: 2;
    overflow-wrap: anywhere;
    white-space: normal;
  }
  .cloudflare-records span {
    grid-column: 2;
    color: var(--app-text-muted);
  }
  h4 {
    font-size: 12px;
    margin-top: 14px;
  }
</style>
