<script lang="ts">
  import { onMount } from 'svelte'
  import { webRequest, webError } from '$lib/webApps'
  import { WEB_NO_CAPABILITIES, type WebCapabilityMap } from '$lib/webAccessView'
  export let appId: string
  export let onchanged: () => void | Promise<void> = () => {}
  export let capabilities: WebCapabilityMap = WEB_NO_CAPABILITIES
  type Resources = { cpuMillicores: number; memoryMiB: number; pids: number }
  type Profile = {
    kind: string
    port: number | null
    dockerfile: string
    image: string
    command: string[]
    entrypoint: string[]
    resources: Resources
    buildTarget?: string
    startCommand?: string
    buildCommand?: string
    pruneDependencies?: boolean
    compose?: { front: string; services: Record<string, unknown> }
  }
  type Value = {
    profile: Profile
    revision: string
    registry: { server: string; username: string; hasPassword: boolean }
    capabilities: { maxCpuMillicores: number; maxMemoryMiB: number; maxPids: number; railpackAvailable?: boolean }
  }
  type Preview = {
    profile: Profile
    changes: string[]
    requiresDeploy: boolean
    strategy: string
    resources: Resources
    warnings: string[]
  }
  let value: Value | null = null,
    busy = '',
    error = '',
    message = '',
    disposed = false,
    dirty = false
  let kind = 'nextjs',
    port = 3000,
    dockerfile = 'Dockerfile',
    image = '',
    commandText = '[]',
    entrypointText = '[]',
    buildTarget = '',
    startCommand = '',
    buildCommand = '',
    pruneDependencies = false,
    cpu = 500,
    memory = 768,
    pids = 256,
    composeText = '',
    workerSource = 'dockerfile'
  let registryServer = '',
    registryUser = '',
    registryPassword = '',
    clearRegistry = false,
    preview: Preview | null = null,
    previewFingerprint = ''
  const choices = [
    ['nextjs', 'Next.js automatique'],
    ['auto', 'Automatique (Railpack)'],
    ['dockerfile', 'Dockerfile du dépôt'],
    ['image', 'Image Docker immuable'],
    ['compose', 'Services groupés · JSON'],
    ['worker', 'Worker · sans URL publique'],
  ]
  const names: Record<string, string> = {
    kind: 'Moteur',
    port: 'Port interne',
    dockerfile: 'Dockerfile',
    image: 'Image',
    command: 'Commande',
    entrypoint: 'Entrypoint',
    resources: 'Quotas',
    compose: 'Services',
    buildTarget: 'Étape de construction',
    startCommand: 'Commande de démarrage',
    buildCommand: 'Commande de construction',
    pruneDependencies: 'Image allégée',
  }
  const sample = {
    front: 'web',
    services: {
      web: {
        dockerfile: 'Dockerfile',
        port: 3000,
        environment: ['POCKETBASE_URL'],
        resources: { cpuMillicores: 500, memoryMiB: 768, pids: 256 },
      },
    },
  }
  const forget = () => {
    dirty = true
    preview = null
    previewFingerprint = ''
    message = ''
  }
  const hydrate = (next: Value) => {
    value = next
    const profile = next.profile
    kind = profile.kind
    port = profile.port || 3000
    dockerfile = profile.dockerfile || 'Dockerfile'
    image = profile.image || ''
    commandText = JSON.stringify(profile.command || [])
    entrypointText = JSON.stringify(profile.entrypoint || [])
    buildTarget = profile.buildTarget || ''
    startCommand = profile.startCommand || ''
    buildCommand = profile.buildCommand || ''
    pruneDependencies = !!profile.pruneDependencies
    cpu = profile.resources.cpuMillicores
    memory = profile.resources.memoryMiB
    pids = profile.resources.pids
    composeText = JSON.stringify(profile.compose || sample, null, 2)
    workerSource = profile.image ? 'image' : 'dockerfile'
    registryServer = next.registry.server || ''
    registryUser = next.registry.username || ''
    registryPassword = ''
    clearRegistry = false
    preview = null
    dirty = false
  }
  const read = async () => {
    busy = 'load'
    error = ''
    try {
      const next = await webRequest<Value>(`/${appId}/runtime/profile`)
      if (!disposed) hydrate(next)
    } catch (e) {
      if (!disposed) error = webError(e)
    } finally {
      busy = ''
    }
  }
  const argumentsOf = (text: string) => {
    const parsed = JSON.parse(text || '[]')
    if (!Array.isArray(parsed) || parsed.some((value) => typeof value !== 'string'))
      throw new Error('La commande et l’entrypoint utilisent des tableaux JSON de chaînes.')
    return parsed as string[]
  }
  const candidate = (): Profile => ({
    kind,
    port: kind === 'worker' ? null : kind === 'nextjs' ? 3000 : Number(port),
    dockerfile,
    image: kind === 'worker' && workerSource === 'dockerfile' ? '' : image,
    command: kind === 'auto' ? [] : argumentsOf(commandText),
    entrypoint: kind === 'auto' ? [] : argumentsOf(entrypointText),
    resources: { cpuMillicores: Number(cpu), memoryMiB: Number(memory), pids: Number(pids) },
    ...(buildTarget && kind !== 'auto' ? { buildTarget } : {}),
    ...(kind === 'auto' && startCommand.trim() ? { startCommand: startCommand.trim() } : {}),
    ...(kind === 'auto' && buildCommand.trim() ? { buildCommand: buildCommand.trim() } : {}),
    ...(kind === 'auto' && pruneDependencies ? { pruneDependencies: true } : {}),
    ...(kind === 'compose' ? { compose: JSON.parse(composeText) } : {}),
  })
  const inspect = async () => {
    busy = 'preview'
    error = ''
    message = ''
    try {
      const profile = candidate()
      preview = await webRequest<Preview>(`/${appId}/runtime/profile/preview`, { method: 'POST', body: { profile } })
      previewFingerprint = JSON.stringify(profile)
    } catch (e) {
      error = webError(e)
      preview = null
    } finally {
      busy = ''
    }
  }
  const save = async () => {
    if (!value || !preview) return
    busy = 'save'
    error = ''
    message = ''
    try {
      const profile = candidate()
      if (JSON.stringify(profile) !== previewFingerprint)
        throw new Error('Le profil a changé. Préparez un nouvel aperçu.')
      const registry = clearRegistry
        ? { clear: true }
        : registryServer || registryUser || registryPassword
          ? {
              server: registryServer.trim(),
              username: registryUser.trim(),
              ...(registryPassword ? { password: registryPassword } : {}),
            }
          : undefined
      const next = await webRequest<Value>(`/${appId}/runtime/profile`, {
        method: 'PATCH',
        body: { profile, revision: value.revision, ...(registry ? { registry } : {}) },
      })
      hydrate(next)
      message =
        'Profil enregistré. Lancez un déploiement pour utiliser ce moteur ; la version publiée reste disponible jusque-là.'
      await onchanged()
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  onMount(() => {
    disposed = false
    void read()
    return () => {
      disposed = true
      registryPassword = ''
      composeText = ''
      commandText = ''
      entrypointText = ''
    }
  })
</script>

<section class="web-panel runtime-profile">
  <div class="web-row">
    <div>
      <span class="web-eyebrow">Construction et exécution</span>
      <h2>Moteur du site</h2>
    </div>
    {#if dirty}<span class="web-status" data-tone="warning">Brouillon non enregistré</span>{/if}
  </div>
  <p class="web-help">
    Choisissez comment produire et démarrer l’application. Un aperçu vérifie les options et annonce la stratégie de
    publication avant l’enregistrement.
  </p>
  {#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}{#if message}<div
      class="web-alert web-alert--success"
      role="status"
    >
      {message}
    </div>{/if}
  {#if !value}<p class="web-muted" role="status">Chargement du moteur…</p>{:else}<form
      class="web-form"
      onsubmit={(event) => {
        event.preventDefault()
        void inspect()
      }}
      oninput={forget}
      onchange={forget}
    >
      <fieldset class="web-fieldset" disabled={!capabilities['app.configure']}>
        <div class="web-form-grid">
          <label class="web-field"
            ><span>Moteur</span><select bind:value={kind} disabled={!!busy}
              >{#each choices as [value, label]}<option {value}>{label}</option>{/each}</select
            ></label
          >{#if !['nextjs', 'worker'].includes(kind)}<label class="web-field"
              ><span>Port HTTP interne du frontal</span><input
                type="number"
                bind:value={port}
                min="1"
                max="65535"
                required
                disabled={!!busy}
              /><span class="web-help"
                >{kind === 'auto'
                  ? 'Injecté dans la variable PORT ; l’application doit l’écouter.'
                  : 'L’application doit écouter sur ce port dans son conteneur.'}</span
              ></label
            >{/if}
        </div>
        {#if kind === 'nextjs'}<div class="profile-note">
            <strong>Configuration Next.js gérée</strong>
            <p class="web-help">
              Le projet est construit avec son lockfile, puis démarré sur le port 3000. La sortie standalone est
              utilisée lorsqu’elle existe.
            </p>
          </div>{/if}
        {#if kind === 'auto'}<div class="profile-note">
            <strong>Construction automatique (Railpack)</strong>
            <p class="web-help">
              Railpack détecte le langage du dossier du projet (Node, Vite, Astro, site statique, Python, Go, PHP,
              Ruby…), installe les outils et construit l’image dans le builder isolé. Un fichier railpack.json ou des
              variables de construction RAILPACK_* permettent d’ajuster la détection. Le fournisseur détecté apparaît
              dans le journal de construction.
            </p>
          </div>
          {#if value.capabilities.railpackAvailable === false}<div class="web-alert">
              Railpack n’est pas installé sur le worker : la construction échouera tant que l’administrateur ne l’a pas
              installé.
            </div>{/if}
          <div class="web-form-grid">
            <label class="web-field"
              ><span>Commande de démarrage · facultative</span><input
                bind:value={startCommand}
                maxlength="1000"
                disabled={!!busy}
                placeholder="npm run start"
                spellcheck="false"
              /><span class="web-help">Vide : commande détectée (script start, Procfile…).</span></label
            ><label class="web-field"
              ><span>Commande de construction · facultative</span><input
                bind:value={buildCommand}
                maxlength="1000"
                disabled={!!busy}
                placeholder="npm run build"
                spellcheck="false"
              /></label
            >
          </div>
          <label class="web-check"
            ><input type="checkbox" bind:checked={pruneDependencies} disabled={!!busy} /> Alléger l’image : retirer les dépendances
            de développement après la construction</label
          >
          <p class="web-help">
            Image plus petite, donc déploiement plus rapide. À laisser décoché si le site utilise une dépendance de
            développement au démarrage (tsx, typescript…). Une variable RAILPACK_PRUNE_DEPS du site reste prioritaire.
          </p>{/if}
        {#if kind === 'worker'}<label class="web-field"
            ><span>Source du worker</span><select bind:value={workerSource} disabled={!!busy}
              ><option value="dockerfile">Construire le Dockerfile du dépôt</option><option value="image"
                >Utiliser une image immuable</option
              ></select
            ></label
          >
          <div class="web-alert">
            Ce moteur exécute un processus sans route HTTP publique. Configurez sa commande et ses ressources.
          </div>{/if}
        {#if kind === 'dockerfile' || (kind === 'worker' && workerSource === 'dockerfile')}<div class="web-form-grid">
            <label class="web-field"
              ><span>Chemin du Dockerfile</span><input
                bind:value={dockerfile}
                required
                maxlength="200"
                disabled={!!busy}
                placeholder="Dockerfile"
              /><span class="web-help">Chemin relatif dans le dépôt, sans remontée de dossier.</span></label
            ><label class="web-field"
              ><span>Étape de construction · facultative</span><input
                bind:value={buildTarget}
                maxlength="31"
                disabled={!!busy}
                placeholder="production"
              /></label
            >
          </div>{/if}
        {#if kind === 'image' || (kind === 'worker' && workerSource === 'image')}<label class="web-field"
            ><span>Image OCI fixée par digest SHA-256</span><input
              bind:value={image}
              required
              disabled={!!busy}
              placeholder="ghcr.io/organisation/site@sha256:…"
              spellcheck="false"
            /><span class="web-help"
              >Une référence immuable est requise. Le registre privé peut être configuré ci-dessous.</span
            ></label
          >{/if}
        {#if kind === 'compose'}<label class="web-field"
            ><span>Services groupés · configuration JSON</span><textarea
              class="profile-code"
              bind:value={composeText}
              rows="16"
              spellcheck="false"
              disabled={!!busy}
            ></textarea><span class="web-help"
              >Un frontal et quatre services maximum. Référencez les noms des variables enregistrées, puis les volumes
              nommés déclarés dans Stockage. Les quotas se règlent par service.</span
            ></label
          >
          <details class="profile-note">
            <summary>Voir un exemple de service</summary>
            <pre>{JSON.stringify(sample, null, 2)}</pre>
          </details>{/if}
        {#if kind !== 'nextjs' && kind !== 'compose' && kind !== 'auto'}<div class="web-form-grid">
            <label class="web-field"
              ><span>Commande · tableau JSON</span><textarea
                class="profile-code"
                bind:value={commandText}
                rows="2"
                disabled={!!busy}
                spellcheck="false"
                placeholder={'["node", "server.js"]'}
              ></textarea><span class="web-help"
                >[] conserve la commande de l’image. Chaque argument est une entrée distincte.</span
              ></label
            ><label class="web-field"
              ><span>Entrypoint · tableau JSON</span><textarea
                class="profile-code"
                bind:value={entrypointText}
                rows="2"
                disabled={!!busy}
                spellcheck="false"
                placeholder="[]"
              ></textarea></label
            >
          </div>{/if}
        {#if kind !== 'compose'}<fieldset class="profile-quota">
            <legend>Ressources de cette application</legend><label class="web-field"
              ><span>CPU · millicœurs</span><input
                type="number"
                bind:value={cpu}
                min="50"
                max={value.capabilities.maxCpuMillicores}
                step="50"
                required
                disabled={!!busy}
              /><span class="web-help">1 000 millicœurs = un cœur.</span></label
            ><label class="web-field"
              ><span>Mémoire · Mio</span><input
                type="number"
                bind:value={memory}
                min="64"
                max={value.capabilities.maxMemoryMiB}
                step="1"
                required
                disabled={!!busy}
              /></label
            ><label class="web-field"
              ><span>Processus maximum</span><input
                type="number"
                bind:value={pids}
                min="16"
                max={value.capabilities.maxPids}
                step="1"
                required
                disabled={!!busy}
              /></label
            >
          </fieldset>{/if}
        <details class="profile-registry">
          <summary>Identifiants d’un registre privé</summary>
          <div class="web-form-grid">
            <label class="web-field"
              ><span>Serveur du registre</span><input
                bind:value={registryServer}
                disabled={!!busy || clearRegistry}
                placeholder="ghcr.io"
                autocomplete="off"
              /></label
            ><label class="web-field"
              ><span>Utilisateur</span><input
                bind:value={registryUser}
                disabled={!!busy || clearRegistry}
                autocomplete="off"
              /></label
            ><label class="web-field web-field--full"
              ><span>Jeton ou mot de passe</span><input
                type="password"
                bind:value={registryPassword}
                disabled={!!busy || clearRegistry}
                autocomplete="new-password"
                placeholder={value.registry.hasPassword
                  ? 'Enregistré · laissez vide pour conserver'
                  : 'Jeton de lecture du registre'}
              /><span class="web-help"
                >Le secret reste masqué après enregistrement. Le workflow CI peut transmettre un identifiant GHCR
                éphémère pour un déploiement précis.</span
              ></label
            >
          </div>
          {#if value.registry.hasPassword}<label class="web-check"
              ><input type="checkbox" bind:checked={clearRegistry} disabled={!!busy} /> Effacer les identifiants enregistrés</label
            >{/if}
        </details>
      </fieldset>
      <div class="web-actions">
        {#if capabilities['app.configure']}<button
            class="web-button web-button--primary"
            type="submit"
            disabled={!!busy}>{busy === 'preview' ? 'Vérification…' : 'Préparer l’aperçu du profil'}</button
          >{:else}<span class="web-help">Consultation seule : le moteur est réglé par un administrateur du projet.</span
          >{/if}<button class="web-button" type="button" onclick={read} disabled={!!busy}>Recharger</button>
      </div>
    </form>{/if}
  {#if preview && capabilities['app.configure']}<section class="profile-preview" aria-live="polite">
      <div class="web-row">
        <h3>Aperçu du prochain déploiement</h3>
        <span class="web-status" data-tone={preview.strategy === 'recreate' ? 'warning' : 'neutral'}
          >{preview.strategy === 'recreate' ? 'Redémarrage avec interruption' : 'Publication progressive'}</span
        >
      </div>
      <p class="web-help">
        Champs concernés : {preview.changes.map((name) => names[name] || name).join(', ') ||
          'aucun changement de profil'}. Budget : {preview.resources.cpuMillicores} millicœurs, {preview.resources
          .memoryMiB} Mio, {preview.resources.pids} processus.
      </p>
      {#each preview.warnings as warning}<p class="web-alert">{warning}</p>{/each}
      <p class="web-help">
        La capacité réellement disponible sera contrôlée au lancement. Cet aperçu ne réserve pas de ressources et ne
        lance aucune migration.
      </p>
      <div>
        <button class="web-button web-button--primary" type="button" onclick={save} disabled={!!busy}
          >{busy === 'save' ? 'Enregistrement…' : 'Confirmer et enregistrer ce profil'}</button
        >
      </div>
    </section>{/if}
</section>

<style>
  .runtime-profile {
    display: grid;
    gap: 1rem;
    min-width: 0;
  }
  .runtime-profile p {
    margin: 0;
  }
  .profile-note,
  .profile-preview {
    display: grid;
    gap: 0.8rem;
    padding: 1rem;
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius, 6px);
  }
  .profile-note strong {
    font-size: 0.8125rem;
  }
  .profile-code,
  .profile-note pre {
    font:
      12px/1.6 ui-monospace,
      SFMono-Regular,
      monospace;
  }
  .profile-note pre {
    overflow: auto;
    white-space: pre-wrap;
    margin: 0;
  }
  .profile-quota {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.9rem;
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius, 6px);
    padding: 1rem;
    min-width: 0;
  }
  .profile-quota legend {
    padding: 0 0.4rem;
    font-size: 0.8rem;
  }
  .profile-registry {
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius, 6px);
    padding: 1rem;
  }
  .profile-registry summary {
    cursor: pointer;
    font-size: 0.8125rem;
    font-weight: 600;
  }
  .profile-registry[open] > summary {
    margin-bottom: 1rem;
  }
  .profile-preview {
    background: var(--web-surface-soft);
  }
  .profile-preview h3 {
    margin: 0;
    font-size: 0.875rem;
  }
  @media (max-width: 650px) {
    .profile-quota {
      grid-template-columns: 1fr;
    }
  }
</style>
