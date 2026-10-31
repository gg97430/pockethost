<script lang="ts">
  import { onMount } from 'svelte'
  import { webRequest, webError, webDate, type WebApp } from '$lib/webApps'
  import { webCiWorkflow, webCiDockerfile, webCiDockerignore, WEB_CI_DEFAULT_RUNNER } from '$lib/webCi'
  export let appId: string
  type Token = {
    id: string
    name: string
    imageRepository: string
    expiresAt: string
    revokedAt: string
    lastUsedAt: string
    created: string
  }
  let items: Token[] = [],
    deployUrl = '',
    branch = 'main',
    kind = '',
    name = 'GitHub Actions',
    imageRepository = '',
    days = 30,
    dockerfile = 'Dockerfile.pockethost-ci',
    runnerLabels = WEB_CI_DEFAULT_RUNNER
  let busy = '',
    error = '',
    message = '',
    issuedToken = '',
    issued: Token | null = null,
    copied = false,
    revoking = '',
    disposed = false
  const load = async () => {
    busy = 'load'
    error = ''
    try {
      const [result, app, runtime] = await Promise.all([
        webRequest<{ items: Token[]; deployUrl: string }>(`/${appId}/deploy-tokens`),
        webRequest<{ app: WebApp }>(`/${appId}`),
        webRequest<{ profile: { kind: string } }>(`/${appId}/runtime/profile`),
      ])
      if (disposed) return
      items = result.items
      deployUrl = result.deployUrl
      branch = app.app.branch
      kind = runtime.profile.kind
      if (!imageRepository) imageRepository = 'ghcr.io/' + app.app.repository.toLowerCase()
    } catch (e) {
      if (!disposed) error = webError(e)
    } finally {
      busy = ''
    }
  }
  const create = async (event: SubmitEvent) => {
    event.preventDefault()
    busy = 'create'
    error = ''
    message = ''
    issuedToken = ''
    issued = null
    copied = false
    try {
      const result = await webRequest<{ token: string; item: Token; deployUrl: string }>(`/${appId}/deploy-tokens`, {
        method: 'POST',
        body: { name: name.trim(), imageRepository: imageRepository.trim(), expiresInDays: Number(days) },
      })
      issuedToken = result.token
      issued = result.item
      deployUrl = result.deployUrl
      items = [result.item, ...items]
      message = 'Jeton créé. Copiez-le maintenant dans le secret GitHub Actions POCKETHOST_DEPLOY_TOKEN.'
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const revoke = async (token: Token) => {
    busy = token.id
    error = ''
    message = ''
    try {
      const result = await webRequest<{ item: Token }>(`/${appId}/deploy-tokens/${token.id}`, { method: 'DELETE' })
      items = items.map((row) => (row.id === token.id ? result.item : row))
      if (issued?.id === token.id) {
        issuedToken = ''
        issued = null
      }
      revoking = ''
      message = 'Jeton révoqué. Les prochains appels utilisant cette clé seront refusés.'
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(issuedToken)
      copied = true
    } catch {
      error = 'Sélectionnez le jeton pour le copier manuellement.'
    }
  }
  const download = (text: string, filename: string) => {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename
    anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  const workflow = (repository = imageRepository) => {
    try {
      download(
        webCiWorkflow({ appId, imageRepository: repository.trim(), deployUrl, branch, dockerfile, runnerLabels }),
        'pockethost.yml'
      )
    } catch (e) {
      error = webError(e)
    }
  }
  const status = (token: Token) =>
    token.revokedAt ? 'Révoqué' : Date.parse(token.expiresAt) <= Date.now() ? 'Expiré' : 'Actif'
  onMount(() => {
    disposed = false
    void load()
    return () => {
      disposed = true
      issuedToken = ''
      issued = null
    }
  })
</script>

<section class="web-panel ci-panel">
  <div class="web-row">
    <div>
      <span class="web-eyebrow">Construire hors du serveur</span>
      <h2>Déploiement depuis GitHub Actions</h2>
    </div>
    <span class="web-status" data-tone={['image', 'worker'].includes(kind) ? 'success' : 'warning'}
      >{['image', 'worker'].includes(kind) ? 'Moteur compatible CI' : 'Moteur Image ou Worker requis'}</span
    >
  </div>
  <p class="web-help">
    GitHub construit l’image, la publie dans GHCR puis demande son déploiement par digest. Le serveur conserve les
    contrôles de santé, les variables et le retour arrière.
  </p>
  {#if !['image', 'worker'].includes(kind)}<div class="web-alert">
      Pour recevoir une image CI, configurez le moteur Image Docker ou Worker dans l’onglet Moteur. Vous pouvez préparer
      le jeton et les fichiers de workflow dès maintenant.
    </div>{/if}
  {#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}{#if message}<div
      class="web-alert web-alert--success"
      role="status"
    >
      {message}
    </div>{/if}
  <form class="web-form" onsubmit={create}>
    <div class="web-form-grid">
      <label class="web-field"
        ><span>Nom du jeton</span><input bind:value={name} required maxlength="80" disabled={!!busy} /></label
      ><label class="web-field"
        ><span>Expiration · jours</span><input
          type="number"
          bind:value={days}
          min="1"
          max="90"
          step="1"
          required
          disabled={!!busy}
        /></label
      ><label class="web-field web-field--full"
        ><span>Dépôt d’images autorisé</span><input
          bind:value={imageRepository}
          required
          maxlength="250"
          disabled={!!busy}
          placeholder="ghcr.io/organisation/site"
          spellcheck="false"
        /><span class="web-help"
          >Le jeton autorise seulement cette application et ce dépôt d’images. Le modèle téléchargé est prévu pour GHCR.</span
        ></label
      >
    </div>
    <div>
      <button class="web-button web-button--primary" type="submit" disabled={!!busy}
        >{busy === 'create' ? 'Création…' : 'Créer un jeton limité à cette application'}</button
      >
    </div>
  </form>
  {#if issuedToken && issued}<section class="ci-secret" aria-live="polite">
      <div class="web-row">
        <h3>Jeton affiché une seule fois</h3>
        <button
          class="web-button"
          type="button"
          onclick={() => {
            issuedToken = ''
            issued = null
          }}>Masquer définitivement</button
        >
      </div>
      <p class="web-help">
        Conservez-le dans les secrets GitHub Actions du dépôt. Il ne sera plus renvoyé par PocketHost après cette vue.
      </p>
      <textarea readonly value={issuedToken} rows="3" spellcheck="false" aria-label="Nouveau jeton CI à copier"
      ></textarea>
      <div class="web-actions">
        <button class="web-button web-button--primary" type="button" onclick={copy}
          >{copied ? 'Jeton copié' : 'Copier le jeton'}</button
        ><button
          class="web-button"
          type="button"
          onclick={() => download(issuedToken + '\n', 'pockethost-ci-token.txt')}>Télécharger le jeton</button
        ><button class="web-button" type="button" onclick={() => workflow(issued!.imageRepository)}
          >Télécharger le workflow pour ce jeton</button
        >
      </div>
    </section>{/if}
</section>
<section class="web-panel ci-panel">
  <div>
    <span class="web-eyebrow">Fichiers à relire avant ajout au dépôt</span>
    <h2>Préparer le pipeline</h2>
  </div>
  <ol class="ci-steps">
    <li>
      <strong>Configurer les secrets GitHub Actions</strong>
      <p>
        Ajoutez <code>POCKETHOST_DEPLOY_TOKEN</code>. Si la construction utilise des variables, ajoutez
        <code>POCKETHOST_BUILD_ENV</code> avec l’export .env.local Next.js de l’onglet Variables.
      </p>
    </li>
    <li>
      <strong>Choisir le Dockerfile</strong>
      <p>
        Le modèle fourni vise un projet Next.js avec npm et package-lock.json. Adaptez-le pour pnpm, Yarn ou un
        monorepo, et relisez le .dockerignore proposé.
      </p>
    </li>
    <li>
      <strong>Ajouter le workflow puis pousser sur la branche choisie</strong>
      <p>
        Le fichier va dans <code>.github/workflows/pockethost.yml</code>, pour la branche <code>{branch}</code>. Il
        reste actif jusqu’à la confirmation du déploiement afin que le serveur puisse récupérer une image GHCR privée
        avec le jeton GitHub éphémère.
      </p>
    </li>
  </ol>
  <label class="web-field"
    ><span>Chemin du Dockerfile utilisé par le workflow</span><input
      bind:value={dockerfile}
      placeholder="Dockerfile.pockethost-ci"
      spellcheck="false"
    /></label
  ><label class="web-field"
    ><span>Runner GitHub Actions</span><input
      bind:value={runnerLabels}
      placeholder={WEB_CI_DEFAULT_RUNNER}
      maxlength="250"
      spellcheck="false"
    /><span class="web-help"
      >Par défaut <code>{WEB_CI_DEFAULT_RUNNER}</code> (runner hébergé par GitHub). Pour un runner auto-hébergé,
      indiquez jusqu’à 5 étiquettes séparées par des virgules, par exemple <code>self-hosted, linux, x64</code>.</span
    ></label
  >
  {#if runnerLabels.trim() && runnerLabels.trim() !== WEB_CI_DEFAULT_RUNNER}<div class="web-alert" role="note">
      Un runner auto-hébergé exécute le code du dépôt et reçoit le jeton de déploiement : il ne doit jamais tourner sur
      les serveurs d’hébergement. Utilisez une machine dédiée et isolée.
    </div>{/if}
  <div class="web-actions">
    <button class="web-button" type="button" onclick={() => workflow()} disabled={!deployUrl}
      >Télécharger le workflow GitHub Actions</button
    ><button
      class="web-button"
      type="button"
      onclick={() => download(webCiDockerfile, dockerfile.split('/').pop() || 'Dockerfile.pockethost-ci')}
      >Télécharger l’exemple Dockerfile Next.js</button
    ><button class="web-button" type="button" onclick={() => download(webCiDockerignore, '.dockerignore')}
      >Télécharger le .dockerignore</button
    >
  </div>
  <p class="web-help">
    Aucun fichier n’est ajouté au dépôt depuis cette page. Les fichiers générés ne contiennent pas le jeton de
    déploiement. Les identifiants GHCR transmis pour le pull sont temporaires.
  </p>
</section>
<section class="web-panel ci-panel">
  <div class="web-row">
    <h2>Jetons de cette application</h2>
    <button class="web-button" type="button" onclick={load} disabled={!!busy}>Actualiser</button>
  </div>
  <div class="web-table-wrap">
    <table>
      <thead
        ><tr
          ><th>Nom</th><th>Dépôt autorisé</th><th>État</th><th>Expiration</th><th>Dernier usage</th><th>Actions</th></tr
        ></thead
      ><tbody
        >{#each items as token}<tr
            ><td>{token.name}</td><td><code>{token.imageRepository}</code></td><td
              ><span class="web-status" data-tone={status(token) === 'Actif' ? 'success' : 'neutral'}
                >{status(token)}</span
              ></td
            ><td>{webDate(token.expiresAt)}</td><td>{webDate(token.lastUsedAt)}</td><td
              >{#if status(token) === 'Actif'}{#if revoking === token.id}<div class="web-actions">
                    <button
                      class="web-button web-button--danger"
                      type="button"
                      onclick={() => revoke(token)}
                      disabled={!!busy}>Confirmer la révocation</button
                    ><button class="web-button" type="button" onclick={() => (revoking = '')} disabled={!!busy}
                      >Conserver</button
                    >
                  </div>{:else}<button
                    class="web-button"
                    type="button"
                    onclick={() => (revoking = token.id)}
                    disabled={!!busy}>Révoquer</button
                  >{/if}{:else}—{/if}</td
            ></tr
          >{:else}<tr
            ><td colspan="6">{busy === 'load' ? 'Chargement…' : 'Aucun jeton CI créé pour cette application.'}</td></tr
          >{/each}</tbody
      >
    </table>
  </div>
</section>

<style>
  .ci-panel {
    display: grid;
    gap: 1rem;
    min-width: 0;
  }
  .ci-panel p {
    margin: 0;
  }
  .ci-secret {
    display: grid;
    gap: 1rem;
    padding: 1rem;
    border: 1px solid var(--app-accent-border, #3b7cff3b);
    border-radius: var(--web-radius, 6px);
    background: var(--app-accent-soft, #3b7cff1c);
  }
  .ci-secret h3 {
    font-size: 0.875rem;
    margin: 0;
  }
  .ci-secret textarea {
    font:
      12px/1.7 ui-monospace,
      SFMono-Regular,
      monospace;
    overflow-wrap: anywhere;
  }
  .ci-steps {
    display: grid;
    gap: 1.2rem;
    margin: 0;
    padding-left: 1.3rem;
  }
  .ci-steps strong {
    font-size: 0.8125rem;
  }
  .ci-steps p {
    margin-top: 0.4rem;
    font-size: 0.8rem;
    color: var(--web-muted);
    line-height: 1.7;
  }
  .ci-panel code {
    overflow-wrap: anywhere;
  }
</style>
