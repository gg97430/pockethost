<script lang="ts">
  import type { WebGroupedApps } from '$lib/webProjectGroupsApi'
  import ResourceSummary, { type SummaryMetric, type SummarySegment } from '$components/ResourceSummary.svelte'
  export let groups: WebGroupedApps | null = null
  export let loading = false

  $: apps = [...(groups?.groups.flatMap((group) => group.apps) || []), ...(groups?.unassigned?.apps || [])]
  const inError = (app: (typeof apps)[number]) => app.status === 'failed' || !!app.lastError
  const inProgress = (app: (typeof apps)[number]) =>
    !!app.deploymentInProgress || ['queued', 'building', 'starting', 'deploying'].includes(app.status)
  $: running = apps.filter((app) => app.status === 'running').length
  $: progress = apps.filter(inProgress).length
  $: failed = apps.filter(inError).length
  $: previousOnline = apps.filter((app) => inError(app) && app.status === 'running').length
  $: healthy = apps.filter((app) => !inError(app) && !inProgress(app) && app.status === 'running').length
  $: updating = apps.filter((app) => !inError(app) && inProgress(app)).length
  $: stopped = apps.filter((app) => !inError(app) && !inProgress(app) && app.status === 'stopped').length
  $: other = apps.length - healthy - updating - stopped - failed
  $: metrics = [
    {
      label: 'Hébergements',
      value: apps.length,
      note: `${groups?.groups.length || 0} projet${(groups?.groups.length || 0) > 1 ? 's' : ''}`,
      icon: 'layer-group',
      tone: 'info',
    },
    { label: 'En ligne', value: running, note: 'Sites et workers actifs', icon: 'circle-check', tone: 'good' },
    {
      label: 'En cours',
      value: progress,
      note: 'En attente ou en traitement',
      icon: 'rocket',
      tone: progress ? 'info' : 'muted',
    },
    {
      label: 'À vérifier',
      value: failed,
      note: previousOnline
        ? `Dont ${previousOnline} encore en ligne`
        : failed
          ? 'Consultez les déploiements'
          : 'Aucune erreur signalée',
      icon: 'triangle-exclamation',
      tone: failed ? 'bad' : 'muted',
    },
  ] satisfies SummaryMetric[]
  $: segments = [
    { label: 'En ligne', value: healthy, tone: 'good' },
    { label: 'En cours', value: updating, tone: 'info' },
    { label: 'Arrêtés', value: stopped, tone: 'muted' },
    { label: 'À vérifier', value: failed, tone: 'bad' },
    { label: 'À déployer / autre état', value: other, tone: 'warning' },
  ] satisfies SummarySegment[]
  $: metadata = [
    `${apps.filter((app) => app.autoDeploy).length} en déploiement auto`,
    ...((groups?.unassigned?.apps.length || 0) > 0 ? [`${groups!.unassigned!.apps.length} sans projet`] : []),
    ...(groups?.truncated ? ['Résumé limité aux éléments chargés'] : []),
  ]
</script>

<ResourceSummary label="Résumé des hébergements" {metrics} {segments} {metadata} {loading} />
