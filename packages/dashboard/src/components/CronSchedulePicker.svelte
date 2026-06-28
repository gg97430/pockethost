<script lang="ts">
  import { CRON_PRESETS, CUSTOM_CRON_PRESET_ID, findPresetIdForCron, getCronPreset } from '$lib/cronExpression'

  interface Props {
    id?: string
    value?: string
    customMode?: boolean
    timezoneLabel?: string
  }

  let {
    id = 'webhook-schedule',
    value = $bindable(''),
    customMode = $bindable(false),
    timezoneLabel = 'UTC',
  }: Props = $props()

  let presetId = $state('')
  let userPickedCustom = $state(false)

  const selectedPreset = $derived(getCronPreset(presetId))
  const isCustom = $derived(presetId === CUSTOM_CRON_PRESET_ID)
  const selectedPresetDescription = $derived(
    selectedPreset?.description ? selectedPreset.description.replace(/\bUTC\b/g, timezoneLabel) : ''
  )

  $effect(() => {
    customMode = isCustom
  })

  $effect(() => {
    if (!value) {
      presetId = ''
      userPickedCustom = false
      return
    }

    if (userPickedCustom) {
      return
    }

    const matchedPresetId = findPresetIdForCron(value)
    if (matchedPresetId !== presetId) {
      presetId = matchedPresetId
    }
  })

  function handlePresetChange(event: Event) {
    const nextPresetId = (event.target as HTMLSelectElement).value
    presetId = nextPresetId
    userPickedCustom = nextPresetId === CUSTOM_CRON_PRESET_ID

    if (nextPresetId === CUSTOM_CRON_PRESET_ID) {
      return
    }

    const preset = getCronPreset(nextPresetId)
    value = preset?.cron ?? ''
  }
</script>

<div class="flex flex-col gap-2">
  <wa-select {id} class="w-full" value={presetId} oninput={handlePresetChange} placeholder="Planification ({timezoneLabel})">
    <wa-option value="" disabled>Choisir une planification</wa-option>
    {#each CRON_PRESETS as preset}
      <wa-option value={preset.id}>{preset.label}</wa-option>
    {/each}
  </wa-select>

  {#if selectedPresetDescription && !isCustom}
    <p class="text-xs leading-snug text-white/55">{selectedPresetDescription}</p>
  {/if}
</div>
