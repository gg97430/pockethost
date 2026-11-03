import { mkdtempSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, expect, it } from 'vitest'
import { createWebLimitPolicy } from './webLimitPolicy'
const dirs: string[] = []
afterEach(() => {
  for (const path of dirs.splice(0)) rmSync(path, { recursive: true, force: true })
})
it('uses the last verified setting, including zero, across missing or invalid files', () => {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'kolab-policy-')))
  dirs.push(root)
  const path = join(root, 'project-limits.json')
  let time = 0
  const read = createWebLimitPolicy(root, 1200, () => time)
  expect(read()).toBe(1200)
  writeFileSync(path, JSON.stringify({ version: 1, ipRequestsPerMinute: 0 }))
  time += 600
  expect(read()).toBe(0)
  for (const invalid of [-1, '50', true, 1000001]) {
    writeFileSync(path, JSON.stringify({ version: 1, ipRequestsPerMinute: invalid }))
    time += 600
    expect(read()).toBe(0)
  }
  rmSync(path)
  const other = join(root, 'other')
  writeFileSync(other, JSON.stringify({ version: 1, ipRequestsPerMinute: 100 }))
  symlinkSync(other, path)
  time += 600
  expect(read()).toBe(0)
})
