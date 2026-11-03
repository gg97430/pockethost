<script lang="ts">
  import { onMount } from 'svelte'
  import { client } from '$src/pocketbase-client'
  import { isWorkspaceAccessAllowed } from '$util/stores'
  import AccountSecurity from './AccountSecurity.svelte'

  let checking = true
  let enrolling = false
  let accountId = ''
  let errorMessage = ''
  let generation = 0
  let authenticated = false

  const check = async () => {
    const version = ++generation
    const auth = client().client.authStore
    authenticated = auth.isValid && !!auth.record
    if (!authenticated) {
      accountId = ''
      enrolling = false
      checking = false
      errorMessage = ''
      isWorkspaceAccessAllowed.set(false)
      return
    }
    if (accountId !== auth.record!.id) {
      accountId = auth.record!.id
      enrolling = false
      isWorkspaceAccessAllowed.set(false)
    }
    // Confirmation replaces the token. Keep the recovery codes visible until explicitly acknowledged.
    if (enrolling) return
    checking = true
    errorMessage = ''
    try {
      const status = await client().getTotpStatus()
      if (version !== generation) return
      enrolling = status.required !== false && !status.enabled
      isWorkspaceAccessAllowed.set(!enrolling)
    } catch (error) {
      if (version !== generation) return
      isWorkspaceAccessAllowed.set(false)
      errorMessage = 'La sécurité de la connexion n’a pas pu être vérifiée. Réessayez ou reconnectez-vous.'
    } finally {
      if (version === generation) checking = false
    }
  }
  const finish = () => {
    enrolling = false
    void check()
  }
  onMount(() => client().client.authStore.onChange(() => void check(), true))
</script>

{#if checking}
  <div class="security-gate" role="status">Vérification de votre connexion…</div>
{:else if authenticated && (enrolling || errorMessage)}
  <main class="security-gate">
    <section class="security-panel" aria-label="Sécuriser la première connexion">
      <header>
        <h1>Sécurisez votre compte</h1>
        <p>Configurez votre application d’authentification et validez un code pour accéder à Kolab.</p>
      </header>
      {#if errorMessage}
        <wa-callout variant="danger" class="wa-callout-padded">{errorMessage}</wa-callout>
        <wa-button onclick={() => void check()}>Réessayer</wa-button>
      {:else}
        <AccountSecurity mandatoryMode onContinue={finish} />
      {/if}
      <wa-button variant="neutral" appearance="plain" onclick={() => client().logOut()}>Se déconnecter</wa-button>
    </section>
  </main>
{:else}
  <slot />
{/if}

<style>
  .security-gate {
    display: grid;
    place-items: center;
    min-height: 100vh;
    padding: 2rem 1rem;
  }
  .security-panel {
    width: min(100%, 44rem);
    display: grid;
    gap: 1.5rem;
  }
  header h1 {
    font-size: clamp(1.6rem, 4vw, 2rem);
    font-weight: 650;
    margin-bottom: 0.5rem;
  }
  header p {
    color: var(--wa-color-text-quiet);
    line-height: 1.6;
  }
</style>
