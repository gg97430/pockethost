<script lang="ts">
  import FeatureTab from '$components/FeatureTab.svelte'
  import { client } from '$src/pocketbase-client'
  import { instance } from '../store'
  import ErrorMessage from '../settings/ErrorMessage.svelte'
  import Toggle from '../Toggle.svelte'
  import { isInstanceFullyOff, isInstanceShuttingDown } from '$util/instancePower'

  const { updateInstance } = client()

  $: ({ id, autoVacuum } = $instance)
  $: isFullyOff = isInstanceFullyOff($instance)
  $: isShuttingDown = isInstanceShuttingDown($instance)

  let errorMessage = ''

  const handleChange = (isChecked: boolean) => {
    if (!isFullyOff) return

    updateInstance({ id, fields: { autoVacuum: isChecked } })
      .then(() => 'saved')
      .catch((error) => {
        errorMessage = error.data.message || error.message
      })
  }
</script>

<FeatureTab title="Nettoyage auto" documentation="/docs/auto-vacuum" powerOffAction="modifier le nettoyage auto" {errorMessage}>
  <svelte:fragment slot="summary">
    <p>
      Le nettoyage auto récupère de l'espace disque dans les bases SQLite de votre instance pendant la maintenance nocturne.
      La compaction est lancée uniquement lorsque votre instance est inactive (hibernée), pas pendant
      qu'elle sert du trafic. Si une requête réveille votre instance pendant le vacuum, vous pouvez constater jusqu'à
      environ 5 secondes d'indisponibilité pendant la fin de la compaction.
    </p>
  </svelte:fragment>

  <wa-card class="border border-white/10 bg-[#111111]/80 shadow-lg overflow-hidden">
    <div class="wa-card-body wa-card-body--lg">
      <Toggle checked={autoVacuum ?? true} onChange={handleChange} disabled={!isFullyOff} loading={isShuttingDown} />
    </div>
  </wa-card>
</FeatureTab>
