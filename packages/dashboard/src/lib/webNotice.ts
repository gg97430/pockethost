import { writable } from 'svelte/store'

export type WebConfirmRequest = {
  title: string
  text: string
  confirmLabel: string
  resolve: (confirmed: boolean) => void
}

export const webConfirmRequest = writable<WebConfirmRequest | null>(null)

/** Confirmation dans une fenêtre centrée (remplace window.confirm) ; une nouvelle demande annule la précédente. */
export const confirmAction = (title: string, text: string, confirmLabel = 'Confirmer') =>
  new Promise<boolean>((resolve) =>
    webConfirmRequest.update((current) => {
      current?.resolve(false)
      return { title, text, confirmLabel, resolve }
    })
  )

export const answerConfirm = (confirmed: boolean) =>
  webConfirmRequest.update((current) => {
    current?.resolve(confirmed)
    return null
  })
