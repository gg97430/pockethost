import { browser } from '$app/environment'
import { userStore } from '$util/stores'
import { derived, get, writable } from 'svelte/store'
import { webAccessMe, type WebAccessMe } from './webAccess'
import { webNavVisible } from './webAccessView'

export const webAppsEnabled = import.meta.env.PUBLIC_WEB_APPS_ENABLED === 'true'
/** Réponse de GET /api/web-access/me pour l'utilisateur connecté (null tant qu'elle n'est pas chargée). */
export const webAccessStore = writable<WebAccessMe | null>(null)
export const webAccessReady = writable(false)

let request = 0
let watchedKey = ''
/** Recharge les droits (après un changement de membre, en ouvrant la rubrique, au changement de session). */
export const refreshWebAccess = async () => {
  const user = get(userStore)
  const current = ++request
  if (!browser || !webAppsEnabled || !user) {
    webAccessStore.set(null)
    webAccessReady.set(true)
    return null
  }
  try {
    const value = await webAccessMe()
    if (current === request) webAccessStore.set(value)
    return value
  } catch {
    if (current === request) webAccessStore.set(null)
    return null
  } finally {
    if (current === request) webAccessReady.set(true)
  }
}

if (browser)
  userStore.subscribe((user) => {
    const key = user ? `${user.id}:${!!user.superAdmin}:${user.suspension || ''}` : ''
    if (key === watchedKey) return
    watchedKey = key
    webAccessReady.set(false)
    void refreshWebAccess()
  })

/** Afficher l'entrée « Applications web » dans la navigation. */
export const webNavStore = derived([userStore, webAccessStore], ([user, me]) => webNavVisible(webAppsEnabled, user, me))
