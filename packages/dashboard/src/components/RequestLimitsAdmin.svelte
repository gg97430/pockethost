<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { client } from '$src/pocketbase-client'

  export let instances: { id: string; subdomain: string }[] = []
  type Limits = { total: number; perApp: number; perBinding: number; perDatabase: number }
  type Rule = { label: string; audience: string; duration: number; maxRequests: number }
  type Native = { enabled: boolean; rules: Rule[]; excludedIPCount: number; revision: string }
  let limits: Limits | undefined
  let revision = ''
  let relayBaseline = ''
  let relayBusy = false
  let relayError = ''
  let relayNotice = ''
  let web: { requestsPerMinute: number; revision: string } | undefined
  let webBaseline = ''
  let webBusy = false
  let webError = ''
  let webNotice = ''
  let selected = ''
  let native: Native | undefined
  let nativeBaseline = ''
  let nativeBusy = false
  let nativeError = ''
  let nativeNotice = ''
  let generation = 0
  const fields: { key: keyof Limits; label: string; help: string; max: number }[] = [
    { key: 'total', label: 'Total du serveur', help: 'Toutes les applications réunies', max: 1024 },
    { key: 'perApp', label: 'Par application', help: 'Tous ses services et liaisons', max: 256 },
    { key: 'perBinding', label: 'Par liaison', help: 'Un accès application → base', max: 256 },
    { key: 'perDatabase', label: 'Par base', help: 'Connexions privées vers une même base', max: 512 },
  ]
  const message = (error: unknown) =>
    error instanceof Error ? client().parseError(error)[0] || error.message : 'Impossible de joindre le serveur.'
  const send = <T,>(path: string, method = 'GET', body?: unknown) =>
    client().client.send<T>(path, { method, body, requestKey: null })
  $: relayDirty = !!limits && JSON.stringify(limits) !== relayBaseline
  $: nativeDirty = !!native && JSON.stringify(native) !== nativeBaseline
  $: webDirty = !!web && JSON.stringify(web) !== webBaseline

  async function loadWeb() {
    webBusy = true
    webError = ''
    webNotice = ''
    try {
      web = await send('/api/admin/request-limits/web')
      webBaseline = JSON.stringify(web)
    } catch (error) {
      webError = message(error)
    } finally {
      webBusy = false
    }
  }
  async function saveWeb(event: SubmitEvent) {
    event.preventDefault()
    webBusy = true
    webError = ''
    webNotice = ''
    try {
      web = await send('/api/admin/request-limits/web', 'PUT', web)
      webBaseline = JSON.stringify(web)
      webNotice = 'Limite web enregistrée. Application automatique en quelques secondes, sans redémarrage des sites.'
    } catch (error) {
      webError = message(error)
    } finally {
      webBusy = false
    }
  }

  async function loadRelay() {
    relayBusy = true
    relayError = ''
    relayNotice = ''
    try {
      const value = await send<{ limits: Limits; revision: string }>('/api/admin/request-limits')
      limits = value.limits
      revision = value.revision
      relayBaseline = JSON.stringify(limits)
    } catch (error) {
      relayError = message(error)
    } finally {
      relayBusy = false
    }
  }
  async function saveRelay(event: SubmitEvent) {
    event.preventDefault()
    relayBusy = true
    relayError = ''
    relayNotice = ''
    try {
      const value = await send<{ limits: Limits; revision: string }>('/api/admin/request-limits', 'PUT', {
        limits,
        revision,
      })
      limits = value.limits
      revision = value.revision
      relayBaseline = JSON.stringify(limits)
      relayNotice = 'Plafonds enregistrés. Application automatique aux nouvelles connexions en quelques secondes.'
    } catch (error) {
      relayError = message(error)
    } finally {
      relayBusy = false
    }
  }
  async function loadNative() {
    const current = ++generation,
      id = selected
    native = undefined
    nativeError = ''
    nativeNotice = ''
    if (!id) return
    nativeBusy = true
    try {
      const value = await send<Native>(`/api/admin/request-limits/databases/${id}`)
      if (current !== generation) return
      native = value
      nativeBaseline = JSON.stringify(value)
    } catch (error) {
      if (current === generation) nativeError = message(error)
    } finally {
      if (current === generation) nativeBusy = false
    }
  }
  async function saveNative(event: SubmitEvent) {
    event.preventDefault()
    if (!native) return
    const current = ++generation,
      id = selected
    nativeBusy = true
    nativeError = ''
    nativeNotice = ''
    try {
      const value = await send<Native>(`/api/admin/request-limits/databases/${id}`, 'PUT', {
        enabled: native.enabled,
        rules: native.rules,
        revision: native.revision,
      })
      if (current !== generation) return
      native = value
      nativeBaseline = JSON.stringify(value)
      nativeNotice = 'Réglage appliqué et relu dans PocketBase, sans redémarrage de la base.'
    } catch (error) {
      if (current === generation) nativeError = message(error)
    } finally {
      if (current === generation) nativeBusy = false
    }
  }
  onMount(() => {
    loadRelay()
    loadWeb()
  })
  onDestroy(() => {
    generation++
  })
