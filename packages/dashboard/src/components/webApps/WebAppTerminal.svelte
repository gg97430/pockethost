<script lang="ts">
  import { onMount, tick } from 'svelte'
  import { webRequest, webError, type WebTerminalGrant } from '$lib/webApps'
  import { webTerminalUrl, terminalClientFrame } from '$lib/webConsoleFlow'
  import type { Terminal } from '@xterm/xterm'
  import type { FitAddon } from '@xterm/addon-fit'

  export let appId: string
  export let activeDeployment = ''
  export let running = false
  let host: HTMLDivElement | undefined
  let term: Terminal | undefined
  let fit: FitAddon | undefined
  let socket: WebSocket | undefined
  let resizeObserver: ResizeObserver | undefined
  let shell: 'sh' | 'bash' = 'sh'
  let state: 'idle' | 'connecting' | 'connected' | 'closed' = 'idle'
  let error = ''
  let status = 'Aucune session ouverte'
  let sessionId = ''
  let openedDeployment = ''
  let copied = false
  let disposed = false
  let generation = 0
  let initialization: Promise<void> | undefined
  const send = (value: Record<string, unknown>) => {
    if (socket?.readyState !== WebSocket.OPEN) return
    try {
      socket.send(terminalClientFrame(value))
    } catch (e) {
      error = webError(e)
    }
  }
  const resize = () => {
    if (!term || !fit || !host || !host.clientWidth) return
    try {
      fit.fit()
      if (term.cols > 500 || term.rows > 200) term.resize(Math.min(500, term.cols), Math.min(200, term.rows))
      send({ type: 'resize', cols: term.cols, rows: term.rows })
    } catch {
      /* The pane may be transitioning out of the DOM. */
    }
  }
  const disconnect = (reason = 'Session fermée') => {
    ++generation
    if (socket?.readyState === WebSocket.OPEN) {
      try {
        send({ type: 'close' })
      } catch {
        /* Socket already closed. */
      }
    }
    socket?.close(1000)
    socket = undefined
    sessionId = ''
    state = 'closed'
    status = reason
    term?.reset()
    if (term) term.options.disableStdin = true
  }
  $: if (state === 'connected' && (!running || (openedDeployment && activeDeployment !== openedDeployment))) {
    disconnect('La version publiée a changé. Ouvrez une nouvelle session.')
  }
  const initialize = () => {
    if (term || !host) return Promise.resolve()
    if (initialization) return initialization
    initialization = initializeTerminal().finally(() => {
      initialization = undefined
    })
    return initialization
  }
  const initializeTerminal = async () => {
    const [{ Terminal: XTerm }, { FitAddon: XFit }] = await Promise.all([
      import('@xterm/xterm'),
      import('@xterm/addon-fit'),
    ])
    await import('@xterm/xterm/css/xterm.css')
    if (disposed || !host) return
    term = new XTerm({
      cursorBlink: true,
      convertEol: false,
      disableStdin: true,
      scrollback: 2000,
      fontSize: 13,
      lineHeight: 1.35,
      fontFamily: '"Geist Mono", "SFMono-Regular", Consolas, monospace',
      theme: {
        background: '#101012',
        foreground: '#d6d6dc',
        cursor: '#3b7cff',
        selectionBackground: '#3b7cff33',
        black: '#101012',
        green: '#8ed18b',
        brightGreen: '#b9e4ae',
      },
    })
    fit = new XFit()
    term.loadAddon(fit)
    term.open(host)
    term.onData((data) => {
      if (state === 'connected') send({ type: 'input', data })
    })
    resizeObserver = new ResizeObserver(resize)
    resizeObserver.observe(host)
    resize()
  }
  const connect = async () => {
    if (!running || state === 'connecting' || state === 'connected' || disposed) return
    const request = ++generation
    state = 'connecting'
    status = 'Ouverture de la session…'
    error = ''
    copied = false
    try {
      await initialize()
      const grant = await webRequest<WebTerminalGrant>(`/${appId}/terminal/session`, {
        method: 'POST',
        body: { shell },
      })
      if (disposed || request !== generation) return
      const url = webTerminalUrl(window.location.origin, grant.wsPath)
      const connection = new WebSocket(url, ['ph-web-terminal.v1', `ph-web-grant.${grant.token}`])
      connection.binaryType = 'arraybuffer'
      socket = connection
      openedDeployment = activeDeployment
      sessionId = grant.sessionId
      connection.onmessage = (event) => {
        if (disposed || request !== generation || connection !== socket) return
        if (event.data instanceof ArrayBuffer) {
          term?.write(new Uint8Array(event.data))
          return
        }
        try {
          const message = JSON.parse(String(event.data))
          if (message.type === 'ready') {
            state = 'connected'
            status = `Session interactive · ${grant.shell}`
            if (term) term.options.disableStdin = false
            resize()
            term?.focus()
          } else if (message.type === 'error') {
            error = typeof message.message === 'string' ? message.message : 'Le terminal est indisponible.'
          } else if (message.type === 'exit') {
            status = typeof message.reason === 'string' ? message.reason : 'Le shell s’est terminé.'
          }
        } catch {
          error = 'Le terminal a reçu une réponse invalide.'
          disconnect('Session fermée')
        }
      }
      connection.onclose = (event) => {
        if (disposed || request !== generation || connection !== socket) return
        socket = undefined
        sessionId = ''
        state = 'closed'
        if (term) {
          term.options.disableStdin = true
          term.reset()
        }
        if (event.code === 1008) error = error || 'La session a expiré ou vos droits ont changé. Reconnectez-vous.'
        else if (event.code === 1009) error = 'Un message dépasse la taille autorisée. Ouvrez une nouvelle session.'
        else if (event.code !== 1000) error = error || 'La connexion au terminal a été interrompue.'
        status = 'Session terminée'
      }
      connection.onerror = () => {
        if (request === generation) error = 'La connexion sécurisée au terminal n’a pas pu être établie.'
      }
    } catch (e) {
      if (request === generation && !disposed) {
        error = webError(e)
        state = 'closed'
        status = 'Connexion indisponible'
      }
    }
  }
  const copy = async () => {
    const selected = term?.getSelection()
    if (!selected) {
      error = 'Sélectionnez du texte dans le terminal avant de le copier.'
      return
    }
    try {
      await navigator.clipboard.writeText(selected)
      copied = true
    } catch {
      error = 'Utilisez le raccourci de copie de votre navigateur.'
    }
  }
  const paste = async () => {
    if (state !== 'connected') return
    try {
      const value = await navigator.clipboard.readText()
      if (value) {
        term?.paste(value)
        term?.focus()
      }
    } catch {
      error = 'Utilisez Ctrl+Maj+V ou Cmd+V pour coller dans le terminal.'
    }
  }
  onMount(() => {
    disposed = false
    void tick()
      .then(initialize)
      .catch((e) => {
        if (!disposed) error = webError(e)
      })
    return () => {
      disposed = true
      disconnect()
      resizeObserver?.disconnect()
      term?.dispose()
      term = undefined
      fit = undefined
      host = undefined
    }
  })
