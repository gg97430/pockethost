import { browser } from '$app/environment'
import { writable } from 'svelte/store'

export type AppTheme = 'dark' | 'light'

const STORAGE_KEY = 'gestion-pocketbase-theme'

const normalizeTheme = (value: string | null | undefined): AppTheme | null => {
  return value === 'light' || value === 'dark' ? value : null
}

const readInitialTheme = (): AppTheme => {
  if (!browser) return 'dark'

  try {
    return (
      normalizeTheme(document.documentElement.dataset.theme) ??
      normalizeTheme(localStorage.getItem(STORAGE_KEY)) ??
      'dark'
    )
  } catch {
    return normalizeTheme(document.documentElement.dataset.theme) ?? 'dark'
  }
}

export const theme = writable<AppTheme>(readInitialTheme())

let unsubscribe: (() => void) | undefined

const applyTheme = (nextTheme: AppTheme) => {
  if (!browser) return

  const root = document.documentElement
  root.dataset.theme = nextTheme
  root.classList.toggle('wa-dark', nextTheme === 'dark')
  root.classList.toggle('wa-light', nextTheme === 'light')
  root.style.colorScheme = nextTheme
}

export const initTheme = () => {
  if (!browser || unsubscribe) return

  const nextTheme = readInitialTheme()
  applyTheme(nextTheme)
  theme.set(nextTheme)

  unsubscribe = theme.subscribe((value) => {
    applyTheme(value)
    try {
      localStorage.setItem(STORAGE_KEY, value)
    } catch {
      // The visual theme still applies if storage is blocked.
    }
  })
}

export const toggleTheme = () => {
  theme.update((value) => (value === 'dark' ? 'light' : 'dark'))
}
