<script lang="ts">
  import AlertBar from '$components/AlertBar.svelte'
  import { client } from '$src/pocketbase-client/index.js'
  import { SECRET_KEY_REGEX, type UpdateInstancePayload } from 'pockethost/common'
  import { instance } from '../store.js'
  import { items } from './stores.js'

  let secretKey: string = ''
  let secretValue: string = ''

  let isKeyValid = false
  let isValueValid = false
  let isFormValid = false

  let successfulSave = false
  let errorMessage: string = ''

  $: {
    secretKey = secretKey.toUpperCase()
    secretValue = secretValue.trim()
    isKeyValid = !!secretKey.match(SECRET_KEY_REGEX)
    isValueValid = secretValue.length > 0
    isFormValid = isKeyValid && isValueValid
  }

  const handleSubmit = async (e: Event) => {
    e.preventDefault()
    errorMessage = ''

    try {
      isFormValid = false

      items.upsert({ name: secretKey, value: secretValue })
      await client().updateInstance({
        id: $instance.id,
        fields: {
          secrets: $items.reduce(
            (c, v) => {
              const { name, value } = v
              c[name] = value
              return c
            },
            {} as NonNullable<UpdateInstancePayload['fields']['secrets']>
          ),
        },
      })

      secretKey = ''
      secretValue = ''
      isFormValid = true
      successfulSave = true

      setTimeout(() => {
        successfulSave = false
      }, 5000)
    } catch (error: any) {
      errorMessage = error.message
    }
  }
</script>

<h3 class="text-xl">Ajouter un secret</h3>

<div class="mb-8">
  {#if successfulSave}
    <AlertBar message="Votre nouveau secret a été enregistré." type="success" />
  {/if}

  <AlertBar message={errorMessage} type="error" />

  <form onsubmit={handleSubmit} class="mb-4">
    <div class="flex flex-row gap-1 mb-4">
      <label class="flex-1 w-2/5">
        <wa-input
          id="secret-key"
          type="text"
          value={secretKey}
          oninput={(e: Event) => (secretKey = (e.currentTarget as HTMLInputElement).value)}
          placeholder="Clé"
          class={!isKeyValid && secretKey.length > 0 ? 'wa-input-error' : ''}
        ></wa-input>
        {#if !isKeyValid && secretKey.length > 0}
          <div class="mt-1">
            <span class="text-error">
              Les noms de clés doivent être en majuscules, alphanumériques, et peuvent inclure un underscore (_).
            </span>
          </div>
        {/if}
      </label>

      <div class="flex-1 w-2/5">
        <wa-input
          id="secret-value"
          type="text"
          value={secretValue}
          oninput={(e: Event) => (secretValue = (e.currentTarget as HTMLInputElement).value)}
          placeholder="Valeur"
        ></wa-input>
      </div>

      <div class="flex-none text-right w-1/5 md:w-auto">
        <wa-button type="submit" variant="brand" class="px-2.5 md:px-3" disabled={!isFormValid}>
          Ajouter
          <wa-icon slot="end" name="floppy-disk"></wa-icon>
        </wa-button>
      </div>
    </div>
  </form>
</div>
