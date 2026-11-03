<script lang="ts" module>
  export type SummaryTone = 'neutral' | 'good' | 'info' | 'warning' | 'bad' | 'muted'
  export type SummaryMetric = { label: string; value: number; note: string; icon: string; tone?: SummaryTone }
  export type SummarySegment = { label: string; value: number; tone: SummaryTone }
</script>

<script lang="ts">
  export let label: string
  export let metrics: SummaryMetric[] = []
  export let segments: SummarySegment[] = []
  export let metadata: string[] = []
  export let loading = false
  $: visible = segments.filter((segment) => segment.value > 0)
  $: total = visible.reduce((sum, segment) => sum + segment.value, 0)
  const number = (value: number) => new Intl.NumberFormat('fr-FR').format(value)
</script>

<section class="resource-summary" aria-label={label} aria-busy={loading}>
  <div class="summary-metrics">
    {#each metrics as metric (metric.label)}
      <div class="summary-metric" data-tone={metric.tone || 'neutral'}>
        <span class="metric-icon"><wa-icon name={metric.icon} aria-hidden="true"></wa-icon></span>
        <div>
          <p class="metric-label">{metric.label}</p>
          <strong class="metric-value">{loading ? '—' : number(metric.value)}</strong>
          <p class="metric-note">{loading ? 'Chargement…' : metric.note}</p>
        </div>
      </div>
    {/each}
  </div>
  <div class="summary-bottom">
    <div class="distribution">
      <div
        class="distribution-track"
        role="img"
        aria-label={loading
          ? 'Répartition en cours de chargement'
          : visible.map((segment) => `${number(segment.value)} ${segment.label}`).join(', ') || 'Aucune ressource'}
      >
        {#if !loading}{#each visible as segment (segment.label)}<span
              data-tone={segment.tone}
              style={`width:${(segment.value / total) * 100}%`}
            ></span>{/each}{/if}
      </div>
      <ul class="legend" aria-label="Répartition des états">
        {#if loading}<li>Chargement des états…</li>
        {:else}{#each visible as segment (segment.label)}<li data-tone={segment.tone}>
              <i aria-hidden="true"></i><span>{segment.label}</span><strong>{number(segment.value)}</strong>
            </li>{:else}<li>Aucune ressource pour le moment</li>{/each}{/if}
      </ul>
    </div>
    {#if metadata.length && !loading}<ul class="summary-meta" aria-label="Informations complémentaires">
        {#each metadata as item}<li>{item}</li>{/each}
      </ul>{/if}
  </div>
</section>

<style>
  .resource-summary {
    --summary-good: light-dark(#24834e, #66ce95);
    --summary-info: light-dark(#3f70d9, #80a7ff);
    --summary-warning: light-dark(#a56b11, #e9b859);
    --summary-bad: light-dark(#bf3746, #f08792);
    --summary-muted: light-dark(#8a90a0, #737b90);
    --summary-neutral: var(--app-text-muted);
    color: var(--app-text);
    border: 1px solid var(--app-border);
    border-radius: 0.6rem;
    background: var(--app-surface);
    margin-bottom: 1.5rem;
    overflow: hidden;
  }
  [data-tone='good'] {
    --tone: var(--summary-good);
  }
  [data-tone='info'] {
    --tone: var(--summary-info);
  }
  [data-tone='warning'] {
    --tone: var(--summary-warning);
  }
  [data-tone='bad'] {
    --tone: var(--summary-bad);
  }
  [data-tone='muted'] {
    --tone: var(--summary-muted);
  }
  [data-tone='neutral'] {
    --tone: var(--summary-neutral);
  }
  .summary-metrics {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    margin: 0;
  }
  .summary-metric {
    display: flex;
    align-items: flex-start;
    gap: 0.8rem;
    padding: 1.1rem 1.2rem;
    min-width: 0;
    position: relative;
  }
  .summary-metric + .summary-metric::before {
    content: '';
    position: absolute;
    inset: 1.1rem auto 1.1rem 0;
    width: 1px;
    background: var(--app-border);
  }
  .metric-icon {
    flex: 0 0 2.2rem;
    width: 2.2rem;
    height: 2.2rem;
    display: grid;
    place-items: center;
    border-radius: 0.5rem;
    color: var(--tone);
    background: color-mix(in srgb, var(--tone) 9%, transparent);
    font-size: 0.85rem;
    margin-top: 0.1rem;
  }
  .metric-label {
    margin: 0;
    color: var(--app-text-muted);
    font-size: 0.7rem;
    font-weight: 500;
  }
  .metric-value {
    display: block;
    margin: 0.25rem 0;
    font-size: 1.65rem;
    font-weight: 600;
    letter-spacing: -0.04em;
    color: var(--app-text-strong);
    font-variant-numeric: tabular-nums;
    line-height: 1.15;
  }
  [data-tone='bad'] .metric-value {
    color: var(--summary-bad);
  }
  [data-tone='warning'] .metric-value {
    color: var(--summary-warning);
  }
  .metric-note {
    margin: 0;
    color: var(--app-text-faint);
    font-size: 0.625rem;
    line-height: 1.5;
  }
  .summary-bottom {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1.5rem;
    padding: 0.8rem 1.2rem;
    border-top: 1px solid var(--app-border);
    background: var(--app-surface-soft);
  }
  .distribution {
    min-width: 0;
    flex: 1 1 55%;
    max-width: 44rem;
  }
  .distribution-track {
    display: flex;
    gap: 2px;
    height: 0.3rem;
    border-radius: 1rem;
    overflow: hidden;
    background: var(--app-border);
  }
  .distribution-track span {
    display: block;
    min-width: 2px;
    height: 100%;
    background: var(--tone);
  }
  .legend,
  .summary-meta {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
  }
  .legend {
    gap: 0.4rem 1rem;
    margin-top: 0.5rem;
    font-size: 0.6rem;
    color: var(--app-text-muted);
  }
  .legend li {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
  }
  .legend i {
    width: 0.3rem;
    height: 0.3rem;
    border-radius: 50%;
    background: var(--tone);
  }
  .legend strong {
    color: var(--app-text);
    font-weight: 600;
  }
  .summary-meta {
    gap: 0.4rem 0.6rem;
    justify-content: flex-end;
    font-size: 0.6rem;
    color: var(--app-text-muted);
    line-height: 1.5;
  }
  .summary-meta li {
    padding: 0.25rem 0.5rem;
    background: var(--app-surface);
    border: 1px solid var(--app-border);
    border-radius: 0.3rem;
  }
  @media (max-width: 1050px) {
    .summary-metric {
      padding: 1rem 0.85rem;
      gap: 0.6rem;
    }
    .metric-icon {
      width: 1.8rem;
      height: 1.8rem;
      flex-basis: 1.8rem;
      font-size: 0.75rem;
    }
    .summary-bottom {
      gap: 0.8rem;
    }
  }
  @media (max-width: 760px) {
    .summary-metrics {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .summary-metric:nth-child(3)::before {
      display: none;
    }
    .summary-metric:nth-child(n + 3) {
      border-top: 1px solid var(--app-border);
    }
    .summary-bottom {
      align-items: stretch;
      flex-direction: column;
    }
    .summary-meta {
      justify-content: flex-start;
    }
    .distribution {
      max-width: none;
    }
  }
  @media (max-width: 380px) {
    .summary-metric {
      padding: 0.85rem 0.65rem;
    }
    .metric-icon {
      display: none;
    }
  }
</style>
