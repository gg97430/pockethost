<script lang="ts">
  import FeatureTab from '$components/FeatureTab.svelte'
  import AlertBar from '$components/AlertBar.svelte'
  import { client } from '$src/pocketbase-client'
  import { userStore } from '$util/stores'

  const { requestEmailChange } = client()

  let newEmail = ''
  let formErrors: string[] = []
  let userShouldCheckTheirEmail = false
  let submittedEmail = ''

  $: isFormButtonDisabled = newEmail.trim().length === 0

  const handleSubmit = async (e: SubmitEvent) => {
    e.preventDefault()

    formErrors = []

    const trimmed = newEmail.trim()
    if (trimmed.toLowerCase() === $userStore?.email?.toLowerCase()) {
      formErrors = ["Le nouvel email doit être différent de l'email actuel."]
      return
    }

    try {
      await requestEmailChange(trimmed)
      submittedEmail = trimmed
      userShouldCheckTheirEmail = true
    } catch (error) {
      if (error instanceof Error) {
        formErrors = client().parseError(error)
      } else {
        formErrors = ["Une erreur est survenue pendant la demande de changement d'email."]
      }
    }
  }
</script>

<svelte:head>
  <title>Changer l'email - Gestion PocketBase</title>
</svelte:head>

<FeatureTab title="Changer l'email">
  <div class="account-card">
    <div class="account-card-body">
      {#if userShouldCheckTheirEmail}
        <div class="text-center py-4">
          <h2 class="text-lg font-semibold text-white mb-3">Consultez votre boîte mail</h2>
          <p class="text-white/80">
            Un lien de confirmation a été envoyé à <br /><strong class="text-white">{submittedEmail}</strong>
          </p>
          <p class="text-sm text-neutral-400 mt-4">
            Votre email actuel reste actif jusqu'à la confirmation depuis la nouvelle boîte avec votre mot de passe.
          </p>
          <p class="mt-6">
            <a href="/account" class="account-stat-link">Retour au compte</a>
          </p>
        </div>
      {:else}
        <form class="max-w-md" onsubmit={handleSubmit}>
          <p class="text-sm text-neutral-400 mb-6">
            Saisissez votre nouvelle adresse email. Nous y enverrons un lien de confirmation. Votre email actuel reste
            actif jusqu'à votre confirmation.
          </p>

          <div class="auth-field-group mb-4">
            <label class="auth-label" for="newEmail">Nouvelle adresse email</label>
            <wa-input
              type="email"
              id="newEmail"
              placeholder="name@example.com"
              value={newEmail}
              oninput={(e: Event) => (newEmail = (e.currentTarget as HTMLInputElement).value)}
              required
              autocomplete="email"
              class="w-full"
            ></wa-input>
          </div>

          {#each formErrors as error}
            <AlertBar message={error} type="error" />
          {/each}

          <div class="flex items-center gap-4 mt-6">
            <button type="submit" class="auth-submit" disabled={isFormButtonDisabled}>
              Envoyer le lien de confirmation
              <wa-icon name="arrow-right"></wa-icon>
            </button>
            <a href="/account" class="account-stat-link text-sm">Annuler</a>
          </div>
        </form>
      {/if}
    </div>
  </div>
</FeatureTab>
