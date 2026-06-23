<script lang="ts">
  import { page } from '$app/stores'
  import { client } from '$src/pocketbase-client'
  import AlertBar from '$components/AlertBar.svelte'

  const { confirmEmailChange } = client()

  let password: string = ''
  let formErrors: string[] = []
  let showPassword = false

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
      await confirmEmailChange(token, password)

      window.location.href = '/login?emailChanged=1'
    } catch (error) {
      if (error instanceof Error) {
        formErrors = client().parseError(error)
      } else {
        formErrors = ["Une erreur est survenue pendant la confirmation du changement d'email."]
      }
    }

    isFormButtonDisabled = false
  }
</script>

<svelte:head>
  <title>Confirmer le changement d'email - Gestion PocketBase</title>
</svelte:head>

<div class="w-full flex items-center justify-center px-4 md:px-16 py-10 md:py-16">
  <div class="auth-card w-full max-w-md">
    <form class="auth-form" method="post" autocomplete="on" onsubmit={handleSubmit}>
      <h2 class="auth-form-title">Confirmer le changement d'email</h2>
      <p class="text-sm text-white/70 mb-4">Saisissez votre mot de passe actuel pour finaliser le changement.</p>

      <div class="auth-field-group">
        <label class="auth-label" for="password">Mot de passe actuel</label>
        <div class="auth-field-wrap">
          <input
            type={showPassword ? 'text' : 'password'}
            id="password"
            name="password"
            class="auth-field auth-field--password"
            placeholder="Mot de passe"
            autocomplete="current-password"
            bind:value={password}
            required
          />
          {#if password.length > 0}
            <button
              type="button"
              class="auth-field-toggle"
              onclick={() => (showPassword = !showPassword)}
              tabindex="-1"
              aria-label={showPassword ? 'Masquer le mot de passe' : 'Afficher le mot de passe'}
            >
              <wa-icon name={showPassword ? 'eye-slash' : 'eye'}></wa-icon>
            </button>
          {/if}
        </div>
      </div>

      {#each formErrors as error}
        <AlertBar message={error} type="error" />
      {/each}

      <button type="submit" class="auth-submit" disabled={isFormButtonDisabled}>
        Confirmer
        <wa-icon name="arrow-right"></wa-icon>
      </button>
    </form>
  </div>
</div>
