import type { NextFunction, Request, Response } from 'express'
import { posix } from 'node:path'

/**
 * Routes du worker et des services locaux (`/api/web-apps/_worker`, `_notifications`, `_platform`, `_terminal`…) :
 * jamais servies depuis Internet. Le contrôleur exige aussi une connexion directe en boucle locale, mais derrière ce
 * pare-feu (même hôte) son pair TCP est 127.0.0.1 : le refus se fait donc ici, avant tout proxy, sur le chemin décodé
 * et normalisé comme le routeur de PocketBase le verrait (`%5F`, `//`, `..`, casse).
 */
const WORKER_ONLY = /^\/api\/web-apps\/_/i

export const isWorkerOnlyPath = (url: string | undefined) => {
  let path = `${url || ''}`.split(/[?#]/, 1)[0]!
  for (let round = 0; round < 3; round++) {
    let next = path
    try {
      next = decodeURIComponent(path)
    } catch {
      break
    }
    if (next === path) break
    path = next
  }
  path = posix.normalize('/' + path.replace(/\\/g, '/'))
  return WORKER_ONLY.test(path)
}

/** Middleware placé avant tout proxy : 404, comme une route inexistante. */
export const refuseWorkerRoutes = (req: Request, res: Response, next: NextFunction) => {
  if (!isWorkerOnlyPath(req.originalUrl)) return next()
  res.status(404).json({ code: 404, message: 'Not found', data: {} })
}
