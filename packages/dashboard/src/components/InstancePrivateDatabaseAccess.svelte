<script lang="ts">
  import { onDestroy, onMount, tick } from 'svelte'
  import CopyField from '$components/CopyField.svelte'
  import { client } from '$src/pocketbase-client'
  import { privateDatabaseStatus, type PrivateDatabaseAccess, type PrivateDatabaseBinding } from '$lib/privateDatabases'

  export let instanceId: string

  let settings: PrivateDatabaseAccess | undefined
  let loading = true
  let busy = ''
  let error = ''
  let refreshError = ''
  let success = ''
  let appId = ''
  let port: number | undefined = 4825
  let enabled = false
  let applicationSelect: HTMLSelectElement
  let timer: ReturnType<typeof setInterval> | undefined
  let mounted = false
  let requestVersion = 0
  let now = Date.now()

  $: selected = settings?.bindings.find((binding) => binding.appId === appId)
  $: validPort = typeof port === 'number' && Number.isInteger(port) && port >= 1024 && port <= 65535
  $: canSave = !!settings?.supported && !!appId && validPort && (enabled || !!selected) && !busy

  const request = <T,>(suffix = '', options: Record<string, unknown> = {}) =>
    client().client.send<T>(`/api/instance/${instanceId}/private-access${suffix}`, {
      method: 'GET',
      ...options,
      requestKey: null,
    })
  const message = (problem: unknown) =>
    problem instanceof Error ? client().parseError(problem)[0] || problem.message : 'Opération impossible.'

  const load = async (background = false) => {
    const version = ++requestVersion
    if (!background) loading = true
    try {
      const value = await request<PrivateDatabaseAccess>()
      if (!mounted || version !== requestVersion) return
      settings = value
      refreshError = ''
      if (!background) error = ''
    } catch (problem) {
      if (mounted && version === requestVersion) {
        if (background) refreshError = 'L’état des connexions ne peut pas être actualisé pour le moment.'
        else error = message(problem)
      }
    } finally {
      if (mounted && version === requestVersion) loading = false
    }
  }

  const chooseApplication = (id: string) => {
    appId = id
    const existing = settings?.bindings.find((binding) => binding.appId === id)
    port = existing?.port || 4825
    enabled = existing?.enabled || false
    error = ''
    success = ''
  }

  const edit = async (binding: PrivateDatabaseBinding) => {
    chooseApplication(binding.appId)
    await tick()
    applicationSelect?.focus()
  }

  const save = async () => {
    if (!canSave) return
    busy = 'save'
    error = ''
    success = ''
    ++requestVersion
    try {
      const result = await request<{ binding: PrivateDatabaseBinding; restartRequired: boolean }>('', {
        method: 'POST',
        body: { appId, port, enabled },
      })
      success = result.binding.enabled
        ? `Accès privé activé pour ${result.binding.appName}.`
        : `Accès privé désactivé pour ${result.binding.appName}.`
      if (settings)
        settings = {
          ...settings,
          bindings: [...settings.bindings.filter((binding) => binding.id !== result.binding.id), result.binding],
        }
      appId = ''
      port = 4825
      enabled = false
      await load(true)
    } catch (problem) {
      error = message(problem)
    } finally {
      busy = ''
    }
  }

  const remove = async (binding: PrivateDatabaseBinding) => {
    if (busy) return
    busy = binding.id
    error = ''
    success = ''
    ++requestVersion
    try {
      await request(`/${binding.id}`, { method: 'DELETE' })
      if (settings) settings = { ...settings, bindings: settings.bindings.filter((item) => item.id !== binding.id) }
      if (appId === binding.appId) {
        appId = ''
        port = 4825
        enabled = false
      }
      success = `Liaison privée retirée pour ${binding.appName}.`
      await load(true)
    } catch (problem) {
      error = message(problem)
    } finally {
      busy = ''
    }
  }

  onMount(() => {
    mounted = true
    void load()
    timer = setInterval(() => {
      now = Date.now()
      if (!busy && !document.hidden && settings?.bindings.some((binding) => binding.enabled)) void load(true)
    }, 5000)
  })
  onDestroy(() => {
    mounted = false
    ++requestVersion
    if (timer) clearInterval(timer)
  })
</script>

