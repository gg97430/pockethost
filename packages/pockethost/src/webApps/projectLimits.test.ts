import { mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { defaultWebProjectLimits } from '../common/webProjectLimits'
import { createProjectPolicyRegistry, createProjectTrafficLimiter } from './projectLimits'

describe('project traffic enforcement', () => {
  it('shares a bucket across sites in one project, isolates other projects and resets its minute', () => {
    let time = 0
    const gate = createProjectTrafficLimiter(() => time),
      limits = { enabled: true, requestsPerMinute: 2, concurrentRequests: 10 }
    gate('a', 'ip', limits).release()
    gate('a', 'ip', limits).release()
    expect(gate('a', 'ip', limits).allowed).toBe(false)
    expect(gate('b', 'ip', limits).allowed).toBe(true)
    expect(gate('a', 'other-ip', limits).allowed).toBe(true)
    time = 60000
    expect(gate('a', 'ip', limits).allowed).toBe(true)
  })
  it('retains WebSocket concurrency across minutes and releases only once on close', () => {
    let time = 0
    const gate = createProjectTrafficLimiter(() => time),
      limits = { enabled: true, requestsPerMinute: 100, concurrentRequests: 1 }
    const connection = gate('project', 'ip', limits)
    time = 120000
    expect(gate('project', 'ip', limits).allowed).toBe(false)
    connection.release()
    connection.release()
    expect(gate('project', 'ip', limits).allowed).toBe(true)
    expect(gate('project', 'ip', limits).allowed).toBe(false)
  })
  it('bounds memory without evicting live connections; disabled projects remain unrestricted', () => {
    let time = 0
    const gate = createProjectTrafficLimiter(() => time, 1),
      limits = { enabled: true, requestsPerMinute: 2, concurrentRequests: 1 }
    const active = gate('project', 'ip1', limits)
    expect(gate('project', 'ip2', limits).allowed).toBe(false)
    expect(gate('project', 'ip2', { ...limits, enabled: false }).allowed).toBe(true)
    active.release()
    time = 60000
    expect(gate('project', 'ip2', limits).allowed).toBe(true)
  })
})

describe('live project registry', () => {
  let root = ''
  afterEach(() => {
    if (root) rmSync(root, { recursive: true, force: true })
  })
  it('updates settings without deployment and retains the last valid policy on invalid reads', () => {
    root = mkdtempSync(join(realpathSync(tmpdir()), 'project-policy-'))
    let time = 0
    const read = createProjectPolicyRegistry(root, () => time),
      app = 'a'.repeat(15),
      project = 'b'.repeat(15),
      limits = defaultWebProjectLimits()
    expect(read(app)).toBeUndefined()
    limits.traffic.enabled = true
    writeFileSync(
      join(root, 'project-limits.json'),
      JSON.stringify({ version: 1, projects: { [project]: limits }, apps: { [app]: project } })
    )
    time = 1000
    expect(read(app)?.limits.traffic.enabled).toBe(true)
    writeFileSync(join(root, 'project-limits.json'), 'invalid')
    time = 2000
    expect(read(app)?.limits.traffic.enabled).toBe(true)
    limits.traffic.enabled = false
    writeFileSync(
      join(root, 'project-limits.json'),
      JSON.stringify({ version: 1, projects: { [project]: limits }, apps: { [app]: project } })
    )
    time = 3000
    expect(read(app)?.limits.traffic.enabled).toBe(false)
  })
})