</script>

<div class="request-limits">
  <div class="intro">
    <span class="eyebrow">Trafic & connexions</span>
    <h2>Limites de requêtes</h2>
    <p>
      Réglez le trafic des hébergements, la capacité du relais privé et les règles propres à chaque base. Ces réglages
      concernent ce serveur.
    </p>
  </div>
  <form class="panel" onsubmit={saveWeb} aria-busy={webBusy}>
    <header>
      <div>
        <span class="number">01</span>
        <h3>Trafic des hébergements</h3>
      </div>
      <span class="badge">Requêtes par minute et par IP</span>
    </header>
    <p>
      Une IP peut représenter plusieurs utilisateurs d’un même bureau. Le compteur est partagé entre les sites hébergés
      sur ce serveur.
    </p>
    {#if web}<fieldset disabled={webBusy} class="web-ceiling">
        <label
          >Requêtes par minute · 0 = désactivé<input
            type="number"
            min="0"
            max="1000000"
            step="1"
            required
            bind:value={web.requestsPerMinute}
          /></label
        >
      </fieldset>
      <p class="hint">
        Les fichiers statiques Next.js sont exclus. Les limites propres aux projets et aux applications restent
        indépendantes.
      </p>{:else if webBusy}<p role="status">Lecture du réglage web…</p>{/if}
    {#if webError}<p class="error" role="alert">{webError}</p>{/if}
    {#if webNotice}<p class="success" role="status">{webNotice}</p>{/if}
    <footer>
      <button type="button" class="secondary" onclick={loadWeb} disabled={webBusy}>Recharger</button><button
        class="primary"
        disabled={webBusy || !webDirty}>Enregistrer le trafic web</button
      >
    </footer>
  </form>
  <form class="panel" onsubmit={saveRelay} aria-busy={relayBusy}>
    <header>
      <div>
        <span class="number">02</span>
        <h3>Relais BDD privé</h3>
      </div>
      <span class="badge">Connexions simultanées</span>
    </header>
    <p>
      Ces plafonds comptent les connexions ouvertes, y compris les connexions persistantes. Les connexions déjà établies
      restent ouvertes lors d’une baisse.
    </p>
    {#if limits}
      <fieldset disabled={relayBusy} class="ceilings">
        {#each fields as field}<label
            >{field.label}<input
              type="number"
              min="1"
              max={field.max}
              step="1"
              required
              bind:value={limits[field.key]}
            /><small>{field.help}</small></label
          >{/each}
      </fieldset>
      <p class="hint">
        Une liaison doit rester sous les plafonds de son application et de sa base. Chacun doit rester sous le total du
        serveur.
      </p>
    {:else if relayBusy}<p role="status">Lecture des plafonds…</p>{/if}
    {#if relayError}<p class="error" role="alert">{relayError}</p>{/if}
    {#if relayNotice}<p class="success" role="status">{relayNotice}</p>{/if}
    <footer>
      <button type="button" class="secondary" onclick={loadRelay} disabled={relayBusy}>Recharger</button><button
        class="primary"
        disabled={relayBusy || !relayDirty}>{relayBusy ? 'Veuillez patienter…' : 'Enregistrer les plafonds'}</button
      >
    </footer>
  </form>
  <section class="panel">
    <header>
      <div>
        <span class="number">03</span>
        <h3>Limiteur PocketBase</h3>
      </div>
      <span class="badge">Requêtes par période · HTTP 429</span>
    </header>
    <p>
      Activez ou désactivez le limiteur natif d’une base et ajustez ses règles. Les autres bases gardent leurs réglages.
    </p>
    <div class="selection">
      <label for="limited-database">Base de données</label><select
        id="limited-database"
        bind:value={selected}
        onchange={loadNative}
        disabled={nativeBusy}
        ><option value="">Choisir une base…</option>{#each instances as instance}<option value={instance.id}
            >{instance.subdomain}</option
          >{/each}</select
      ><button class="secondary" type="button" onclick={loadNative} disabled={!selected || nativeBusy}>Recharger</button
      >
    </div>
    {#if nativeBusy}<p role="status">Lecture ou enregistrement dans PocketBase…</p>{/if}
    {#if nativeError}<p class="error" role="alert">{nativeError}</p>{/if}
    {#if nativeNotice}<p class="success" role="status">{nativeNotice}</p>{/if}
    {#if native}
      <form onsubmit={saveNative} aria-busy={nativeBusy}>
        <fieldset disabled={nativeBusy}>
          <label class="switch"
            ><input type="checkbox" bind:checked={native.enabled} /><span
              >Limiteur natif <strong>{native.enabled ? 'activé' : 'désactivé'}</strong></span
            ></label
          >
          <div class="rules">
            {#each native.rules as rule, i}<div class="rule">
                <label
                  >Cible<input required maxlength="100" bind:value={rule.label} placeholder="/api/ ou *:auth" /></label
                >
                <label
                  >Audience<select bind:value={rule.audience}
                    ><option value="">Tout le monde</option><option value="@guest">Non connectés</option><option
                      value="@auth">Connectés</option
                    ></select
                  ></label
                >
                <label
                  >Requêtes max.<input
                    type="number"
                    min="1"
                    max="1000000"
                    step="1"
                    required
                    bind:value={rule.maxRequests}
                  /></label
                >
                <label
                  >Période (secondes)<input
                    type="number"
                    min="1"
                    max="86400"
                    step="1"
                    required
                    bind:value={rule.duration}
                  /></label
                >
                <button
                  class="secondary remove"
                  type="button"
                  aria-label={`Retirer la règle ${i + 1}`}
                  onclick={() => {
                    if (native) native.rules = native.rules.filter((_, index) => index !== i)
                  }}>Retirer</button
                >
              </div>{/each}
          </div>
          <button
            class="secondary"
            type="button"
            disabled={native.rules.length >= 50}
            onclick={() => {
              if (native)
                native.rules = [...native.rules, { label: '/api/', audience: '@guest', maxRequests: 300, duration: 10 }]
            }}>+ Ajouter une règle</button
          >
        </fieldset>
        <p class="hint">
          La cible utilise la syntaxe PocketBase : par exemple <code>/api/</code> ou <code>*:auth</code>. {native.excludedIPCount}
          exception(s) IP existante(s) conservée(s).
        </p>
        <footer>
          <span class="hint">Application immédiate à cette base</span><button
            class="primary"
            disabled={nativeBusy || !nativeDirty}>Enregistrer cette base</button
          >
        </footer>
      </form>
    {/if}
  </section>
</div>

<style>
  .request-limits {
    display: grid;
    gap: 1.25rem;
    max-width: 76rem;
    color: var(--app-text);
  }
  .intro h2 {
    font-size: 1.5rem;
    letter-spacing: -0.035em;
    margin: 0.25rem 0 0.4rem;
    color: var(--app-text-strong);
  }
  .eyebrow,
  .number {
    font-size: 0.7rem;
    font-weight: 650;
    color: var(--app-text-muted);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  p {
    font-size: 0.8rem;
    line-height: 1.65;
    color: var(--app-text-muted);
    margin: 0.5rem 0 1rem;
  }
  .panel {
    padding: 1.4rem;
    border: 1px solid var(--app-border);
    border-radius: 0.65rem;
    background: var(--app-surface, var(--app-bg));
    min-width: 0;
  }
  header,
  header > div,
  footer {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    justify-content: space-between;
    flex-wrap: wrap;
  }
  header > div {
    justify-content: flex-start;
  }
  h3 {
    font-size: 1rem;
    font-weight: 650;
    margin: 0;
    color: var(--app-text-strong);
  }
  .number {
    border: 1px solid var(--app-border);
    padding: 0.35rem 0.4rem;
    border-radius: 0.3rem;
    font-variant-numeric: tabular-nums;
  }
  .badge {
    font-size: 0.68rem;
    color: var(--app-text-muted);
    background: var(--app-bg);
    border: 1px solid var(--app-border);
    padding: 0.3rem 0.5rem;
    border-radius: 2rem;
  }
  fieldset {
    border: 0;
    padding: 0;
    margin: 0;
    min-width: 0;
  }
  .ceilings {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 1rem;
  }
  label {
    display: grid;
    gap: 0.45rem;
    min-width: 0;
    font-size: 0.75rem;
    font-weight: 550;
    color: var(--app-text-muted);
  }
  input:not([type='checkbox']),
  select {
    width: 100%;
    min-width: 0;
    min-height: 2.5rem;
    background: var(--app-bg);
    border: 1px solid var(--app-border-strong, var(--app-border));
    border-radius: 0.35rem;
    padding: 0.5rem 0.65rem;
    color: var(--app-text-strong);
    font: inherit;
  }
  .ceilings input {
    font-size: 1.65rem;
    letter-spacing: -0.035em;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .web-ceiling {
    max-width: 20rem;
  }
  small,
  .hint {
    font-size: 0.7rem;
    line-height: 1.6;
    color: var(--app-text-muted);
    font-weight: 400;
  }
  .hint {
    margin: 0.9rem 0;
  }
  footer {
    padding-top: 1rem;
    margin-top: 1rem;
    border-top: 1px solid var(--app-border);
    justify-content: flex-end;
  }
  footer > .hint {
    margin-right: auto;
  }
  button {
    border: 1px solid var(--app-border-strong, var(--app-border));
    border-radius: 0.35rem;
    padding: 0.55rem 0.8rem;
    font-size: 0.75rem;
    font-weight: 550;
    cursor: pointer;
    min-height: 2.3rem;
  }
  .secondary {
    background: var(--app-bg);
    color: var(--app-text-strong);
  }
  .primary {
    background: var(--wa-color-brand-fill-loud, #446fed);
    color: white;
    border-color: transparent;
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  input:focus-visible,
  select:focus-visible,
  button:focus-visible {
    outline: 2px solid var(--wa-color-brand-fill-loud, #446fed);
    outline-offset: 2px;
  }
  .selection {
    display: grid;
    grid-template-columns: auto minmax(12rem, 26rem) auto;
    align-items: center;
    justify-content: start;
    gap: 0.75rem;
    margin: 1.2rem 0;
  }
  .switch {
    display: flex;
    align-items: center;
    gap: 0.65rem;
    padding: 0.8rem 0;
  }
  .switch input {
    width: 1rem;
    height: 1rem;
    accent-color: #446fed;
  }
  .switch strong {
    color: var(--app-text-strong);
  }
  .rules {
    display: grid;
    gap: 0.75rem;
    margin: 0.5rem 0 1rem;
  }
  .rule {
    display: grid;
    grid-template-columns: minmax(8rem, 2fr) minmax(8rem, 1.4fr) minmax(6rem, 1fr) minmax(7rem, 1fr) auto;
    gap: 0.75rem;
    align-items: end;
    padding: 0.85rem;
    border: 1px solid var(--app-border);
    border-radius: 0.4rem;
  }
  .error,
  .success {
    padding: 0.75rem 1rem;
    border: 1px solid currentColor;
    border-radius: 0.35rem;
    font-size: 0.8rem;
  }
  .error {
    color: var(--wa-color-danger-on-quiet, #bc403c);
  }
  .success {
    color: var(--wa-color-success-on-quiet, #31834e);
  }
  @media (max-width: 900px) {
    .ceilings {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .rule {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .remove {
      justify-self: start;
    }
  }
  @media (max-width: 540px) {
    .panel {
      padding: 1rem;
    }
    .ceilings {
      grid-template-columns: 1fr;
    }
    .selection {
      grid-template-columns: 1fr;
    }
    .selection label {
      grid-column: 1;
    }
    .rule {
      grid-template-columns: 1fr;
    }
    footer {
      justify-content: stretch;
    }
    footer button {
      flex: 1;
    }
    .badge {
      font-size: 0.63rem;
    }
  }
</style>
