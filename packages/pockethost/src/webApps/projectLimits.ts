import { lstatSync, readFileSync, realpathSync } from 'node:fs'
import { join } from 'node:path'
import { normalizeWebProjectLimits, type WebProjectLimits } from '../common/webProjectLimits'

const ID = /^[a-z0-9]{15}$/
type Registry = { projects: Record<string, WebProjectLimits>; apps: Record<string, string> }

/** Retain the last valid policy during a controller/worker outage. Never derive policy from request headers. */
export function createProjectPolicyRegistry(root: string, now = Date.now) {
  let value: Registry = { projects: {}, apps: {} },
    checked = -Infinity
  return (appId: string) => {
    if (now() - checked >= 500) {
      checked = now()
      try {
        const path = join(root, 'project-limits.json'),
          stat = lstatSync(path)
        if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 2_000_000 || realpathSync(path) !== path)
          throw Error()
        const raw = JSON.parse(readFileSync(path, 'utf8'))
        if (raw.version !== 1 || !raw.projects || !raw.apps || Array.isArray(raw.projects) || Array.isArray(raw.apps))
          throw Error()
        const next: Registry = { projects: {}, apps: {} }
        if (Object.keys(raw.projects).length > 10000 || Object.keys(raw.apps).length > 10000) throw Error()
        for (const [id, limits] of Object.entries(raw.projects)) {
          if (!ID.test(id)) throw Error()
          next.projects[id] = normalizeWebProjectLimits(limits)
        }
        for (const [id, project] of Object.entries(raw.apps)) {
          if (!ID.test(id) || typeof project !== 'string' || !ID.test(project) || !next.projects[project]) throw Error()
          next.apps[id] = project
        }
        value = next
      } catch {
        /* Last valid settings continue to apply. An absent registry starts with every project disabled. */
      }
    }
    const projectId = value.apps[appId]
    return projectId ? { projectId, limits: value.projects[projectId]! } : undefined
  }
}

type Bucket = { start: number; requests: number; active: number }
/** Shared across every domain/site of a project. Active buckets never expire until their connections close. */
export function createProjectTrafficLimiter(now = Date.now, maxKeys = 100000) {
  const buckets = new Map<string, Bucket>()
  let swept = -Infinity
  return (projectId: string, ip: string, limits: WebProjectLimits['traffic']) => {
    if (!limits.enabled) return { allowed: true, release: () => {}, retryAfter: 0 }
    const at = now()
    if (at - swept >= 60_000) {
      swept = at
      for (const [key, bucket] of buckets) if (!bucket.active && at - bucket.start >= 60_000) buckets.delete(key)
    }
    const key = projectId + ':' + ip
    let bucket = buckets.get(key)
    if (!bucket) {
      if (buckets.size >= maxKeys) return { allowed: false, release: () => {}, retryAfter: 60 }
      bucket = { start: at, requests: 0, active: 0 }
      buckets.set(key, bucket)
    }
    if (at - bucket.start >= 60_000) {
      bucket.start = at
      bucket.requests = 0
    }
    if (bucket.requests >= limits.requestsPerMinute || bucket.active >= limits.concurrentRequests)
      return {
        allowed: false,
        release: () => {},
        retryAfter:
          bucket.active >= limits.concurrentRequests ? 1 : Math.max(1, Math.ceil((60000 - (at - bucket.start)) / 1000)),
      }
    bucket.requests++
    bucket.active++
    let released = false
    return {
      allowed: true,
      retryAfter: 0,
      release: () => {
        if (!released) {
          released = true
          bucket!.active--
        }
      },
    }
  }
}
