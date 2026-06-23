<script lang="ts">
  import { page } from '$app/stores'
  import { client } from '$src/pocketbase-client'
  import AlertBar from '$components/AlertBar.svelte'
  import { authInputAutofill } from '$lib/authInputAutofill'

  const { requestPasswordResetConfirm } = client()

  let password: string = ''
  let formErrors: string[] = []

  $: ({ token } = $page.params)

  let isFormButtonDisabled: boolean = true
  $: isFormButtonDisabled = password.length === 0

  const handleSubmit = async (e: Event) => {
    e.preventDefault()

    formErrors = []

    if (!token) {
      formErrors = ['Aucun jeton trouvé. Veuillez vérifier à nouveau le lien reçu par email.']
      return
    }

    isFormButtonDisabled = true

    try {
      await requestPasswordResetConfirm(token, password)

      window.location.href = '/login'
    } catch (error) {
      if (error instanceof Error) {
        formErrors = client().parseError(error)
      } else {
        formErrors = ['Une erreur est survenue pendant la confirmation du changement de mot de passe.']
      }
    }

    isFormButtonDisabled = false
  }
</script>

<svelte:head>
  <title>Réinitialisez votre mot de passe - Gestion PocketBase</title>
</svelte:head>

<div class="w-full flex items-center justify-center px-4 md:px-16 py-10 md:py-16">
  <div class="auth-card w-full max-w-md">
    <form class="auth-form" method="post" autocomplete="on" onsubmit={handleSubmit}>
      <h2 class="auth-form-title">Nouveau mot de passe</h2>

      <div class="auth-field-group">
        <label class="auth-label" for="password">Nouveau mot de passe</label>
        <input
          type="password"
          id="password"
          name="password"
          class="auth-field"
          autocomplete="new-password"
          bind:value={password}
          use:authInputAutofill
          required
        />
      </div>

      {#each formErrors as error}
        <AlertBar message={error} type="error" />
      {/each}

      <button type="submit" class="auth-submit" disabled={isFormButtonDisabled}>
        Enregistrer
        <wa-icon name="arrow-right"></wa-icon>
      </button>
    </form>
  </div>
</div>
