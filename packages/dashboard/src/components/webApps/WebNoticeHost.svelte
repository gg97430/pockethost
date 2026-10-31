<script lang="ts">
  import { onDestroy, tick } from 'svelte'
  import { answerConfirm, webConfirmRequest } from '$lib/webNotice'

  /** Zone des applications web : ses messages de succès et d'erreur sont repris au centre de l'écran. */
  export let root: HTMLElement | undefined = undefined

  type Notice = { kind: 'success' | 'error'; text: string }
  let notice: Notice | null = null,
    okButton: HTMLButtonElement | undefined,
    timer: ReturnType<typeof setTimeout> | undefined,
    observer: MutationObserver | undefined,
    observed: HTMLElement | undefined
  const announced = new WeakMap<Element, string>(),
    focused = new WeakSet<Element>()

  const close = () => {
    clearTimeout(timer)
    notice = null
  }
  const show = async (next: Notice) => {
    clearTimeout(timer)
    notice = next
    // Un succès se referme seul ; une erreur reste jusqu'à « OK ».
    if (next.kind === 'success') timer = setTimeout(close, 3500)
    await tick()
    okButton?.focus()
  }
  const scan = () => {
    if (!observed) return
    for (const alert of observed.querySelectorAll('.web-alert--success, .web-alert--error')) {
      // Une confirmation ou un dialogue affiche déjà ses erreurs au centre ; data-notice="off" marque un état (dernier
      // déploiement en échec, sauvegarde…) et non le résultat d'une action : il reste dans la page sans fenêtre.
      if (alert.closest('.web-confirm, .web-notice, wa-dialog, [data-notice="off"]')) continue
      const text = alert.textContent?.trim() || ''
      if (!text || announced.get(alert) === text) continue
      announced.set(alert, text)
      void show({ kind: alert.classList.contains('web-alert--error') ? 'error' : 'success', text })
    }
    // Une confirmation qui s'ouvre reçoit le focus clavier sur son premier bouton.
    for (const panel of observed.querySelectorAll('.web-confirm')) {
      if (focused.has(panel)) continue
      focused.add(panel)
      panel.querySelector<HTMLElement>('button:not([disabled]), input:not([disabled])')?.focus()
    }
  }
  $: if (root && root !== observed) {
    observer?.disconnect()
    observed = root
    observer = new MutationObserver(scan)
    observer.observe(root, { childList: true, subtree: true, characterData: true })
    scan()
  }
  const keydown = (event: KeyboardEvent) => {
    if (event.key !== 'Escape') return
    if (notice) return close()
    if ($webConfirmRequest) return answerConfirm(false)
    // Échap = « Annuler » (ou « Fermer ») de la dernière confirmation ouverte.
    const panels = [...(observed?.querySelectorAll('.web-confirm') || [])]
    const cancel = [...(panels.at(-1)?.querySelectorAll<HTMLButtonElement>('button') || [])].find((button) =>
      ['Annuler', 'Fermer'].includes(button.textContent?.trim() || '')
    )
    cancel?.click()
  }
  onDestroy(() => {
    observer?.disconnect()
    clearTimeout(timer)
    answerConfirm(false)
  })
</script>

<svelte:window onkeydown={keydown} />

{#if $webConfirmRequest}<div class="web-confirm" role="alertdialog" aria-label={$webConfirmRequest.title}>
    <h3>{$webConfirmRequest.title}</h3>
    <p>{$webConfirmRequest.text}</p>
    <div class="web-actions">
      <button class="web-button web-button--primary" onclick={() => answerConfirm(true)}
        >{$webConfirmRequest.confirmLabel}</button
      ><button class="web-button" onclick={() => answerConfirm(false)}>Annuler</button>
    </div>
  </div>{/if}
{#if notice}<div
    class="web-notice web-notice--{notice.kind}"
    role={notice.kind === 'error' ? 'alertdialog' : 'status'}
    aria-label={notice.kind === 'error' ? 'Erreur' : 'Information'}
  >
    <p>{notice.text}</p>
    <div class="web-actions">
      <button class="web-button web-button--primary" bind:this={okButton} onclick={close}>OK</button>
    </div>
  </div>{/if}
