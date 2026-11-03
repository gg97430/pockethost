import { lstatSync, readFileSync, realpathSync } from 'node:fs'
import { join } from 'node:path'

/** The worker publishes the authenticated controller policy; never read a browser-supplied path. */
export const createWebLimitPolicy = (root: string, fallback: number, now = Date.now) => {
  let value = fallback,
    checked = -Infinity
  return () => {
    if (now() - checked < 500) return value
    checked = now()
    try {
      const path = join(root, 'project-limits.json'),
        info = lstatSync(path)
      if (!info.isFile() || info.isSymbolicLink() || info.size > 2000000 || realpathSync(path) !== path) return value
      const data = JSON.parse(readFileSync(path, 'utf8'))
      if (
        data.version === 1 &&
        Number.isSafeInteger(data.ipRequestsPerMinute) &&
        data.ipRequestsPerMinute >= 0 &&
        data.ipRequestsPerMinute <= 1000000
      )
        value = data.ipRequestsPerMinute
    } catch {
      /* Retain the last verified policy while the controller or worker is unavailable. */
    }
    return value
  }
}
