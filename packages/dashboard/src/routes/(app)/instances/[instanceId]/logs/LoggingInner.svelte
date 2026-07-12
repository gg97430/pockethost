<script lang="ts">
  import { client } from '$src/pocketbase-client'
  import { mkCleanup } from '$util/componentCleanup'
  import { StreamNames, type InstanceLogFields, type Unsubscribe } from 'pockethost/common'
  import { onMount, tick } from 'svelte'
  import { derived, writable } from 'svelte/store'
  import { instance } from '../store'

  const MAX_VISIBLE_LOGS = 2_000

  $: ({ id } = $instance)

  const logClass = (type: StreamNames) => {
    if (type === StreamNames.StdOut) return 'log-line--stdout'
    if (type === StreamNames.StdErr) return 'log-line--stderr'
    return 'log-line--info'
  }

  const logText = (log: InstanceLogFields) => {
    try {
      const parsed = JSON.parse(log.message)
      return typeof parsed === 'string' ? parsed : log.message
    } catch {
      return log.message
    }
  }

  const handleFullScreenModal = () => {
    const modal = document.getElementById('loggingFullscreenModal') as HTMLDialogElement
    modal?.showModal()
  }

  let logElement: HTMLElement
  let logElementPopup: HTMLElement
  let autoScroll = true
  let scrollFrame: number | undefined

  const scrollToBottom = (node: HTMLElement | undefined) => {
    if (node) node.scroll({ top: node.scrollHeight, behavior: 'auto' })
  }

  const scheduleScrollToBottom = () => {
    if (scrollFrame) cancelAnimationFrame(scrollFrame)
    scrollFrame = requestAnimationFrame(() => {
      scrollToBottom(logElement)
      scrollToBottom(logElementPopup)
      scrollFrame = undefined
    })
  }

  $: if ($logs.length && autoScroll) {
    tick().then(scheduleScrollToBottom)
  }

  const logs = writable<InstanceLogFields[]>([])
  const onDestroy = mkCleanup()
  const instanceId = derived(instance, (instance) => instance.id)

  onMount(async () => {
    let unwatch: Unsubscribe | undefined
    const unsub = instanceId.subscribe(() => {
      unwatch?.()
      logs.set([])
      unwatch = client().watchInstanceLog($instance, (newLog) => {
        logs.update((currentLogs) => {
          const nextLog = {
            time: '<no time>',
            stream: StreamNames.StdOut,
            message: '<no message>',
            ...newLog,
          }
          const visibleLogs =
            currentLogs.length >= MAX_VISIBLE_LOGS ? currentLogs.slice(-(MAX_VISIBLE_LOGS - 1)) : currentLogs
          return [...visibleLogs, nextLog]
        })
      })
    })
    onDestroy(unsub)
    onDestroy(() => unwatch?.())
    onDestroy(() => {
      if (scrollFrame) cancelAnimationFrame(scrollFrame)
    })
  })
</script>

