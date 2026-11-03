<script lang="ts">
  import { onMount } from 'svelte'
  import FeatureTab from '$components/FeatureTab.svelte'
  import { client } from '$src/pocketbase-client'

  export let mandatoryMode = false
  export let onContinue: () => void = () => {}
  let required = true
  let enabled = false
  let remaining = 0
  let loading = true
  let busy = false
  let password = ''
  let code = ''
  let setup: { secret: string; qrSvg: string } | null = null
  let recoveryCodes: string[] = []
  let errorMessage = ''
  let successMessage = ''
  let confirmDisable = false
  const errorText = (error: unknown) =>
    error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
  const refresh = async () => {
    const state = await client().getTotpStatus()
    required = state.required !== false
    enabled = state.enabled
    remaining = state.recoveryCodesRemaining
  }
  const configure = async () => {
    if (busy) return
    busy = true
    errorMessage = ''
    successMessage = ''
    recoveryCodes = []
    try {
      setup = await client().setupTotp(password)
    } catch (error) {
      errorMessage = errorText(error)
    } finally {
      busy = false
    }
  }
  const confirm = async () => {
    if (busy) return
    busy = true
    errorMessage = ''
    try {
      recoveryCodes = (await client().confirmTotp(password, code)).recoveryCodes
      setup = null
      password = ''
      code = ''
      await refresh()
      successMessage = 'Authentification à deux facteurs activée.'
    } catch (error) {
      errorMessage = errorText(error)
    } finally {
      busy = false
    }
  }
  const regenerate = async () => {
    if (busy) return
    busy = true
    errorMessage = ''
    try {
      recoveryCodes = (await client().regenerateTotpRecovery(password, code)).recoveryCodes
      password = ''
      code = ''
      await refresh()
      successMessage = 'Nouveaux codes de secours générés. Les anciens codes sont invalidés.'
    } catch (error) {
      errorMessage = errorText(error)
    } finally {
      busy = false
    }
  }
  const disable = async () => {
    confirmDisable = false
    busy = true
    errorMessage = ''
    try {
      await client().disableTotp(password, code)
      recoveryCodes = []
      password = ''
      code = ''
      await refresh()
      successMessage = 'Authentification à deux facteurs désactivée.'
    } catch (error) {
      errorMessage = errorText(error)
    } finally {
      busy = false
    }
  }
  const download = () => {
    const url = URL.createObjectURL(new Blob([recoveryCodes.join('\n') + '\n'], { type: 'text/plain' }))
    const link = document.createElement('a')
    link.href = url
    link.download = 'pockethost-codes-de-secours.txt'
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  onMount(async () => {
    try {
      await refresh()
    } catch (error) {
      errorMessage = errorText(error)
    } finally {
      loading = false
    }
  })
</script>

<svelte:head><title>Sécurité du compte - Kolab Hosting</title></svelte:head>

<FeatureTab title="Sécurité" bind:errorMessage {successMessage} successFlash>
  <svelte:fragment slot="summary">
    <p>Le TOTP est obligatoire sur Kolab. Validez un code de votre application pour sécuriser votre connexion.</p>
  </svelte:fragment>

  <div class="grid max-w-xl gap-5">
    {#if loading}
      <p role="status">Chargement de la sécurité du compte…</p>
    {:else}
      <div class="flex flex-wrap items-center gap-3">
        <strong>Authentification TOTP</strong>
        <wa-badge variant={enabled ? 'success' : 'neutral'}
          >{enabled ? 'Activée' : 'Configuration obligatoire'}</wa-badge
        >
        {#if enabled}<span class="text-sm text-muted">{remaining} codes de secours restants</span>{/if}
      </div>
      {#if !enabled && !setup}
        <form
          class="grid gap-4"
          onsubmit={(event) => {
            event.preventDefault()
            void configure()
          }}
        >
          <label class="grid gap-2 text-sm" for="totp-password"
            >Mot de passe actuel
            <input
              class="auth-field"
              id="totp-password"
              type="password"
              autocomplete="current-password"
              bind:value={password}
              required
              disabled={busy}
            />
          </label>
          <div>
            <wa-button type="submit" variant="brand" disabled={busy || !password} loading={busy}
              >Configurer le TOTP</wa-button
            >
          </div>
        </form>
      {:else if setup}
        <wa-card>
          <form
            class="wa-card-body grid gap-4"
            onsubmit={(event) => {
              event.preventDefault()
              void confirm()
            }}
          >
            <p class="text-sm text-muted">
              Scannez le QR code avec Google Authenticator, Aegis ou votre application habituelle.
            </p>
            <img
              src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(setup.qrSvg)}`}
              width="220"
              height="220"
              alt="QR code de configuration TOTP"
            />
            <wa-details summary="Saisir la clé manuellement"><code class="break-all">{setup.secret}</code></wa-details>
            <label class="grid gap-2 text-sm" for="totp-confirmation"
              >Code à six chiffres
              <input
                class="auth-field"
                id="totp-confirmation"
                type="text"
                inputmode="numeric"
                autocomplete="one-time-code"
                bind:value={code}
                pattern={'[0-9]{6}'}
                maxlength="6"
                required
                disabled={busy}
              />
            </label>
            <div class="flex flex-wrap gap-2">
              <wa-button type="submit" variant="brand" disabled={busy || !/^\d{6}$/.test(code)} loading={busy}
                >Activer le TOTP</wa-button
              >
              <wa-button
                type="button"
                variant="neutral"
                disabled={busy}
                onclick={() => {
                  setup = null
                  code = ''
                  password = ''
                }}>Annuler</wa-button
              >
            </div>
          </form>
        </wa-card>
      {:else if !mandatoryMode}
        <form
          class="grid gap-4"
          onsubmit={(event) => {
            event.preventDefault()
            void regenerate()
          }}
        >
          <label class="grid gap-2 text-sm" for="totp-current-password"
            >Mot de passe actuel
            <input
              class="auth-field"
              id="totp-current-password"
              type="password"
              autocomplete="current-password"
              bind:value={password}
              required
              disabled={busy}
            />
          </label>
          <label class="grid gap-2 text-sm" for="totp-current-code"
            >Code d’authentification ou code de secours
            <input
              class="auth-field"
              id="totp-current-code"
              type="text"
              autocomplete="one-time-code"
              bind:value={code}
              maxlength="80"
              required
              disabled={busy}
            />
          </label>
          <div class="flex flex-wrap gap-2">
            <wa-button type="submit" variant="brand" disabled={busy || !password || !code} loading={busy}
              >Renouveler les codes de secours</wa-button
            >
            {#if !required}
              <wa-button
                type="button"
                variant="neutral"
                disabled={busy || !password || !code}
                onclick={() => (confirmDisable = true)}>Désactiver le TOTP</wa-button
              >
            {/if}
          </div>
        </form>
      {/if}
      {#if recoveryCodes.length}
        <wa-card>
          <div class="wa-card-body grid gap-3">
            <strong>Codes de secours</strong>
            <p class="text-sm text-muted">
              Enregistrez ces codes avant de quitter cette page. Chaque code remplace une seule fois le code de votre
              application.
            </p>
            <div class="auth-field-group">
              <label class="auth-label" for="totp-recovery-codes">Vos codes de secours</label>
              <textarea id="totp-recovery-codes" class="auth-field" value={recoveryCodes.join('\n')} readonly rows="10"
              ></textarea>
            </div>
            <div>
              <wa-button variant="neutral" onclick={download}
                ><wa-icon slot="start" name="download"></wa-icon>Télécharger les codes</wa-button
              >
            </div>
            {#if mandatoryMode}<wa-button variant="brand" onclick={onContinue}
                >J’ai enregistré mes codes, continuer</wa-button
              >{/if}
          </div>
        </wa-card>
      {/if}
      <p class="text-sm text-muted">
        Pour une console PocketBase, ouvrez son lien Admin puis « Configurer l’authentification à deux facteurs ».
        Chaque compte administrateur possède sa propre configuration.
      </p>
    {/if}
  </div>
</FeatureTab>

{#if !required}
  <wa-dialog label="Désactiver le TOTP" open={confirmDisable} onwa-hide={() => (confirmDisable = false)}>
    <p>Les prochaines connexions à ce compte utiliseront uniquement le mot de passe.</p>
    <wa-button slot="footer" variant="neutral" onclick={() => (confirmDisable = false)}>Annuler</wa-button>
    <wa-button slot="footer" variant="brand" onclick={disable}>Confirmer la désactivation</wa-button>
  </wa-dialog>
{/if}
