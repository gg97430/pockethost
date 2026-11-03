<script lang="ts">
  import { onMount } from 'svelte'
  import { webRequest, webError, webDate, type WebAppEnvironment } from '$lib/webApps'
  import {
    emptyApplicationChecks,
    validateApplicationChecks,
    type ApplicationChecks,
    type JourneyCheck,
    type CheckRun,
  } from '$lib/webChecks'
  import { WEB_NO_CAPABILITIES, type WebCapabilityMap } from '$lib/webAccessView'
  export let appId: string
  export let environment: WebAppEnvironment[] = []
  export let capabilities: WebCapabilityMap = WEB_NO_CAPABILITIES
  $: canManage = capabilities['app.configure']
  type Binding = { id: string; instanceName: string; port: number; enabled: boolean }
  type Domain = { id: string; host: string; status: string }
  type Response = {
    settings: { revision: string; config: ApplicationChecks; hasSecret: Record<string, boolean> } | null
    history: CheckRun[]
    summary: Record<string, CheckRun>
    journeysActive: boolean
  }
  let config = emptyApplicationChecks(),
    revision = '',
    hasSecret: Record<string, boolean> = {},
    passwords: Record<string, string> = {},
    clear: Record<string, boolean> = {}
  let history: CheckRun[] = [],
    summary: Record<string, CheckRun> = {},
    bindings: Binding[] = [],
    domains: Domain[] = []
  let dirty = false,
    loading = true,
    refreshing = false,
    disposed = false,
    busy = '',
    error = '',
    message = '',
    selectedRun = ''
  let journeysActive = true
  $: suspended = !journeysActive && config.journeys.some((row) => row.enabled)
  const labels: Record<string, string> = {
    passed: 'Parcours réussi',
    failed: 'Parcours en échec',
    cancelled: 'Configuration modifiée',
    queued: 'En attente',
    running: 'En cours',
  }
  const reasons: Record<string, string> = {
    origin_unavailable: 'Domaine public inaccessible ou refusé.',
    authentication_failed: 'Le compte de test ne peut pas se connecter.',
    journey_failed: 'Une lecture ne correspond pas au résultat attendu.',
    configuration: 'Configuration du contrôle indisponible.',
    configuration_changed: 'La configuration ou les droits ont changé avant le contrôle.',
    worker_lost: 'Le worker n’a pas confirmé la fin du contrôle.',
    timeout: 'Le contrôle a dépassé sa durée maximale.',
  }
  const mutate = (change: () => void) => {
    change()
    config = { ...config }
    dirty = true
    message = ''
  }
  const load = async (initial = false) => {
    if (refreshing || disposed) return
    refreshing = true
    try {
      const value = await webRequest<Response>(`/${appId}/checks`)
      if (disposed) return
      if (!dirty && !busy && value.settings) {
        config = value.settings.config
        revision = value.settings.revision
        hasSecret = value.settings.hasSecret
      }
      history = value.history
      summary = value.summary
      journeysActive = value.journeysActive
      if (initial && canManage) {
        const [privateAccess, domainList] = await Promise.all([
          webRequest<{ bindings: Binding[] }>(`/${appId}/private-databases`),
          webRequest<{ items: Domain[] }>(`/${appId}/domains`),
        ])
        if (!disposed) {
          bindings = privateAccess.bindings.filter((row) => row.enabled)
          domains = domainList.items.filter((row) => row.status === 'active')
        }
      }
    } catch (e) {
      if (!disposed) error = webError(e)
    } finally {
      refreshing = false
      loading = false
    }
  }
  const addJourney = () =>
    mutate(
      () =>
        (config.journeys = [
          ...config.journeys,
          {
            id: crypto.randomUUID().replaceAll('-', '').slice(0, 15),
            name: 'Nouveau parcours',
            enabled: false,
            intervalMinutes: 5,
            domain: 'generated',
            auth: { kind: 'none', path: '', identityField: '', passwordField: '', identity: '', tokenField: '' },
            steps: [{ name: 'Accueil', path: '/', status: 200, jsonField: '', expected: null }],
          },
        ])
    )
  const changeAuth = (journey: JourneyCheck, kind: 'none' | 'json') =>
    mutate(() => {
      if (kind === 'json')
        journey.auth = {
          kind: 'json',
          path: '/api/auth/login',
          identityField: 'email',
          passwordField: 'password',
          identity: '',
          tokenField: 'token',
        }
      else journey.auth = { kind: 'none', path: '', identityField: '', passwordField: '', identity: '', tokenField: '' }
    })
  const save = async (event: SubmitEvent) => {
    event.preventDefault()
    busy = 'save'
    error = ''
    message = ''
    try {
      const checked = validateApplicationChecks(config)
      const secrets: Record<string, { password: string | null }> = {}
      for (const row of config.journeys) {
        const password = passwords[row.id]
        if (clear[row.id]) secrets[row.id] = { password: null }
        else if (password) secrets[row.id] = { password }
      }
      const value = await webRequest<{ revision: string }>(`/${appId}/checks`, {
        method: 'PUT',
        body: { config: checked, revision, secrets },
      })
      revision = value.revision
      dirty = false
      passwords = {}
      clear = {}
      message = 'Contrôles enregistrés. Les variables du site sont conservées.'
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
    if (!dirty) await load()
  }
  const run = async (id: string) => {
    busy = id
    error = ''
    message = ''
    try {
      await webRequest(`/${appId}/checks/run`, { method: 'POST', body: { journeyId: id } })
      message = 'Parcours ajouté à la file de contrôles.'
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
    await load()
  }
  onMount(() => {
    disposed = false
    void load(true)
    const timer = setInterval(() => {
      if (!document.hidden) void load()
    }, 5000)
    return () => {
      disposed = true
      passwords = {}
      clearInterval(timer)
    }
  })
</script>

<section class="web-panel checks-panel">
  <div class="web-row">
    <div>
      <span class="web-eyebrow">Fiabilité de l’application</span>
      <h2>Avant chaque déploiement</h2>
    </div>
    {#if dirty}<span class="web-status">Modifications à enregistrer</span>{/if}
  </div>
  <p class="web-help">
    Détectez les variables manquantes, les URL incohérentes et les accès BDD indisponibles avant de construire. Un
    contrôle refusé conserve la version publiée.
  </p>
  {#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}{#if message}<div
      class="web-alert web-alert--success"
      role="status"
    >
      {message}
    </div>{/if}
  {#if loading}<p class="web-muted" role="status">Chargement des contrôles…</p>{:else if canManage}
    <form class="web-form checks-form" onsubmit={save} oninput={() => (dirty = true)} onchange={() => (dirty = true)}>
      <label class="web-check"
        ><input type="checkbox" bind:checked={config.preflight.enabled} disabled={!!busy} /> Activer les contrôles avant déploiement</label
      >
      {#if config.preflight.enabled}
        <div class="check-section">
          <h3>Variables obligatoires</h3>
          {#each config.preflight.requiredEnvironment as row, index}<div class="checks-fields">
              <label class="web-field"
                ><span>Variable</span><input
                  bind:value={row.name}
                  list={'checks-env-' + appId}
                  maxlength="120"
                  required
                  disabled={!!busy}
                  placeholder="DATABASE_URL"
                /></label
              ><label class="web-field"
                ><span>Utilisation requise</span><select bind:value={row.scope} disabled={!!busy}
                  ><option value="runtime">Exécution</option><option value="build">Construction</option><option
                    value="both">Construction et exécution</option
                  ></select
                ></label
              ><button
                class="web-button"
                type="button"
                onclick={() =>
                  mutate(
                    () =>
                      (config.preflight.requiredEnvironment = config.preflight.requiredEnvironment.filter(
                        (_, i) => i !== index
                      ))
                  )}
                disabled={!!busy}>Retirer</button
              >
            </div>{/each}
          <button
            type="button"
            class="web-button"
            onclick={() =>
              mutate(
                () =>
                  (config.preflight.requiredEnvironment = [
                    ...config.preflight.requiredEnvironment,
                    { name: '', scope: 'runtime' },
                  ])
              )}
            disabled={!!busy || config.preflight.requiredEnvironment.length >= 100}>Ajouter une variable requise</button
          >
        </div>
        <div class="check-section">
          <h3>Accès aux bases</h3>
          <label class="web-check"
            ><input
              type="checkbox"
              bind:checked={config.preflight.privateDatabases}
              disabled={!!busy || !bindings.length}
            />
            Vérifier la santé des {bindings.length} liaison(s) privée(s)</label
          >
          {#if !bindings.length}<p class="web-help">
              Configurez une liaison BDD privée pour vérifier son accès direct.
            </p>{/if}
          {#each config.preflight.authentication as row, index}<div class="check-card">
              <div class="web-form-grid">
                <label class="web-field"
                  ><span>Base liée</span><select bind:value={row.bindingId} required disabled={!!busy}
                    ><option value="" disabled>Choisir une base</option>{#each bindings as binding}<option
                        value={binding.id}>{binding.instanceName} · port {binding.port}</option
                      >{/each}</select
                  ></label
                ><label class="web-field"
                  ><span>Collection de connexion</span><input
                    bind:value={row.collection}
                    maxlength="100"
                    required
                    disabled={!!busy}
                  /></label
                ><label class="web-field"
                  ><span>Variable de l’identifiant</span><input
                    bind:value={row.identityEnv}
                    list={'checks-env-' + appId}
                    required
                    disabled={!!busy}
                  /></label
                ><label class="web-field"
                  ><span>Variable privée du mot de passe</span><input
                    bind:value={row.passwordEnv}
                    list={'checks-env-' + appId}
                    required
                    disabled={!!busy}
                  /></label
                >
              </div>
              <button
                type="button"
                class="web-button"
                onclick={() =>
                  mutate(
                    () =>
                      (config.preflight.authentication = config.preflight.authentication.filter((_, i) => i !== index))
                  )}
                disabled={!!busy}>Retirer ce test de connexion</button
              >
            </div>{/each}
          <button
            type="button"
            class="web-button"
            onclick={() =>
              mutate(
                () =>
                  (config.preflight.authentication = [
                    ...config.preflight.authentication,
                    { bindingId: bindings[0]?.id || '', collection: '_superusers', identityEnv: '', passwordEnv: '' },
                  ])
              )}
            disabled={!!busy || !bindings.length || config.preflight.authentication.length >= 4}
            >Vérifier des identifiants BDD</button
          >
          <p class="web-help">
            Choisissez les noms des variables existantes. Leurs valeurs ne sont jamais affichées dans le résultat.
          </p>
        </div>
        <div class="check-section">
          <h3>Cohérence des URL</h3>
          {#each config.preflight.publicUrls as row, index}<div class="check-card">
              <div class="web-form-grid">
                <label class="web-field"
                  ><span>Variable contenant l’URL</span><input
                    bind:value={row.name}
                    list={'checks-env-' + appId}
                    required
                    disabled={!!busy}
                  /></label
                ><label class="web-field"
                  ><span>Destination attendue</span><select
                    bind:value={row.target}
                    disabled={!!busy}
                    onchange={(event) => {
                      row.target = event.currentTarget.value as 'application' | 'privateDatabase'
                      if (row.target === 'application') delete row.bindingId
                      else row.bindingId = bindings[0]?.id || ''
                      dirty = true
                    }}
                    ><option value="application">Domaine public de cette application</option><option
                      value="privateDatabase"
                      disabled={!bindings.length}>Base privée sur localhost</option
                    ></select
                  ></label
                >{#if row.target === 'privateDatabase'}<label class="web-field"
                    ><span>Base privée</span><select bind:value={row.bindingId} required disabled={!!busy}
                      >{#each bindings as binding}<option value={binding.id}
                          >{binding.instanceName} · port {binding.port}</option
                        >{/each}</select
                    ></label
                  >{/if}
              </div>
              <button
                class="web-button"
                type="button"
                onclick={() =>
                  mutate(
                    () => (config.preflight.publicUrls = config.preflight.publicUrls.filter((_, i) => i !== index))
                  )}
                disabled={!!busy}>Retirer</button
              >
            </div>{/each}
          <button
            class="web-button"
            type="button"
            onclick={() =>
              mutate(
                () =>
                  (config.preflight.publicUrls = [...config.preflight.publicUrls, { name: '', target: 'application' }])
              )}
            disabled={!!busy || config.preflight.publicUrls.length >= 20}>Vérifier une URL</button
          >
        </div>
        <p class="web-help">
          Les résultats seront dans le journal du prochain déploiement. Un dépôt peut aussi déclarer ses variables
          requises dans <code>.kolab/checks.json</code>.
        </p>
      {/if}
      <div class="check-section">
        <div class="web-row">
          <div>
            <h2>Parcours utilisateur</h2>
            <p class="web-help">
              Vérifiez une connexion et les lectures utiles de votre application. Ces résultats complètent sa santé
              technique.
            </p>
          </div>
          <button type="button" class="web-button" onclick={addJourney} disabled={!!busy || config.journeys.length >= 6}
            >Ajouter un parcours</button
          >
        </div>
        {#if suspended}<p class="web-alert" role="status">
            Les parcours automatiques sont suspendus car les droits de leur auteur ont changé. Réenregistrez les
            contrôles pour les reprendre avec vos droits actuels.
          </p>{/if}
        {#if !config.journeys.length}<p class="web-muted">Aucun parcours configuré.</p>{/if}
        {#each config.journeys as journey, index (journey.id)}{@const last = summary[journey.id]}
          <article class="check-card">
            <div class="web-row">
              <h3>{journey.name || 'Nouveau parcours'}</h3>
              <span
                class="web-status"
                data-tone={last?.status === 'passed' ? 'success' : last?.status === 'failed' ? 'error' : 'neutral'}
                >{last ? labels[last.status] || last.status : 'Pas encore contrôlé'}</span
              >
            </div>
            <div class="web-form-grid">
              <label class="web-field"
                ><span>Nom du parcours</span><input
                  bind:value={journey.name}
                  required
                  maxlength="80"
                  disabled={!!busy}
                /></label
              ><label class="web-field"
                ><span>Contrôler toutes les · minutes</span><input
                  type="number"
                  min="1"
                  max="1440"
                  step="1"
                  required
                  bind:value={journey.intervalMinutes}
                  disabled={!!busy}
                /></label
              ><label class="web-field"
                ><span>Domaine</span><select bind:value={journey.domain} disabled={!!busy}
                  ><option value="generated">Domaine généré de l’application</option>{#each domains as domain}<option
                      value={domain.id}>{domain.host}</option
                    >{/each}</select
                ></label
              ><label class="web-field"
                ><span>Connexion</span><select
                  bind:value={journey.auth.kind}
                  disabled={!!busy}
                  onchange={(event) => changeAuth(journey, event.currentTarget.value as 'none' | 'json')}
                  ><option value="none">Lecture sans connexion</option><option value="json"
                    >Compte de test · connexion JSON</option
                  ></select
                ></label
              >
            </div>
            {#if journey.auth.kind === 'json'}<div class="check-section">
                <p class="web-help">
                  Utilisez un compte dédié à la lecture. Seule la connexion envoie un formulaire ; les autres étapes
                  lisent des pages ou des API.
                </p>
                <div class="web-form-grid">
                  <label class="web-field"
                    ><span>Adresse de connexion</span><input
                      bind:value={journey.auth.path}
                      placeholder="/api/auth/login"
                      required
                      disabled={!!busy}
                    /></label
                  ><label class="web-field"
                    ><span>Identifiant du compte de test</span><input
                      bind:value={journey.auth.identity}
                      maxlength="254"
                      autocomplete="off"
                      required
                      disabled={!!busy}
                    /></label
                  ><label class="web-field"
                    ><span>Nom du champ identifiant</span><input
                      bind:value={journey.auth.identityField}
                      required
                      disabled={!!busy}
                    /></label
                  ><label class="web-field"
                    ><span>Nom du champ mot de passe</span><input
                      bind:value={journey.auth.passwordField}
                      required
                      disabled={!!busy}
                    /></label
                  ><label class="web-field"
                    ><span
                      >{hasSecret[journey.id]
                        ? 'Nouveau mot de passe · vide pour conserver'
                        : 'Mot de passe du compte de test'}</span
                    ><input
                      type="password"
                      bind:value={passwords[journey.id]}
                      maxlength="4096"
                      autocomplete="new-password"
                      required={journey.enabled && !hasSecret[journey.id] && !clear[journey.id]}
                      disabled={!!busy || clear[journey.id]}
                    /></label
                  ><label class="web-field"
                    ><span>Champ du jeton · vide pour une session cookie</span><input
                      bind:value={journey.auth.tokenField}
                      placeholder="token"
                      disabled={!!busy}
                    /></label
                  >
                </div>
                {#if hasSecret[journey.id]}<label class="web-check"
                    ><input type="checkbox" bind:checked={clear[journey.id]} disabled={!!busy} /> Effacer le mot de passe
                    enregistré</label
                  >{/if}
              </div>{/if}
            <div class="check-section">
              <h3>Lectures à vérifier</h3>
              {#each journey.steps as step, stepIndex}<div class="check-step">
                  <div class="web-form-grid">
                    <label class="web-field"
                      ><span>Étape {stepIndex + 1}</span><input
                        bind:value={step.name}
                        maxlength="80"
                        required
                        disabled={!!busy}
                      /></label
                    ><label class="web-field"
                      ><span>Page ou API · GET</span><input
                        bind:value={step.path}
                        placeholder="/api/me"
                        maxlength="1000"
                        required
                        disabled={!!busy}
                      /></label
                    ><label class="web-field"
                      ><span>Code HTTP attendu</span><input
                        type="number"
                        bind:value={step.status}
                        min="200"
                        max="499"
                        required
                        disabled={!!busy}
                      /></label
                    ><label class="web-field"
                      ><span>Champ JSON à vérifier · facultatif</span><input
                        bind:value={step.jsonField}
                        placeholder="active"
                        disabled={!!busy}
                      /></label
                    >{#if step.jsonField}<label class="web-field"
                        ><span>Valeur attendue · vide pour vérifier la présence</span><input
                          value={step.expected ?? ''}
                          oninput={(event) => {
                            step.expected = event.currentTarget.value || null
                            dirty = true
                          }}
                          maxlength="256"
                          placeholder="true"
                          disabled={!!busy}
                        /></label
                      >{/if}
                  </div>
                  <button
                    type="button"
                    class="web-button"
                    onclick={() => mutate(() => (journey.steps = journey.steps.filter((_, i) => i !== stepIndex)))}
                    disabled={!!busy || journey.steps.length === 1}>Retirer l’étape</button
                  >
                </div>{/each}<button
                type="button"
                class="web-button"
                onclick={() =>
                  mutate(
                    () =>
                      (journey.steps = [
                        ...journey.steps,
                        { name: 'Nouvelle lecture', path: '/', status: 200, jsonField: '', expected: null },
                      ])
                  )}
                disabled={!!busy || journey.steps.length >= 10}>Ajouter une lecture</button
              >
            </div>
            <label class="web-check"
              ><input type="checkbox" bind:checked={journey.enabled} disabled={!!busy} /> Activer les contrôles automatiques</label
            >
            <div class="web-actions">
              <button type="button" class="web-button" onclick={() => run(journey.id)} disabled={!!busy || dirty}
                >{busy === journey.id ? 'Ajout…' : 'Tester maintenant'}</button
              ><button
                type="button"
                class="web-button"
                onclick={() => mutate(() => (config.journeys = config.journeys.filter((_, i) => i !== index)))}
                disabled={!!busy}>Retirer le parcours</button
              >
            </div>
            {#if last?.finishedAt}<p class="web-help">Dernier contrôle : {webDate(last.finishedAt)}</p>{/if}
          </article>{/each}
      </div>
      <datalist id={'checks-env-' + appId}
        >{#each environment as row}<option value={row.name}></option>{/each}</datalist
      >
      <div class="web-actions">
        <button class="web-button web-button--primary" type="submit" disabled={!!busy || (!dirty && !suspended)}
          >{busy === 'save' ? 'Enregistrement…' : 'Enregistrer les contrôles'}</button
        >{#if dirty}<span class="web-help">Enregistrez avant de tester un parcours.</span>{/if}
      </div>
    </form>
  {:else}<p class="web-help">La configuration de ces contrôles est réservée aux administrateurs du projet.</p>{/if}
</section>
<section class="web-panel checks-panel">
  <h2>Résultats fonctionnels</h2>
  <p class="web-help">
    Derniers contrôles de cette application. Les mots de passe, jetons et contenus des réponses restent masqués.
  </p>
  <div class="web-table-wrap">
    <table>
      <thead><tr><th>Parcours</th><th>Résultat</th><th>Dernier contrôle</th><th>Détail</th></tr></thead><tbody
        >{#each history as run (run.id)}<tr
            ><td>{run.journeyName}</td><td
              ><span
                class="web-status"
                data-tone={run.status === 'passed' ? 'success' : run.status === 'failed' ? 'error' : 'neutral'}
                >{labels[run.status] || run.status}</span
              ></td
            ><td>{webDate(run.finishedAt || run.startedAt || run.scheduledAt)}</td><td
              ><button
                class="web-button"
                type="button"
                onclick={() => (selectedRun = selectedRun === run.id ? '' : run.id)}
                >{selectedRun === run.id ? 'Masquer' : 'Voir le détail'}</button
              ></td
            ></tr
          >{#if selectedRun === run.id}<tr
              ><td colspan="4"
                >{#if run.result?.errorCode}<p class="web-help">
                    {reasons[run.result.errorCode] || 'Contrôle non confirmé.'}
                  </p>{/if}{#each run.result?.steps || [] as step}<p class="check-result">
                    <strong>{step.name}</strong><span
                      >{step.status === 'passed' ? 'Réussie' : 'Échec'} · HTTP {step.httpStatus || '—'} · {step.durationMs}
                      ms</span
                    >
                  </p>{/each}</td
              ></tr
            >{/if}{:else}<tr><td colspan="4">Aucun contrôle fonctionnel enregistré.</td></tr>{/each}</tbody
      >
    </table>
  </div>
</section>

<style>
  .checks-panel {
    min-width: 0;
  }
  .checks-form,
  .check-section,
  .check-card,
  .check-step {
    display: grid;
    gap: 1rem;
    min-width: 0;
  }
  .check-section {
    padding-block: 1rem;
    border-top: 1px solid var(--web-border);
  }
  .check-card {
    padding: 1rem;
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius);
  }
  .check-step {
    padding-bottom: 1rem;
    border-bottom: 1px solid var(--web-border);
  }
  .checks-fields {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
    gap: 0.75rem;
    align-items: end;
  }
  .check-result {
    display: flex;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 0.75rem;
    margin-block: 0.5rem;
  }
  .check-result span {
    color: var(--web-muted);
  }
  @media (max-width: 650px) {
    .checks-fields {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