<section class="private-access" aria-labelledby="private-access-title" aria-busy={!!busy || loading}>
  <div class="private-access-heading">
    <div>
      <h3 id="private-access-title">Accès privé depuis l’hébergement</h3>
      <p>Reliez cette base à une application du même serveur avec une adresse locale.</p>
    </div>
    <button type="button" class="private-access-button" disabled={!!busy || loading} onclick={() => load()}>
      <wa-icon name="arrows-rotate" aria-hidden="true"></wa-icon>Actualiser
    </button>
  </div>

  {#if error}<wa-callout variant="danger" role="alert">{error}</wa-callout>{/if}
  {#if refreshError}<p class="private-access-muted" role="status">{refreshError}</p>{/if}
  {#if success}<p class="private-access-feedback" role="status">{success}</p>{/if}

  {#if loading && !settings}
    <p class="private-access-muted" role="status">Chargement des accès privés…</p>
  {:else if !settings}
    <p class="private-access-muted">Les accès privés sont indisponibles pour le moment. Vous pouvez réessayer.</p>
  {:else}
    {#if !settings.supported}
      <wa-callout variant="neutral">L’accès privé n’est pas disponible sur ce serveur.</wa-callout>
    {/if}

    {#if settings.applications.length}
      <form
        onsubmit={(event) => {
          event.preventDefault()
          void save()
        }}
      >
        <div class="private-access-fields">
          <div class="auth-field-group">
            <label class="auth-label" for="private-db-application">Application hébergée</label>
            <select
              bind:this={applicationSelect}
              id="private-db-application"
              class="auth-field"
              value={appId}
              onchange={(event) => chooseApplication(event.currentTarget.value)}
              disabled={!!busy || !settings.supported}
              required
            >
              <option value="">Choisir une application</option>
              {#each settings.applications as application (application.id)}
                <option value={application.id}>{application.name}</option>
              {/each}
            </select>
          </div>
          <div class="auth-field-group">
            <label class="auth-label" for="private-db-port">Port local</label>
            <input
              id="private-db-port"
              class="auth-field"
              type="number"
              min="1024"
              max="65535"
              step="1"
              bind:value={port}
              disabled={!!busy || !settings.supported}
              aria-describedby="private-db-port-help"
              required
            />
          </div>
        </div>
        <p id="private-db-port-help" class="private-access-muted">
          Choisissez un port libre dans l’application, entre 1024 et 65535.
        </p>
        <label class="private-access-consent">
          <input type="checkbox" bind:checked={enabled} disabled={!!busy || !appId || !settings.supported} />
          <span>Autoriser cette application à utiliser la connexion privée</span>
        </label>
        <div class="private-access-save">
          <button class="private-access-button private-access-button--primary" type="submit" disabled={!canSave}>
            {busy === 'save' ? 'Enregistrement…' : selected ? 'Enregistrer la liaison' : 'Activer l’accès privé'}
          </button>
        </div>
      </form>
    {:else if settings.supported}
      <p class="private-access-muted">Aucune application que vous pouvez configurer n’est disponible sur ce serveur.</p>
    {/if}

    {#if settings.bindings.length}
      <div class="private-access-bindings">
        {#each settings.bindings as binding (binding.id)}
          {@const state = privateDatabaseStatus(binding, now)}
          <article class="private-access-binding" aria-label={`Liaison avec ${binding.appName}`}>
            <div class="private-access-binding-head">
              <div>
                <h4>{binding.appName}</h4>
                <span class="private-access-state" data-tone={state.tone}>{state.label}</span>
              </div>
              <div class="private-access-actions">
                {#if settings.applications.some((application) => application.id === binding.appId)}
                  <button
                    type="button"
                    class="private-access-button"
                    disabled={!!busy || !settings.supported}
                    onclick={() => edit(binding)}
                  >
                    Modifier
                  </button>
                {/if}
                <button
                  type="button"
                  class="private-access-button"
                  disabled={!!busy}
                  aria-label={`Retirer la liaison avec ${binding.appName}`}
                  onclick={() => remove(binding)}>{busy === binding.id ? 'Retrait…' : 'Retirer'}</button
                >
              </div>
            </div>
            {#if state.detail}<p class="private-access-muted">{state.detail}</p>{/if}
            <div class="private-access-addresses">
              <div role="group" aria-label={`Adresse locale pour ${binding.appName}`}>
                <p class="private-access-address-label">Adresse dans le conteneur</p>
                <CopyField text={binding.url} className="private-database-copy" embedded />
              </div>
              <div role="group" aria-label={`Variable côté serveur pour ${binding.appName}`}>
                <p class="private-access-address-label">Variable côté serveur</p>
                <CopyField text={`${binding.envName}=${binding.url}`} className="private-database-copy" embedded />
              </div>
            </div>
            {#if binding.enabled && binding.needsRestart}
              <p class="private-access-muted">
                La variable sera injectée au prochain démarrage ou déploiement de l’application.
              </p>
            {/if}
          </article>
        {/each}
      </div>
    {:else if settings.supported}
      <p class="private-access-empty">Aucune application n’utilise encore d’accès privé à cette base.</p>
    {/if}

    <p class="private-access-note">
      Utilisez l’adresse locale uniquement dans le code côté serveur. Les appels depuis le navigateur conservent l’URL
      publique de la base. L’authentification PocketBase reste nécessaire.
    </p>
  {/if}
</section>

<style>
  .private-access {
    display: grid;
    gap: 1rem;
    padding: 1.25rem;
    border: 1px solid var(--app-border);
    border-radius: 0.75rem;
    background: var(--app-surface);
    color: var(--app-text);
  }
  .private-access-heading,
  .private-access-binding-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 1rem;
  }
  .private-access-heading h3,
  .private-access-binding h4 {
    margin: 0;
    color: var(--app-text-strong);
    font-size: 0.875rem;
    font-weight: 600;
  }
  .private-access-heading p,
  .private-access-muted,
  .private-access-note,
  .private-access-empty {
    margin: 0;
    color: var(--app-text-muted);
    font-size: 0.75rem;
    line-height: 1.6;
  }
  .private-access-heading p {
    margin-top: 0.35rem;
  }
  form {
    display: grid;
    gap: 0.75rem;
  }
  .private-access-fields {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 8rem;
    gap: 1rem;
  }
  .auth-field-group {
    display: grid;
    gap: 0.375rem;
    min-width: 0;
  }
  .auth-label {
    color: var(--app-text);
    font-size: 0.75rem;
  }
  .auth-field {
    width: 100%;
  }
  .private-access-consent {
    display: flex;
    align-items: flex-start;
    gap: 0.6rem;
    color: var(--app-text);
    font-size: 0.8125rem;
    line-height: 1.5;
    min-height: 2rem;
  }
  .private-access-consent input {
    flex: none;
    margin-top: 0.2rem;
    accent-color: var(--app-accent);
  }
  .private-access-feedback {
    margin: 0;
    font-size: 0.8125rem;
    color: var(--app-accent-text);
  }
  .private-access-bindings {
    display: grid;
  }
  .private-access-binding {
    display: grid;
    gap: 0.75rem;
    margin-top: 0.25rem;
    padding-top: 1rem;
    border-top: 1px solid var(--app-border);
  }
  .private-access-binding + .private-access-binding {
    margin-top: 1rem;
  }
  .private-access-binding-head > div:first-child {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.6rem;
    min-width: 0;
  }
  .private-access-binding h4 {
    overflow-wrap: anywhere;
  }
  .private-access-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    flex: none;
  }
  .private-access-button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.45rem;
    min-height: 34px;
    padding: 0.4rem 0.7rem;
    border: 1px solid var(--app-border);
    border-radius: 5px;
    background: var(--app-surface);
    color: var(--app-text);
    font-size: 0.75rem;
    line-height: 1.4;
    white-space: nowrap;
    cursor: pointer;
  }
  .private-access-button:hover:not(:disabled) {
    border-color: var(--app-border-strong);
    background: var(--app-surface-hover);
  }
  .private-access-button--primary {
    border-color: var(--app-accent-border);
    background: var(--app-accent-soft);
    color: var(--app-accent-text);
  }
  .private-access-button--primary:hover:not(:disabled) {
    border-color: var(--app-accent);
    background: var(--app-accent-soft);
  }
  .private-access-button:focus-visible {
    outline: 2px solid var(--app-accent);
    outline-offset: 3px;
  }
  .private-access-button:disabled {
    opacity: 0.55;
    cursor: default;
  }
  .private-access-state {
    display: inline-flex;
    align-items: center;
    min-height: 1.5rem;
    padding: 0.1rem 0.5rem;
    border: 1px solid var(--app-border);
    border-radius: 4px;
    color: var(--app-text-muted);
    font-size: 0.6875rem;
  }
  .private-access-state[data-tone='success'] {
    color: var(--wa-color-success-on-quiet);
    background: var(--wa-color-success-fill-quiet);
  }
  .private-access-state[data-tone='warning'] {
    color: var(--wa-color-warning-on-quiet);
    background: var(--wa-color-warning-fill-quiet);
  }
  .private-access-addresses {
    display: grid;
    gap: 0.75rem;
    min-width: 0;
  }
  .private-access-addresses > div {
    min-width: 0;
  }
  .private-access-address-label {
    margin: 0 0 0.35rem;
    color: var(--app-text-muted);
    font-size: 0.6875rem;
  }
  .private-access-addresses :global(.private-database-copy) {
    border-color: var(--app-border);
    background: var(--app-bg);
  }
  .private-access-addresses :global(.private-database-copy .copy-field-plain) {
    color: var(--app-text);
  }
  .private-access-note {
    padding-top: 1rem;
    border-top: 1px solid var(--app-border);
  }
  @media (max-width: 600px) {
    .private-access {
      padding: 1rem;
    }
    .private-access-heading,
    .private-access-binding-head {
      flex-direction: column;
      gap: 0.75rem;
    }
    .private-access-fields {
      grid-template-columns: minmax(0, 1fr);
      gap: 0.75rem;
    }
    .private-access-button,
    .private-access-consent {
      min-height: 44px;
    }
  }
</style>
