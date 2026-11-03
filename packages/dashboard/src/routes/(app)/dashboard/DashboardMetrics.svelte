<script lang="ts">
  import { globalInstancesStore, userStore } from '$util/stores'
  import { getInstanceRuntimeState, type InstanceRuntimeState } from '$util/instancePower'
  import ResourceSummary, { type SummaryMetric, type SummarySegment } from '$components/ResourceSummary.svelte'

  $: instances = Object.values($globalInstancesStore)
  $: states = instances.reduce(
    (counts, instance) => {
      const state = getInstanceRuntimeState(instance)
      counts[state] = (counts[state] || 0) + 1
      return counts
    },
    {} as Partial<Record<InstanceRuntimeState, number>>
  )
  $: running = states.running || 0
  $: paused = (states.off || 0) + (states.sleeping || 0)
  $: pending = (states.starting || 0) + (states.vacuuming || 0)
  $: failed = states.failed || 0
  $: versions = [...new Set(instances.map((instance) => instance.version).filter(Boolean))]
  $: owned = instances.filter((instance) => instance.uid === $userStore?.id).length
  $: quota = $userStore?.subscription_quantity || 0
  $: metrics = [
    {
      label: 'Bases de données',
      value: instances.length,
      note: `${versions.length} version${versions.length > 1 ? 's' : ''} PocketBase`,
      icon: 'database',
      tone: 'info',
    },
    {
      label: 'En service',
      value: running,
      note: pending ? `${pending} en démarrage ou maintenance` : 'Instances actives',
      icon: 'circle-check',
      tone: 'good',
    },
    {
      label: 'En pause',
      value: paused,
      note: `${states.sleeping || 0} en veille · ${states.off || 0} éteintes`,
      icon: 'circle-pause',
      tone: 'muted',
    },
    {
      label: 'À vérifier',
      value: failed,
      note: failed ? 'Consultez les journaux des bases' : 'Aucune base en échec',
      icon: 'triangle-exclamation',
      tone: failed ? 'bad' : 'muted',
    },
  ] satisfies SummaryMetric[]
  $: segments = [
    { label: 'Actives', value: running, tone: 'good' },
    { label: 'Démarrage / maintenance', value: pending, tone: 'info' },
    { label: 'En veille', value: states.sleeping || 0, tone: 'warning' },
    { label: 'Éteintes', value: states.off || 0, tone: 'muted' },
    { label: 'En échec', value: failed, tone: 'bad' },
  ] satisfies SummarySegment[]
  $: metadata = [
    ...(!$userStore?.superAdmin && quota > 0 ? [`Quota personnel : ${owned} / ${quota}`] : []),
    `${instances.filter((instance) => instance.syncAdmin).length} avec synchro admin`,
    ...versions
      .slice(0, 2)
      .map((version) => `v${version} · ${instances.filter((instance) => instance.version === version).length}`),
  ]
</script>

<ResourceSummary label="Résumé des bases de données" {metrics} {segments} {metadata} />