</script>

<section class="web-panel terminal-panel">
  <div class="web-row">
    <div>
      <span class="web-eyebrow">Conteneur du site</span>
      <h2>Terminal interactif</h2>
    </div>
    <span class="terminal-state" data-connected={state === 'connected'}
      ><span aria-hidden="true"></span>{state === 'connected'
        ? 'Connecté'
        : state === 'connecting'
          ? 'Connexion…'
          : 'Déconnecté'}</span
    >
  </div>
  <p class="web-help">
    Le shell utilise le compte du conteneur publié. Une session se ferme après 10 minutes d’inactivité ou 30 minutes au
    total ; un nouveau déploiement demande une reconnexion.
  </p>
  <div class="terminal-toolbar">
    <label class="web-field"
      ><span>Shell</span><select bind:value={shell} disabled={state === 'connecting' || state === 'connected'}
        ><option value="sh">/bin/sh</option><option value="bash">/bin/bash</option></select
      ></label
    >
    <div class="web-actions">
      {#if state === 'connected' || state === 'connecting'}<button
          class="web-button"
          onclick={() => disconnect()}
          type="button">Déconnecter</button
        >{:else}<button class="web-button web-button--primary" onclick={connect} type="button" disabled={!running}
          >Ouvrir le terminal</button
        >{/if}
      <button class="web-button" onclick={copy} type="button" disabled={!term}
        >{copied ? 'Sélection copiée' : 'Copier la sélection'}</button
      >
      <button class="web-button" onclick={paste} type="button" disabled={state !== 'connected'}>Coller</button>
      <button class="web-button" onclick={() => term?.clear()} type="button" disabled={!term}>Effacer l’écran</button>
    </div>
  </div>
  {#if !running}<div class="web-alert" role="status">
      Démarrez une version de l’application pour ouvrir son terminal.
    </div>{/if}
  {#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}
  <div class="terminal-surface" bind:this={host} role="group" aria-label="Terminal du conteneur de l’application"></div>
  <div class="terminal-footer" role="status">
    <span>{status}</span>{#if sessionId}<code>Session {sessionId.slice(0, 8)}</code>{/if}
  </div>
  <p class="web-help">
    Les fichiers du conteneur sont éphémères. Les commandes et la sortie restent dans cette session du navigateur ;
    elles ne sont pas ajoutées à l’historique des déploiements.
  </p>
</section>

<style>
  .terminal-panel {
    min-width: 0;
    display: grid;
    gap: 1rem;
  }
  .terminal-panel p {
    margin: 0;
  }
  .terminal-state {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius, 6px);
    padding: 0.4rem 0.7rem;
    font-size: 0.73rem;
    white-space: nowrap;
  }
  .terminal-state > span {
    height: 0.4rem;
    width: 0.4rem;
    background: var(--app-text-faint, #71717a);
    border-radius: 50%;
  }
  .terminal-state[data-connected='true'] > span {
    background: var(--web-success);
  }
  .terminal-toolbar {
    display: flex;
    align-items: end;
    justify-content: space-between;
    gap: 0.9rem;
    flex-wrap: wrap;
  }
  .terminal-toolbar label {
    width: 9rem;
  }
  .terminal-surface {
    min-height: 25rem;
    height: 31rem;
    width: 100%;
    max-width: 100%;
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius, 6px);
    padding: 0.8rem;
    background: #101012;
    overflow: hidden;
  }
  .terminal-surface :global(.xterm) {
    height: 100%;
  }
  .terminal-surface :global(.xterm-viewport) {
    scrollbar-color: #3a3a42 #101012;
  }
  .terminal-footer {
    display: flex;
    justify-content: space-between;
    gap: 0.5rem;
    flex-wrap: wrap;
    color: var(--web-muted);
    font-size: 0.75rem;
  }
  @media (max-width: 600px) {
    .terminal-surface {
      height: 25rem;
      min-height: 20rem;
      padding: 0.5rem;
    }
    .terminal-toolbar .web-actions {
      width: 100%;
    }
  }
</style>
