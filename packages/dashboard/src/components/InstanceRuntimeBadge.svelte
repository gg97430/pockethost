<script lang="ts">
  import { getInstanceRuntimeState, runtimeStateLabel, type InstanceRuntimeState } from '$util/instancePower'
  import type { InstanceFields } from 'pockethost/common'

  interface Props {
    instance: Pick<InstanceFields, 'power' | 'status'>
  }

  let { instance }: Props = $props()

  const state = $derived(getInstanceRuntimeState(instance))

  const badgeClass: Record<InstanceRuntimeState, string> = {
    running: 'runtime-badge--running',
    sleeping: 'runtime-badge--sleeping',
    starting: 'runtime-badge--starting',
    vacuuming: 'runtime-badge--vacuuming',
    failed: 'runtime-badge--failed',
    off: 'runtime-badge--off',
  }
</script>

<span
  class="runtime-badge {badgeClass[state]}"
  title={runtimeStateLabel[state]}
>
  {#if state === 'running'}
    <span class="runtime-badge-dot runtime-badge-dot--running"></span>
  {:else if state === 'starting'}
    <span class="runtime-badge-spinner runtime-badge-spinner--starting"></span>
  {:else if state === 'vacuuming'}
    <span class="runtime-badge-spinner runtime-badge-spinner--vacuuming"></span>
  {:else if state === 'sleeping'}
    <span class="runtime-badge-dot runtime-badge-dot--sleeping"></span>
  {:else if state === 'failed'}
    <wa-icon name="circle-xmark" class="text-xs"></wa-icon>
  {:else if state === 'off'}
    <span class="runtime-badge-dot runtime-badge-dot--off"></span>
  {/if}
  {runtimeStateLabel[state]}
</span>

<style>
  .runtime-badge {
    display: inline-flex;
    align-items: center;
    flex-shrink: 0;
    gap: 0.375rem;
    height: 1.5rem;
    border: 1px solid transparent;
    border-radius: 999px;
    padding: 0 0.5rem;
    font-size: 0.75rem;
    font-weight: 700;
    line-height: 1;
  }

  .runtime-badge--running {
    border-color: rgb(30 184 84 / 0.28);
    background: rgb(30 184 84 / 0.12);
    color: #15803d;
  }

  .runtime-badge--sleeping {
    border-color: rgb(71 85 105 / 0.22);
    background: rgb(71 85 105 / 0.1);
    color: #475569;
  }

  .runtime-badge--starting {
    border-color: rgb(217 119 6 / 0.28);
    background: rgb(251 191 36 / 0.14);
    color: #b45309;
  }

  .runtime-badge--vacuuming {
    border-color: rgb(2 132 199 / 0.28);
    background: rgb(56 189 248 / 0.14);
    color: #0369a1;
  }

  .runtime-badge--failed {
    border-color: rgb(220 38 38 / 0.28);
    background: rgb(239 68 68 / 0.12);
    color: #b91c1c;
  }

  .runtime-badge--off {
    border-color: var(--app-border);
    background: var(--app-surface-soft);
    color: var(--app-text-muted);
  }

  .runtime-badge-dot {
    width: 0.375rem;
    height: 0.375rem;
    border-radius: 999px;
  }

  .runtime-badge-dot--running {
    background: #1eb854;
    animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
  }

  .runtime-badge-dot--sleeping {
    background: #64748b;
  }

  .runtime-badge-dot--off {
    background: var(--app-text-faint);
  }

  .runtime-badge-spinner {
    display: inline-block;
    width: 0.75rem;
    height: 0.75rem;
    border-radius: 999px;
    animation: spin 1s linear infinite;
  }

  .runtime-badge-spinner--starting {
    border: 1px solid rgb(217 119 6 / 0.28);
    border-top-color: #d97706;
  }

  .runtime-badge-spinner--vacuuming {
    border: 1px solid rgb(2 132 199 / 0.28);
    border-top-color: #0284c7;
  }

  :global(html[data-theme='dark']) .runtime-badge--running {
    color: #4ade80;
  }

  :global(html[data-theme='dark']) .runtime-badge--sleeping {
    color: #94a3b8;
  }

  :global(html[data-theme='dark']) .runtime-badge--starting {
    color: #fbbf24;
  }

  :global(html[data-theme='dark']) .runtime-badge--vacuuming {
    color: #38bdf8;
  }

  :global(html[data-theme='dark']) .runtime-badge--failed {
    color: #f87171;
  }

  @keyframes pulse {
    50% {
      opacity: 0.45;
    }
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>
