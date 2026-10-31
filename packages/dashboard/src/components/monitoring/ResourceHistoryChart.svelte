<script lang="ts">
  import {
    resourceFormatDate,
    resourceMetricFormat,
    resourceMetricLabel,
    resourceSegments,
    resourceValue,
    type PlatformResourceHistoryPoint,
    type PlatformResourceRange,
    type ResourceMetric,
    type ResourceSeries,
  } from '$lib/platformResources'
  export let loading = false
  export let metric: ResourceMetric
  export let points: PlatformResourceHistoryPoint[] = []
  export let series: ResourceSeries[] = []
  export let start = 0
  export let end = 1
  export let maximum = 100
  export let intervalSeconds = 15
  export let range: PlatformResourceRange = '1h'
  let cursor: number | null = null
  $: label = resourceMetricLabel(metric)
  $: ceiling = maximum > 0 ? maximum : 1
  $: lines = series.map((item) => ({
    ...item,
    segments: resourceSegments(points, item, metric, start, end, ceiling, intervalSeconds),
  }))
  $: measured = lines.some((line) => line.segments.length > 0)
  $: index = cursor === null ? points.length - 1 : Math.max(0, Math.min(points.length - 1, cursor))
  $: selected = points[index]
  $: readout = series.map((item) => ({ ...item, value: selected ? resourceValue(selected, item, metric) : null }))
  $: accessibleValues = readout.map((item) => `${item.label} : ${resourceMetricFormat(metric, item.value)}`).join(', ')
  $: if (range) cursor = null
  const axis = (value: number) =>
    `${new Intl.NumberFormat('fr-FR', { maximumFractionDigits: value < 10 ? 1 : 0 }).format(value)}${metric === 'cpuPercent' ? ' %' : ' Go'}`
  const time = (timestamp: number) =>
    new Date(timestamp).toLocaleString(
      'fr-FR',
      range === '7d' || range === '24h'
        ? { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }
        : { hour: '2-digit', minute: '2-digit' }
    )
  const hover = (event: PointerEvent) => {
    if (!points.length) return
    const box = event.currentTarget instanceof SVGElement ? event.currentTarget.getBoundingClientRect() : null
    if (!box) return
    const target =
      start + Math.max(0, Math.min(1, (((event.clientX - box.left) / box.width) * 720 - 54) / 642)) * (end - start)
    cursor = points.reduce(
      (best, point, candidate) =>
        Math.abs(point.timestamp - target) < Math.abs(points[best]!.timestamp - target) ? candidate : best,
      0
    )
  }
</script>

