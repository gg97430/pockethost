/**
 * Applications web — équité de la file de déploiement (une file et un worker par hôte). Logique pure, partagée par les
 * hooks mothership (Goja JSVM).
 */
/** Jobs en attente par projet : au-delà, la mise en file répond 429. */
export const WEB_QUEUE_PROJECT_MAX = 3

/**
 * `queue` : jobs en attente, du plus ancien au plus récent. Un seul locataire : FIFO strict. Sinon le plus ancien job
 * de l'application servie il y a le plus longtemps (jamais servie : 0) ; à égalité, le plus ancien job.
 */
export const nextWebQueuedJob = <T extends { appId: string }>(
  queue: T[],
  tenantOf: (appId: string) => string,
  lastServedAt: (appId: string) => number
): T | undefined => {
  const heads = new Map<string, T>()
  for (const job of queue) if (!heads.has(job.appId)) heads.set(job.appId, job)
  if (new Set([...heads.keys()].map(tenantOf)).size < 2) return queue[0]
  let next: T | undefined,
    oldest = Infinity
  for (const [appId, job] of heads) {
    const at = lastServedAt(appId)
    if (at < oldest) {
      next = job
      oldest = at
    }
  }
  return next
}