<dialog id="loggingFullscreenModal" class="log-dialog">
  <div class="log-dialog-shell">
    <div class="log-console-toolbar">
      <div class="log-console-title">
        <span class="log-console-live-dot"></span>
        <div>
          <strong>Logs de l’instance</strong>
          <small>Les 2 000 dernières lignes</small>
        </div>
      </div>

      <wa-button variant="neutral" size="s" onclick={() => (autoScroll = !autoScroll)}>
        {autoScroll ? 'Défilement auto' : 'Défilement manuel'}
        <wa-icon slot="end" name={autoScroll ? 'arrow-down' : 'pause'}></wa-icon>
      </wa-button>
    </div>

    <div class="log-console-scroll log-console-scroll--fullscreen" bind:this={logElementPopup}>
      {#each $logs as log}
        <div class="log-line {logClass(log.stream)}">
          <time class="log-time" datetime={log.time}>{log.time}</time>
          <span class="log-message">{logText(log)}</span>
        </div>
      {/each}
    </div>

    <form method="dialog" class="log-dialog-footer">
      <wa-button type="submit" variant="neutral" size="s">Fermer</wa-button>
    </form>
  </div>
</dialog>

<section class="log-console" aria-label="Console des logs de l’instance">
  <div class="log-console-toolbar">
    <div class="log-console-title">
      <span class="log-console-live-dot"></span>
      <div>
        <strong>Flux en direct</strong>
        <small>{$logs.length.toLocaleString('fr-FR')} / {MAX_VISIBLE_LOGS.toLocaleString('fr-FR')} lignes</small>
      </div>
    </div>

    <div class="log-console-actions">
      <wa-button variant="neutral" size="s" onclick={() => (autoScroll = !autoScroll)}>
        {autoScroll ? 'Défilement auto' : 'Défilement manuel'}
        <wa-icon slot="end" name={autoScroll ? 'arrow-down' : 'pause'}></wa-icon>
      </wa-button>
      <wa-button variant="neutral" size="s" onclick={handleFullScreenModal}>
        Plein écran
        <wa-icon slot="end" name="up-right-and-down-left-from-center"></wa-icon>
      </wa-button>
    </div>
  </div>

  <div class="log-console-scroll" bind:this={logElement}>
    {#each $logs as log}
      <div class="log-line {logClass(log.stream)}">
        <time class="log-time" datetime={log.time}>{log.time}</time>
        <span class="log-message">{logText(log)}</span>
      </div>
    {/each}

    {#if $logs.length === 0}
      <div class="log-console-empty">En attente des premières lignes…</div>
    {/if}
  </div>
</section>

<style>
  .log-console,
  .log-dialog-shell {
    --console-bg: #080b09;
    --console-surface: #101612;
    --console-border: #29362e;
    --console-text: #dce7e0;
    --console-muted: #91a199;
    --console-time: #8da2fb;
    display: flex;
    overflow: hidden;
    flex-direction: column;
    border: 1px solid var(--console-border);
    border-radius: 0.8rem;
    background: var(--console-bg);
    box-shadow:
      0 18px 45px rgb(0 0 0 / 0.18),
      inset 0 1px 0 rgb(255 255 255 / 0.04);
    color: var(--console-text);
  }

  .log-console {
    height: clamp(22rem, 50vh, 46rem);
  }

  .log-console-toolbar {
    z-index: 1;
    display: flex;
    flex: 0 0 auto;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    min-height: 4.25rem;
    border-bottom: 1px solid var(--console-border);
    background: linear-gradient(180deg, #151d18 0%, var(--console-surface) 100%);
    padding: 0.75rem 0.9rem;
  }

  .log-console-title {
    display: flex;
    align-items: center;
    gap: 0.65rem;
    min-width: 0;
  }

  .log-console-title > div {
    display: grid;
    gap: 0.08rem;
  }

  .log-console-title strong {
    color: #f2f7f4;
    font-size: 0.82rem;
    font-weight: 800;
    letter-spacing: 0.02em;
  }

  .log-console-title small {
    color: var(--console-muted);
    font-size: 0.7rem;
  }

  .log-console-live-dot {
    width: 0.58rem;
    height: 0.58rem;
    border-radius: 999px;
    background: #4ade80;
    box-shadow:
      0 0 0 0.28rem rgb(74 222 128 / 0.12),
      0 0 1rem rgb(74 222 128 / 0.35);
  }

  .log-console-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 0.4rem;
  }

  .log-console-scroll {
    min-height: 0;
    flex: 1 1 auto;
    overflow: auto;
    padding: 0.65rem 0 1rem;
    scrollbar-color: #3d5145 var(--console-bg);
    scrollbar-width: thin;
  }

  .log-line {
    display: grid;
    grid-template-columns: max-content minmax(0, 1fr);
    gap: 0.8rem;
    border-left: 2px solid transparent;
    padding: 0.22rem 0.9rem;
    font-family: ui-monospace, 'SFMono-Regular', 'Cascadia Code', Menlo, Consolas, monospace;
    font-size: clamp(0.78rem, 0.76rem + 0.12vw, 0.92rem);
    line-height: 1.55;
  }

  .log-line:hover {
    background: rgb(255 255 255 / 0.035);
  }

  .log-time {
    color: var(--console-time);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  .log-message {
    min-width: 0;
    color: var(--console-text);
    overflow-wrap: anywhere;
    white-space: pre-wrap;
  }

  .log-line--stderr {
    border-left-color: #fb7185;
    background: rgb(251 113 133 / 0.055);
  }

  .log-line--stderr .log-message {
    color: #ff9eaa;
  }

  .log-line--info .log-message {
    color: #7dd3fc;
  }

  .log-console-empty {
    display: grid;
    min-height: 12rem;
    place-items: center;
    color: var(--console-muted);
    font-family: ui-monospace, 'SFMono-Regular', Menlo, Consolas, monospace;
    font-size: 0.82rem;
  }

  .log-dialog {
    width: min(96vw, 100rem);
    max-width: none;
    height: 92vh;
    max-height: none;
    overflow: hidden;
    border: 0;
    border-radius: 1rem;
    background: transparent;
    padding: 0;
  }

  .log-dialog::backdrop {
    background: rgb(3 7 5 / 0.76);
    backdrop-filter: blur(8px);
  }

  .log-dialog-shell {
    height: 100%;
  }

  .log-console-scroll--fullscreen {
    min-height: 0;
  }

  .log-dialog-footer {
    display: flex;
    flex: 0 0 auto;
    justify-content: flex-end;
    border-top: 1px solid var(--console-border);
    background: var(--console-surface);
    padding: 0.65rem 0.9rem;
  }

  @media (max-width: 720px) {
    .log-console {
      height: 60vh;
    }

    .log-console-actions {
      width: 100%;
      justify-content: stretch;
    }

    .log-console-actions :global(wa-button) {
      flex: 1;
    }

    .log-line {
      grid-template-columns: 1fr;
      gap: 0.06rem;
      padding-block: 0.42rem;
    }

    .log-time {
      color: #a8b5fb;
      font-size: 0.7rem;
    }
  }
</style>