<section class="resource-chart" aria-labelledby={`resources-${metric}-heading`}>
  <div class="chart-heading">
    <h2 id={`resources-${metric}-heading`}>{label}</h2>
    <span
      >{metric === 'cpuPercent'
        ? '100 % = tous les cœurs'
        : metric === 'memoryBytes'
          ? 'Mémoire utilisée'
          : 'Espace occupé'}</span
    >
  </div>
  <div class="chart-canvas">
    <svg
      viewBox="0 0 720 218"
      role="img"
      aria-labelledby={`resources-${metric}-title resources-${metric}-description`}
      onpointermove={hover}
      onpointerleave={() => (cursor = null)}
    >
      <title id={`resources-${metric}-title`}>Historique {label}</title>
      <desc id={`resources-${metric}-description`}
        >Les espaces sans courbe correspondent aux mesures absentes. {selected
          ? `${resourceFormatDate(selected.timestamp)}. ${accessibleValues}`
          : 'Aucune mesure disponible.'}</desc
      >
      {#each [0, 0.5, 1] as fraction}
        <line x1="54" x2="696" y1={20 + (1 - fraction) * 160} y2={20 + (1 - fraction) * 160} class="chart-grid" />
        <text x="44" y={24 + (1 - fraction) * 160} text-anchor="end">{axis(ceiling * fraction)}</text>
      {/each}
      {#each [0, 0.5, 1] as fraction}
        <text x={54 + fraction * 642} y="207" text-anchor={fraction === 0 ? 'start' : fraction === 1 ? 'end' : 'middle'}
          >{time(start + fraction * (end - start))}</text
        >
      {/each}
      {#each lines as line (line.id)}
        {#each line.segments as segment}
          {#if segment.length === 1}<circle cx={segment[0]!.x} cy={segment[0]!.y} r="3" fill={line.color} />
          {:else}<path
              d={segment.map((point, i) => `${i ? 'L' : 'M'}${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(' ')}
              fill="none"
              stroke={line.color}
              stroke-width={line.id === 'host' ? 2.4 : 1.6}
              stroke-linecap="round"
              stroke-linejoin="round"
            />{/if}
        {/each}
      {/each}
      {#if cursor !== null && selected}<line
          x1={54 + ((selected.timestamp - start) / Math.max(1, end - start)) * 642}
          x2={54 + ((selected.timestamp - start) / Math.max(1, end - start)) * 642}
          y1="20"
          y2="180"
          class="chart-cursor"
        />{/if}
    </svg>
    {#if !measured}<p class="chart-empty">
        {loading
          ? 'Lecture de l’historique…'
          : series.length
            ? 'Aucune mesure sur cette période'
            : 'Sélectionnez une courbe dans la légende'}
      </p>{/if}
  </div>
  <div class="chart-reading">
    <time datetime={selected ? new Date(selected.timestamp).toISOString() : undefined}
      >{selected ? resourceFormatDate(selected.timestamp) : 'En attente de mesures'}</time
    >
    {#if points.length > 1}<input
        type="range"
        min="0"
        max={points.length - 1}
        value={index}
        oninput={(event) => (cursor = Number(event.currentTarget.value))}
        aria-label={`Parcourir les mesures ${label}`}
        aria-valuetext={`${selected ? resourceFormatDate(selected.timestamp) : ''}. ${accessibleValues}`}
      />{/if}
  </div>
  <dl class="chart-values">
    {#each readout as item (item.id)}<div>
        <dt><span style={`--series-color:${item.color}`}></span>{item.label}</dt>
        <dd>{resourceMetricFormat(metric, item.value)}</dd>
      </div>{/each}
  </dl>
</section>

<style>
  .resource-chart {
    min-width: 0;
    padding: 1rem 1.1rem;
    border: 1px solid var(--web-border);
    border-radius: 7px;
    background: var(--web-surface);
  }
  .chart-heading {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 1rem;
  }
  .chart-heading h2 {
    font-size: 0.85rem;
  }
  .chart-heading > span {
    color: var(--web-muted);
    font-size: 0.69rem;
  }
  .chart-canvas {
    position: relative;
    margin: 1rem -0.25rem 0.3rem;
    min-height: 150px;
  }
  svg {
    display: block;
    width: 100%;
    height: auto;
    overflow: visible;
  }
  svg text {
    fill: var(--web-muted);
    font-size: 10px;
    font-variant-numeric: tabular-nums;
  }
  .chart-grid {
    stroke: var(--web-border);
    stroke-width: 1;
    stroke-dasharray: 3 5;
  }
  .chart-cursor {
    stroke: var(--web-muted);
    stroke-width: 1;
    stroke-dasharray: 3 3;
  }
  .chart-empty {
    position: absolute;
    inset: 30% 10% auto;
    text-align: center;
    color: var(--web-muted);
    background: var(--web-surface);
    padding: 0.5rem;
  }
  .chart-reading {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin: 0.4rem 0 0.85rem;
    min-height: 1.3rem;
  }
  .chart-reading time {
    color: var(--web-muted);
    font-size: 0.68rem;
    flex: 0 0 auto;
  }
  .chart-reading input {
    width: 100%;
    min-width: 30px;
    accent-color: var(--web-accent);
    height: 12px;
  }
  .chart-values {
    display: flex;
    gap: 0.5rem 1rem;
    flex-wrap: wrap;
    margin: 0;
  }
  .chart-values > div {
    display: flex;
    gap: 0.45rem;
    align-items: center;
    font-size: 0.68rem;
  }
  dt {
    display: inline-flex;
    gap: 0.35rem;
    align-items: center;
    color: var(--web-muted);
  }
  dt span {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--series-color);
  }
  dd {
    margin: 0;
    font-variant-numeric: tabular-nums;
    color: var(--app-text-strong);
  }
  @media (max-width: 700px) {
    .resource-chart {
      padding: 0.85rem 0.6rem;
    }
    .chart-heading {
      padding-inline: 0.35rem;
    }
    .chart-reading {
      flex-wrap: wrap;
      padding-inline: 0.35rem;
    }
    .chart-reading input {
      flex: 1;
    }
    .chart-values {
      padding-inline: 0.35rem;
    }
  }
</style>
