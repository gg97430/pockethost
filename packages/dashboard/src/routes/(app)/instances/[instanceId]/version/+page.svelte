<script lang="ts">
  import FeatureTab from '$components/FeatureTab.svelte'
  import { client } from '$src/pocketbase-client'
  import { instance } from '../store'
  import VersionPicker from './VersionPicker.svelte'
  import { versions } from '$src/util/stores'
  import { isInstanceFullyOff } from '$util/instancePower'

  $: ({ id, version } = $instance)
  $: isFullyOff = isInstanceFullyOff($instance)
  $: showV23Notice = minorVersion(version) <= 22

  const minorVersion = (v: string) => Number(v.split('.')[1] ?? 0)

  const crossesV23Boundary = (from: string, to: string) => {
    const fromMinor = minorVersion(from)
    const toMinor = minorVersion(to)
    return (fromMinor <= 22 && toMinor >= 23) || (fromMinor >= 23 && toMinor <= 22)
  }

  const confirmVersionChangeMessage = (from: string, to: string) => {
    if (crossesV23Boundary(from, to)) {
      if (minorVersion(to) >= 23) {
        return `v0.23+ réécrit les API JSVM de PocketBase. Faites d'abord une sauvegarde et vérifiez vos pb_hooks personnalisés avant la mise à niveau.\n\nChanger la version vers ${to} ?`
      }
      return `Le retour en arrière au-delà de la limite v0.23 n'est pas pris en charge par PocketBase et peut casser votre instance.\n\nChanger la version vers ${to} ?`
    }
    return `Voulez-vous vraiment changer la version vers ${to} ?`
  }

  let selectedVersion = version
  $: {
    selectedVersion = version
  }

  let isButtonDisabled = false
  let errorMessage = ''
  let successMessage = ''

  const handleSave = async (e: Event) => {
    e.preventDefault()

    if (!isFullyOff) return

    errorMessage = ''
    successMessage = ''
    isButtonDisabled = true

    const confirmVersionChange = confirm(confirmVersionChangeMessage(version, selectedVersion))

    if (confirmVersionChange) {
      errorMessage = ''
      client()
        .updateInstance({
          id,
          fields: { version: selectedVersion },
        })
        .then(() => {
          successMessage = 'Version mise à jour'
        })
        .catch((error) => {
          errorMessage = error.message
        })
    } else {
      selectedVersion = version
    }

    isButtonDisabled = false
  }
</script>

<FeatureTab
  title="Changement de version"
  documentation="/docs/versions"
  powerOffAction="changer la version"
  bind:errorMessage
  {successMessage}
  successFlash
>
  <svelte:fragment slot="alerts">
    {#if showV23Notice}
      <wa-callout variant="warning" class="wa-callout-padded mb-4">
        <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
        <p>
          PocketBase <strong>v0.23+</strong> modifie l'API JSVM. Les <code>pb_hooks</code> personnalisés écrits pour
          v0.22 ou une version antérieure peuvent nécessiter des mises à jour avant la mise à niveau.
          <a href="https://github.com/pocketbase/pocketbase/releases/tag/v0.23.0" class="text-primary"
            >Consulter les notes de version v0.23</a
          >.
        </p>
      </wa-callout>
    {/if}
  </svelte:fragment>

  <svelte:fragment slot="summary">
    <p>
      Nous vous recommandons de <strong>faire une sauvegarde complète</strong>
      avant tout changement. Nous prenons en charge le dernier correctif de
      <a href="https://github.com/pocketbase/pocketbase/releases" class="text-primary">chaque version mineure</a> de PocketBase.
    </p>
  </svelte:fragment>

  <form class="flex change-version-form-container-query gap-4" onsubmit={handleSave}>
    <VersionPicker bind:selectedVersion versions={$versions} disabled={!isFullyOff} />
    <wa-button type="submit" variant="danger" disabled={!isFullyOff || isButtonDisabled}>Changer la version</wa-button>
  </form>
</FeatureTab>

<style>
  .change-version-form-container-query {
    flex-direction: column;
  }

  @container (min-width: 400px) {
    .change-version-form-container-query {
      flex-direction: row;
    }
  }
</style>
